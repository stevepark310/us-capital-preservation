"""청산 규칙 — 사다리 / v2 샹들리에 / DIP_SWING / 실적 가드."""
import pytest

from core import exit_rules

from conftest import clock_at


def ladder(**kw):
    base = {"bought_qty": 2, "entry_price": 50.43, "tp1": 51.94, "tp1_qty": 1, "tp1_filled": False,
            "tp2": 53.46, "tp2_qty": 1, "tp2_filled": False, "sl": 49.17, "breakeven_stop": 50.60,
            "sl_active_after_kst": "23:45", "force_exit_kst": "05:40", "sl_triggered": False,
            "closed": False}
    base.update(kw)
    return base


def v2(**kw):
    base = {"bought_qty": 1, "entry_price": 50.0, "exit_mode": "chandelier_v2", "sl": 46.0,
            "trail_atr_mult": 0.9, "atr_pct_at_plan": 10.0, "be_after_atr": 1.0,
            "breakeven_pct": 0.34, "max_hold_sessions": 3, "sl_active_after_kst": "23:45",
            "highest_high": 50.0, "closed": False, "sl_triggered": False}
    base.update(kw)
    return base


def test_remaining_qty_accounts_for_partials():
    b = ladder(tp1_filled=True)
    assert exit_rules.remaining_qty(b) == 1
    b2 = ladder(tp1_filled=True, tp2_filled=True)
    assert exit_rules.remaining_qty(b2) == 0


def test_is_open_excludes_carry_and_closed():
    assert exit_rules.is_open(ladder()) is True
    assert exit_rules.is_open(ladder(closed=True)) is False
    assert exit_rules.is_open({"carry_parking": True, "entry_price": 100.53, "bought_qty": 1}) is False
    assert exit_rules.is_open({"entry_price": 50.0, "bought_qty": 0}) is False


def test_ladder_tp1_sells_one_and_raises_stop():
    b = ladder()
    d = exit_rules.ladder_exit_decision(b, 52.10, clock_at("00:30"))
    assert d["action"] == "SELL" and d["qty"] == 1
    assert d["post"]["tp1_filled"] is True
    assert d["post"]["sl"] == pytest.approx(50.60)


def test_ladder_tp2_after_tp1():
    b = ladder(tp1_filled=True, sl=50.60)
    d = exit_rules.ladder_exit_decision(b, 53.60, clock_at("01:30"))
    assert d["qty"] == 1 and d["post"]["tp2_filled"] is True
    assert d["post"]["sl"] == pytest.approx(51.19, abs=0.01)   # 50.43 × 1.015


def test_ladder_stop_priority_over_take_profit():
    b = ladder(tp1=51.0)
    d = exit_rules.ladder_exit_decision(b, 48.90, clock_at("01:00"))
    assert d["stop"] is True and d["qty"] == 2


def test_opening_grace_blocks_stop_before_2345():
    b = ladder()
    assert exit_rules.ladder_exit_decision(b, 48.0, clock_at("23:35")) is None
    d = exit_rules.ladder_exit_decision(b, 48.0, clock_at("23:50"))
    assert d and d["stop"] is True


def test_eanings_force_exit_with_failsafe_buffer():
    """강제청산은 정책 시각(05:40)보다 10분 먼저 발동한다 — 사이클 1회 누락 흡수."""
    b = ladder()
    assert exit_rules.ladder_exit_decision(b, 51.0, clock_at("05:29")) is None
    d = exit_rules.ladder_exit_decision(b, 51.0, clock_at("05:31"))
    assert d["post"]["force_exited"] is True and d["qty"] == 2
    assert "페일세이프" in d["reason"]


def test_eanings_force_exit_exact_time_when_buffer_zero():
    b = ladder()
    b["force_exit_early_min"] = 0
    assert exit_rules.ladder_exit_decision(b, 51.0, clock_at("05:39")) is None
    d = exit_rules.ladder_exit_decision(b, 51.0, clock_at("05:41"))
    assert d["post"]["force_exited"] is True


def test_nvda_locked_bracket_never_force_exits():
    nvda = {"bought_qty": 1, "entry_price": 173.3, "tp1": 173.0, "tp1_qty": 1, "tp1_filled": False,
            "tp2": None, "tp2_qty": 0, "tp2_filled": False, "sl": 150.0, "levels_locked": True,
            "force_exit_kst": None, "sl_active_after_kst": None, "closed": False}
    assert exit_rules.ladder_exit_decision(nvda, 160.0, clock_at("05:50")) is None
    d = exit_rules.ladder_exit_decision(nvda, 149.9, clock_at("03:00"))
    assert d and d["stop"] is True


def test_v2_initial_stop_and_trail():
    b = v2(highest_high=50.0)
    # 최고가 50, ATR 10% → trail 50 - 4.5 = 45.5 < SL 46 → SL이 먼저
    d = exit_rules.chandelier_v2_exit(b, 45.9, clock_at("01:00"))
    assert d["stop"] is True and "STOP" in d["reason"]


def test_v2_trail_after_high_rises():
    b = v2(entry_price=50.0, sl=46.0)
    exit_rules.chandelier_v2_exit(b, 60.0, clock_at("01:00"))     # 최고가 갱신
    b["sl"] = 46.0
    d = exit_rules.chandelier_v2_exit(b, 55.4, clock_at("01:10"))  # 60 - 4.5 = 55.5 → 이탈
    assert d is not None and ("TRAIL" in d["reason"] or d["stop"] is True)


def test_v2_breakeven_floor_after_one_atr():
    b = v2(entry_price=50.0, sl=46.0, highest_high=50.0)
    exit_rules.chandelier_v2_exit(b, 55.5, clock_at("01:00"))     # +1.1 ATR → 본전 하한 적용
    assert b["trail_stop"] >= 50.0 * 1.0034


def test_v2_time_exit_on_third_session():
    b = v2(sessions_held=3, last_session_date="2026-11-17", highest_high=50.0)
    d = exit_rules.chandelier_v2_exit(b, 50.5, clock_at("05:50", session_date="2026-11-17"))
    assert d and "TIME" in d["reason"]


def test_swing_target_and_stop():
    b = {"bought_qty": 2, "entry_price": 50.0, "swing_hold": True, "tp": 54.0, "sl": 48.0,
         "max_hold_sessions": 3, "closed": False}
    d = exit_rules.swing_exit_decision(b, 54.2, clock_at("02:00"))
    assert d["close"] is True and d["qty"] == 2
    d2 = exit_rules.swing_exit_decision(dict(b), 47.9, clock_at("02:00"))
    assert d2["stop"] is True


def test_decide_dispatches_by_bracket_kind():
    assert exit_rules.decide(v2(), 45.0, clock_at("01:00"))["stop"] is True
    swing = {"bought_qty": 1, "entry_price": 50.0, "swing_hold": True, "tp": 55.0, "sl": 45.0}
    assert exit_rules.decide(swing, 55.5, clock_at("01:00"))["close"] is True
    assert exit_rules.decide(ladder(), 52.0, clock_at("01:00"))["post"]["tp1_filled"] is True
