"""Live session gate: allow orders only during explicitly authorized US session."""
from datetime import datetime, timezone

from core.live_session_gate import check_live_gate

NOW = datetime(2026, 11, 19, 18, 30, tzinfo=timezone.utc)
CLOCK = {"phase": "REGULAR", "session_date": "2026-11-19"}
POLICY = {"enabled": True, "session_date": "2026-11-19",
          "expires_at_kst": "2026-11-20 06:00:00",
          "max_buy_order_usd": 65.0, "max_session_buy_usd": 120.0,
          "max_session_buy_orders": 2}


def test_authorized_only_before_expiry():
    approved = check_live_gate(POLICY, CLOCK, NOW)
    assert approved["ok"] is True
    assert approved["max_buy_order_usd"] == 65
    assert check_live_gate(POLICY, CLOCK, datetime(2026, 11, 19, 21, 0,
                                                   tzinfo=timezone.utc))["ok"] is False


def test_wrong_session_and_nonregular_are_blocked():
    assert not check_live_gate(POLICY, {"phase": "REGULAR", "session_date": "2026-11-20"},
                               NOW)["ok"]
    assert not check_live_gate(POLICY, {"phase": "PRE_MARKET",
                                       "session_date": "2026-11-19"}, NOW)["ok"]
    assert not check_live_gate({**POLICY, "enabled": False}, CLOCK, NOW)["ok"]


def test_missing_expiry_or_buy_caps_is_fail_closed():
    for key in ("session_date", "expires_at_kst", "max_buy_order_usd",
                "max_session_buy_usd", "max_session_buy_orders"):
        invalid = dict(POLICY)
        del invalid[key]
        assert not check_live_gate(invalid, CLOCK, NOW)["ok"], key
    assert not check_live_gate({**POLICY, "max_buy_order_usd": 1000}, CLOCK, NOW)["ok"]
