"""인제스트 — 원문 스냅샷(픽스처 또는 크롤 결과)을 스테이징 테이블로 적재.

원칙 1 집행 지점: 모든 spec_claims 행은 source_id·extracted_quote 없이는
삽입될 수 없다(스키마 NOT NULL). 여기서 단위 정규화도 수행하며, 정규화
이상(단위 오기 등)은 conditions["_anomaly"]로 표시해 검증 엔진에 넘긴다.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path

from db import spec_def_map, ROOT
from normalize import normalize_name, normalize_value

TIER_TRUST = {"S1_manufacturer": 1.0, "S2_vendor": 0.6, "S3_benchmark": 0.9, "S4_certification": 1.0}


def _upsert_manufacturer(conn, m: dict) -> int:
    key = normalize_name(m["name"])
    row = conn.execute("SELECT id FROM manufacturers WHERE name_normalized=?", (key,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO manufacturers (name, name_normalized, hq_country, website) VALUES (?,?,?,?)",
        (m["name"], key, m.get("hq_country"), m.get("website")),
    )
    return cur.lastrowid


def _upsert_vendor(conn, v: dict | None) -> int | None:
    if not v:
        return None
    row = conn.execute("SELECT id FROM vendors WHERE name=?", (v["name"],)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO vendors (name, country, trust_weight) VALUES (?,?,?)",
        (v["name"], v.get("country"), v.get("trust_weight", 0.6)),
    )
    return cur.lastrowid


def _upsert_component(conn, category: str, manufacturer_id: int, model_name: str,
                      variant: str | None, is_synthetic: bool) -> int:
    key = normalize_name(model_name)
    row = conn.execute(
        """SELECT id FROM components WHERE category_id=? AND manufacturer_id=?
           AND model_normalized=? AND IFNULL(variant,'')=IFNULL(?, '')""",
        (category, manufacturer_id, key, variant),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO components (category_id, manufacturer_id, model_name,
           model_normalized, variant, is_synthetic_test) VALUES (?,?,?,?,?,?)""",
        (category, manufacturer_id, model_name, key, variant, 1 if is_synthetic else 0),
    )
    return cur.lastrowid


def ingest_snapshots(conn: sqlite3.Connection, snapshots_path: Path) -> dict:
    data = json.loads(snapshots_path.read_text(encoding="utf-8"))
    defs = spec_def_map(conn)
    stats = {"sources": 0, "claims": 0, "prices": 0, "components": set()}

    for snap in data["snapshots"]:
        vendor_id = _upsert_vendor(conn, snap.get("vendor"))
        content_hash = hashlib.sha256(snap.get("raw_excerpt", "").encode()).hexdigest()[:16]
        cur = conn.execute(
            """INSERT OR IGNORE INTO sources
               (tier, origin_url, vendor_id, title, fetched_at, snapshot_uri, content_hash)
               VALUES (?,?,?,?,?,?,?)""",
            (snap["tier"], snap["origin_url"], vendor_id, snap.get("title"),
             snap["fetched_at"],
             # 원문 아카이브 실제 경로 보존 — 리뷰 UI가 원문 발췌를 역참조한다
             f"{snapshots_path.resolve().relative_to(ROOT).as_posix()}#{snap['snapshot_id']}",
             content_hash),
        )
        source_id = cur.lastrowid or conn.execute(
            "SELECT id FROM sources WHERE origin_url=? AND content_hash=?",
            (snap["origin_url"], content_hash)).fetchone()["id"]
        stats["sources"] += 1
        trust = (snap.get("vendor") or {}).get("trust_weight") or TIER_TRUST[snap["tier"]]
        if snap["tier"] in ("S1_manufacturer", "S4_certification"):
            trust = TIER_TRUST[snap["tier"]]

        for claim in snap["claims"]:
            man_id = _upsert_manufacturer(conn, claim["manufacturer"])
            comp_id = _upsert_component(
                conn, claim["category"], man_id, claim["model_name"],
                claim.get("variant"), claim.get("is_synthetic_test", False))
            stats["components"].add(comp_id)

            mfg = claim.get("mfg_country")
            if mfg:
                conn.execute(
                    """INSERT INTO verification_log (component_id, rule_id, verdict, detail)
                       VALUES (?, 'R-ORIGIN', 'pass', ?)""",
                    (comp_id, json.dumps({
                        "country": mfg["country"], "status": mfg["status"],
                        "quote": mfg.get("quote"), "source_id": source_id,
                        "summary": f"생산국 주장: {mfg['country']} ({mfg['status']})",
                    }, ensure_ascii=False)),
                )

            for spec in claim.get("specs", []):
                d = defs.get((claim["category"], spec["key"]))
                if d is None:
                    raise KeyError(f"미정의 사양 키: {claim['category']}.{spec['key']}")
                conditions = dict(spec.get("conditions") or {})
                value_num = spec.get("value")
                value_text = spec.get("value_text")
                unit_org = spec.get("unit")
                norm = value_num
                if value_num is not None:
                    norm, anomaly = normalize_value(float(value_num), unit_org, d["unit"])
                    if anomaly:
                        conditions["_anomaly"] = anomaly
                conn.execute(
                    """INSERT INTO spec_claims
                       (component_id, spec_def_id, value_num, value_text,
                        value_num_original, unit_original, conditions, source_id,
                        extracted_quote, trust_weight)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (comp_id, d["id"], norm, value_text, value_num, unit_org,
                     json.dumps(conditions, ensure_ascii=False), source_id,
                     spec["quote"], trust),
                )
                stats["claims"] += 1

            price = claim.get("price")
            if price:
                conn.execute(
                    """INSERT INTO prices (component_id, vendor_id, source_id,
                       currency, amount, pack_qty, in_stock, observed_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (comp_id, vendor_id, source_id, price["currency"], price["amount"],
                     price.get("pack_qty", 1),
                     1 if price.get("in_stock") else None, snap["fetched_at"]),
                )
                stats["prices"] += 1

    conn.commit()
    stats["components"] = len(stats["components"])
    return stats
