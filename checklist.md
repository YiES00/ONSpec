# 실수집 어댑터 — 체크리스트

## 2차: S2 판매처 어댑터 (2026-08-09)

- [x] GetFPV 정찰 → Cloudflare 봇 차단 확인, 우회하지 않음 (Foxtech·RobotShop도 403)
- [x] DrUAV(Shopify) 정찰 — robots 허용 확인, 공개 카탈로그 JSON 구조 파악
- [x] collect.py: DrUAVAdapter — products.json → 가격·재고 + body_html 사양표 파싱
- [x] S1 한도 15로 확대 (MN1115~MN1130 포함 → 교차 출처 대상 확보)
- [x] run_daily.py: S1 수집 모델 집합으로 S2 대조 수집 연결
- [x] 실행 검증 — 부품 5종 S1+S2 교차 병합, 셀 수 표기 불일치 2건 검출(C등급 강등)
- [x] 커밋

## 1차: T-Motor 스토어 어댑터

- [x] store.tmotor.com robots.txt 확인 + 페이지 구조 정찰
- [x] collect.py: PoliteFetcher.fetch 실구현 (stdlib urllib, UA·레이트리밋·robots 준수)
- [x] collect.py: TMotorStoreAdapter — 모터 목록 → 제품 페이지 → 사양 테이블 파싱 → snapshots 형식 claims 생성
- [x] 원문 스냅샷 보존 (fixtures/collected/ 에 JSON 아카이브 — 원칙 1)
- [x] run_daily.py: 비픽스처 경로를 어댑터 수집 → ingest_snapshots 로 연결
- [x] 실수집 run_daily 실행 → 검증·등급·data.json 생성 확인 (부품 12 · 주장값 111 · 플래그 0)
- [x] site/generate.py 빌드 + 결과 확인 (122 KB)
- [x] 커밋
