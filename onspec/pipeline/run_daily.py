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
from load_export import load_canonical_specs, export_site_data, export_review_data


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
        print("[2/5] 실수집 — T-Motor Store(S1) + DrUAV(S2) + Tyto Robotics(S3) 어댑터")
        from datetime import datetime, timezone
        import json
        from collect import collect_tmotor, collect_druav, collect_tyto
        data = collect_tmotor(max_products=27)   # 멀티로터 모터 카테고리 전체
        if not data["snapshots"]:
            print("      수집 결과 0건 — 중단")
            return 1
        # S2·S3는 S1에서 수집된 모델만 대조 수집 → 교차 검증·가격 축적·실측 연계(A등급)
        s1_models = {c["model_name"] for s in data["snapshots"] for c in s["claims"]}
        # 실측 추력 조건 매칭용 컨텍스트: 모델별 변형 집합 + 추력 주장 측정 조건
        s1_variants, thrust_ctx = {}, {}
        for s in data["snapshots"]:
            for c in s["claims"]:
                s1_variants.setdefault(c["model_name"], set()).add(c["variant"])
                for sp in c["specs"]:
                    if sp["key"] == "max_thrust_g" and sp["conditions"].get("prop_diameter_in"):
                        thrust_ctx[(c["model_name"], c["variant"])] = sp["conditions"]
        data["snapshots"] += collect_druav(models=s1_models)["snapshots"]
        data["snapshots"] += collect_tyto(models=s1_models, s1_variants=s1_variants,
                                          thrust_ctx=thrust_ctx)["snapshots"]
        # 원칙 1: 원문 스냅샷 아카이브 보존
        archive = (ROOT / "fixtures" / "collected" /
                   f"collected-{datetime.now(timezone.utc):%Y%m%d}.json")
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"      스냅샷 아카이브: {archive.relative_to(ROOT)}")
        stats = ingest_snapshots(conn, archive)
    print(f"      출처 {stats['sources']} · 부품 {stats['components']} · "
          f"주장값 {stats['claims']} · 가격 {stats['prices']}")

    print("[3/5] 검증 엔진 — 물리 타당성 + 교차 출처 대조")
    v = run_verification(conn)
    print(f"      통과 {v['pass']} · 주의 {v['caution']} · 플래그 {v['flag']} · 오류 {v['error']}")

    print("[4/5] 적재 — 대표값 선정 + 필드 단위 등급(A~D) 확정")
    g = load_canonical_specs(conn)
    print(f"      등급 분포  A:{g['A']}  B:{g['B']}  C:{g['C']}  D:{g['D']}")

    print("[5/5] 내보내기 — site/data.json + 리뷰 큐 생성")
    s = export_site_data(conn, ROOT / "site" / "data.json")
    print(f"      부품 {s['components']} · 사양행 {s['spec_rows']} · 출처 {s['sources']}")
    rq = export_review_data(conn, ROOT / "site" / "review-data.json")
    print(f"      리뷰 큐 {rq['total']}건 (결정 완료 {rq['decided']}건)")
    print("완료. 다음: python3 ../site/generate.py 로 정적 사이트 빌드")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
