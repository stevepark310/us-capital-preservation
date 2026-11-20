"""청산 규칙 단일 권한 — SQQQ(BEAR_ALPHA_HEDGE) vs 평단 사다리 충돌 재발방지."""
import pytest

from core import avg_anchor_engine, exit_authority


def hedge_bracket():
    """11-19 실측 SQQQ 브래킷(active_brackets.json)."""
    return {"bought_qty": 1, "entry_price": 48.18, "planned_price": 48.09,
            "sl": 45.99, "initial_sl_pct": 4.54, "breakeven_stop": 48.34,
            "sl_triggered": False, "closed": False, "atr_pct_at_plan": 6.06,
            "sl_active_after_kst": "23:45", "highest_high": 48.18,
            "opened_at": "2026-11-20 03:32:05", "autonomous": True,
            "entry_source": "BEAR_ALPHA_HEDGE", "bracket_class": "INTRADAY",
            "atr_basis": "intraday", "bracket_spec": "BEAR_ALPHA_HEDGE",
            "exit_mode": "chandelier_v2", "bracket_version": "chandelier_v2",
            "trail_atr_mult": 0.9, "force_exit_kst": "05:40"}


def ladder_for_sqqq():
    """11-19 실측 avg_anchor_plan.json 사다리(충돌 상대)."""
    return {"ticker": "SQQQ", "qty": 1, "avg_price": 48.18, "atr_pct": 6.0,
            "breakeven": 48.34, "tp1": 49.19, "tp1_pct": 2.1, "tp2": 50.2, "tp2_pct": 4.2,
            "sl": 47.46, "sl_pct": -1.5, "tp1_qty": 1, "tp2_qty": 0, "runner_qty": 0,
            "levels_locked": False, "force_exit_kst": None, "sl_active_after_kst": "23:45",
            "expected_pnl_usd": {"tp1": 1.01, "tp2": 0.0, "sl_full": -0.72}}


def test_owner_of_intraday_hedge_is_spec():
    info = exit_authority.owner_of("SQQQ", hedge_bracket())
    assert info["owner"] == "SPEC:BEAR_ALPHA_HEDGE"
    assert info["exit_rule"] == "chandelier_v2"
    assert info["writable_by_ladder"] is False


def test_locked_levels_never_writable():
    nvda = {"bought_qty": 1, "entry_price": 173.3, "sl": 150.0, "levels_locked": True,
            "force_exit_kst": None}
    info = exit_authority.owner_of("NVDA", nvda)
    assert info["owner"] == "USER_LOCKED" and info["writable_by_ladder"] is False


def test_legacy_fill_anchored_is_ladder_owned():
    leg = {"bought_qty": 2, "entry_price": 50.43, "sl": 49.17, "bracket_version": "fill_anchored_v2",
           "ladder_engine": "AVG_ANCHOR_PROFIT_DESK"}
    info = exit_authority.owner_of("SOXL", leg)
    assert info["owner"] == "AVG_ANCHOR" and info["writable_by_ladder"] is True


def test_apply_to_brackets_does_not_overwrite_hedge():
    brackets = {"SQQQ": hedge_bracket()}
    plan = {"engine": "AVG_ANCHOR_PROFIT_DESK", "ladders": [ladder_for_sqqq()]}
    out = avg_anchor_engine.apply_to_brackets(plan, brackets, holdings={"SQQQ": 1})
    kept = out["SQQQ"]
    assert kept["sl"] == pytest.approx(45.99)          # 사다리 47.46으로 당겨지지 않는다
    assert kept["force_exit_kst"] == "05:40"           # 05:40 강제청산이 살아 있다
    assert kept["exit_mode"] == "chandelier_v2"
    assert plan["apply_result"]["skipped"][0]["ticker"] == "SQQQ"


def test_apply_to_brackets_still_writes_legacy_position():
    brackets = {"SOXL": {"bought_qty": 2, "entry_price": 50.43, "sl": 49.17,
                         "bracket_version": "fill_anchored_v2", "closed": False}}
    plan = {"engine": "AVG_ANCHOR_PROFIT_DESK",
            "ladders": [{"ticker": "SOXL", "qty": 2, "avg_price": 50.43, "atr_pct": 25.9,
                         "tp1": 51.94, "tp1_pct": 3.0, "tp2": 53.46, "tp2_qty": 1,
                         "runner_qty": 0, "sl": 49.17, "breakeven": 50.6,
                         "levels_locked": False, "force_exit_kst": "05:40",
                         "sl_active_after_kst": "23:45"}]}
    out = avg_anchor_engine.apply_to_brackets(plan, brackets, holdings={"SOXL": 2})
    assert out["SOXL"]["sl"] == pytest.approx(49.17)
    assert plan["apply_result"]["written"] == ["SOXL"]


def test_conflicts_detects_level_and_force_exit_diff():
    plan = {"ladders": [ladder_for_sqqq()]}
    conf = exit_authority.conflicts({"SQQQ": hedge_bracket()}, plan)
    assert len(conf) == 1
    c = conf[0]
    assert c["ticker"] == "SQQQ"
    assert c["diffs"]["sl"]["bracket"] == 45.99 and c["diffs"]["sl"]["ladder"] == 47.46
    assert "force_exit_kst" in c["diffs"]
    assert c["severity"] == "HIGH"


def test_annotate_plan_marks_advisory_only():
    plan = {"ladders": [ladder_for_sqqq()]}
    out = exit_authority.annotate_plan(plan, {"SQQQ": hedge_bracket()}, persist=False)
    lad = out["ladders"][0]
    assert lad["advisory_only"] is True
    assert lad["exit_rule"] == "chandelier_v2"
    assert lad["authoritative_levels"] == {"sl": 45.99, "tp1": None, "tp2": None,
                                           "force_exit_kst": "05:40"}
    assert out["exit_authority"]["conflicts"]


def test_report_ok_when_single_owner():
    brackets = {"SQQQ": hedge_bracket()}
    plan = {"ladders": [dict(ladder_for_sqqq(), sl=45.99, force_exit_kst="05:40")]}
    doc = exit_authority.report(brackets, plan, persist=False)
    assert doc["ok"] is True
    assert doc["owners"]["SQQQ"]["owner"] == "SPEC:BEAR_ALPHA_HEDGE"
