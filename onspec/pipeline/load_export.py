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
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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

    for comp in conn.execute("SELECT id, category_id FROM components").fetchall():
        logs = [dict(r) for r in conn.execute(
            "SELECT * FROM verification_log WHERE component_id=? AND rule_id != 'R-ORIGIN'",
            (comp["id"],))]
        logs_by_key: dict[str, list] = {}
        for lg in logs:
            det = json.loads(lg["detail"])
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
                if lg["verdict"] in ("flag", "error"):
                    grade = "D"
                    notes.append(lg["det"]["summary"])
                elif lg["verdict"] == "caution":
                    grade = _worst(grade, "C")
                    notes.append(lg["det"]["summary"])
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
