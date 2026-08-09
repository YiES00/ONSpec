"""LLM 구조화 추출기(운영용 스켈레톤) — 계획서 §4.4.

데모는 픽스처의 사전 추출 claims를 쓰므로 이 모듈은 실행되지 않는다.
운영: ANTHROPIC_API_KEY 설정 후 collect.py 스냅샷을 입력으로 호출.

§4.4 강제 사항:
  1. 카테고리 표준 JSON 스키마를 응답 형식으로 강제, "원문에 없으면 null, 추측 금지"
  2. 모든 값에 원문 근거 인용(quote) 필수 — 환각 사후검증 가능
  3. 고가치 필드(추력·통달거리·에너지밀도 등)는 이중 추출, 불일치 시 자동 플래그
  4. 무작위 샘플 사람 감사로 추출 정확도 KPI 관리
"""
from __future__ import annotations
import json
import os
from pathlib import Path

CATEGORIES_DIR = Path(__file__).resolve().parent.parent / "categories"
HIGH_VALUE_KEYS = {"max_thrust_g", "capacity_mah", "c_rating", "cont_current_a"}

SYSTEM_PROMPT = """당신은 드론 부품 데이터시트 추출기입니다. 규칙:
1. 아래 JSON 스키마로만 응답합니다. 다른 텍스트·마크다운 금지.
2. 원문에 명시된 값만 추출합니다. 원문에 없으면 해당 키를 생략합니다. 추측·보정 금지.
3. 모든 값에는 반드시 원문에서 그대로 복사한 근거 인용(quote)을 붙입니다.
4. 값의 측정 조건(프로펠러, 전압, 지속시간 등)이 원문에 있으면 conditions에 구조화합니다.
5. 단위는 원문 그대로 unit에 적습니다. 변환하지 않습니다(정규화는 파이프라인이 수행).
"""


def build_user_prompt(category: str, raw_text: str) -> str:
    schema = json.loads((CATEGORIES_DIR / f"{category}.json").read_text(encoding="utf-8"))
    keys = [{"key": s["key"], "name": s["name_ko"], "unit": s.get("unit")}
            for s in schema["specs"] if not s.get("derived")]
    return (
        f"카테고리: {category}\n추출 대상 사양 키 목록:\n{json.dumps(keys, ensure_ascii=False)}\n\n"
        '응답 스키마: {"manufacturer": {"name": str, "hq_country": "ISO2|null"}, '
        '"model_name": str, "variant": "str|null", '
        '"mfg_country": {"country": "ISO2", "status": "claimed", "quote": str} | null, '
        '"specs": [{"key": str, "value": number|null, "value_text": "str|null", '
        '"unit": "str|null", "quote": str, "conditions": {}}], '
        '"price": {"currency": "ISO4217", "amount": number, "pack_qty": int, "quote": str} | null}'
        f"\n\n원문:\n<<<\n{raw_text[:12000]}\n>>>"
    )


def extract_claims(category: str, raw_text: str, model: str = "claude-sonnet-4-6") -> dict:
    """스냅샷 원문 → 구조화 claim. 고가치 필드는 이중 추출 후 대조."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY 미설정 — 운영 모드 전용")
    try:
        import anthropic  # requirements-prod.txt
    except ImportError as e:
        raise ImportError("pip install anthropic (운영 의존성)") from e

    client = anthropic.Anthropic(api_key=api_key)

    def _once() -> dict:
        msg = client.messages.create(
            model=model, max_tokens=4000, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(category, raw_text)}],
        )
        return json.loads(msg.content[0].text)

    first = _once()
    second = _once()  # §4.4-3 이중 추출

    def _vals(claim):
        return {s["key"]: (s.get("value"), s.get("value_text"))
                for s in claim.get("specs", []) if s["key"] in HIGH_VALUE_KEYS}

    mismatched = {k for k in _vals(first)
                  if _vals(first).get(k) != _vals(second).get(k)}
    for s in first.get("specs", []):
        if s["key"] in mismatched:
            s.setdefault("conditions", {})["_anomaly"] = {
                "type": "double_extract_mismatch",
                "detail": "이중 추출 불일치 — 사람 리뷰 필요",
            }
    return first
