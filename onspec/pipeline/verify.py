"""검증 엔진 — 계획서 §5.

1단계: 물리 타당성 규칙 (R-M*, R-E*, R-B*, R-P*)
2단계: 교차 출처 대조 (R-X1 수치, R-X2 텍스트) — 출처 신뢰 가중 평균 대비 편차
3단계: 실측 데이터 연계(A등급)는 S3/S4 소스 연동 시 활성화 (데모 미포함)

모든 판정은 verification_log에 근거 수치와 함께 기록된다. detail.spec_keys가
가리키는 사양 필드의 등급이 load 단계에서 강등된다(flag/error→D, caution→C 상한).
규칙 문서: docs/METHODOLOGY.md
"""
from __future__ import annotations
import json
import math
import sqlite3

from normalize import parse_cells_range, parse_prop_rec_max_inch

RHO = 1.225          # 해수면 공기밀도 kg/m^3
FOM_CEILING = 1.0    # 이상적 물리 한계 (Figure of Merit)
FOM_CAUTION = 0.85   # 소형 로터 현실 상한
STEEL_DENSITY = 7850.0

X1_FLAG = 0.15       # 교차 출처 수치 편차: 플래그 임계
X1_CAUTION = 0.05    # 교차 출처 수치 편차: 주의 임계


def _log(logs: list, rule_id: str, verdict: str, spec_keys: list[str],
         summary: str, **detail):
    logs.append({
        "rule_id": rule_id, "verdict": verdict,
        "detail": {"spec_keys": spec_keys, "summary": summary, **detail},
    })


def _canonical(claims: list[dict]) -> dict | None:
    if not claims:
        return None
    return sorted(
        claims,
        key=lambda c: (-c["trust_weight"], 0 if c["tier"] == "S1_manufacturer" else 1, c["id"]),
    )[0]


def _num(claims_by_key: dict, key: str) -> tuple[float | None, dict | None]:
    c = _canonical(claims_by_key.get(key, []))
    if c and c["value_num"] is not None:
        return float(c["value_num"]), c
    return None, c


def _text(claims_by_key: dict, key: str) -> str | None:
    c = _canonical(claims_by_key.get(key, []))
    return c["value_text"] if c else None


# ---------------- 카테고리별 물리·정합 규칙 ----------------

def rules_motor(claims_by_key: dict) -> list[dict]:
    logs: list[dict] = []
    thrust, thrust_c = _num(claims_by_key, "max_thrust_g")
    power, _ = _num(claims_by_key, "max_power_w")
    current, _ = _num(claims_by_key, "max_current_a")
    voltage, _ = _num(claims_by_key, "max_voltage_v")
    kv, _ = _num(claims_by_key, "kv")
    weight, _ = _num(claims_by_key, "weight_g")
    dia, dia_c = _num(claims_by_key, "stator_dia_mm")
    length, _ = _num(claims_by_key, "stator_len_mm")

    # R-M1: 모멘텀 이론 상한 — 주장 추력·출력이 이상적 물리 한계를 넘는가
    prop_in = None
    if thrust_c:
        prop_in = (json.loads(thrust_c["conditions"]) or {}).get("prop_diameter_in")
    if prop_in is None:
        prop_in = parse_prop_rec_max_inch(_text(claims_by_key, "prop_rec") or "")
    if thrust and power and prop_in:
        t_n = thrust / 1000 * 9.80665
        area = math.pi * (prop_in * 0.0254 / 2) ** 2
        p_ideal = t_n ** 1.5 / math.sqrt(2 * RHO * area)
        fom = p_ideal / power
        d = dict(thrust_g=thrust, power_w=power, prop_in=prop_in,
                 p_ideal_w=round(p_ideal, 1), fom_implied=round(fom, 3))
        if fom > FOM_CEILING:
            _log(logs, "R-M1", "flag", ["max_thrust_g"],
                 f"물리적으로 불가능한 추력 주장 — 이상적 최소 소요출력 {p_ideal:.0f}W가 "
                 f"표기 출력 {power:.0f}W의 {fom:.1f}배 (모멘텀 이론 한계 초과)", **d)
        elif fom > FOM_CAUTION:
            _log(logs, "R-M1", "caution", ["max_thrust_g"],
                 f"추력 주장이 물리 한계에 근접 (implied FoM {fom:.2f} > {FOM_CAUTION})", **d)
        else:
            _log(logs, "R-M1", "pass", ["max_thrust_g"],
                 f"모멘텀 이론 검사 통과 (implied FoM {fom:.2f})", **d)

    # R-M2: 전기 정합 — 최대출력 vs 최대전류×최대전압
    if power and current and voltage and voltage < 100:
        iv = current * voltage
        dev = abs(power - iv) / max(power, iv)
        d = dict(power_w=power, i_x_v=round(iv, 1), deviation=round(dev, 4))
        if dev > 0.25:
            _log(logs, "R-M2", "caution", ["max_power_w"],
                 f"출력({power:.0f}W)과 전류×전압({iv:.0f}W) 불일치 {dev*100:.0f}%", **d)
        else:
            _log(logs, "R-M2", "pass", ["max_power_w"],
                 f"전기 사양 정합 (출력 {power:.0f}W ≈ 전류×전압 {iv:.0f}W, 편차 {dev*100:.2f}%)", **d)

    # R-M4: 무게-부피 밀도 상한 — 표기 무게가 물리적으로 가능한가
    if weight and dia and length:
        cond_note = ""
        if dia_c:
            cond_note = ((json.loads(dia_c["conditions"]) or {}).get("note") or "")
        outer = "모터 외" in cond_note
        vol = math.pi * (dia / 2000) ** 2 * (length / 1000) * (1.0 if outer else 4.0)
        density = (weight / 1000) / vol
        d = dict(weight_g=weight, dia_mm=dia, len_mm=length,
                 basis="모터 외형" if outer else "스테이터×외피보정(×4)",
                 implied_density_kg_m3=round(density))
        if density > 9000:
            _log(logs, "R-M4", "flag", ["weight_g"],
                 f"표기 무게 {weight:.0f}g는 부피 대비 밀도 {density:,.0f}kg/m³ — "
                 f"강철({STEEL_DENSITY:,.0f})보다 무거워 물리적으로 불가 (포장중량 혼동 의심)", **d)
        elif density > 6000 or density < 250:
            _log(logs, "R-M4", "caution", ["weight_g"],
                 f"무게-부피 비율 이상 (밀도 {density:,.0f}kg/m³)", **d)
        else:
            _log(logs, "R-M4", "pass", ["weight_g"],
                 f"무게-부피 검사 통과 (밀도 {density:,.0f}kg/m³)", **d)

    # R-M5: 전압 상식 — 소형 모터 클래스에서 비상식적 고전압 표기
    if voltage and voltage > 61:
        d = dict(voltage_v=voltage)
        if kv and abs(voltage - kv) < 1e-6:
            d["note"] = "표기 전압이 KV값과 동일 — KV/전압 혼동 오기로 추정"
        _log(logs, "R-M5", "flag", ["max_voltage_v"],
             f"전압 표기 {voltage:.0f}V는 이 클래스에서 비상식적"
             + (" (KV값을 전압으로 오기한 것으로 추정)" if "note" in d else ""), **d)
    return logs


def rules_esc(claims_by_key: dict) -> list[dict]:
    logs: list[dict] = []
    cont, _ = _num(claims_by_key, "cont_current_a")
    burst, burst_c = _num(claims_by_key, "burst_current_a")
    voltage, _ = _num(claims_by_key, "max_voltage_v")
    cells_text = _text(claims_by_key, "cells_range")

    if cont and burst:
        if burst < cont:
            _log(logs, "R-E1", "error", ["burst_current_a"],
                 f"순간 전류({burst:.0f}A) < 연속 전류({cont:.0f}A) — 논리 오류",
                 burst_a=burst, cont_a=cont)
        else:
            _log(logs, "R-E1", "pass", ["burst_current_a"],
                 f"전류 사양 정합 (연속 {cont:.0f}A ≤ 순간 {burst:.0f}A)",
                 burst_a=burst, cont_a=cont)

    if burst_c:
        conds = json.loads(burst_c["conditions"]) or {}
        if "duration_s" not in conds:
            _log(logs, "R-E2", "caution", ["burst_current_a"],
                 "순간 전류의 지속시간 조건 미기재 — 비교 시 주의 (원칙 2)")

    if voltage and cells_text:
        _, max_s = parse_cells_range(cells_text)
        if max_s:
            full = max_s * 4.2
            dev = abs(full - voltage) / full
            d = dict(cells_max=max_s, full_charge_v=round(full, 1),
                     stated_v=voltage, deviation=round(dev, 3))
            if dev > 0.05:
                _log(logs, "R-E3", "caution", ["max_voltage_v"],
                     f"셀수({max_s}S 만충 {full:.1f}V)와 표기 전압({voltage:.1f}V) "
                     f"불일치 {dev*100:.0f}%", **d)
            else:
                _log(logs, "R-E3", "pass", ["max_voltage_v"],
                     f"셀수-전압 정합 ({max_s}S ↔ {voltage:.1f}V)", **d)
    return logs


def rules_battery(claims_by_key: dict) -> list[dict]:
    logs: list[dict] = []
    cap, _ = _num(claims_by_key, "capacity_mah")
    cells, _ = _num(claims_by_key, "cells_s")
    v_nom, _ = _num(claims_by_key, "nominal_voltage_v")
    c_rate, _ = _num(claims_by_key, "c_rating")
    weight, _ = _num(claims_by_key, "weight_g")

    # R-B2: 셀 구성 정합 (정격전압 ÷ 3.7V = 셀수)
    if cells and v_nom:
        implied = round(v_nom / 3.7)
        d = dict(stated_cells=int(cells), nominal_v=v_nom, implied_cells=implied)
        if implied != int(cells):
            _log(logs, "R-B2", "flag", ["cells_s"],
                 f"셀 구성 모순 — 정격 {v_nom}V는 {implied}S에 해당하나 {int(cells)}S로 표기"
                 " (출처 원문 오기 추정)", **d)
        else:
            _log(logs, "R-B2", "pass", ["cells_s"],
                 f"셀 구성 정합 ({int(cells)}S ↔ {v_nom}V)", **d)

    # R-B1: 에너지밀도 상한 (LiPo 셀 수준 물리 한계 기반)
    v_use = v_nom or (cells * 3.7 if cells else None)
    if cap and v_use and weight:
        ed = cap / 1000 * v_use / (weight / 1000)
        d = dict(capacity_mah=cap, voltage_v=v_use, weight_g=weight,
                 energy_density_wh_kg=round(ed, 1))
        if ed > 260:
            _log(logs, "R-B1", "flag", ["energy_density"],
                 f"에너지밀도 {ed:.0f}Wh/kg — LiPo 팩 물리 한계 초과 (용량 과장 의심)", **d)
        elif ed > 200:
            _log(logs, "R-B1", "caution", ["energy_density"],
                 f"에너지밀도 {ed:.0f}Wh/kg — 상위 한계 근접, 실측 확인 권장", **d)
        else:
            _log(logs, "R-B1", "pass", ["energy_density"],
                 f"에너지밀도 검사 통과 ({ed:.0f}Wh/kg)", **d)

    # R-B3: C율 상식
    if c_rate and cap and c_rate > 60 and cap > 5000:
        _log(logs, "R-B3", "caution", ["c_rating"],
             f"대용량 팩의 연속 {c_rate:.0f}C 표기 — 마케팅 과장 가능성, 실측 필요",
             c_rating=c_rate, capacity_mah=cap)
    return logs


def rules_propeller(claims_by_key: dict) -> list[dict]:
    logs: list[dict] = []
    dia, _ = _num(claims_by_key, "diameter_in")
    pitch, _ = _num(claims_by_key, "pitch_in")
    weight, _ = _num(claims_by_key, "weight_g")

    if dia and pitch:
        ratio = pitch / dia
        if ratio > 1.2:
            _log(logs, "R-P1", "caution", ["pitch_in"],
                 f"피치/직경비 {ratio:.2f} — 통상 범위 밖", ratio=round(ratio, 2))
        else:
            _log(logs, "R-P1", "pass", ["pitch_in"],
                 f"피치/직경비 정상 ({ratio:.2f})", ratio=round(ratio, 2))

    if dia and weight:
        lo, hi = 0.6 * dia, 8.0 * dia
        d = dict(weight_g=weight, bounds_g=[round(lo, 1), round(hi, 1)])
        if not (lo <= weight <= hi):
            _log(logs, "R-P2", "caution", ["weight_g"],
                 f"무게 {weight:.0f}g가 {dia:.0f}인치급 통상 범위({lo:.0f}~{hi:.0f}g) 밖", **d)
        else:
            _log(logs, "R-P2", "pass", ["weight_g"],
                 f"무게-직경 검사 통과 ({weight:.0f}g)", **d)
    return logs


CATEGORY_RULES = {
    "motor": rules_motor, "esc": rules_esc,
    "battery": rules_battery, "propeller": rules_propeller,
}


# ---------------- 공통 규칙 ----------------

def rules_common(claims_by_key: dict) -> list[dict]:
    logs: list[dict] = []
    for key, claims in claims_by_key.items():
        # R-N1: 정규화 단계에서 표시된 단위 이상
        for c in claims:
            anomaly = (json.loads(c["conditions"]) or {}).get("_anomaly")
            if anomaly:
                _log(logs, "R-N1", "caution", [key],
                     f"{anomaly['detail']} (원본: {c['value_num_original']}{c['unit_original']})",
                     anomaly=anomaly)

        # R-X1: 교차 출처 수치 대조 (신뢰 가중 평균 대비 편차)
        nums = [c for c in claims if c["value_num"] is not None]
        if len(nums) >= 2 and len({round(float(c["value_num"]), 6) for c in nums}) > 1:
            values = [float(c["value_num"]) for c in nums]
            weights = [float(c["trust_weight"]) for c in nums]
            wmean = sum(v * w for v, w in zip(values, weights)) / sum(weights)
            spread = (max(values) - min(values)) / wmean
            srcs = [{"value": float(c["value_num"]), "tier": c["tier"],
                     "trust": float(c["trust_weight"]), "source_id": c["source_id"]}
                    for c in nums]
            d = dict(weighted_mean=round(wmean, 2), spread=round(spread, 4), sources=srcs)
            if spread > X1_FLAG:
                _log(logs, "R-X1", "flag", [key],
                     f"출처 간 값 불일치 {spread*100:.0f}% (가중평균 {wmean:,.0f}) — 모든 출처 값 병기", **d)
            elif spread > X1_CAUTION:
                _log(logs, "R-X1", "caution", [key],
                     f"출처 간 값 편차 {spread*100:.1f}% — 확인 필요, 모든 출처 값 병기", **d)
            else:
                _log(logs, "R-X1", "pass", [key],
                     f"출처 간 값 일치(편차 {spread*100:.1f}%)", **d)

        # R-X2: 교차 출처 텍스트 대조
        texts = {(c["value_text"] or "").strip() for c in claims if c["value_text"]}
        texts.discard("")
        if len(texts) > 1:
            _log(logs, "R-X2", "caution", [key],
                 "출처 간 표기 불일치: " + " / ".join(sorted(texts)),
                 variants=sorted(texts))
    return logs


def run_verification(conn: sqlite3.Connection) -> dict:
    """모든 부품에 대해 규칙 실행 후 verification_log 기록."""
    stats = {"pass": 0, "caution": 0, "flag": 0, "error": 0}
    comps = conn.execute("SELECT id, category_id FROM components").fetchall()
    def_rows = conn.execute("SELECT id, spec_key FROM spec_definitions").fetchall()
    key_by_def = {r["id"]: r["spec_key"] for r in def_rows}
    def_by_catkey = {}
    for r in conn.execute("SELECT id, category_id, spec_key FROM spec_definitions"):
        def_by_catkey[(r["category_id"], r["spec_key"])] = r["id"]

    for comp in comps:
        rows = conn.execute(
            """SELECT sc.*, s.tier FROM spec_claims sc
               JOIN sources s ON s.id = sc.source_id
               WHERE sc.component_id=?""", (comp["id"],)).fetchall()
        claims_by_key: dict[str, list] = {}
        for r in rows:
            claims_by_key.setdefault(key_by_def[r["spec_def_id"]], []).append(dict(r))

        logs = CATEGORY_RULES[comp["category_id"]](claims_by_key)
        logs += rules_common(claims_by_key)

        for log in logs:
            keys = log["detail"].get("spec_keys") or []
            spec_def_id = def_by_catkey.get((comp["category_id"], keys[0])) if keys else None
            conn.execute(
                """INSERT INTO verification_log (component_id, spec_def_id, rule_id, verdict, detail)
                   VALUES (?,?,?,?,?)""",
                (comp["id"], spec_def_id, log["rule_id"], log["verdict"],
                 json.dumps(log["detail"], ensure_ascii=False)),
            )
            stats[log["verdict"]] += 1
    conn.commit()
    return stats
