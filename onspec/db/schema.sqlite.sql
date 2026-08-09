-- SQLite 데모용 스키마 — db/schema.postgres.sql 과 동일 구조 (운영 정식은 PostgreSQL)
PRAGMA foreign_keys = ON;

CREATE TABLE manufacturers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL UNIQUE,
    hq_country TEXT,
    website TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    website TEXT,
    country TEXT,
    trust_weight REAL NOT NULL DEFAULT 0.60,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name_ko TEXT NOT NULL,
    name_en TEXT NOT NULL,
    phase INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE spec_definitions (
    id INTEGER PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES categories(id),
    spec_key TEXT NOT NULL,
    name_ko TEXT NOT NULL,
    unit TEXT,
    value_type TEXT NOT NULL CHECK (value_type IN ('numeric','integer','text','bool','range')),
    requires_conditions INTEGER NOT NULL DEFAULT 0,
    compare_default INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100,
    UNIQUE (category_id, spec_key)
);

CREATE TABLE components (
    id INTEGER PRIMARY KEY,
    category_id TEXT NOT NULL REFERENCES categories(id),
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
    model_name TEXT NOT NULL,
    model_normalized TEXT NOT NULL,
    variant TEXT,
    mfg_country TEXT,
    mfg_country_status TEXT NOT NULL DEFAULT 'unconfirmed'
        CHECK (mfg_country_status IN ('confirmed','claimed','unconfirmed')),
    is_synthetic_test INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (category_id, manufacturer_id, model_normalized, variant)
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('S1_manufacturer','S2_vendor','S3_benchmark','S4_certification')),
    origin_url TEXT NOT NULL,
    vendor_id INTEGER REFERENCES vendors(id),
    title TEXT,
    fetched_at TEXT NOT NULL,
    snapshot_uri TEXT,
    content_hash TEXT,
    UNIQUE (origin_url, content_hash)
);

CREATE TABLE component_specs (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    spec_def_id INTEGER NOT NULL REFERENCES spec_definitions(id),
    value_num REAL,
    value_text TEXT,
    unit_original TEXT,
    value_num_original REAL,
    conditions TEXT NOT NULL DEFAULT '{}',      -- JSON 문자열
    source_id INTEGER NOT NULL REFERENCES sources(id),  -- 원칙 1: NOT NULL 강제
    extracted_quote TEXT,
    grade TEXT NOT NULL DEFAULT 'C' CHECK (grade IN ('A','B','C','D')),
    grade_note TEXT,
    valid_from TEXT NOT NULL DEFAULT (datetime('now')),
    valid_to TEXT,
    is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_specs_current ON component_specs (component_id, spec_def_id, is_current);

CREATE TABLE spec_claims (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    spec_def_id INTEGER NOT NULL REFERENCES spec_definitions(id),
    value_num REAL,
    value_text TEXT,
    value_num_original REAL,
    unit_original TEXT,
    conditions TEXT NOT NULL DEFAULT '{}',
    source_id INTEGER NOT NULL REFERENCES sources(id),
    extracted_quote TEXT NOT NULL,
    trust_weight REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_claims_component_spec ON spec_claims (component_id, spec_def_id);

CREATE TABLE prices (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    currency TEXT NOT NULL,
    amount REAL NOT NULL,
    amount_usd REAL,
    amount_krw REAL,
    fx_date TEXT,
    pack_qty INTEGER NOT NULL DEFAULT 1,
    in_stock INTEGER,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE verification_log (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    spec_def_id INTEGER REFERENCES spec_definitions(id),
    rule_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass','caution','flag','error')),
    detail TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
