"""시장 시계 — 서머타임(EDT/EST) 자동 판정과 KST 게이트 이동."""
from datetime import datetime

import pytest

from core import market_clock as mc
from core import session_time


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def test_est_regular_session():
    c = mc.resolve(at("2026-11-18 23:35:00"))
    assert c["phase"] == "REGULAR" and c["session_date"] == "2026-11-18"
    assert c["kst_shift_min"] == 0
    assert c["session_times_kst"]["regular_open"] == "23:30"
    assert c["session_times_kst"]["regular_close"] == "06:00"


def test_est_late_session_and_post_market():
    assert mc.resolve(at("2026-11-19 05:50:00"))["phase"] == "REGULAR"
    c = mc.resolve(at("2026-11-19 06:30:00"))
    assert c["phase"] == "POST_MARKET" and c["session_date"] == "2026-11-18"


def test_off_hours_uses_last_completed_session():
    c = mc.resolve(at("2026-11-19 10:49:00"))
    assert c["phase"] == "OFF_HOURS"
    assert c["session_date"] == "2026-11-18"     # 11-18 세션이 끝난 뒤
    assert c["next_open_kst"].startswith("2026-11-19 23:30")


def test_edt_session_starts_one_hour_earlier_in_kst():
    c = mc.resolve(at("2026-06-17 22:35:00"))
    assert c["phase"] == "REGULAR"
    assert c["kst_shift_min"] == -60
    assert c["session_times_kst"]["regular_open"] == "22:30"
    assert c["session_times_kst"]["regular_close"] == "05:00"


def test_edt_premarket_boundary():
    assert mc.resolve(at("2026-06-17 17:30:00"))["phase"] == "PRE_MARKET"
    assert mc.resolve(at("2026-06-17 16:30:00"))["phase"] == "OFF_HOURS"


def test_weekend_prep_and_next_open_skips_weekend():
    c = mc.resolve(at("2026-11-21 12:00:00"))      # KST 토요일 = ET 금요일 저녁
    assert c["phase"] in ("OFF_HOURS", "WEEKEND_PREP")
    assert c["next_open_kst"].startswith("2026-11-23")


def test_half_day_close():
    assert mc.session_close_et(datetime(2026, 11, 27).date()) == mc.HALF_DAY_CLOSE_ET
    assert mc.session_close_et(datetime(2026, 11, 25).date()) == mc.REGULAR_CLOSE_ET


def test_dst_offset_rules():
    assert mc.et_offset_hours(datetime(2026, 1, 15, 12, 0)) == -5
    assert mc.et_offset_hours(datetime(2026, 7, 15, 12, 0)) == -4
    # 2026년 DST 시작: 3/8 07:00 UTC, 종료: 11/1 06:00 UTC
    assert mc.et_offset_hours(datetime(2026, 3, 8, 6, 59)) == -5
    assert mc.et_offset_hours(datetime(2026, 3, 8, 7, 0)) == -4
    assert mc.et_offset_hours(datetime(2026, 11, 1, 5, 59)) == -4
    assert mc.et_offset_hours(datetime(2026, 11, 1, 6, 0)) == -5


def test_kst_et_roundtrip():
    for s in ("2026-01-15 09:00:00", "2026-07-15 09:00:00", "2026-11-19 05:40:00"):
        assert mc.et_to_kst(mc.kst_to_et(at(s))) == at(s)


def test_session_gate_shifts_with_dst():
    est = {"now_kst": "2026-11-19 05:40:00", "kst_shift_min": 0}
    edt = {"now_kst": "2026-06-18 04:40:00", "kst_shift_min": -60}
    # 둘 다 '15:40 ET' — EST는 05:40 KST, EDT는 04:40 KST
    assert session_time.time_reached(est, "05:40") is True
    assert session_time.time_reached(edt, "05:40") is True
    assert session_time.time_reached({"now_kst": "2026-06-18 04:39:00", "kst_shift_min": -60}, "05:40") is False
    # 개장 유예 창도 함께 이동한다
    assert session_time.in_window({"now_kst": "2026-06-17 22:40:00", "kst_shift_min": -60},
                                  "23:30-00:00") is True
    assert session_time.in_window({"now_kst": "2026-06-17 23:40:00", "kst_shift_min": -60},
                                  "23:30-00:00") is False


def test_late_session_uses_real_close():
    est = {"now_kst": "2026-11-19 05:46:00", "session_times_kst": {"regular_close": "06:00"}}
    assert session_time.late_session(est, 15) is True
    edt = {"now_kst": "2026-06-18 04:46:00", "session_times_kst": {"regular_close": "05:00"}}
    assert session_time.late_session(edt, 15) is True
    assert session_time.late_session({"now_kst": "2026-06-18 04:40:00",
                                      "session_times_kst": {"regular_close": "05:00"}}, 15) is False
