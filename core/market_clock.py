"""
CUBE Market Clock — 24/7 session phase resolver (US equities, KST anchored)

The desk previously treated "now" as either pre-market or regular session and
replayed static templates the rest of the time. This module gives every agent a
single, explicit notion of *when* it is:

  REGULAR       09:30-16:00 ET  (23:30-06:00 KST in EST, 22:30-05:00 KST in EDT)
  POST_MARKET   16:00-20:00 ET
  OFF_HOURS     weekday gap
  PRE_MARKET    04:00-09:30 ET
  WEEKEND_PREP  Sat/Sun after the Friday close until Monday pre-market
  HOLIDAY_PREP  US market holiday (exchange closed)

2026-11-19 (일광절약시간 결함 수정)
----------------------------------
The KST boundaries used to be hard-coded as if the US were always on EST
(23:30 KST open). From March to November the exchange opens at 09:30 EDT which
is 22:30 KST, so for eight months of the year every gate (open, close, 05:40
force exit, 04:30 entry cut-off) was an hour late. The clock is now resolved by
converting the wall clock to US Eastern time with the real DST rule (second
Sunday of March 02:00 → first Sunday of November 02:00) instead of assuming a
fixed offset, and it exposes `kst_shift_min` (-60 during EDT, 0 during EST) so
the KST gate strings in the policy files can be shifted onto the session.

Everything is computed from a naive-localtime Asia/Seoul clock so the module
needs no tz database beyond the fixed KST offset (UTC+9).
"""

from datetime import datetime, timedelta, date, time, timezone
from typing import Dict, Any, Optional, Tuple

KST = timedelta(hours=9)
UTC = timedelta(0)

# US exchange holidays (2026). Source: NYSE 2026 holiday calendar.
US_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}
# Early closes (13:00 ET). In KST the close lands at 03:00 (EST) / 02:00 (EDT).
US_HALF_DAYS_2026 = {date(2026, 11, 27), date(2026, 12, 24)}

# Session boundaries in US Eastern local time. KST values are derived, never hard-coded.
PRE_MARKET_OPEN_ET = time(4, 0)
REGULAR_OPEN_ET = time(9, 30)
REGULAR_CLOSE_ET = time(16, 0)
HALF_DAY_CLOSE_ET = time(13, 0)
POST_MARKET_CLOSE_ET = time(20, 0)

# --------------------------------------------------------------------- DST
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """해당 월의 n번째 요일(weekday: 월=0)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def dst_bounds_utc(year: int) -> Tuple[datetime, datetime]:
    """미국 동부 DST 구간(UTC 기준 시작/끝).

    시작: 3월 둘째 일요일 02:00 EST = 07:00 UTC
    종료: 11월 첫째 일요일 02:00 EDT = 06:00 UTC
    """
    start_day = _nth_weekday(year, 3, 6, 2)     # 6 = Sunday
    end_day = _nth_weekday(year, 11, 6, 1)
    return (datetime.combine(start_day, time(7, 0)),
            datetime.combine(end_day, time(6, 0)))


def et_offset_hours(utc_dt: datetime) -> int:
    """UTC 시각의 미국 동부 오프셋(-4 EDT / -5 EST)."""
    start, end = dst_bounds_utc(utc_dt.year)
    return -4 if start <= utc_dt < end else -5


def utc_to_et(utc_dt: datetime) -> datetime:
    return utc_dt + timedelta(hours=et_offset_hours(utc_dt))


def kst_to_utc(dt_kst: datetime) -> datetime:
    return dt_kst - KST


def kst_to_et(dt_kst: datetime) -> datetime:
    """KST 벽시계 → 미국 동부 벽시계(naive)."""
    return utc_to_et(kst_to_utc(dt_kst))


def et_to_kst(dt_et: datetime) -> datetime:
    """미국 동부 벽시계 → KST 벽시계(naive). DST는 그 시각의 UTC로 판정한다."""
    # ET → UTC: 후보 오프셋 두 개를 넣어 실제 DST 판정과 맞는 쪽을 쓴다.
    for guess in (-5, -4):
        utc_dt = dt_et - timedelta(hours=guess)
        if et_offset_hours(utc_dt) == guess:
            return utc_dt + KST
    return dt_et - timedelta(hours=-5) + KST


def kst_shift_min(now: Optional[datetime] = None) -> int:
    """EST 기준 KST 게이트(23:30 개장 등)를 이 세션에 맞추기 위한 이동 분.

    EST = 0 · EDT = -60. 정책 파일의 KST 시각 문자열은 EST 기준으로 쓰여 있으므로
    이 값을 게이트에 더하면 서머타임 장에서도 같은 ET 시각을 가리킨다.
    """
    now = now or now_kst()
    et = kst_to_et(now)
    d = et.date()
    regular_kst = et_to_kst(datetime.combine(d, REGULAR_OPEN_ET))
    baseline = datetime.combine(d, time(23, 30))
    return int(round((regular_kst - baseline).total_seconds() / 60.0))


def now_kst() -> datetime:
    """Current wall clock on the Asia/Seoul desk (naive, KST)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST


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


def session_close_et(d: date) -> time:
    return HALF_DAY_CLOSE_ET if d in US_HALF_DAYS_2026 else REGULAR_CLOSE_ET


def resolve(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resolve the current phase plus the next scheduled session open.

    판정은 미국 동부 벽시계로 한다(DST 자동). 반환 구조는 기존과 호환되며,
    `kst_shift_min`·`session_times_kst`·`now_et`를 추가로 담는다.
    """
    now = now or now_kst()
    et = kst_to_et(now)
    et_d, et_t = et.date(), et.timetz().replace(tzinfo=None)

    close_et = session_close_et(et_d)
    trading = is_trading_day(et_d)
    shift = kst_shift_min(now)

    def _kst_str(et_dt: datetime) -> str:
        return et_to_kst(et_dt).strftime("%Y-%m-%d %H:%M")

    if trading and REGULAR_OPEN_ET <= et_t < close_et:
        phase, session_date = "REGULAR", et_d
    elif trading and close_et <= et_t < POST_MARKET_CLOSE_ET:
        phase, session_date = "POST_MARKET", et_d
    elif et_d.weekday() >= 5:
        phase, session_date = "WEEKEND_PREP", prev_trading_day(et_d)
    elif et_d in US_HOLIDAYS_2026:
        phase, session_date = "HOLIDAY_PREP", prev_trading_day(et_d)
    elif trading and PRE_MARKET_OPEN_ET <= et_t < REGULAR_OPEN_ET:
        phase, session_date = "PRE_MARKET", et_d
    else:
        # 개장 전이면 직전 거래일, 마감 후면 오늘(ET) 세션이 '마지막 완료 세션'이다.
        phase = "OFF_HOURS"
        session_date = et_d if (trading and et_t >= close_et) else prev_trading_day(et_d)

    # Next regular open (09:30 ET) in ET, then converted to KST.
    if trading and et_t < REGULAR_OPEN_ET:
        next_open_et = datetime.combine(et_d, REGULAR_OPEN_ET)
    else:
        next_open_et = datetime.combine(next_trading_day(et_d), REGULAR_OPEN_ET)
    next_open_kst = et_to_kst(next_open_et)

    # Next pre-market open (04:00 ET)
    if trading and et_t < PRE_MARKET_OPEN_ET:
        next_pre_et = datetime.combine(et_d, PRE_MARKET_OPEN_ET)
    elif trading:
        next_pre_et = datetime.combine(next_trading_day(et_d), PRE_MARKET_OPEN_ET)
    else:
        next_pre_et = datetime.combine(next_trading_day(et_d), PRE_MARKET_OPEN_ET)
    next_pre_kst = et_to_kst(next_pre_et)

    hours_to_open = round((next_open_kst - now).total_seconds() / 3600.0, 2)
    off_hours = phase in {"OFF_HOURS", "POST_MARKET", "WEEKEND_PREP", "HOLIDAY_PREP"}

    def _kst_time(et_time: time) -> str:
        return et_to_kst(datetime.combine(et_d, et_time)).strftime("%H:%M")

    return {
        "now_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "now_et": et.strftime("%Y-%m-%d %H:%M:%S"),
        "et_date": et_d.isoformat(),
        "et_offset_hours": et_offset_hours(kst_to_utc(now)),
        "kst_shift_min": shift,
        "phase": phase,
        "is_off_hours": off_hours,
        "is_weekend": et_d.weekday() >= 5,
        "is_trading_day": trading,
        "is_half_day": et_d in US_HALF_DAYS_2026,
        "session_date": session_date.isoformat(),
        "last_completed_session": session_date.isoformat(),
        "next_open_kst": next_open_kst.strftime("%Y-%m-%d %H:%M KST"),
        "next_premarket_kst": next_pre_kst.strftime("%Y-%m-%d %H:%M KST"),
        "hours_to_next_open": hours_to_open,
        "session_times_kst": {
            "pre_market_open": _kst_time(PRE_MARKET_OPEN_ET),
            "regular_open": _kst_time(REGULAR_OPEN_ET),
            "regular_close": _kst_time(close_et),
            "post_market_close": _kst_time(POST_MARKET_CLOSE_ET),
        },
        "dst": {"in_effect": shift != 0, "shift_min": shift,
                "note": "EST 기준 정책 시각에 kst_shift_min을 더하면 이 세션의 KST 시각"},
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
