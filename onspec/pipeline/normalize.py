"""정규화 — 계획서 §4.2 [정규화] 단계.

단위를 카테고리 표준 단위로 변환하되 원본 값·단위를 함께 보존한다.
비표준 단위(예: 프로펠러 피치의 '°' 표기)는 변환 대신 이상 신호를 남겨
검증 엔진(R-N1)이 플래그하게 한다.
"""
from __future__ import annotations
import re
import unicodedata


def normalize_name(name: str) -> str:
    """모델명·제조사명 중복 병합용 키: 소문자, 공백/기호 제거."""
    s = unicodedata.normalize("NFKC", name).lower()
    return re.sub(r"[\s\-_/·.]+", "", s)


# (spec_key 표준단위, 원본단위) -> 변환 계수. None이면 값 유지 + 이상 신호.
_CONVERSIONS: dict[tuple[str, str], float | None] = {
    ("g", "kg"): 1000.0,
    ("g", "kgf"): 1000.0,   # 추력: 킬로그램힘 → 그램힘(g 표준단위와 동치 취급)
    ("g", "gf"): 1.0,
    ("g", "oz"): 28.3495,
    ("in", "mm"): 1 / 25.4,
    ("in", "cm"): 1 / 2.54,
    ("mm", "in"): 25.4,
    ("mm", "cm"): 10.0,
    ("W", "kW"): 1000.0,
    ("V", "mV"): 0.001,
    ("A", "mA"): 0.001,
    ("mAh", "Ah"): 1000.0,
    ("Wh", "kWh"): 1000.0,
}

# 명백한 단위 오기 패턴: 변환 불가 → 값 유지 + anomaly 표시
_SUSPECT_UNITS: dict[str, set[str]] = {
    "in": {"deg", "°"},          # 피치를 도(°)로 표기한 리스팅 오류
}


def normalize_value(value: float, unit_original: str | None, unit_standard: str | None):
    """반환: (정규화값, anomaly | None). 원본 값·단위는 호출부가 별도 보존."""
    if value is None or unit_standard is None or unit_original is None:
        return value, None
    u_org = unit_original.strip()
    if u_org == unit_standard:
        return value, None
    factor = _CONVERSIONS.get((unit_standard, u_org))
    if factor is not None:
        return round(value * factor, 6), None
    if u_org in _SUSPECT_UNITS.get(unit_standard, set()):
        return value, {
            "type": "unit_suspect",
            "detail": f"단위 오기 의심: '{u_org}' → '{unit_standard}'로 간주(값 유지)",
        }
    return value, {
        "type": "unit_unknown",
        "detail": f"미지원 단위 '{u_org}' (표준 '{unit_standard}') — 값 유지",
    }


_CELLS_RE = re.compile(r"(\d+)\s*[~\-–]\s*(\d+)\s*S", re.IGNORECASE)
_CELLS_SINGLE_RE = re.compile(r"(\d+)\s*S", re.IGNORECASE)


def parse_cells_range(text: str) -> tuple[int | None, int | None]:
    """'3~6S' → (3, 6). '6S' → (6, 6)."""
    if not text:
        return None, None
    m = _CELLS_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _CELLS_SINGLE_RE.search(text)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None


_PROP_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_prop_rec_max_inch(text: str) -> float | None:
    """'20~22인치' → 22.0 (권장 프로펠러 최대 직경)."""
    if not text:
        return None
    nums = [float(x) for x in _PROP_RE.findall(text)]
    return max(nums) if nums else None
