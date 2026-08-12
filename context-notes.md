# 컨텍스트 노트 — 실수집 어댑터 (2026-08-09)

## 5차-b: 리뷰 결정 웹 커밋 (2026-08-13)

- **방식**: 정적 페이지라 서버가 없으므로 리뷰어 본인의 GitHub fine-grained PAT
  (이 저장소만, Contents R/W)를 페이지에 1회 등록 → GitHub Contents API로
  reviews/decisions.json을 직접 PUT. 토큰은 localStorage에만 저장, 저장소 미포함.
- **경합 처리**: GET으로 현재 sha·원격 결정을 받아 병합(원격 < 빌드 반영분 < 로컬)
  후 PUT, 409면 1회 재시도. `_` 접두 키는 load_reviews가 메타로 무시.
- **검증**: PUT 흐름은 임시 브랜치(review-ui-test)에 실제 커밋 후 삭제로 확인.
  페이지 쪽은 토큰 미설정 안내·저장/삭제 상태·병합 로직을 브라우저에서 확인.
  (토큰을 페이지에 직접 입력하는 실커밋 테스트는 하지 않음 — 자격증명 입력은
  리뷰어 몫. 첫 실사용 시 401/403 힌트가 안내한다.)
- **버그**: .exportbar button의 흰 글자색이 토큰 버튼에 상속돼 흰 배경에 흰 글씨
  → 색 명시로 수정.

## 5차: 플래그 리뷰 큐 웹 UI (2026-08-13)

- **아키텍처 유지**: 파이썬 정적 생성·단일 파일 원칙 그대로 `site/review.html` 추가.
  `export_review_data()`가 flag/error/caution 판정을 출처별 주장값·근거 인용·원문
  스냅샷 발췌(아카이브 역참조)와 함께 review-data.json으로 내보내고, 템플릿에 임베드.
- **리뷰 왕복 구조**: 결정은 브라우저 localStorage에 쌓고 "결정 JSON 내려받기" →
  `reviews/decisions.json` 커밋 → 다음 파이프라인 실행에서 등급 반영.
  dismiss=강등 해제(+"사람 리뷰로 해제됨" 노트), confirm=강등 유지(+"확인됨" 노트).
  결정 키는 (모델|변형|규칙|사양키|요약) 해시 — DB 재구축에도 안정.
- **snapshot_uri 버그 수정**: ingest가 "fixtures/snapshots.json"을 하드코딩해
  수집 아카이브의 원문 발췌 역참조가 불가능했음 → 실제 경로 저장으로 수정.
- **왕복 검증 완료**: 테스트 dismiss 결정으로 MN1130 KV45 max_thrust C→B 복원 확인 후
  결정 파일은 {}로 원복(실제 리뷰 결정은 사람 몫).

## 4차: 추력 조건 매칭 → 실측 최대추력 연계 (2026-08-12)

- **조건 매칭 기준**: 테스트 프로펠러 직경 ±5%·피치 ±10%(Tyto propeller.measures 사용),
  전압 ±12%, 그리고 **풀스로틀 도달**(µs 스로틀 ≥1900 또는 % ≥97, 최대추력 지점이
  시리즈 최대 스로틀의 97% 이상)일 때만 실측 시리즈 최대값을 max_thrust_g로 채택.
  U15 V2 테스트는 1614µs까지만 스윕해 정당하게 기각됨(전 회차 판단 유지).
- **S1 한도 15→27**(멀티로터 카테고리 전체) → Tyto 대조 3건(U15II·MN4006·MN2806).
  MN4006 KV380: 테스트 6건 중 조건 일치 3건, "TM-MF1302(13.4×4.8) 24.4V 풀스로틀"
  실측 1,546g 채택 — S1 주장(13×4.4" 24V, 1,633g)과 편차 5.6%.
- **R-X3(실측 대조) 신설**: 실측(S3)은 동급 출처가 아니라 판정 기준 — R-X1에서 S3를
  제외하고, 조건 호환 주장을 실측 대비 편차로 판정(≤10% "실측 확인" pass·A 유지,
  10~20% 주의, >20% 플래그). pass 사유는 grade_note로 사이트에 노출.
  주의: R-M1은 모멘텀 이론 규칙이 선점한 ID — 실측 대조는 R-X3로 명명(충돌 회피).
- **R-X1 조건 그룹핑**: requires_conditions 필드는 프로펠러 시그니처(직경·피치 반올림)
  그룹 내에서만 교차 비교. 조건 미명시 주장은 전 그룹 참여(제조사 대표 주장 가정).
- **결과**: A등급 7건 — MN4006 KV380 max_thrust_g(실측 1,546g) 포함. 부품 47 ·
  주장값 541 · A:7 B:410 C:6 D:0. 편집 실수(rules_common 선언부 소실)로 1회 재실행 —
  아카이브가 수집 직후 저장된 덕에 재수집 없이 복구.

## 3차: Tyto Robotics(S3) 실측 연계 — 첫 A등급 (2026-08-12)

- **접근성**: robots.txt 전면 허용. /motors 목록의 인라인 JSON(:components 속성)에
  브랜드·측정 속성(kv·weight)·테스트 수가 다 있음. 테스트 메타는 `GET /tests/search`
  (filters/relations를 JSON 문자열로 인코딩하는 Laravel API — 앱 번들에서 계약 확인).
- **엔티티 해소**: Tyto "U15 V2" ≡ T-Motor 스토어 "U15II/U15Ⅱ" — `_series_key`로
  세대 표기(V2↔Ⅱ/II)를 동치화해 S1 모델 집합과 대조. NFKC 정규화가 Ⅱ→II를 처리.
- **A등급 원칙**: S3 주장값은 조건 독립적인 측정 속성(kv·무게)만. 테스트의 실측
  **최대 추력**(U15 V2: Xoar 40x10, 16.3kgf)은 제조사 주장(G40x13.1, 28.9kg)과
  프로펠러가 달라 동일 지표가 아님 — 맹목 비교하면 부당한 플래그(56% 편차)가 되므로
  조건 매칭 로직 전까지 보류. conditions에 테스트 출처·장비·스태프 검증 여부 기록.
- **대표값 정렬 변경**(load_export): 실측(S3) > 신뢰 가중치 > S1 — 계획서 §5의
  "실측 연계 필드 A등급"을 코드로 집행. S3 신뢰 가중 0.9인 채로 두되 정렬만 우선.
- **부수 버그 수정**: T-Motor 테스트 표 컬럼 순서가 페이지마다 달라(U15: Thrust 7번째,
  U13 KV65: Thrust가 Throttle 바로 뒤) 고정 위치 파싱이 U15Ⅱ 추력을 5520g(실은 Power W)
  으로 오추출하고 있었음 → 헤더 행 파싱 + 행별 스로틀 셀 동적 탐지로 재작성.
  수정 후 U15Ⅱ KV80 28,900g / KV100 37,000g (Tyto 실측과 자릿수 정합).
- **결과**: A등급 2건 발행 — U15II KV80 kv=80(S1과 일치), weight=1780g(S1 1740g,
  편차 2.3% 상호 확인). 출처 20 · 주장값 339 · A:2 B:262 C:6 D:0.

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
