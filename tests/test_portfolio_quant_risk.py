"""Deterministic quantitative risk overlay: no network, no real orders."""
import math

from core import portfolio_quant_risk as qr


def test_gross_shock_keeps_small_position_and_refuses_oversize():
    existing = {"positions": {"NVDA": qr.exposure_row("NVDA", 1, 173.3)}, "unpriced": []}
    okay = qr.assess_entry("SOXL", 2, 50.10, 891.63, 7.5, existing)
    assert okay["ok"] and okay["approved_qty"] == 2
    high = qr.assess_entry("SOXL", 1, 160.0, 891.63, 7.5, existing)
    assert not high["ok"] and high["approved_qty"] == 0


def test_inverse_pair_does_not_get_free_hedge_netting():
    p = {"positions": {"TQQQ": qr.exposure_row("TQQQ", 1, 60)}, "unpriced": []}
    risk = qr.assess_entry("SQQQ", 1, 45, 900, 4.0, p)
    assert risk["ok"] is False
    assert "conflicting" in risk["reasons"][0]


def test_quant_decreases_quantity_but_never_increases_it():
    p = {"positions": {}, "unpriced": []}
    x = qr.assess_entry("SOXL", 4, 50, 900, 8.0, p)
    assert x["approved_qty"] == 2
    assert x["approved_qty"] <= x["proposed_qty"]


def test_high_realized_volatility_reduces_leveraged_sleeve():
    base = [100.0]
    for i in range(65):
        base.append(base[-1] * (1.19 if i % 2 else 0.85))
    vol = qr.annualized_vol_pct(base)
    assert vol is not None and vol > 150
    out = qr.assess_entry("SOXL", 2, 50, 900, 7, {"positions": {}, "unpriced": []},
                          history=base)
    assert out["approved_qty"] < 2
    assert out["realized_annual_vol_pct"] > 150


def test_no_price_or_missing_stop_refuses_additional_risk():
    assert not qr.assess_entry("SOXL", 1, 50, 900, 7.5,
                               {"positions": {}, "unpriced": ["NVDA"]})["ok"]
    assert not qr.assess_entry("SOXL", 1, 50, 900, 0,
                               {"positions": {}, "unpriced": []})["ok"]


def test_leverage_group_counts_without_offsetting_different_index():
    p = {"positions": {"SQQQ": qr.exposure_row("SQQQ", 3, 50)}, "unpriced": []}
    x = qr.assess_entry("SOXS", 1, 60, 900, 7, p)
    assert not x["ok"]


def test_pending_buy_is_included_in_gross_and_shock_limits():
    led = {"positions": [{"ticker": "SGOV", "qty": 1, "avg_price": 100}]}
    pending = [{"ticker": "SOXL", "side": "BUY", "qty": 1, "status": "SUBMITTED", "price": 50}]
    result = qr.positions_from_ledger(led, {}, {"SOXL": {"intraday_price": 50}}, pending)
    assert result["positions"]["SOXL"]["qty"] == 1
    assert result["positions"]["SOXL"]["gross_factor_usd"] == 150


def test_managed_exit_overdue_next_day_blocks_new_risk():
    clock = {"now_kst": "2026-11-20 23:45:00", "phase": "REGULAR"}
    b = {"SQQQ": {"opened_at": "2026-11-20 03:32:05",
                  "force_exit_kst": "05:40", "bought_qty": 1, "closed": False}}
    assert qr.overdue_managed_exits(b, clock) == ["SQQQ"]
    assert qr.overdue_managed_exits(b, {"now_kst": "2026-11-20 05:30:00"}) == []
    b["SQQQ"]["closed"] = True
    assert qr.overdue_managed_exits(b, clock) == []


def test_post_midnight_open_and_next_morning_exit():
    b = {"SQQQ": {"opened_at": "2026-11-19 23:40:00",
                  "force_exit_kst": "05:40", "bought_qty": 1}}
    assert qr.overdue_managed_exits(b, {"now_kst": "2026-11-20 04:00:00"}) == []
    assert qr.overdue_managed_exits(b, {"now_kst": "2026-11-20 23:35:00"}) == ["SQQQ"]


def test_config_may_only_tighten_caps():
    limits = qr.limits({"limits": {"max_gross_factor_pct": 900,
                                    "max_trade_stop_risk_pct": 0.5}})
    assert limits["max_gross_factor_pct"] == qr.HARD_CAPS["max_gross_factor_pct"]
    assert limits["max_trade_stop_risk_pct"] == 0.5
