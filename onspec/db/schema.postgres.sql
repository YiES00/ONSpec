-- =============================================================
-- 드론 상용부품(COTS) DB — 운영용 PostgreSQL 스키마 (계획서 §6)
-- 3원칙을 스키마 수준에서 강제:
--   원칙 1  출처 없는 데이터는 존재할 수 없다  → component_specs.source_id NOT NULL FK
--   원칙 2  조건 없는 성능 수치는 비교할 수 없다 → component_specs.conditions JSONB
--   원칙 3  모든 수치는 신뢰도 등급을 갖는다   → component_specs.grade (A~D)
-- =============================================================

CREATE TYPE source_tier AS ENUM ('S1_manufacturer', 'S2_vendor', 'S3_benchmark', 'S4_certification');
CREATE TYPE spec_grade  AS ENUM ('A', 'B', 'C', 'D');
CREATE TYPE verify_verdict AS ENUM ('pass', 'caution', 'flag', 'error');

-- ---------- 제조사 / 판매처 ----------
CREATE TABLE manufacturers (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    name_normalized TEXT NOT NULL UNIQUE,          -- 소문자·공백 제거 정규화 키
    hq_country    CHAR(2),                          -- ISO 3166-1 alpha-2, 본사국
    website       TEXT,
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE vendors (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    website       TEXT,
    country       CHAR(2),
    trust_weight  NUMERIC(3,2) NOT NULL DEFAULT 0.60,  -- 교차검증 가중치 (S1=1.0 기준)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 카테고리 표준 사양 정의 ----------
CREATE TABLE categories (
    id            TEXT PRIMARY KEY,                 -- 'motor' | 'esc' | 'propeller' | 'battery' ...
    name_ko       TEXT NOT NULL,
    name_en       TEXT NOT NULL,
    phase         SMALLINT NOT NULL DEFAULT 1       -- 로드맵 Phase
);

CREATE TABLE spec_definitions (
    id            BIGSERIAL PRIMARY KEY,
    category_id   TEXT NOT NULL REFERENCES categories(id),
    spec_key      TEXT NOT NULL,                    -- 'max_thrust_g', 'kv', 'capacity_mah' ...
    name_ko       TEXT NOT NULL,
    unit          TEXT,                             -- 정규화 단위(SI 우선). NULL=무단위/텍스트
    value_type    TEXT NOT NULL CHECK (value_type IN ('numeric','integer','text','bool','range')),
    requires_conditions BOOLEAN NOT NULL DEFAULT FALSE,  -- 원칙 2: 성능형 사양 여부
    compare_default BOOLEAN NOT NULL DEFAULT FALSE, -- 비교표 기본 노출 여부
    sort_order    SMALLINT NOT NULL DEFAULT 100,
    UNIQUE (category_id, spec_key)
);

-- ---------- 부품 ----------
CREATE TABLE components (
    id              BIGSERIAL PRIMARY KEY,
    category_id     TEXT NOT NULL REFERENCES categories(id),
    manufacturer_id BIGINT NOT NULL REFERENCES manufacturers(id),
    model_name      TEXT NOT NULL,
    model_normalized TEXT NOT NULL,                 -- 중복 병합용 정규화 키
    variant         TEXT,                           -- KV320, 15C/XT90-S 등 변형 구분
    mfg_country     CHAR(2),                        -- 실제 생산국 (확인된 경우에만)
    mfg_country_status TEXT NOT NULL DEFAULT 'unconfirmed'
                    CHECK (mfg_country_status IN ('confirmed','claimed','unconfirmed')),
    is_synthetic_test BOOLEAN NOT NULL DEFAULT FALSE, -- 검증엔진 테스트용 합성 픽스처 표시
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category_id, manufacturer_id, model_normalized, variant)
);

-- ---------- 출처 (원칙 1의 앵커) ----------
CREATE TABLE sources (
    id            BIGSERIAL PRIMARY KEY,
    tier          source_tier NOT NULL,
    origin_url    TEXT NOT NULL,
    vendor_id     BIGINT REFERENCES vendors(id),    -- S2일 때
    title         TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL,
    snapshot_uri  TEXT,                             -- 오브젝트 스토리지의 원문 스냅샷 경로
    content_hash  TEXT,                             -- 변경 감지용 SHA-256
    UNIQUE (origin_url, content_hash)
);

-- ---------- 사양 값 (SCD Type 2 이력) ----------
CREATE TABLE component_specs (
    id            BIGSERIAL PRIMARY KEY,
    component_id  BIGINT NOT NULL REFERENCES components(id),
    spec_def_id   BIGINT NOT NULL REFERENCES spec_definitions(id),
    value_num     NUMERIC,                          -- value_type=numeric/integer
    value_text    TEXT,                             -- value_type=text/bool
    unit_original TEXT,                             -- 원문 단위 (정규화 전)
    value_num_original NUMERIC,                     -- 원문 값 (정규화 전)
    conditions    JSONB NOT NULL DEFAULT '{}'::jsonb, -- 원칙 2: {"prop":"22in","voltage_v":24.0,...}
    source_id     BIGINT NOT NULL REFERENCES sources(id),  -- 원칙 1: NOT NULL 강제
    extracted_quote TEXT,                           -- LLM 추출 근거 원문 인용 (환각 방지)
    grade         spec_grade NOT NULL DEFAULT 'C',  -- 원칙 3
    grade_note    TEXT,
    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to      TIMESTAMPTZ,                      -- NULL = 현재 유효 (SCD2)
    is_current    BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX idx_specs_current ON component_specs (component_id, spec_def_id) WHERE is_current;
CREATE INDEX idx_specs_conditions ON component_specs USING GIN (conditions);

-- ---------- 스테이징: 출처별 원시 주장값 (교차검증 입력) ----------
CREATE TABLE spec_claims (
    id            BIGSERIAL PRIMARY KEY,
    component_id  BIGINT NOT NULL REFERENCES components(id),
    spec_def_id   BIGINT NOT NULL REFERENCES spec_definitions(id),
    value_num     NUMERIC,                          -- 정규화 후 값
    value_text    TEXT,
    value_num_original NUMERIC,
    unit_original TEXT,
    conditions    JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_id     BIGINT NOT NULL REFERENCES sources(id),
    extracted_quote TEXT NOT NULL,                  -- LLM 추출 근거 인용 강제
    trust_weight  NUMERIC(3,2) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_claims_component_spec ON spec_claims (component_id, spec_def_id);

-- ---------- 가격 시계열 (append-only) ----------
CREATE TABLE prices (
    id            BIGSERIAL PRIMARY KEY,
    component_id  BIGINT NOT NULL REFERENCES components(id),
    vendor_id     BIGINT NOT NULL REFERENCES vendors(id),
    source_id     BIGINT NOT NULL REFERENCES sources(id),
    currency      CHAR(3) NOT NULL,                 -- 원 통화 그대로 저장
    amount        NUMERIC(12,2) NOT NULL,
    amount_usd    NUMERIC(12,2),                    -- 일일 환율 배치로 갱신
    amount_krw    NUMERIC(14,0),
    fx_date       DATE,
    pack_qty      SMALLINT NOT NULL DEFAULT 1,      -- 묶음 판매 수량 (단가 환산용)
    in_stock      BOOLEAN,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_prices_component_time ON prices (component_id, observed_at DESC);

-- ---------- 검증 로그 ----------
CREATE TABLE verification_log (
    id            BIGSERIAL PRIMARY KEY,
    component_id  BIGINT NOT NULL REFERENCES components(id),
    spec_def_id   BIGINT REFERENCES spec_definitions(id),  -- NULL = 부품 수준 판정
    rule_id       TEXT NOT NULL,                    -- 'R-M1' 등 (docs/METHODOLOGY.md 참조)
    verdict       verify_verdict NOT NULL,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,      -- 계산 근거, 비교값 목록 등
    reviewed_by   TEXT,                             -- 사람 리뷰 시 기록
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_vlog_component ON verification_log (component_id, created_at DESC);

-- ---------- 뷰: 현재 유효 사양 + 정의 조인 ----------
CREATE VIEW v_current_specs AS
SELECT cs.component_id, sd.category_id, sd.spec_key, sd.name_ko, sd.unit,
       cs.value_num, cs.value_text, cs.conditions, cs.grade, cs.grade_note,
       cs.source_id, cs.extracted_quote
FROM component_specs cs
JOIN spec_definitions sd ON sd.id = cs.spec_def_id
WHERE cs.is_current;
