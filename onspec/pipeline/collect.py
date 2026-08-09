"""수집기(운영용 스켈레톤) — 계획서 §4.2 [수집기 커넥터] + §4.5 수집 윤리.

데모는 fixtures/snapshots.json을 입력으로 쓰므로 이 모듈은 실행되지 않는다.
운영 전환 시 소스별 어댑터를 SOURCES에 등록하고 run_daily.py에서 호출한다.

집행 사항(§4.5):
  · robots.txt 준수 — 허용되지 않은 경로는 수집하지 않음
  · 명시적 User-Agent + 문의 연락처
  · 요청 간격 제한(기본 10초/도메인), 야간 수집
  · 원문 HTML/PDF는 오브젝트 스토리지에 스냅샷 보존(원칙 1), 재호스팅 금지
  · 콘텐츠 해시 비교로 변경된 페이지만 추출기로 전달(LLM 비용 절감, §4.3)
"""
from __future__ import annotations
import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone

USER_AGENT = "DroneCOTSDB-Collector/0.1 (+contact: 운영자 이메일 기재)"
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
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"https://{host}/robots.txt")
            try:
                rp.read()
            except OSError:
                pass
            self._robots[host] = rp
        return self._robots[host].can_fetch(USER_AGENT, url)

    def throttle(self, url: str) -> None:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        wait = self._last_hit.get(host, 0) + self.delay_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def fetch(self, url: str) -> dict:
        """반환: snapshots.json 항목과 동일한 형식(claims는 추출기가 채움).

        운영 구현: requests/Playwright로 본문 획득 → 오브젝트 스토리지 업로드.
        """
        if not self.allowed(url):
            raise PermissionError(f"robots.txt 불허: {url}")
        self.throttle(url)
        raise NotImplementedError(
            "운영 환경에서 HTTP 클라이언트를 연결하세요. "
            "본문 획득 후 content_hash로 변경 감지, 스냅샷 보존, extract.py로 전달.")

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
