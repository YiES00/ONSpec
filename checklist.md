# T-Motor 스토어 실수집 어댑터 — 체크리스트

- [x] store.tmotor.com robots.txt 확인 + 페이지 구조 정찰
- [x] collect.py: PoliteFetcher.fetch 실구현 (stdlib urllib, UA·레이트리밋·robots 준수)
- [x] collect.py: TMotorStoreAdapter — 모터 목록 → 제품 페이지 → 사양 테이블 파싱 → snapshots 형식 claims 생성
- [x] 원문 스냅샷 보존 (fixtures/collected/ 에 JSON 아카이브 — 원칙 1)
- [x] run_daily.py: 비픽스처 경로를 어댑터 수집 → ingest_snapshots 로 연결
- [x] 실수집 run_daily 실행 → 검증·등급·data.json 생성 확인 (부품 12 · 주장값 111 · 플래그 0)
- [x] site/generate.py 빌드 + 결과 확인 (122 KB)
- [x] 커밋
