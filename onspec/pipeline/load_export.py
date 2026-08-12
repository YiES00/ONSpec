"""적재 + 내보내기.

적재: spec_claims에서 출처 신뢰 순으로 대표값을 선정해 component_specs에
기록하고, 검증 로그를 반영해 필드 단위 등급(원칙 3)을 확정한다.
  기본 등급  S3(실측)=A · S1/S4=B · S2=C
  강등      해당 필드를 지목한 로그가 flag/error → D, caution → C 상한
파생값(에너지, 에너지밀도)은 입력 필드들의 최저 등급을 물려받고 근거
출처는 용량 필드의 출처를 따른다.

내보내기: 사이트가 소비하는 단일 data.json — 사양·등급·조건·모든 출처
값·검증 로그·가격·생산국 판정을 포함한다.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_PATH = ROOT / "reviews" / "decisions.json"


def review_key(model: str, variant: str | None, rule_id: str,
               spec_keys: list[str], summary: str) -> str:
    """리뷰 결정의 안정 키 — DB 재구축 간에도 동일 판정을 추적한다."""
    raw = f"{model}|{variant or ''}|{rule_id}|{','.join(sorted(spec_keys))}|{summary}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_reviews() -> dict:
    """사람 리뷰 결정(reviews/decisions.json). {key: {decision, note, reviewed_at}}"""
    if not REVIEWS_PATH.exists():
        return {}
    try:
        return json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return {}

BASE_GRADE = {"S1_manufacturer": "B", "S4_certification": "B",
              "S3_benchmark": "A", "S2_vendor": "C"}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def _worst(*grades: str) -> str:
    return max(grades, key=lambda g: GRADE_ORDER[g])


def _canonical_sort_key(c):
    # 실측(S3) > 주장(S1/S2/S4) — 계획서 §5: 실측 연계 필드가 대표값이 되어 A등급.
    # 실측이 없으면 신뢰 가중치 순, 동률이면 제조사 공식(S1) 우선.
    return (0 if c["tier"] == "S3_benchmark" else 1,
            -c["trust_weight"], 0 if c["tier"] == "S1_manufacturer" else 1, c["id"])


def load_canonical_specs(conn: sqlite3.Connection) -> dict:
    """대표값 선정 + 등급 확정 → component_specs 기록."""
    key_by_def = {r["id"]: r["spec_key"] for r in
                  conn.execute("SELECT id, spec_key FROM spec_definitions")}
    def_by_catkey = {(r["category_id"], r["spec_key"]): r["id"] for r in
                     conn.execute("SELECT id, category_id, spec_key FROM spec_definitions")}
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    reviews = load_reviews()
    for comp in conn.execute(
            "SELECT id, category_id, model_name, variant FROM components").fetchall():
        logs = [dict(r) for r in conn.execute(
            "SELECT * FROM verification_log WHERE component_id=? AND rule_id != 'R-ORIGIN'",
            (comp["id"],))]
        logs_by_key: dict[str, list] = {}
        for lg in logs:
            det = json.loads(lg["detail"])
            # 사람 리뷰(§5.5) 반영: dismiss=오탐(강등 해제) / confirm=확인(강등 유지)
            rk = review_key(comp["model_name"], comp["variant"], lg["rule_id"],
                            det.get("spec_keys", []), det.get("summary", ""))
            lg["review"] = reviews.get(rk)
            for k in det.get("spec_keys", []):
                logs_by_key.setdefault(k, []).append({**lg, "det": det})

        rows = conn.execute(
            """SELECT sc.*, s.tier FROM spec_claims sc
               JOIN sources s ON s.id = sc.source_id
               WHERE sc.component_id=?""", (comp["id"],)).fetchall()
        by_key: dict[str, list] = {}
        for r in rows:
            by_key.setdefault(key_by_def[r["spec_def_id"]], []).append(dict(r))

        comp_grades: dict[str, str] = {}
        canon_by_key: dict[str, dict] = {}
        for key, claims in by_key.items():
            canon = sorted(claims, key=_canonical_sort_key)[0]
            grade = BASE_GRADE[canon["tier"]]
            notes = []
            for lg in logs_by_key.get(key, []):
                review = lg.get("review")
                if review and review.get("decision") == "dismiss" \
                   and lg["verdict"] in ("flag", "error", "caution"):
                    notes.append(f"사람 리뷰로 해제됨({review.get('note') or '오탐'}): "
                                 f"{lg['det']['summary']}")
                    continue                     # 강등하지 않음
                if lg["verdict"] in ("flag", "error"):
                    grade = "D"
                    notes.append(lg["det"]["summary"]
                                 + (" · 사람 리뷰 확인됨" if review else ""))
                elif lg["verdict"] == "caution":
                    grade = _worst(grade, "C")
                    notes.append(lg["det"]["summary"]
                                 + (" · 사람 리뷰 확인됨" if review else ""))
                elif lg["rule_id"] == "R-X3" and lg["verdict"] == "pass":
                    notes.append(lg["det"]["summary"])   # A등급 근거를 사이트에 노출
            conn.execute(
                """INSERT INTO component_specs
                   (component_id, spec_def_id, value_num, value_text, unit_original,
                    value_num_original, conditions, source_id, extracted_quote,
                    grade, grade_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (comp["id"], canon["spec_def_id"], canon["value_num"], canon["value_text"],
                 canon["unit_original"], canon["value_num_original"], canon["conditions"],
                 canon["source_id"], canon["extracted_quote"], grade,
                 " · ".join(dict.fromkeys(notes)) or None),
            )
            comp_grades[key] = grade
            canon_by_key[key] = canon
            grade_counts[grade] += 1

        # ---- 배터리 파생값: 에너지(Wh), 에너지밀도(Wh/kg) ----
        if comp["category_id"] == "battery":
            cap = canon_by_key.get("capacity_mah")
            v = canon_by_key.get("nominal_voltage_v")
            w = canon_by_key.get("weight_g")
            if cap and v and "energy_wh" not in canon_by_key:
                e = round(float(cap["value_num"]) / 1000 * float(v["value_num"]), 1)
                g = _worst(comp_grades["capacity_mah"], comp_grades["nominal_voltage_v"])
                conn.execute(
                    """INSERT INTO component_specs (component_id, spec_def_id, value_num,
                       conditions, source_id, extracted_quote, grade, grade_note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (comp["id"], def_by_catkey[("battery", "energy_wh")], e, "{}",
                     cap["source_id"], "계산값: 용량×정격전압", g, "계산값(용량×정격전압)"),
                )
                grade_counts[g] += 1
            if cap and v and w:
                ed = round(float(cap["value_num"]) * float(v["value_num"])
                           / float(w["value_num"]), 1)
                g = _worst(comp_grades["capacity_mah"], comp_grades["nominal_voltage_v"],
                           comp_grades["weight_g"])
                notes = ["계산값(용량×정격전압÷무게)"]
                for lg in logs_by_key.get("energy_density", []):
                    if lg["verdict"] in ("flag", "error"):
                        g = "D"
                        notes.append(lg["det"]["summary"])
                    elif lg["verdict"] == "caution":
                        g = _worst(g, "C")
                        notes.append(lg["det"]["summary"])
                conn.execute(
                    """INSERT INTO component_specs (component_id, spec_def_id, value_num,
                       conditions, source_id, extracted_quote, grade, grade_note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (comp["id"], def_by_catkey[("battery", "energy_density")], ed, "{}",
                     cap["source_id"], "계산값: 용량×정격전압÷무게", g,
                     " · ".join(notes)),
                )
                grade_counts[g] += 1

        # ---- 생산국 판정 (R-ORIGIN 로그 집계) ----
        origins = [json.loads(r["detail"]) for r in conn.execute(
            "SELECT detail FROM verification_log WHERE component_id=? AND rule_id='R-ORIGIN'",
            (comp["id"],))]
        confirmed = {o["country"] for o in origins if o["status"] == "confirmed"}
        claimed = {o["country"] for o in origins if o["status"] == "claimed"}
        if len(confirmed) == 1:
            conn.execute("UPDATE components SET mfg_country=?, mfg_country_status='confirmed' WHERE id=?",
                         (confirmed.pop(), comp["id"]))
        elif len(claimed) == 1 and not confirmed:
            conn.execute("UPDATE components SET mfg_country=?, mfg_country_status='claimed' WHERE id=?",
                         (claimed.pop(), comp["id"]))
    conn.commit()
    return grade_counts


def export_site_data(conn: sqlite3.Connection, out_path: Path) -> dict:
    sources = {r["id"]: dict(r) for r in conn.execute(
        """SELECT s.*, v.name AS vendor_name FROM sources s
           LEFT JOIN vendors v ON v.id = s.vendor_id""")}
    defs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM spec_definitions")}

    categories = []
    for c in conn.execute("SELECT * FROM categories ORDER BY id"):
        cat_defs = sorted((d for d in defs.values() if d["category_id"] == c["id"]),
                          key=lambda d: d["sort_order"])
        categories.append({
            "id": c["id"], "name_ko": c["name_ko"], "name_en": c["name_en"],
            "specs": [{"key": d["spec_key"], "name_ko": d["name_ko"], "unit": d["unit"],
                       "type": d["value_type"], "compare_default": bool(d["compare_default"]),
                       "requires_conditions": bool(d["requires_conditions"])}
                      for d in cat_defs],
        })

    components = []
    for comp in conn.execute(
            """SELECT c.*, m.name AS man_name, m.hq_country FROM components c
               JOIN manufacturers m ON m.id = c.manufacturer_id ORDER BY c.category_id, c.id"""):
        specs = {}
        for r in conn.execute(
                "SELECT * FROM component_specs WHERE component_id=? AND is_current=1",
                (comp["id"],)):
            d = defs[r["spec_def_id"]]
            src = sources[r["source_id"]]
            all_claims = [
                {"value": c2["value_num"], "value_text": c2["value_text"],
                 "value_original": c2["value_num_original"], "unit_original": c2["unit_original"],
                 "quote": c2["extracted_quote"], "tier": sources[c2["source_id"]]["tier"],
                 "vendor": sources[c2["source_id"]]["vendor_name"],
                 "url": sources[c2["source_id"]]["origin_url"],
                 "title": sources[c2["source_id"]]["title"],
                 "trust": c2["trust_weight"]}
                for c2 in conn.execute(
                    "SELECT * FROM spec_claims WHERE component_id=? AND spec_def_id=? ORDER BY trust_weight DESC",
                    (comp["id"], r["spec_def_id"]))]
            conditions = json.loads(r["conditions"])
            conditions.pop("_anomaly", None)
            specs[d["spec_key"]] = {
                "value": r["value_num"], "value_text": r["value_text"],
                "unit": d["unit"], "grade": r["grade"], "grade_note": r["grade_note"],
                "conditions": conditions, "quote": r["extracted_quote"],
                "source": {"url": src["origin_url"], "title": src["title"],
                           "tier": src["tier"], "vendor": src["vendor_name"]},
                "all_sources": all_claims,
            }

        verifications = []
        for r in conn.execute(
                """SELECT rule_id, verdict, detail FROM verification_log
                   WHERE component_id=? AND rule_id != 'R-ORIGIN' ORDER BY id""",
                (comp["id"],)):
            det = json.loads(r["detail"])
            verifications.append({"rule_id": r["rule_id"], "verdict": r["verdict"],
                                  "summary": det.pop("summary", ""), "spec_keys": det.pop("spec_keys", []),
                                  "detail": det})

        origin_claims = [json.loads(r["detail"]) for r in conn.execute(
            "SELECT detail FROM verification_log WHERE component_id=? AND rule_id='R-ORIGIN'",
            (comp["id"],))]
        for oc in origin_claims:
            sid = oc.pop("source_id", None)
            if sid and sid in sources:
                oc["url"] = sources[sid]["origin_url"]

        prices = []
        for r in conn.execute(
                """SELECT p.*, v.name AS vendor_name FROM prices p
                   JOIN vendors v ON v.id = p.vendor_id
                   WHERE p.component_id=? ORDER BY p.observed_at DESC""", (comp["id"],)):
            prices.append({
                "currency": r["currency"], "amount": r["amount"], "pack_qty": r["pack_qty"],
                "unit_amount": round(r["amount"] / r["pack_qty"], 2),
                "vendor": r["vendor_name"], "url": sources[r["source_id"]]["origin_url"],
                "observed_at": r["observed_at"], "amount_usd": r["amount_usd"],
                "amount_krw": r["amount_krw"],
            })

        components.append({
            "id": comp["id"], "category": comp["category_id"],
            "manufacturer": {"name": comp["man_name"], "hq_country": comp["hq_country"]},
            "model_name": comp["model_name"], "variant": comp["variant"],
            "is_synthetic": bool(comp["is_synthetic_test"]),
            "mfg_country": {"code": comp["mfg_country"], "status": comp["mfg_country_status"],
                            "claims": origin_claims},
            "specs": specs, "verifications": verifications, "prices": prices,
        })

    verdict_counts = {r["verdict"]: r["n"] for r in conn.execute(
        "SELECT verdict, COUNT(*) n FROM verification_log WHERE rule_id!='R-ORIGIN' GROUP BY verdict")}
    grade_counts = {r["grade"]: r["n"] for r in conn.execute(
        "SELECT grade, COUNT(*) n FROM component_specs GROUP BY grade")}

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "components": len(components),
            "sources": len(sources),
            "spec_rows": sum(len(c["specs"]) for c in components),
            "verdicts": {k: verdict_counts.get(k, 0) for k in ("pass", "caution", "flag", "error")},
            "grades": {k: grade_counts.get(k, 0) for k in "ABCD"},
        },
        "categories": categories,
        "components": components,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data["stats"]


def export_review_data(conn: sqlite3.Connection, out_path: Path) -> dict:
    """리뷰 큐 데이터(§5.5) — flag/error/caution 판정을 원문 근거와 함께 내보낸다.

    각 항목: 판정 요약·규칙·심각도, 영향 사양의 모든 출처 주장값(인용·URL),
    원문 스냅샷 발췌(아카이브 역참조), 안정 리뷰 키, 기존 리뷰 결정.
    """
    reviews = load_reviews()
    defs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM spec_definitions")}
    archives: dict[str, dict] = {}       # 아카이브 파일 캐시: path → {snapshot_id: snap}

    def snapshot_excerpt(snapshot_uri: str | None) -> dict | None:
        if not snapshot_uri or "#" not in snapshot_uri:
            return None
        path, snap_id = snapshot_uri.split("#", 1)
        if path not in archives:
            f = ROOT / path
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                archives[path] = {s["snapshot_id"]: s for s in data.get("snapshots", [])}
            except (OSError, ValueError):
                archives[path] = {}
        snap = archives[path].get(snap_id)
        if not snap:
            return None
        return {"title": snap.get("title"), "fetched_at": snap.get("fetched_at"),
                "raw_excerpt": (snap.get("raw_excerpt") or "")[:1200]}

    items = []
    rows = conn.execute(
        """SELECT v.*, c.model_name, c.variant, c.category_id, m.name AS manufacturer
           FROM verification_log v
           JOIN components c ON c.id = v.component_id
           JOIN manufacturers m ON m.id = c.manufacturer_id
           WHERE v.verdict IN ('flag', 'error', 'caution') AND v.rule_id != 'R-ORIGIN'""")
    for r in rows:
        det = json.loads(r["detail"])
        spec_keys = det.get("spec_keys", [])
        rk = review_key(r["model_name"], r["variant"], r["rule_id"],
                        spec_keys, det.get("summary", ""))
        claims = []
        if spec_keys:
            q = conn.execute(
                f"""SELECT sc.value_num, sc.value_text, sc.unit_original,
                           sc.value_num_original, sc.conditions, sc.extracted_quote,
                           sd.spec_key, sd.name_ko, sd.unit,
                           s.tier, s.origin_url, s.title AS source_title,
                           s.snapshot_uri, vn.name AS vendor
                    FROM spec_claims sc
                    JOIN spec_definitions sd ON sd.id = sc.spec_def_id
                    JOIN sources s ON s.id = sc.source_id
                    LEFT JOIN vendors vn ON vn.id = s.vendor_id
                    WHERE sc.component_id=? AND sd.spec_key IN
                          ({','.join('?' * len(spec_keys))})""",
                (r["component_id"], *spec_keys))
            for c in q:
                claims.append({
                    "spec_key": c["spec_key"], "name_ko": c["name_ko"],
                    "value": c["value_num"], "value_text": c["value_text"],
                    "unit": defs and c["unit"], "value_original": c["value_num_original"],
                    "unit_original": c["unit_original"],
                    "conditions": json.loads(c["conditions"] or "{}"),
                    "quote": c["extracted_quote"], "tier": c["tier"],
                    "vendor": c["vendor"], "url": c["origin_url"],
                    "source_title": c["source_title"],
                    "snapshot": snapshot_excerpt(c["snapshot_uri"]),
                })
        items.append({
            "review_key": rk,
            "verdict": r["verdict"], "rule_id": r["rule_id"],
            "summary": det.get("summary"), "detail": det,
            "component": {"manufacturer": r["manufacturer"], "model": r["model_name"],
                          "variant": r["variant"], "category": r["category_id"]},
            "spec_keys": spec_keys, "claims": claims,
            "decision": reviews.get(rk),
        })

    severity = {"error": 0, "flag": 1, "caution": 2}
    items.sort(key=lambda i: (severity[i["verdict"]], i["component"]["model"]))
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {"total": len(items),
                  "by_verdict": {v: sum(1 for i in items if i["verdict"] == v)
                                 for v in ("error", "flag", "caution")},
                  "decided": sum(1 for i in items if i["decision"])},
        "items": items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data["stats"]
