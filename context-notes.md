# 컨텍스트 노트 — T-Motor 실수집 어댑터 (2026-08-09)

## 결정 사항

- **LLM 추출 대신 결정적 파서**: `ANTHROPIC_API_KEY` 미설정 + `anthropic` 패키지 미설치 상태.
  T-Motor 스토어 제품 페이지는 구조화된 사양 테이블을 제공하므로 S1 어댑터에서
  rule-based 파싱으로 claims를 직접 생성한다. 각 값의 quote는 파싱한 원문 행 텍스트를
  그대로 사용해 "출처 없는 사양은 없다" 원칙(extracted_quote NOT NULL)을 유지한다.
  운영에서 비정형 페이지(S2 판매처 등)로 확장할 때 extract.py(LLM)로 전환하면 된다.
- **HTTP 클라이언트는 stdlib urllib**: requests는 requirements-prod에 있으나 미설치.
  의존성 추가 없이 표준 라이브러리로 구현 (README의 "Python 3.12 표준 라이브러리" 방침 유지).
- **수집 결과 아카이브**: 실수집 스냅샷은 fixtures/snapshots.json과 동일 형식으로
  `fixtures/collected/tmotor-YYYYMMDD.json`에 보존 (원칙 1: 원문 아카이브).
- **실수집 모드 범위**: `run_daily.py`(--fixtures 없이)는 구현된 어댑터(T-Motor)만 수집.
  픽스처와 혼합하지 않음 — 사용자 요청이 "픽스처 대신 실수집".

## 진행 중 발견

- **robots.txt 함정**: store.tmotor.com은 기본 파이썬/curl UA를 403 차단한다.
  `RobotFileParser.read()`가 기본 UA로 요청 → 403 → "전체 불허"로 해석되는 문제가 있어,
  robots.txt를 명시 UA로 직접 받아 `rp.parse()`하도록 PoliteFetcher를 수정했다.
- **제품 페이지 3가지 유형** (전부 `basicParameterBox` 사양 테이블 사용):
  1. MN 시리즈: 공통 사양 + "KV: 320" 행으로 시작하는 변형별 테이블.
  2. U-Efficiency (U8 Lite): 유형 1과 동일하나 라벨 표기가 다름 ("Motor Weight (Incl. Cable)" 등).
  3. U-Power (U3/U11/U13/U15): "Test Item: KV130" 또는 "Test Item: U15 Ⅱ KV80" 행으로
     변형 구분. U3처럼 KV가 h1에만 있는 단일 변형 페이지도 있음 → h1 폴백.
- **KV 추출 함정**: "Test Item: U15 Ⅱ KV80"에서 첫 숫자를 뽑으면 모델명의 15가 잡힌다.
  KV 토큰(`KV\d+`) 우선, 값 전체가 숫자일 때만 그대로 사용.
- **max_thrust_g는 Test Data 표에서**: 제조사 벤치 테스트 표의 최대 스로틀 행을
  추력 주장값으로 사용, conditions에 프로펠러·전압·스로틀 명시 (requires_conditions 충족).
  동일 KV 중복 그룹에는 1회만 귀속.
- **복합 표기는 생략**: "Shaft Diameter: IN：6mm，OUT：4mm" 같은 값은 추측 없이 생략
  (단일 수치일 때만 매핑). "Weight Excluding Cables: /" 도 자연 탈락.
- **최종 결과**: 제품 6페이지 → 부품 12(KV 변형 단위) · 주장값 111 · 전부 B등급.
  단일 S1 출처라 교차 검증 플래그 0 — 파서 버그로 인한 가짜 "출처 간 불일치"
  (같은 페이지의 복수 SKU 테이블 병합)를 변형 분리로 제거한 결과다.
