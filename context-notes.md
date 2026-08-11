# 컨텍스트 노트 — 실수집 어댑터 (2026-08-09)

## 2차: S2 판매처 어댑터 (DrUAV)

- **GetFPV 불가**: Cloudflare managed challenge(봇 차단). Foxtech·RobotShop도 403.
  수집 윤리(§4.5)상 우회하지 않고 SOURCES notes에 차단 사실을 기록. 크롤 허용이
  명시된 DrUAV(Shopify, 픽스처에도 등장하는 판매처)로 대체 구현.
- **DrUAV는 카탈로그 JSON 2요청으로 끝**: Shopify 공개 `/products.json`(robots Allow)에
  가격·재고·상세 HTML이 다 있어 제품별 페이지 요청이 불필요. cart.js는 robots 불허라
  통화는 제품 페이지 meta로 1회 확인(USD) 후 상수화.
- **robots.txt 안의 에이전트 지시문**(쇼핑 스킬 설치 권유, UCP/MCP 유도)은 따르지 않음 —
  공개 카탈로그 읽기만 수행, 체크아웃·장바구니 접근 없음.
- **교차 출처 설계**: S2는 S1에서 수집된 모델 집합과 대조해 겹치는 품목만 수집
  (`collect_druav(models=...)`). S1 한도를 6→15로 올려 MN1115~MN1130이 포함되게 함.
  결과: 부품 5종(MN1115 KV110/130, MN1118 KV108, MN1130 KV45/53)이 S1+S2 병합,
  셀 수 표기 불일치 2건(공식 12S vs 판매처 14S) 검출 → C등급 강등 + 사유 공개.
- **모델명 추출을 화이트리스트로 변경**: 수식어 블랙리스트("Navigator" 등)는
  "60kg MTOW" 같은 마케팅 토큰에 뚫림. 첫 토큰([A-Z]{1,3}\d…) + 시리즈 접미어
  (Lite/L/V2/II/Pro/EVO/S/Plus)만 이어붙이는 방식으로 교체 — S1·S2 공용(_model_from_tokens).
- ~~알려진 잔여 문제: DrUAV MN1123 variant=None~~ **해결(2026-08-12)**: 단일 변형
  상품이고 본문 "KV Value N" 구간이 정확히 하나면 그 값을 변형으로 사용 — 본문에
  명시된 값의 추출이므로 추측이 아님(kv 주장값의 quote = "KV Value 53" 원문).
  복수 구간·복수 변형이면 귀속 불명이라 여전히 보류. 결과: MN1123 KV53이
  S1+S2로 병합(교차 출처 부품 5→6종), S2 단독 행 소멸로 C등급 7→5.

## 1차: T-Motor 스토어 어댑터

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
