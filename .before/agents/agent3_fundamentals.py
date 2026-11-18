"""
Agent 3: Catalyst & Earnings Specialist

Earnings dates come from Yahoo Finance (yfinance Ticker.calendar / earnings_dates,
cached 12h), converted to KST with a D-day countdown — so the desk knows that
NVDA reports at 2026-11-18 06:20 KST instead of repeating a stale "11/17" line.
Macro release dates are a small, explicitly-labelled reference list (official
calendars), never mixed into price data.
"""
import json
import os
from datetime import datetime, timedelta, date, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data", "earnings_calendar.json")

WATCH = ["NVDA", "TSLA", "AVGO", "TSM", "MU", "AMD", "SMH", "QQQ"]

# Official-calendar reference dates (static, reviewed manually). KST.
MACRO_CALENDAR = [
    {"event": "FOMC 회의", "date_kst": "2026-12-09 04:00", "impact": "CRITICAL",
     "note": "12/8~9 회의 결과·점도표. 현재 시장은 인상(25bp 89%)을 반영"},
    {"event": "11월 고용보고서(NFP)", "date_kst": "2026-12-04 22:30", "impact": "HIGH", "note": "연준 경로 재가격"},
    {"event": "11월 CPI", "date_kst": "2026-12-10 22:30", "impact": "HIGH", "note": "10월 CPI +0.4% m/m 이후 첫 확인"},
]


def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _fetch_earnings() -> Dict[str, Any]:
    cache = _load_cache()
    try:
        age = (datetime.now() - datetime.fromisoformat(cache.get("fetched_at", "2000-01-01"))).total_seconds() / 3600
    except Exception:
        age = 999
    if age < 12 and cache.get("entries"):
        return cache
    entries = {}
    try:
        import yfinance as yf
        for sym in WATCH:
            try:
                tk = yf.Ticker(sym)
                cal = tk.calendar or {}
                eps = cal.get("Earnings Average")
                rev = cal.get("Revenue Average")
                report_et = None
                # exact timestamp first (tz-aware, ET)
                try:
                    ed = tk.earnings_dates
                    if ed is not None and len(ed):
                        ts = ed.index[0]
                        if ts.tzinfo is None:
                            ts = ts.tz_localize("America/New_York")
                        report_et = ts.tz_convert("America/New_York")
                except Exception:
                    report_et = None
                if report_et is None:
                    d = cal.get("Earnings Date")
                    if isinstance(d, list):
                        d = d[0] if d else None
                    if d is None:
                        continue
                    if isinstance(d, str):
                        d = datetime.fromisoformat(d).date()
                    report_et = datetime.combine(d, datetime.min.time()).replace(hour=16, tzinfo=None)
                # ET → KST (+14h in EDT)
                report_kst_dt = report_et.replace(tzinfo=None) + timedelta(hours=14)
                entries[sym] = {
                    "report_at_kst": report_kst_dt.strftime("%Y-%m-%d %H:%M"),
                    "earnings_date_et": str(report_et.date()),
                    "eps_estimate": eps,
                    "revenue_estimate": rev,
                }
            except Exception:
                continue
    except Exception:
        pass
    payload = {"fetched_at": datetime.now().isoformat(), "entries": entries}
    if entries:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload or cache


class FundamentalsAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=3,
            name="Agent 3: 단기 실적 촉매(Catalyst) & 어닝 서프라이즈 분석가",
            code="FUNDAMENTALS_CATALYST_SPECIALIST",
            role="실적 D-day 캘린더와 갭 유발 이벤트 관리",
            description="Yahoo Finance 실적 캘린더를 KST로 변환해 D-day를 계산하고, FOMC·CPI 등 공식 일정과 결합해 이벤트 전후 포지션 규칙을 제시합니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        now_kst = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)
        clock = context.get("clock") or {}
        snap = context.get("snapshot") or {}
        news = context.get("news_payload") or {}
        cache = _fetch_earnings()

        rows = []
        for sym, e in (cache.get("entries") or {}).items():
            try:
                report_kst = datetime.strptime(e["report_at_kst"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            dday = (report_kst.date() - now_kst.date()).days
            rows.append({
                "ticker": sym, "earnings_date_et": e["earnings_date_et"],
                "report_kst": report_kst.strftime("%Y-%m-%d %H:%M KST"),
                "d_day": dday,
                "eps_estimate": e.get("eps_estimate"),
                "revenue_estimate": e.get("revenue_estimate"),
                "rule": ("당일 신규 진입 금지, 보유분은 실적 전 절반 축소" if dday <= 1
                         else "실적 3일 전부터 신규 레버리지 진입 축소"),
            })
        rows.sort(key=lambda r: r["d_day"])

        events = []
        for ev in MACRO_CALENDAR:
            try:
                dt = datetime.strptime(ev["date_kst"], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            events.append({**ev, "d_day": (dt.date() - now_kst.date()).days})
        events.sort(key=lambda e: e["d_day"])

        catalysts = [it for it in news.get("items", [])
                     if any(k in it["title"].lower() for k in ("earnings", "guidance", "launch", "approval", "wins", "contract", "forecast"))][:8]
        next_ev = rows[0] if rows else None

        self.output_data = {
            "phase": clock.get("phase"),
            "earnings_calendar": rows,
            "macro_calendar": events,
            "headline_catalysts": [{"title": c["title"], "source": c["source"], "published": c["published"]} for c in catalysts],
            "next_major_event": (next_ev["ticker"] + " " + next_ev["report_kst"]) if next_ev else None,
            "key_takeaway": (
                (f"{next_ev['ticker']} 실적 D-{next_ev['d_day']} ({next_ev['report_kst']}), "
                 f"EPS 컨센서스 {next_ev['eps_estimate']}. " if next_ev else "")
                + f"매크로 이벤트 {len(events)}건 추적, 최근접 {events[0]['event']} D-{events[0]['d_day']}. "
                f"헤드라인 촉매 {len(catalysts)}건."
            ),
        }
        self.confidence_score = 0.9 if rows else 0.7
        self.status = "COMPLETED"
        return self.output_data
