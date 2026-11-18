"""구조 개선 3건 (2026-11-18 11:37 사용자 승인).

① 숏 게이트 2-of-2 → 1-of-1 (VIX ≥ 24 하한 고정, 가격구조 확인 추가)
② DIP의 RVOL 필수 → 가점 (방어 모드에서만 다시 필수)
③ 장중 트레일 0.9 → 1.5×ATR · 일봉/장중/C5 브래킷 규격 분리
"""
import pandas as pd
import pytest

from core import autotrader, bracket_specs, entry_signals, exit_rules, strategy_evolver, swing_engine
from core.autotrader import AutonomousTrader

from conftest import clock_at


# ---------------------------------------------------------------- ① 숏 게이트
def snapshot(vix, dxy=None):
    macro = {"vix": {"last": vix}}
    if dxy is not None:
        macro["dxy"] = {"last": dxy}
    return {"macro": macro}


def test_gate_1of1_arms_with_price_structure():
    gate = entry_signals.bear_alpha_gate(snapshot(24.6, 103.4), None,
                                        price_structure={"below_vwap": True, "price": 49.0, "vwap": 49.8})
    assert gate["armed"] is True
    assert gate["confirmations_required"] == 1
    assert any("VWAP" in c for c in gate["confirmations"])


def test_gate_needs_one_confirmation():
    gate = entry_signals.bear_alpha_gate(snapshot(24.6, 103.4), 55.0,
                                        price_structure={"below_vwap": False, "price": 51.0, "vwap": 50.0})
    assert gate["armed"] is False
    assert "확인 0/1" in gate["reason"]


def test_gate_vix_floor_is_not_relaxed():
    """VIX 하한 24.0 미만은 어떤 확인으로도 열리지 않는다(위임 밖)."""
    gate = entry_signals.bear_alpha_gate(snapshot(23.9, 105.0), 20.0,
                                        price_structure={"below_vwap": True, "price": 49.0, "vwap": 50.0})
    assert gate["armed"] is False
    assert "VIX 23.9 < 24.0" in gate["reason"]


def test_gate_old_2of2_still_possible():
    gate = entry_signals.bear_alpha_gate(snapshot(26.0, 105.0), 20.0,
                                        price_structure={"below_vwap": False},
                                        confirmations_required=2)
    assert gate["armed"] is True
    two = entry_signals.bear_alpha_gate(snapshot(26.0, 103.0), 20.0,
                                       price_structure={"below_vwap": False},
                                       confirmations_required=2)
    assert two["armed"] is False


def test_vwap_state_detects_below_vwap():
    bars = [{"t": "2026-11-17 09:30:00", "c": 50.0 + i * 0.1, "h": 50.1 + i * 0.1, "l": 49.9 + i * 0.1,
             "v": 1000} for i in range(12)]
    bars.append({"t": "2026-11-17 10:30:00", "c": 49.0, "h": 49.1, "l": 48.9, "v": 4000})
    st = entry_signals.intraday_vwap_state(bars)
    assert st is not None and st["below_vwap"] is True
    up = [dict(b) for b in bars]
    up[-1].update({"c": 55.0, "h": 55.1, "l": 54.9})
    assert entry_signals.intraday_vwap_state(up)["below_vwap"] is False


# ---------------------------------------------------------------- ② RVOL 가점
def test_rvol_low_no_longer_blocks_dip():
    """11-12 10:10 사례: RSI2 11.4 · %B 0.02 · RVOL 0.66 → 이제 신호가 나온다."""
    m = {"rsi2": 11.4, "pct_b": 0.02, "rvol": 0.66, "atr_pct": 2.1}
    v = entry_signals.dip_signal_from_metrics(m)
    assert v["ok"] is True
    assert v["bonus"]["rvol"] == 0.0
    assert v["score"] > 0


def test_rvol_adds_score_not_gate():
    low = entry_signals.dip_signal_from_metrics({"rsi2": 11.4, "pct_b": 0.02, "rvol": 0.66, "atr_pct": 2.1})
    high = entry_signals.dip_signal_from_metrics({"rsi2": 11.4, "pct_b": 0.02, "rvol": 1.8, "atr_pct": 2.1})
    assert low["ok"] and high["ok"]
    assert high["score"] > low["score"]
    assert high["bonus"]["rvol"] > 0


def test_oversold_still_required():
    v = entry_signals.dip_signal_from_metrics({"rsi2": 44.7, "pct_b": 0.02, "rvol": 2.0, "atr_pct": 2.0})
    assert v["ok"] is False and "RSI2" in v["why_not"]
    v2 = entry_signals.dip_signal_from_metrics({"rsi2": 11.0, "pct_b": 0.38, "rvol": 2.0, "atr_pct": 2.0})
    assert v2["ok"] is False and "%B" in v2["why_not"]


def test_defensive_mode_restores_rvol_requirement():
    m = {"rsi2": 11.4, "pct_b": 0.02, "rvol": 0.66, "atr_pct": 2.1}
    v = entry_signals.dip_signal_from_metrics(m, {"rvol_required": True, "rvol_min": 1.5})
    assert v["ok"] is False and "방어 모드" in v["why_not"]


def test_swing_engine_rvol_bonus():
    row = pd.Series({"ATR": 1.2, "RSI2": 12.0, "RSI14": 25.0, "PCT_B": 0.05, "RVOL": 0.6,
                     "ATR_PCT": 2.4, "Close": 50.0})
    loose = swing_engine.signal_row(row, {**swing_engine.DIP_PARAMS, "rvol_required": False})
    strict = swing_engine.signal_row(row, {**swing_engine.DIP_PARAMS, "rvol_required": True})
    assert loose is not None and "score" in loose
    assert strict is None
    hot = row.copy()
    hot["RVOL"] = 2.0
    scored = swing_engine.signal_row(hot, {**swing_engine.DIP_PARAMS, "rvol_required": False})
    assert scored["score"] > loose["score"]


# ---------------------------------------------------------------- ③ 브래킷 분리
def test_specs_split_daily_intraday_c5():
    daily = bracket_specs.spec_for("DIP_SWING_DAILY")
    intra = bracket_specs.spec_for("DIP_SWING_INTRADAY")
    c5 = bracket_specs.spec_for("C5_OPEN30_DIRECTION")
    assert daily["bracket_class"] == "DAILY_SWING" and daily["exit_mode"] == "swing_hold"
    assert daily["atr_basis"] == "daily" and daily["initial_sl_atr_mult"] == 1.5
    assert intra["bracket_class"] == "INTRADAY" and intra["trail_atr_mult"] == 1.5
    assert intra["session_close_exit_kst"] == "05:40"
    assert c5["bracket_class"] == "PILOT_C5" and c5["exit_mode"] == "time_exit"
    assert c5["initial_sl_atr_mult"] == 3.0


def test_sl_tp_clamps_per_spec():
    daily = bracket_specs.spec_for("DIP_SWING_DAILY")
    assert bracket_specs.sl_pct(daily, 25.9) == 15.0          # 일봉 ATR 26% → 상한 클램프
    assert bracket_specs.tp_pct(daily, 25.9) == 20.0
    assert bracket_specs.sl_pct(daily, 1.0) == 3.0            # 하한 클램프
    intra = bracket_specs.spec_for("DIP_SWING_INTRADAY")
    assert bracket_specs.sl_pct(intra, 2.0) == 1.5            # 0.75×2.0 = 1.5


@pytest.fixture()
def trader(tmp_trader):
    t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), dry_run=True,
                         market={"clock": clock_at("01:00"), "quotes": {}, "techs": {},
                                 "daily": {}, "snapshot": {}, "vix": 22.0})
    return t


def test_daily_bracket_uses_symmetric_atr15_and_swing_exit(trader):
    cand = {"source": "DIP_SWING_DAILY", "atr_pct": 20.0, "why": "일봉 DIP"}
    b = trader._new_bracket(cand, "SOXL", 50.0, 3, None, "DIP_SWING_DAILY", {"trail_atr_mult": 1.5})
    assert b["bracket_class"] == "DAILY_SWING" and b["swing_hold"] is True
    assert b["initial_sl_pct"] == 15.0                        # 1.5×20% = 30% → 15% 클램프
    assert b["tp_pct_at_plan"] == 20.0                        # 30% → 20% 클램프
    assert b["tp"] == pytest.approx(60.0)
    assert b["max_hold_sessions"] == 3


def test_intraday_bracket_uses_15atr_trail_and_session_exit(trader):
    cand = {"source": "DIP_SWING_INTRADAY", "atr_pct": 2.0, "why": "장중 DIP"}
    b = trader._new_bracket(cand, "SOXL", 50.0, 3, None, "DIP_SWING_INTRADAY", {"trail_atr_mult": 1.5})
    assert b["bracket_class"] == "INTRADAY" and b["exit_mode"] == "chandelier_v2"
    assert b["trail_atr_mult"] == 1.5
    assert b["force_exit_kst"] == "05:40"
    assert b["initial_sl_pct"] == 1.5                          # 0.75×2.0 → 하한 1.5


def test_wider_trail_holds_through_noise():
    """1.5×ATR 트레일은 0.9×ATR이 끊기던 되돌림을 견딘다(11-17 SOXL 구간 재현)."""
    base = {"bought_qty": 1, "entry_price": 44.56, "sl": 42.0, "atr_pct_at_plan": 2.0,
            "be_after_atr": 1.0, "breakeven_pct": 0.34, "max_hold_sessions": 3,
            "sl_active_after_kst": "23:45", "closed": False, "exit_mode": "chandelier_v2"}
    tight = dict(base, trail_atr_mult=0.9)
    wide = dict(base, trail_atr_mult=1.5)
    # 최고가 46.40, 현재 45.20 → ATR 2%($0.89): 0.9 → 45.60(청산), 1.5 → 45.06(보유)
    for b in (tight, wide):
        b["highest_high"] = 46.40
    assert exit_rules.decide(tight, 45.20, clock_at("02:00")) is not None
    assert exit_rules.decide(wide, 45.20, clock_at("02:00")) is None


def test_legacy_bracket_keeps_09_mult():
    b = {"bought_qty": 1, "entry_price": 50.0, "sl": 40.0, "atr_pct_at_plan": 2.0,
         "highest_high": 51.0, "closed": False, "exit_mode": "chandelier_v2"}
    assert exit_rules.decide(b, 50.0, clock_at("02:00")) is not None       # 51.0 − 0.9×1.0 = 50.1


def test_intraday_session_close_exit():
    b = {"bought_qty": 1, "entry_price": 50.0, "sl": 40.0, "atr_pct_at_plan": 2.0,
         "highest_high": 50.2, "closed": False, "exit_mode": "chandelier_v2",
         "trail_atr_mult": 1.5, "force_exit_kst": "05:40", "sl_active_after_kst": "23:45"}
    assert exit_rules.decide(b, 50.0, clock_at("05:20")) is None
    d = exit_rules.decide(b, 50.0, clock_at("05:41"))
    assert d and "SESSION EXIT" in d["reason"]


def test_evolver_defaults_and_clamps_follow_widened_trail():
    assert strategy_evolver.BASE_PARAMS["trail_atr_mult"] == 1.5
    assert strategy_evolver.CLAMPS["trail_atr_mult"] == (1.20, 2.00)
    assert strategy_evolver.BASE_PARAMS["entry_filters"]["rvol_required"] is False
