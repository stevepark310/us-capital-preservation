"""
Session time helpers (KST) for the single-writer execution engine.

미국 정규장(23:30~06:00 KST)은 자정을 넘으므로 00:00~11:59는 '다음 날'로
취급해 분 단위로 비교한다. 이 모듈이 시간 비교의 유일한 구현이고,
`scripts/monitor_and_exit.py`와 `core/autotrader.py`, `core/exit_rules.py`가
모두 여기를 쓴다(엔진마다 시계 해석이 갈라지면 손절 시각이 달라진다).

2026-11-19: 일광절약시간 보정. 정책 파일의 KST 시각(23:30 개장·05:40 강제청산 등)은
EST 기준으로 쓰여 있으므로, `clock["kst_shift_min"]`(-60 EDT / 0 EST)을 게이트에 더해
서머타임 장에서도 같은 ET 시각을 가리키게 한다. 시계 자체(`now`)는 KST 벽시계 그대로다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def kst_hhmm(clock: Dict[str, Any]) -> str:
    return str(clock.get("now_kst") or "")[11:16]


def session_minutes(hhmm: str) -> int:
    """정규장 세션 기준 분. 00:00~11:59 → 1440~2159 (자정 이후)."""
    try:
        h, m = str(hhmm).split(":")[:2]
        total = int(h) * 60 + int(m)
    except Exception:
        return -1
    return total + 24 * 60 if int(h) < 12 else total


def shift_minutes(clock: Dict[str, Any], minutes: int) -> int:
    """EST 기준 세션 분 → 이 세션(서머타임 반영)의 세션 분."""
    try:
        shift = int(clock.get("kst_shift_min") or 0)
    except (TypeError, ValueError):
        shift = 0
    if minutes < 0 or shift == 0:
        return minutes
    return minutes + shift


def time_reached(clock: Dict[str, Any], hhmm: Optional[str]) -> bool:
    """세션 시계가 hhmm(KST)을 지났으면 True. 게이트가 없으면 항상 True."""
    if not hhmm:
        return True
    now = session_minutes(kst_hhmm(clock))
    gate = shift_minutes(clock, session_minutes(hhmm))
    if now < 0 or gate < 0:
        return True
    return now >= gate


def in_window(clock: Dict[str, Any], window: Optional[str]) -> bool:
    """'23:30-00:00' 형태의 세션 구간 안이면 True (시작 포함, 끝 제외)."""
    if not window or "-" not in window:
        return False
    start_s, end_s = window.split("-", 1)
    now = session_minutes(kst_hhmm(clock))
    start = shift_minutes(clock, session_minutes(start_s))
    end = shift_minutes(clock, session_minutes(end_s))
    if now < 0 or start < 0 or end < 0:
        return False
    if end <= start:          # 자정을 넘는 구간
        end += 24 * 60
        if now < start:
            now += 24 * 60
    return start <= now < end


def minutes_since(clock: Dict[str, Any], hhmm: Optional[str]) -> Optional[int]:
    """현재 세션 시각에서 hhmm까지 지난 분(음수면 아직 남음)."""
    if not hhmm:
        return None
    now = session_minutes(kst_hhmm(clock))
    gate = shift_minutes(clock, session_minutes(hhmm))
    if now < 0 or gate < 0:
        return None
    return now - gate


def session_close_hhmm(clock: Dict[str, Any]) -> Optional[str]:
    """정규장 마감 KST 시각(서머타임 반영). clock에 없으면 None."""
    times = clock.get("session_times_kst") or {}
    return times.get("regular_close")


def late_session(clock: Dict[str, Any], minutes_before_close: int = 15) -> bool:
    """정규장 마감 N분 전 이후인가 — 05:45 하드코딩 대신 실제 마감 시각을 쓴다."""
    close = session_close_hhmm(clock)
    now = session_minutes(kst_hhmm(clock))
    if not close or now < 0:
        return now >= 345      # 폴백: 05:45 KST (EST 기준)
    end = session_minutes(close)
    if end < 0:
        return False
    return now >= end - int(minutes_before_close)
