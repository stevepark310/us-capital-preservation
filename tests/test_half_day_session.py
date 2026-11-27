"""조기마감(반일장) 세션청산·신규진입제한 보정 — 2026-11-27 13:37 지시.

미국 거래소가 13:00 ET에 조기마감하면 KST 마감은 06:00 → 03:00으로 당겨진다.
정책 파일의 게이트(강제청산 05:40 · 신규 진입 마감 04:30)는 '마감 20분/90분 전'이라는
상대 의미이므로, 마감이 당겨진 만큼 같이 당겨져야 한다(02:40 · 01:30).
개장 기준 게이트(개장 유예 23:30)는 그대로여야 한다.
"""
from datetime import datetime

import pytest

from core import exit_failsafe, exit_rules, market_clock as mc, portfolio_quant_risk, session_time

HALF_DAY_ET_DATE = "2026-11-27"      # 블랙프라이데이 반일장 (13:00 ET 마감)


def at(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def half_day_clock(hhmm: str):
    """반일장 세션(2026-11-27 ET)의 clock — KST는 다음 날 새벽."""
    return mc.resolve(at(f"2026-11-28 {hhmm}:00"))


def normal_clock(hhmm: str):
    return mc.resolve(at(f"2026-11-25 {hhmm}:00"))


# ------------------------------------------------------------------ market clock
def test_half_day_clock_reports_close_shift_and_session_gates():
    c = half_day_clock("02:45")
    assert c["is_half_day"] is True
    assert c["close_shift_min"] == -180
    assert c["session_times_kst"]["regular_close"] == "03:00"
    assert c["session_times_kst"]["force_exit"] == "02:40"
    assert c["session_times_kst"]["no_new_entry_after"] == "01:30"


def test_normal_day_has_no_close_shift():
    c = normal_clock("02:45")
    assert c["is_half_day"] is False
    assert c["close_shift_min"] == 0
    assert c["session_times_kst"]["regular_close"] == "06:00"
    assert c["session_times_kst"]["force_exit"] == "05:40"
    assert c["session_times_kst"]["no_new_entry_after"] == "04:30"


def test_close_shift_function_is_half_day_only():
    assert mc.close_shift_min(datetime(2026, 11, 27).date()) == -180
    assert mc.close_shift_min(datetime(2026, 12, 24).date()) == -180
    assert mc.close_shift_min(datetime(2026, 11, 25).date()) == 0


# ------------------------------------------------------------------ session_time
def test_force_exit_gate_moves_with_early_close():
    assert session_time.time_reached_close(half_day_clock("02:00"), "05:40") is False
    assert session_time.time_reached_close(half_day_clock("02:41"), "05:40") is True
    assert session_time.close_gate_hhmm(half_day_clock("02:00"), "05:40") == "02:40"


def test_entry_cutoff_moves_with_early_close():
    assert session_time.time_reached_close(half_day_clock("01:20"), "04:30") is False
    assert session_time.time_reached_close(half_day_clock("01:31"), "04:30") is True


def test_open_anchored_gates_are_not_shifted_by_early_close():
    # 개장 유예는 개장(23:30) 기준 — 조기마감과 무관하게 그대로여야 한다.
    c = half_day_clock("23:40")
    assert session_time.in_window(c, "23:30-00:00") is True
    assert session_time.time_reached(c, "00:00") is False
    # 손절 유예(23:45)도 개장 기준이다.
    assert session_time.time_reached(half_day_clock("00:10"), "23:45") is True


def test_late_session_uses_actual_half_day_close():
    assert session_time.late_session(half_day_clock("02:50"), 15) is True   # 마감 03:00 − 15분
    assert session_time.late_session(half_day_clock("02:30"), 15) is False
    assert session_time.late_session(normal_clock("05:50"), 15) is True


# ------------------------------------------------------------------ exit rules
def _bracket(**kw):
    b = {"bought_qty": 1, "entry_price": 100.0, "sl": 50.0, "closed": False,
         "exit_mode": "chandelier_v2", "bracket_class": "INTRADAY",
         "force_exit_kst": "05:40", "atr_pct_at_plan": 5.0, "trail_atr_mult": 0.9,
         "sl_active_after_kst": "23:45", "max_hold_sessions": 3, "highest_high": 101.0}
    b.update(kw)
    return b


def test_chandelier_forces_exit_at_half_day_close():
    # 02:35 = 마감 20분 전(02:40) − 10분 버퍼 → 발동
    decision = exit_rules.chandelier_v2_exit(_bracket(), 101.0, half_day_clock("02:35"))
    assert decision and decision["action"] == "SELL" and decision["close"] is True
    assert "02:40" in decision["reason"]


def test_chandelier_does_not_exit_early_in_half_day_session():
    assert exit_rules.chandelier_v2_exit(_bracket(), 101.0, half_day_clock("01:00")) is None


def test_chandelier_still_exits_at_normal_close():
    decision = exit_rules.chandelier_v2_exit(_bracket(), 101.0, normal_clock("05:35"))
    assert decision and decision["close"] is True
    assert "05:40" in decision["reason"]


def test_ladder_force_exit_guard_is_half_day_aware():
    b = {"bought_qty": 1, "entry_price": 100.0, "sl": 50.0, "tp1": 110.0, "tp1_qty": 1,
         "sl_active_after_kst": "23:45", "force_exit_kst": "05:40"}
    assert exit_rules.ladder_exit_decision(dict(b), 101.0, half_day_clock("02:45"))["close"] is True
    assert exit_rules.ladder_exit_decision(dict(b), 101.0, half_day_clock("01:00")) is None


# ------------------------------------------------------------------ failsafe / quant
def test_failsafe_overdue_uses_effective_gate():
    clock = half_day_clock("02:50")
    doc = exit_failsafe.record({"SOXL": _bracket()}, clock, persist=False)
    row = next(r for r in doc["overdue"] if r["ticker"] == "SOXL")
    assert row["effective_force_exit_kst"] == "02:40"
    assert row["close_shift_min"] == -180
    assert doc["overdue"]


def test_quant_overdue_detects_half_day_close():
    clock = half_day_clock("02:50")
    b = _bracket(opened_at="2026-11-27 23:31:00")
    # 정규장 기준 05:40이면 아직 연체가 아니지만, 반일장 마감은 03:00이고 게이트는 02:40이다.
    assert portfolio_quant_risk.overdue_managed_exits({"SOXL": b}, clock) == ["SOXL"]
    assert portfolio_quant_risk.overdue_managed_exits(
        {"SOXL": b}, normal_clock("02:50")) == []
