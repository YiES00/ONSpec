# ONSPEC (온스펙) — 드론 상용부품(COTS) 검증 DB · Phase 1 MVP

「드론 상용부품 DB·비교 플랫폼 구축 계획서」의 **Phase 1(추진계 4개 카테고리: 모터·ESC·프로펠러·배터리)** 을
실행 가능한 형태로 구현한 저장소다. 이름은 규격 판정 용어 on-spec/off-spec(규격 내/규격 이탈)에서 왔다 — 이 DB가 하는 일이 곧 그 판정이다.
슬로건이 곧 설계 원칙이다: **출처 없는 사양은 없다.**

## 지금 바로 실행

```bash
python3 pipeline/run_daily.py --fixtures   # 인제스트 → 검증 → 등급 → data.json
python3 pipeline/run_daily.py              # 실수집 모드: T-Motor Store(S1) 어댑터
python3 site/generate.py                   # site/dist/index.html 빌드
# dist/index.html 을 브라우저로 열면 끝 (외부 요청 없는 단일 파일)
```

의존성: Python 3.12 표준 라이브러리 + Jinja2 (`pip install -r requirements.txt`).

## 계획서 ↔ 구현 대응

| 계획서 | 구현 |
|---|---|
| §2 데이터 3원칙 | `db/schema.postgres.sql` — 출처 NOT NULL FK, conditions JSONB, 필드 단위 grade |
| §3 카테고리 표준 사양 | `categories/*.json` (4개 카테고리 × 사양 키 정의) |
| §4.2 수집 파이프라인 | `pipeline/collect.py`(robots·레이트리밋·변경감지) → `extract.py`(LLM, 근거인용 강제·이중추출) → `ingest.py`(정규화·적재) |
| §4.3 일일 갱신 | `pipeline/run_daily.py` + `.github/workflows/daily.yml` (KST 03:00 크론) |
| §5 검증 3단계 | `pipeline/verify.py` — 물리 규칙 14종 + 교차 출처 대조. 실측 연계(A등급)는 S3/S4 커넥터 연동 시 활성화 |
| §5.5 사람 리뷰 | 플래그는 D등급 유지 + Actions 알림. 리뷰 UI는 Phase 1 잔여 과제 |
| §6 DB 설계 | 운영 정식 DDL `db/schema.postgres.sql` / 데모 실행용 `db/schema.sqlite.sql` (동일 구조) |
| §7 사이트 | `site/` — 비교표(등급 칩·조건 툴팁), 필드별 출처 드로어, 방법론 전문 공개 |
| 방법론 공개(신뢰 구축) | `docs/METHODOLOGY.md` = 사이트 「검증 방법론」 탭 |

## 데모 데이터에 대한 정직 고지

- 시드 21개 스냅샷은 **2026-08-08 실제 웹 수집 결과**다(픽스처 = 보존된 원문 스냅샷, 계획서 원칙 1의
  "원문 아카이브"와 동일한 역할). 모든 출처 URL·원문 인용이 실제다.
- 예외 1건: `2207 PowerBeast`는 검증 엔진 시연용 **합성 픽스처(실제 제품 아님)** 이며 DB·사이트 모두에 명시된다.
- 검증 엔진이 실제 데이터에서 잡아낸 것들: 판매처 리스팅의 물리적으로 불가능한 무게 표기(밀도가 강철 초과),
  KV값을 전압으로 오기한 리스팅, 제조사 공식 페이지의 셀 구성 모순(22.2V인데 12S1P 표기),
  출처 간 최대추력 불일치(6.6/6.7/7kg), 프로펠러 피치 단위 오기('°') 등 — 전부 D/C등급 강등 + 사유 공개로 처리된다.
- A등급(실측 검증)은 S3(스러스트 스탠드)·S4(FCC) 커넥터 연동 전이라 0건이다. 숨기지 않는다.

## 데모 ↔ 운영 경계

| | 데모(이 저장소 그대로) | 운영 전환 |
|---|---|---|
| DB | SQLite (`db/drone_cots.sqlite3`) | PostgreSQL — `schema.postgres.sql` 적용, `pipeline/db.py`에 psycopg 연결 추가 |
| 수집 | 픽스처(`--fixtures`) 또는 T-Motor Store 실수집(구현됨) | `collect.py`에 나머지 소스 어댑터 추가 |
| 추출 | 픽스처의 사전 추출 claims | `extract.py` + `ANTHROPIC_API_KEY` (근거 인용·이중 추출 프롬프트 내장) |
| 프런트 | Python 정적 생성(단일 파일) | 동일한 `data.json` 계약 위에 Astro/Next(ISR)로 교체 가능 |
| 환율 | 표시 대기 | 한국은행/ECB 고시 환율 일일 배치 → `prices.amount_usd/krw` |

기술 스택 결정: MVP는 계획서의 저비용 경로(정적 생성 + GitHub Actions 크론)를 택하되, 프런트 프레임워크
의존 없이 파이프라인과 같은 Python으로 통일했다. 데이터 접근이 `site/data.json` 단일 계약이므로
Phase 2에서 프런트만 독립적으로 교체할 수 있다.

## 디렉터리

```
db/          스키마 (postgres=정식, sqlite=데모) + 생성된 DB
categories/  카테고리별 표준 사양 정의
fixtures/    보존된 원문 스냅샷(실수집) — 운영의 "원문 아카이브"에 대응
pipeline/    collect → extract → ingest → verify → load_export → run_daily
site/        generate.py + 템플릿 → dist/index.html
docs/        METHODOLOGY.md (공개 검증 방법론)
```

## 다음 단계 (Phase 1 잔여 → Phase 2)

1. S1/S2 실커넥터 2~3곳 구현 → 픽스처 모드 졸업, 가격 시계열 축적 시작
2. Tyto Robotics·커뮤니티 스러스트 스탠드(S3) 연계 → 첫 A등급 발행
3. 플래그 리뷰 큐 웹 UI(원문 스냅샷·근거·판정 사유 한 화면, 일 30분 목표)
4. PostgreSQL 전환 + Meilisearch 검색
5. Phase 2 카테고리 착수: EO/IR·데이터링크 — FCC ID(S4) 커넥터가 핵심 검증 수단
