"""
CUBE Market Clock — 24/7 session phase resolver (US equities, KST anchored)

The desk previously treated "now" as either pre-market or regular session and
replayed static templates the rest of the time. This module gives every agent a
single, explicit notion of *when* it is:

  REGULAR       09:30-16:00 ET  (23:30-06:00 KST, EDT)
  POST_MARKET   16:00-20:00 ET  (06:00-10:00 KST)
  OFF_HOURS     weekday gap    (10:00-18:00 KST)
  PRE_MARKET    04:00-09:30 ET  (18:00-23:30 KST)
  WEEKEND_PREP  Sat/Sun after the Friday close until Monday pre-market
  HOLIDAY_PREP  US market holiday (exchange closed)

Everything is computed from a naive-localtime Asia/Seoul clock so the module
needs no tz database beyond the fixed KST offset (UTC+9).
"""

from datetime import datetime, timedelta, date, time
from typing import Dict, Any, Optional

KST = timedelta(hours=9)

# US exchange holidays (2026). Source: NYSE 2026 holiday calendar.
US_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}
# Early closes (13:00 ET = 02:00 KST next day)
US_HALF_DAYS_2026 = {date(2026, 11, 27), date(2026, 12, 24)}

# Session boundaries in KST (EDT: KST = ET + 14h)
PRE_MARKET_START = time(18, 0)   # 04:00 ET
REGULAR_START = time(23, 30)     # 09:30 ET
REGULAR_END = time(6, 0)         # 16:00 ET (next calendar day in KST)
POST_MARKET_END = time(10, 0)    # 20:00 ET


def now_kst() -> datetime:
    """Current wall clock on the Asia/Seoul desk (naive, KST)."""
    return datetime.utcnow() + KST


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in US_HOLIDAYS_2026


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def prev_trading_day(d: date) -> date:
    prv = d - timedelta(days=1)
    while not is_trading_day(prv):
        prv -= timedelta(days=1)
    return prv


def resolve(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resolve the current phase plus the next scheduled session open."""
    now = now or now_kst()
    d = now.date()
    t = now.time()

    # A session that opened at 23:30 KST yesterday is still live until 06:00 KST today.
    if t < REGULAR_END and is_trading_day(d - timedelta(days=1)):
        phase = "REGULAR"
        session_date = d - timedelta(days=1)
    elif t < POST_MARKET_END and is_trading_day(d - timedelta(days=1)):
        phase = "POST_MARKET"
        session_date = d - timedelta(days=1)
    elif d.weekday() >= 5:
        phase = "WEEKEND_PREP"
        session_date = prev_trading_day(d)
    elif d in US_HOLIDAYS_2026:
        phase = "HOLIDAY_PREP"
        session_date = prev_trading_day(d)
    elif t >= PRE_MARKET_START:
        phase = "PRE_MARKET"
        session_date = d
    else:
        phase = "OFF_HOURS"
        session_date = prev_trading_day(d)

    # Next regular open (23:30 KST). If today is a trading day and we are before
    # 23:30 KST, that is today; otherwise the next trading day.
    if is_trading_day(d) and t < REGULAR_START:
        next_open_date = d
    else:
        next_open_date = next_trading_day(d)
    next_open = datetime.combine(next_open_date, REGULAR_START)

    # Next pre-market open (18:00 KST on the session date)
    if is_trading_day(d) and t < PRE_MARKET_START:
        next_premarket = datetime.combine(d, PRE_MARKET_START)
    elif is_trading_day(d):
        next_premarket = datetime.combine(next_open_date, PRE_MARKET_START)
    else:
        next_premarket = datetime.combine(next_trading_day(d), PRE_MARKET_START)

    hours_to_open = round((next_open - now).total_seconds() / 3600.0, 2)

    off_hours = phase in {"OFF_HOURS", "POST_MARKET", "WEEKEND_PREP", "HOLIDAY_PREP"}
    return {
        "now_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "now_et": (now - timedelta(hours=14)).strftime("%Y-%m-%d %H:%M:%S"),
        "phase": phase,
        "is_off_hours": off_hours,
        "is_weekend": d.weekday() >= 5,
        "is_trading_day": is_trading_day(d),
        "is_half_day": d in US_HALF_DAYS_2026,
        "session_date": session_date.isoformat(),
        "last_completed_session": prev_trading_day(d).isoformat() if is_trading_day(d) else session_date.isoformat(),
        "next_open_kst": next_open.strftime("%Y-%m-%d %H:%M KST"),
        "next_premarket_kst": next_premarket.strftime("%Y-%m-%d %H:%M KST"),
        "hours_to_next_open": hours_to_open,
        "label_ko": {
            "REGULAR": "정규장 운영 중",
            "PRE_MARKET": "프리마켓 (개장 대기)",
            "POST_MARKET": "시간외 세션",
            "OFF_HOURS": "휴장 시간대 (아시아·유럽 세션)",
            "WEEKEND_PREP": "주말 준비 모드 (월요일 개장 대기)",
            "HOLIDAY_PREP": "휴장일 준비 모드",
        }.get(phase, phase),
    }


def mode_flags(clock: Dict[str, Any]) -> Dict[str, bool]:
    """Convenience flags used by agents to pick their output branch."""
    phase = clock["phase"]
    return {
        "live_auction": phase in {"REGULAR", "PRE_MARKET"},
        "weekend": phase == "WEEKEND_PREP",
        "off_hours": clock["is_off_hours"],
        "can_trade_now": phase == "REGULAR",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(resolve(), ensure_ascii=False, indent=2))
