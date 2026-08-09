"""일일 갱신 오케스트레이터 — 계획서 §4.2/§4.3.

데모:  python3 run_daily.py --fixtures
운영:  python3 run_daily.py            (collect→extract 실연동 후)

운영 타임라인(KST): 03:00 크롤 → 05:00 추출·정규화 → 05:30 검증 →
06:00 플래그 리뷰 알림 → 09:00 사이트 갱신. GitHub Actions 크론은
.github/workflows/daily.yml 참조.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import init_db, ROOT
from ingest import ingest_snapshots
from verify import run_verification
from load_export import load_canonical_specs, export_site_data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", action="store_true",
                    help="크롤 대신 fixtures/snapshots.json(보존된 원문 스냅샷)을 입력으로 사용")
    args = ap.parse_args()

    print("[1/5] DB 초기화 + 카테고리 사양 정의 시드")
    conn = init_db(fresh=True)

    if args.fixtures:
        print("[2/5] 인제스트(픽스처 모드) — 원문 스냅샷 → 출처·부품·주장값 스테이징")
        stats = ingest_snapshots(conn, ROOT / "fixtures" / "snapshots.json")
    else:
        print("[2/5] 수집·추출 — 운영 모드는 collect.py/extract.py 연동 필요")
        print("      (ANTHROPIC_API_KEY 설정 후 소스 커넥터 활성화)")
        return 1
    print(f"      출처 {stats['sources']} · 부품 {stats['components']} · "
          f"주장값 {stats['claims']} · 가격 {stats['prices']}")

    print("[3/5] 검증 엔진 — 물리 타당성 + 교차 출처 대조")
    v = run_verification(conn)
    print(f"      통과 {v['pass']} · 주의 {v['caution']} · 플래그 {v['flag']} · 오류 {v['error']}")

    print("[4/5] 적재 — 대표값 선정 + 필드 단위 등급(A~D) 확정")
    g = load_canonical_specs(conn)
    print(f"      등급 분포  A:{g['A']}  B:{g['B']}  C:{g['C']}  D:{g['D']}")

    print("[5/5] 내보내기 — site/data.json 생성")
    s = export_site_data(conn, ROOT / "site" / "data.json")
    print(f"      부품 {s['components']} · 사양행 {s['spec_rows']} · 출처 {s['sources']}")
    print("완료. 다음: python3 ../site/generate.py 로 정적 사이트 빌드")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
