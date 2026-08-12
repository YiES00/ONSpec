"""정적 사이트 생성 — 계획서 §4.2 마지막 단계 [사이트 재생성].

data.json을 단일 index.html에 임베드한다(외부 요청 없는 완전 독립 파일).
운영 전환: 동일한 data.json 계약을 유지한 채 Astro/Next(ISR)로 프런트만
교체하면 된다 — README '기술 스택' 참조.
"""
from __future__ import annotations
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

SITE = Path(__file__).resolve().parent
DIST = SITE / "dist"


def build() -> Path:
    data_path = SITE / "data.json"
    if not data_path.exists():
        raise SystemExit("site/data.json 없음 — 먼저 pipeline/run_daily.py --fixtures 실행")
    data = json.loads(data_path.read_text(encoding="utf-8"))
    # <script type=application/json> 안전 임베드: 종료 태그 시퀀스 이스케이프
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    env = Environment(loader=FileSystemLoader(SITE / "templates"),
                      autoescape=select_autoescape(enabled_extensions=()))
    html = env.get_template("index.html.j2").render(data_json=data_json)

    DIST.mkdir(exist_ok=True)
    out = DIST / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"빌드 완료: {out}  ({out.stat().st_size/1024:.0f} KB, "
          f"부품 {data['stats']['components']} · 사양 {data['stats']['spec_rows']})")

    # 리뷰 큐 페이지(§5.5) — review-data.json이 있을 때만
    review_path = SITE / "review-data.json"
    if review_path.exists():
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review_json = json.dumps(review, ensure_ascii=False).replace("</", "<\\/")
        rhtml = env.get_template("review.html.j2").render(review_json=review_json)
        rout = DIST / "review.html"
        rout.write_text(rhtml, encoding="utf-8")
        print(f"리뷰 큐 빌드: {rout}  ({rout.stat().st_size/1024:.0f} KB, "
              f"{review['stats']['total']}건)")
    return out


if __name__ == "__main__":
    build()
