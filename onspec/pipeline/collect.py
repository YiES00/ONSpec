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
                  notes="가격·재고 시계열 — Cloudflare 봇 차단으로 수집 불가(2026-08 확인)"),
    SourceAdapter("Foxtech", "S2_vendor", ["https://www.foxtechfpv.com/"], daily=True,
                  notes="WAF 403 차단으로 수집 불가(2026-08 확인)"),
    SourceAdapter("DrUAV", "S2_vendor", ["https://druav.com/"], daily=True,
                  notes="Shopify 공개 카탈로그 — 실구현(collect_druav)"),
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


_MODEL_CONT_RE = re.compile(r"^(Lite|L|V2|V3|II|Ⅱ|Pro|EVO|S|Plus)$", re.I)


def _model_from_tokens(tokens: list[str]) -> str | None:
    """선행 토큰열 → 모델명. 첫 토큰은 영문+숫자 조합, 이후는 시리즈 접미어만 허용.

    "MN505-S IP45 Navigator ..." → MN505-S / "U8 Lite L Efficiency ..." → U8 Lite L
    (수식어 블랙리스트 대신 접미어 화이트리스트 — "60kg MTOW" 같은 마케팅 토큰 오염 방지)
    """
    if not tokens or not re.match(r"^[A-Z]{1,3}\d", tokens[0], re.I):
        return None
    model = [tokens[0]]
    for tok in tokens[1:]:
        if not _MODEL_CONT_RE.match(tok):
            break
        model.append(tok)
    return " ".join(model)


def _map_spec(label: str, value: str) -> dict | None:
    """T-Motor 사양 라벨 → 카테고리 표준 키. 미대응 라벨은 None(추측 금지)."""
    key = re.sub(r"[^a-z0-9]", "", label.lower())
    quote = f"{label}: {value}"
    conditions = {}
    cond = _LABEL_COND_RE.search(label)
    if cond:
        conditions["stated_condition"] = cond.group(1).strip()

    if key in ("kv", "testitem", "kvvalue"):
        # "KV: 320" | "Test Item: KV130" | "Test Item: U15 Ⅱ KV80" | "KV Value: 110"
        # — KV 토큰 우선(모델명 속 숫자 오인 방지), 값 전체가 숫자일 때만 그대로 사용.
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
    if key.endswith("shaftdiameter"):    # "Shaft Diameter" / "Output Shaft Diameter"
        num, unit = _num_unit(value)
        if num is None:      # "IN：6mm，OUT：4mm" 같은 복합 표기는 추측하지 않고 생략
            return None
        return {"key": "shaft_dia_mm", "value": num, "unit": unit, "quote": quote,
                "conditions": {}}
    if key.startswith("maxthrust"):      # MN1115류: "Max Thrust: 34.3kg"
        num, unit = _num_unit(value)
        if not conditions:
            conditions["note"] = "사양표 표기 — 측정 조건은 원문 참조"
        return {"key": "max_thrust_g", "value": num, "unit": unit, "quote": quote,
                "conditions": conditions}
    if key == "ip":
        return {"key": "ip_rating", "value_text": value, "quote": quote, "conditions": {}}
    return None


def _parse_test_data_thrust(body: str) -> dict[str, dict]:
    """Test Data 표에서 KV 변형별 최대 스로틀 행의 추력을 추출.

    표마다 컬럼 순서가 다르므로(MN505: Throttle·Voltage·Thrust… / U15: Voltage가
    rowspan 선두, Thrust 7번째) 헤더 행을 파싱해 컬럼 위치를 결정한다.
    반환: {"320": {"thrust_g": ..., "conditions": {...}, "quote": ...}, ...}
    """
    section = _section(body, "testParameterBox")
    if not section:
        return {}

    header: list[str] = []
    th_i = tr_i = vo_i = None            # throttle/thrust/voltage 컬럼 인덱스
    kv = prop = volt = None              # rowspan 선두 셀은 다음 행들에 sticky
    best: dict[str, tuple[float, dict]] = {}

    def _idx(pat):
        return next((i for i, h in enumerate(header) if re.search(pat, h, re.I)), None)

    for tr in _TR_RE.findall(section):
        tds = [_text(td) for td in _TD_RE.findall(tr)]
        if not tds:
            continue
        # 헤더 행 — 한 섹션에 컬럼 순서가 다른 표가 여러 개일 수 있어 매번 갱신
        if any(re.search(r"Throttle", t, re.I) for t in tds) \
           and any(re.search(r"Thrust", t, re.I) for t in tds):
            header = tds
            th_i, tr_i, vo_i = _idx(r"Throttle"), _idx(r"Thrust"), _idx(r"Voltage")
            kv = prop = volt = None
            continue
        if not header:
            continue
        # 행마다 스로틀 셀 위치를 동적으로 탐지 — rowspan 구성이 행마다 달라진다
        idx_t = next((i for i, c in enumerate(tds) if re.match(r"^\d+%$", c)), None)
        if idx_t is None:
            continue
        for c in tds[:idx_t]:            # 선두 셀: KV / 프로펠러 / 전압
            if m := re.search(r"KV\s*(\d+)", c, re.I):
                kv = m.group(1)
            elif re.search(r"\d\s*[*x×]\s*\d", c):
                prop = c
            else:
                try:
                    volt = float(c)
                except ValueError:
                    pass
        trailing = tds[idx_t:]
        try:
            throttle = float(trailing[0].rstrip("%"))
            thrust = float(trailing[tr_i - th_i])
        except (ValueError, IndexError):
            continue
        if vo_i is not None and vo_i > th_i and len(trailing) > vo_i - th_i:
            try:
                volt = float(trailing[vo_i - th_i])
            except ValueError:
                pass
        if kv is None:
            continue
        prev = best.get(kv)
        if prev is None or throttle > prev[0]:
            conditions = {"throttle": trailing[0], "propeller": prop,
                          "source": "제조사 벤치 테스트 표"}
            if volt is not None:
                conditions["voltage_v"] = volt
            dia = re.search(r"[PG]?(\d{2}(?:\.\d+)?)\s*[*xX×]", prop or "")
            if dia:
                conditions["prop_diameter_in"] = float(dia.group(1))
            best[kv] = (throttle, {
                "thrust_g": thrust, "conditions": conditions,
                "quote": f"Test Data {prop or ''} @ {trailing[0]}: "
                         f"Thrust {trailing[tr_i - th_i]}g".replace("  ", " "),
            })
    return {k: data for k, (_, data) in best.items()}


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
        model_name = _model_from_tokens(h1_text.split())
        if not model_name:
            return None

        pairs = _table_pairs(_section(body, "basicParameterBox"))
        if not pairs:
            return None
        # 변형 그룹 시작 행: "KV: 320" 또는 "Test Item: KV130". 그 이전 = 공통 사양.
        # 시작 행이 없는 단일 변형 페이지(U3 등)는 h1의 "KV700"을 근거로 사용.
        def _group_kv(label: str, value: str) -> str | None:
            if re.sub(r"[^a-z0-9]", "", label.lower()) not in ("kv", "testitem", "kvvalue"):
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


# ─────────────────────────── DrUAV 어댑터(S2_vendor, 실구현) ───────────────────────────
# Shopify 공개 카탈로그 JSON(/products.json)에서 T-Motor 취급 품목의 가격·재고와
# 상세 설명(body_html)에 실린 사양표를 수집한다. GetFPV·Foxtech·RobotShop은
# 봇 차단(403/Cloudflare)으로 수집 불가 — 우회하지 않는다(§4.5 수집 윤리).

DRUAV_BASE = "https://druav.com"
DRUAV_VENDOR = {"name": "DrUAV", "country": None, "trust_weight": 0.5}

_KV_SEG_RE = re.compile(r"KV\s*Value\s*(\d+)", re.I)

# body_html 평문에서 뽑는 사양 패턴 — group(1)=값. quote는 매치 원문 전체.
_DRUAV_COMMON = [
    ("ip_rating", "text", re.compile(r"\bIP\s+(IP\d{2})\b")),
    ("config", "text", re.compile(r"Configuration\s+(\d+N\d+P)", re.I)),
]
_DRUAV_PER_KV = [
    ("cells_range", "text", re.compile(r"Rated Voltage\s*\(Lipo\)\s*([\d\-~]+S)", re.I)),
    ("max_power_w", "W", re.compile(r"Max\.?\s*Power\s*(?:\(180[sS]\))?\s*([\d.]+)\s*W")),
    ("max_current_a", "A", re.compile(r"Peak Current\s*(?:\(\d+[sS]\))?\s*([\d.]+)\s*A")),
    ("weight_g", "g", re.compile(r"Motor Weight\s*\(Incl[^)]*\)\s*([\d.]+)\s*g", re.I)),
    ("resistance_mohm", "mΩ", re.compile(r"Internal Resistance\s*([\d.]+)\s*mΩ", re.I)),
]
_DRUAV_THRUST_RE = re.compile(r"Max\.?\s*Thrust\s*([\d.]+)\s*(kg|g)\b", re.I)


def _druav_specs(patterns, text: str) -> list[dict]:
    specs = []
    for key, unit, rx in patterns:
        m = rx.search(text)
        if not m:
            continue
        quote = re.sub(r"\s+", " ", m.group(0)).strip()
        if unit == "text":
            specs.append({"key": key, "value_text": m.group(1), "quote": quote,
                          "conditions": {}})
        else:
            conditions = {}
            if key in ("max_power_w", "max_current_a"):
                stated = re.search(r"\((\d+[sS])\)", m.group(0))
                conditions["stated_condition"] = stated.group(1) if stated else quote
            specs.append({"key": key, "value": float(m.group(1)), "unit": unit,
                          "quote": quote, "conditions": conditions})
    return specs


class DrUAVAdapter:
    """druav.com(Shopify) T-Motor 취급 품목 수집기 — 가격·재고 + 리스팅 사양."""

    def __init__(self, fetcher: PoliteFetcher | None = None):
        self.fetcher = fetcher or PoliteFetcher()

    def catalog(self, pages: int = 2) -> list[dict]:
        prods = []
        for page in range(1, pages + 1):
            import json as _json
            body = self.fetcher.fetch(f"{DRUAV_BASE}/products.json?limit=250&page={page}")
            batch = _json.loads(body).get("products", [])
            prods.extend(batch)
            if len(batch) < 250:
                break
        return prods

    @staticmethod
    def _model(title: str) -> str | None:
        tokens = title.split()
        while tokens and tokens[0].lower() in ("t-motor", "tmotor", "antigravity"):
            tokens = tokens[1:]
        return _model_from_tokens(tokens)

    def parse_product(self, p: dict, fetched_at: str) -> dict | None:
        model_name = self._model(p["title"])
        if not model_name:
            return None
        url = f"{DRUAV_BASE}/products/{p['handle']}"
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(p.get("body_html") or "")))
        # 추천 조합·테스트 표에는 동일 라벨이 재등장하므로 사양 구간만 사용
        spec_zone = re.split(r"Recommended Combinations|Test Data", body)[0]
        common = _druav_specs(_DRUAV_COMMON, spec_zone)

        # "KV Value 110 ... KV Value 130 ..." → KV별 구간 (매치 원문도 근거로 보존)
        seg_matches = list(_KV_SEG_RE.finditer(spec_zone))
        segments, seg_quotes = {}, {}
        for i, m in enumerate(seg_matches):
            end = seg_matches[i + 1].start() if i + 1 < len(seg_matches) else len(spec_zone)
            segments[m.group(1)] = spec_zone[m.end():end]
            seg_quotes[m.group(1)] = re.sub(r"\s+", " ", m.group(0))

        claims = []
        for v in p.get("variants", []):
            v_title = v.get("title") or ""
            # KV 식별 우선순위: 변형명 → 상품명 → (단일 변형 상품이면) 본문 명시 값.
            # 본문 사용은 "KV Value N" 구간이 정확히 하나일 때만 — 복수면 귀속 불명이라 보류.
            kv_m = (re.search(r"KV\s*(\d+)", v_title, re.I)
                    or re.search(r"KV\s*(\d+)", p["title"], re.I))
            if kv_m:
                kv_num, kv_quote = kv_m.group(1), f"{p['title']} — {v_title}"
            elif len(p.get("variants", [])) == 1 and len(segments) == 1:
                kv_num = next(iter(segments))
                kv_quote = seg_quotes[kv_num]        # 예: "KV Value 53" (리스팅 본문 원문)
            else:
                kv_num = None
            variant = (f"KV{kv_num}" if kv_num
                       else (None if v_title == "Default Title" else v_title))
            specs = list(common)
            if kv_num:
                specs.append({"key": "kv", "value": float(kv_num), "unit": None,
                              "quote": kv_quote, "conditions": {}})
                seg = segments.get(kv_num)
                if seg:
                    specs += _druav_specs(_DRUAV_PER_KV, seg)
                    t = _DRUAV_THRUST_RE.search(seg)
                    if t:
                        specs.append({
                            "key": "max_thrust_g", "value": float(t.group(1)),
                            "unit": t.group(2),
                            "quote": re.sub(r"\s+", " ", t.group(0)),
                            "conditions": {"note": "판매처 리스팅 사양표 — 측정 조건은 원문 참조"}})
            claims.append({
                "category": "motor",
                "manufacturer": {"name": "T-Motor", "hq_country": "CN"},
                "model_name": model_name, "variant": variant,
                "mfg_country": None, "specs": specs,
                "price": {"currency": "USD", "amount": float(v["price"]),
                          "pack_qty": 1, "in_stock": bool(v.get("available")),
                          "quote": f"{p['title']} — {v.get('title')}: ${v['price']} USD"},
            })
        if not claims:
            return None
        return {
            "snapshot_id": f"snap-druav-{p['handle']}"[:64],
            "tier": "S2_vendor", "origin_url": url,
            "title": p["title"], "fetched_at": fetched_at,
            "vendor": dict(DRUAV_VENDOR),
            "raw_excerpt": body[:2000],
            "content_hash": hashlib.sha256((p.get("body_html") or "").encode()).hexdigest()[:16],
            "claims": claims,
        }

    def collect(self, models: set[str] | None = None, max_products: int = 8,
                log=print) -> list[dict]:
        """models: S1에서 수집된 모델명 집합 — 교차 출처 대상만 선별. None이면 모터 전체."""
        from normalize import normalize_name
        targets = {normalize_name(m) for m in models} if models else None
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        picked = []
        for p in self.catalog():
            if (p.get("vendor") or "").lower() not in ("t-motor", "tmotor"):
                continue
            model = self._model(p["title"])
            if not model:
                continue
            if targets is not None:
                if normalize_name(model) not in targets:
                    continue
            elif not ("motor" in p["title"].lower()
                      and not re.search(r"propeller|combo|kit|esc|prop\b", p["title"], re.I)):
                continue
            picked.append(p)
            if len(picked) >= max_products:
                break
        log(f"      DrUAV 카탈로그: 대상 {len(picked)}건"
            + (f" (S1 모델 {len(targets)}종과 대조)" if targets else ""))
        snapshots = []
        for p in picked:
            snap = self.parse_product(p, fetched_at)
            if snap:
                snapshots.append(snap)
                log(f"      수집 {snap['claims'][0]['model_name']}: 변형 {len(snap['claims'])} · "
                    f"가격 {snap['claims'][0]['price']['amount']} USD")
        return snapshots


def collect_druav(models: set[str] | None = None, max_products: int = 8, log=print) -> dict:
    """run_daily.py 진입점 — snapshots.json과 동일한 봉투 형식 반환."""
    return {"snapshots": DrUAVAdapter().collect(models, max_products, log)}


# ──────────────── Tyto Robotics DB 어댑터(S3_benchmark, 실구현) ────────────────
# 스러스트 스탠드 실측 DB(robots 전면 허용). /motors 목록의 인라인 JSON에서
# 테스트 보유(benchmarks_count≥1) T-Motor 모터를 S1 모델과 대조하고, 모터 페이지의
# 측정 속성(kv·weight)을 S3 주장값으로 수집한다. 최대 추력은 테스트마다 프로펠러가
# 달라 제조사 '최대 추력' 주장과 동일 지표가 아니므로 조건 매칭 로직 전까지 보류.

TYTO_BASE = "https://database.tytorobotics.com"

_TYTO_COMPONENTS_RE = re.compile(r":components\s*=\s*'(\[.*?\])'", re.S)


def _series_key(name: str) -> str:
    """모델명 대조 키 — 세대 표기 동치화(V2 ↔ Ⅱ/II). 예: 'U15 V2'≡'U15II'≡'U15Ⅱ'."""
    from normalize import normalize_name
    return normalize_name(name).replace("v2", "ii")


class TytoRoboticsAdapter:
    """database.tytorobotics.com 실측 연계 수집기."""

    def __init__(self, fetcher: PoliteFetcher | None = None):
        self.fetcher = fetcher or PoliteFetcher()

    def listing(self) -> list[dict]:
        import json as _json
        body = self.fetcher.fetch(f"{TYTO_BASE}/motors")
        m = _TYTO_COMPONENTS_RE.search(body)
        return _json.loads(html.unescape(m.group(1))) if m else []

    def _test_provenance(self, motor_hash: str) -> dict | None:
        """/tests/search 로 해당 모터의 실측 테스트 메타데이터 획득."""
        import json as _json
        from urllib.parse import urlencode
        params = urlencode({
            "per_page": 5, "page": 1,
            "filters": _json.dumps({"conjunction": "AND", "filters": [
                {"field": "powertrains.motor.common.hash",
                 "condition": {"operator": "=", "value": motor_hash}}]}),
            "relations": _json.dumps(["creator"]),
            "aggregates": "[]", "order_by": "[]",
        })
        data = _json.loads(self.fetcher.fetch(f"{TYTO_BASE}/tests/search?{params}"))
        tests = data.get("data", [])
        if not tests:
            return None
        t = tests[0]
        return {"test_title": t["title"], "test_url": t["link"],
                "device": t.get("device"),
                "staff_verified": bool((t.get("creator") or {}).get("is_rcbenchmark_staff")),
                "tests_total": data.get("meta", {}).get("total")}

    def collect(self, models: set[str], log=print) -> list[dict]:
        """models: S1 수집 모델명 집합 — 대조되는 실측 보유 모터만 수집."""
        targets = {_series_key(m): m for m in models}
        matched = []
        for e in self.listing():
            if (e.get("brand") or "").lower() != "t-motor" or not e.get("benchmarks_count"):
                continue
            s1_model = targets.get(_series_key(e.get("name") or ""))
            kv = ((e.get("measures") or {}).get("kv_value") or {}).get("value")
            if s1_model and kv:
                matched.append((e, s1_model, kv))
        log(f"      Tyto 목록: T-Motor 실측 보유 모터 중 S1 대조 {len(matched)}건")

        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshots = []
        for e, s1_model, kv in matched:
            url = e["link"]
            body = self.fetcher.fetch(url)          # 원문 스냅샷(원칙 1)
            prov = self._test_provenance(e["hash"])
            conditions = {"note": "Tyto Robotics DB 등재 측정 속성 (스러스트 스탠드 테스트 보유 모터)"}
            if prov:
                conditions.update(prov)
            specs = [{"key": "kv", "value": float(kv), "unit": None,
                      "quote": f"Kv value (rpm/v): {kv}", "conditions": dict(conditions)}]
            weight = ((e.get("measures") or {}).get("weight") or {}).get("value")
            if weight:
                specs.append({"key": "weight_g", "value": float(weight), "unit": "g",
                              "quote": f"weight (g): {weight}", "conditions": dict(conditions)})
            snapshots.append({
                "snapshot_id": f"snap-tyto-{e['hash']}",
                "tier": "S3_benchmark", "origin_url": url,
                "title": e["title"], "fetched_at": fetched_at,
                "vendor": None,
                "raw_excerpt": _text(body[body.find("component-attributes"):][:3000])[:2000],
                "content_hash": hashlib.sha256(body.encode()).hexdigest()[:16],
                "claims": [{
                    "category": "motor",
                    "manufacturer": {"name": "T-Motor", "hq_country": "CN"},
                    "model_name": s1_model, "variant": f"KV{int(kv)}",
                    "mfg_country": None, "specs": specs,
                }],
            })
            log(f"      실측 연계 {s1_model} KV{int(kv)} ← {e['title']} "
                f"(테스트 {e['benchmarks_count']}건)")
        return snapshots


def collect_tyto(models: set[str], log=print) -> dict:
    """run_daily.py 진입점 — snapshots.json과 동일한 봉투 형식 반환."""
    return {"snapshots": TytoRoboticsAdapter().collect(models, log)}
