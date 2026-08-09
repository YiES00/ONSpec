"""수집기 — 계획서 §4.2 [수집기 커넥터] + §4.5 수집 윤리.

T-Motor Store(S1) 어댑터는 실구현 상태다: run_daily.py(--fixtures 없이)가
collect_tmotor()를 호출해 실수집한다. 나머지 소스는 SOURCES에 등록된 스켈레톤.

집행 사항(§4.5):
  · robots.txt 준수 — 허용되지 않은 경로는 수집하지 않음
  · 명시적 User-Agent + 문의 연락처
  · 요청 간격 제한(기본 10초/도메인), 야간 수집
  · 원문 HTML/PDF는 오브젝트 스토리지에 스냅샷 보존(원칙 1), 재호스팅 금지
  · 콘텐츠 해시 비교로 변경된 페이지만 추출기로 전달(LLM 비용 절감, §4.3)
"""
from __future__ import annotations
import hashlib
import html
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone

USER_AGENT = "DroneCOTSDB-Collector/0.1 (+contact: superles949@gmail.com)"
DEFAULT_DELAY_S = 10.0


@dataclass
class SourceAdapter:
    """소스별 어댑터 정의. selector류는 어댑터 구현체에서 사용."""
    name: str
    tier: str                       # S1_manufacturer | S2_vendor | S3_benchmark | S4_certification
    seed_urls: list[str]
    daily: bool = True              # §4.3: 가격·재고=매일, 사양=주1회+변경감지
    notes: str = ""


# 계획서 §4.1의 4계층 대표 소스 — 운영 시 어댑터 구현체를 연결
SOURCES: list[SourceAdapter] = [
    SourceAdapter("T-Motor Store", "S1_manufacturer", ["https://store.tmotor.com/"], daily=False),
    SourceAdapter("iFlight Shop", "S1_manufacturer", ["https://shop.iflight.com/"], daily=False),
    SourceAdapter("Hobbywing", "S1_manufacturer", ["https://www.hobbywing.com/"], daily=False),
    SourceAdapter("APC Propellers", "S1_manufacturer", ["https://www.apcprop.com/"], daily=False),
    SourceAdapter("Gens Tattu", "S1_manufacturer", ["https://genstattu.com/"], daily=False),
    SourceAdapter("Tattu World", "S1_manufacturer", ["https://tattuworld.com/"], daily=False),
    SourceAdapter("GetFPV", "S2_vendor", ["https://www.getfpv.com/"], daily=True,
                  notes="가격·재고 시계열"),
    SourceAdapter("Foxtech", "S2_vendor", ["https://www.foxtechfpv.com/"], daily=True),
    SourceAdapter("Tyto Robotics DB", "S3_benchmark", ["https://database.tytorobotics.com/"],
                  daily=False, notes="모터 실측 — A등급 연계"),
    SourceAdapter("FCC ID DB", "S4_certification", ["https://fccid.io/"], daily=False,
                  notes="데이터링크 실측 출력·생산지 — Phase 2"),
]


class PoliteFetcher:
    """robots.txt 준수 + 도메인별 레이트리밋 페처(스켈레톤)."""

    def __init__(self, delay_s: float = DEFAULT_DELAY_S):
        self.delay_s = delay_s
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_hit: dict[str, float] = {}

    def allowed(self, url: str) -> bool:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if host not in self._robots:
            # rp.read()는 기본 파이썬 UA로 요청해 403(→전체 불허 해석)을 받는
            # 사이트가 있으므로, 명시 UA로 직접 받아 파싱한다.
            rp = urllib.robotparser.RobotFileParser()
            req = urllib.request.Request(f"https://{host}/robots.txt",
                                         headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    rp.parse(resp.read().decode("utf-8", errors="replace").splitlines())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    rp.allow_all = True
                else:
                    rp.disallow_all = True
            except OSError:
                rp.disallow_all = True
            self._robots[host] = rp
        return self._robots[host].can_fetch(USER_AGENT, url)

    def throttle(self, url: str) -> None:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        wait = self._last_hit.get(host, 0) + self.delay_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def fetch(self, url: str) -> str:
        """robots·레이트리밋 준수 후 본문 HTML 반환."""
        if not self.allowed(url):
            raise PermissionError(f"robots.txt 불허: {url}")
        self.throttle(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @staticmethod
    def make_snapshot(url: str, tier: str, title: str, body: str) -> dict:
        return {
            "snapshot_id": hashlib.sha256(url.encode()).hexdigest()[:12],
            "tier": tier, "origin_url": url, "title": title,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_excerpt": body[:2000],
            "content_hash": hashlib.sha256(body.encode()).hexdigest()[:16],
            "claims": [],   # extract.py가 채움
        }


# ─────────────────────────── T-Motor Store 어댑터(S1, 실구현) ───────────────────────────
# 제품 페이지의 Specification 테이블이 구조화돼 있어 LLM 없이 결정적 파싱으로
# claims를 생성한다. 각 값의 quote는 파싱한 원문 행 텍스트("라벨: 값")를 보존해
# extracted_quote NOT NULL 원칙을 유지한다. 비정형 소스(S2 등)는 extract.py(LLM) 경로.

TMOTOR_BASE = "https://store.tmotor.com"
TMOTOR_MOTOR_CATEGORY = f"{TMOTOR_BASE}/categorys/multi-rotor-drone-motor"

_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_NUM_UNIT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-ZΩμ°\"'*]*)\s*$")
_LABEL_COND_RE = re.compile(r"[（(]([^）)]+)[）)]")


def _text(fragment: str) -> str:
    """HTML 조각 → 공백 정리된 평문."""
    s = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _num_unit(text: str) -> tuple[float | None, str | None]:
    """'225g' → (225.0, 'g'), '320' → (320.0, None). 복합 표기는 (None, None)."""
    m = _NUM_UNIT_RE.match(text)
    if not m:
        return None, None
    return float(m.group(1)), (m.group(2) or None)


def _section(body: str, css_class: str) -> str:
    idx = body.find(f'class="{css_class}"')
    if idx < 0:
        return ""
    start = body.find(">", idx) + 1
    return body[start:body.find("</div>", start)]


def _table_pairs(section_html: str) -> list[tuple[str, str]]:
    """사양 테이블의 (라벨, 값) 쌍을 문서 순서대로 평탄화."""
    pairs = []
    for tr in _TR_RE.findall(section_html):
        tds = [_text(td) for td in _TD_RE.findall(tr)]
        for i in range(0, len(tds) - 1, 2):
            if tds[i]:
                pairs.append((tds[i], tds[i + 1]))
    return pairs


def _map_spec(label: str, value: str) -> dict | None:
    """T-Motor 사양 라벨 → 카테고리 표준 키. 미대응 라벨은 None(추측 금지)."""
    key = re.sub(r"[^a-z0-9]", "", label.lower())
    quote = f"{label}: {value}"
    conditions = {}
    cond = _LABEL_COND_RE.search(label)
    if cond:
        conditions["stated_condition"] = cond.group(1).strip()

    if key in ("kv", "testitem"):
        # "KV: 320" | "Test Item: KV130" | "Test Item: U15 Ⅱ KV80" — KV 토큰 우선
        # (모델명 속 숫자 오인 방지), 값 전체가 숫자일 때만 그대로 사용.
        m = re.search(r"KV\s*(\d+)", value, re.I) or re.fullmatch(r"\s*(\d+)\s*", value)
        if not m:
            return None
        return {"key": "kv", "value": float(m.group(1)), "unit": None,
                "quote": quote, "conditions": {}}
    if key.startswith("motorweight") or key.startswith("weightincl"):
        num, unit = _num_unit(value)
        return {"key": "weight_g", "value": num, "unit": unit, "quote": quote, "conditions": {}}
    if key.startswith("maxpower"):
        num, unit = _num_unit(value)
        return {"key": "max_power_w", "value": num, "unit": unit, "quote": quote,
                "conditions": conditions}
    if key.startswith("peakcurrent") or key.startswith("maxcontinuouscurrent"):
        num, unit = _num_unit(value)
        if not conditions:
            conditions["stated_condition"] = label.strip()   # "Max Continuous Current 180S"
        return {"key": "max_current_a", "value": num, "unit": unit, "quote": quote,
                "conditions": conditions}
    if key.startswith("ratedvoltage") or key.startswith("noofcells"):
        return {"key": "cells_range", "value_text": value, "quote": quote, "conditions": {}}
    if key == "propellerrecommendation":
        return {"key": "prop_rec", "value_text": value, "quote": quote, "conditions": {}}
    if key == "configuration":
        return {"key": "config", "value_text": value, "quote": quote, "conditions": {}}
    if key == "internalresistance":
        num, unit = _num_unit(value)
        return {"key": "resistance_mohm", "value": num, "unit": unit, "quote": quote,
                "conditions": {}}
    if key == "shaftdiameter":
        num, unit = _num_unit(value)
        if num is None:      # "IN：6mm，OUT：4mm" 같은 복합 표기는 추측하지 않고 생략
            return None
        return {"key": "shaft_dia_mm", "value": num, "unit": unit, "quote": quote,
                "conditions": {}}
    if key == "ip":
        return {"key": "ip_rating", "value_text": value, "quote": quote, "conditions": {}}
    return None


def _parse_test_data_thrust(body: str) -> dict[str, dict]:
    """Test Data 표에서 KV 변형별 최대 스로틀 행의 추력을 추출.

    반환: {"320": {"thrust_g": ..., "conditions": {...}, "quote": ...}, ...}
    """
    out: dict[str, dict] = {}
    section = _section(body, "testParameterBox")
    if not section:
        return out
    current_kv, current_prop = None, None
    best: dict[str, tuple[float, dict]] = {}
    for tr in _TR_RE.findall(section):
        tds = [_text(td) for td in _TD_RE.findall(tr)]
        if not tds:
            continue
        # rowspan 헤더 셀: "MN505S KV320", "T-MOTOR P20*6"
        kv_m = re.search(r"KV\s*(\d+)", tds[0], re.I)
        if kv_m and len(tds) > 2:
            current_kv, current_prop = kv_m.group(1), tds[1]
            tds = tds[2:]
        if not tds or not tds[0].endswith("%") or current_kv is None:
            continue
        throttle = float(tds[0].rstrip("%"))
        try:
            voltage, thrust = float(tds[1]), float(tds[2])
        except (ValueError, IndexError):
            continue
        prev = best.get(current_kv)
        if prev is None or throttle > prev[0]:
            conditions = {"throttle": tds[0], "voltage_v": voltage,
                          "propeller": current_prop, "source": "제조사 벤치 테스트 표"}
            dia = re.search(r"P?(\d{2}(?:\.\d+)?)\s*\*", current_prop or "")
            if dia:
                conditions["prop_diameter_in"] = float(dia.group(1))
            best[current_kv] = (throttle, {
                "thrust_g": thrust, "conditions": conditions,
                "quote": f"Test Data {current_prop} @ {tds[0]}: Thrust {tds[2]}g",
            })
    return {kv: data for kv, (_, data) in best.items()}


class TMotorStoreAdapter:
    """store.tmotor.com 멀티로터 모터 수집기."""

    def __init__(self, fetcher: PoliteFetcher | None = None):
        self.fetcher = fetcher or PoliteFetcher()

    def product_urls(self, listing_html: str) -> list[str]:
        urls, seen = [], set()
        for href in re.findall(r'href="([^"]*product/[^"]+\.html)"', listing_html):
            url = href if href.startswith("http") else f"{TMOTOR_BASE}/{href.lstrip('/')}"
            slug = url.rsplit("/", 1)[-1]
            if url in seen or not re.match(r"^(mn|u)\d", slug):
                continue
            if any(x in slug for x in ("combo", "gimbal", "arm", "manned")):
                continue
            seen.add(url)
            urls.append(url)
        return urls

    def parse_product(self, url: str, body: str) -> dict | None:
        h1 = re.search(r'<h1[^>]*>(.*?)</h1>', body, re.S)
        title_tag = re.search(r"<title>(.*?)</title>", body, re.S)
        h1_text = _text(h1.group(1)) if h1 else ""
        # h1 선행 토큰이 모델명: "MN505-S IP45 Navigator ..." → MN505-S,
        # "U8 Lite L Efficiency ..." → U8 Lite L. 수식어 토큰에서 절단.
        stop = re.compile(r"^(IP\d+|KV\d+|Navigator|Antigravity|Efficiency|Multirotor|"
                          r"Multi(-Motor)?|UAV|Drone|Motor|Type|Waterproof|Power|High|"
                          r"[UP]-\w+)$", re.I)
        tokens = []
        for tok in h1_text.split():
            if stop.match(tok):
                break
            tokens.append(tok)
        if not tokens or not re.match(r"^[A-Z]{1,3}\d", tokens[0], re.I):
            return None
        model_name = " ".join(tokens)

        pairs = _table_pairs(_section(body, "basicParameterBox"))
        if not pairs:
            return None
        # 변형 그룹 시작 행: "KV: 320" 또는 "Test Item: KV130". 그 이전 = 공통 사양.
        # 시작 행이 없는 단일 변형 페이지(U3 등)는 h1의 "KV700"을 근거로 사용.
        def _group_kv(label: str, value: str) -> str | None:
            if re.sub(r"[^a-z0-9]", "", label.lower()) not in ("kv", "testitem"):
                return None
            m = re.search(r"KV\s*(\d+)", value, re.I) or re.fullmatch(r"\s*(\d+)\s*", value)
            return m.group(1) if m else None

        starts = [(i, kv) for i, (l, v) in enumerate(pairs) if (kv := _group_kv(l, v))]
        kv_specs: list[dict | None]
        if starts:
            common = pairs[:starts[0][0]]
            bounds = [i for i, _ in starts] + [len(pairs)]
            groups = [pairs[bounds[j]:bounds[j + 1]] for j in range(len(starts))]
            kvs = [kv for _, kv in starts]
            kv_specs = [None] * len(groups)     # kv 행은 그룹 안에서 _map_spec이 매핑
        else:
            h1_kv = re.search(r"KV\s*(\d+)", h1_text, re.I)
            if not h1_kv:
                return None
            common, groups, kvs = [], [pairs], [h1_kv.group(1)]
            kv_specs = [{"key": "kv", "value": float(h1_kv.group(1)), "unit": None,
                         "quote": h1_text, "conditions": {}}]

        thrust_by_kv = _parse_test_data_thrust(body)
        claims = []
        for group, kv_value, kv_spec in zip(groups, kvs, kv_specs):
            specs = [s for label, value in common + group
                     if value and (s := _map_spec(label, value))]
            if kv_spec:
                specs.insert(0, kv_spec)
            thrust = thrust_by_kv.pop(kv_value, None)   # 동일 KV 중복 그룹엔 1회만 귀속
            if thrust:
                specs.append({"key": "max_thrust_g", "value": thrust["thrust_g"], "unit": "g",
                              "quote": thrust["quote"], "conditions": thrust["conditions"]})
            if not specs:
                continue
            # 동일 KV 변형이 복수(전압 구성 차이 등)면 셀 구성으로 구분
            variant = f"KV{kv_value}"
            if kvs.count(kv_value) > 1:
                cells = next((s.get("value_text") for s in specs
                              if s["key"] == "cells_range" and s.get("value_text")), None)
                variant = f"{variant}/{cells}" if cells else f"{variant}#{len(claims) + 1}"
            claims.append({
                "category": "motor",
                "manufacturer": {"name": "T-Motor", "hq_country": "CN",
                                 "website": TMOTOR_BASE},
                "model_name": model_name, "variant": variant,
                "mfg_country": None, "specs": specs,
            })
        if not claims:
            return None

        slug = url.rsplit("/", 1)[-1].removesuffix(".html")
        spec_text = _text(_section(body, "basicParameterBox"))
        return {
            "snapshot_id": f"snap-tmotor-{slug}"[:64],
            "tier": "S1_manufacturer",
            "origin_url": url,
            "title": _text(title_tag.group(1)) if title_tag else h1_text,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "vendor": None,
            "raw_excerpt": (h1_text + " | " + spec_text)[:2000],
            "content_hash": hashlib.sha256(body.encode()).hexdigest()[:16],
            "claims": claims,
        }

    def collect(self, max_products: int = 6, log=print) -> list[dict]:
        listing = self.fetcher.fetch(TMOTOR_MOTOR_CATEGORY)
        urls = self.product_urls(listing)[:max_products]
        log(f"      T-Motor 목록: 제품 {len(urls)}건 수집 예정 (도메인당 {self.fetcher.delay_s:.0f}s 간격)")
        snapshots = []
        for url in urls:
            try:
                snap = self.parse_product(url, self.fetcher.fetch(url))
            except (OSError, PermissionError) as e:
                log(f"      건너뜀 {url} — {e}")
                continue
            if snap:
                snapshots.append(snap)
                log(f"      수집 {snap['claims'][0]['model_name']}: 변형 {len(snap['claims'])} · "
                    f"사양 {sum(len(c['specs']) for c in snap['claims'])}")
            else:
                log(f"      파싱 실패(사양 테이블 없음): {url}")
        return snapshots


def collect_tmotor(max_products: int = 6, log=print) -> dict:
    """run_daily.py 진입점 — snapshots.json과 동일한 봉투 형식 반환."""
    return {"snapshots": TMotorStoreAdapter().collect(max_products, log)}
