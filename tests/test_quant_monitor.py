"""Read-only shared quant monitor plus live new-entry freeze tests."""
import json

from scripts import quant_monitor as qm
from core import autotrader
from core.autotrader import AutonomousTrader
from conftest import FakeRouter, clock_at, ledger


def test_shared_monitor_flags_unclosed_forced_exit():
    live = {"current_total_equity_usd": 900,
            "positions": [{"ticker": "SQQQ", "qty": 1, "avg_price": 48.18},
                          {"ticker": "SGOV", "qty": 1, "avg_price": 100.5}]}
    brackets = {"SQQQ": {"bought_qty": 1, "entry_price": 48.18,
                         "opened_at": "2026-11-20 03:32:05",
                         "force_exit_kst": "05:40", "closed": False}}
    r = qm.report(live, brackets, {}, {"now_kst": "2026-11-20 23:30:00",
                                       "phase": "REGULAR", "session_date": "2026-11-20"})
    assert r["status"] == "FREEZE_NEW_RISK"
    assert r["overdue_managed_exits"] == ["SQQQ"]
    assert r["three_x_notional_pct"] > 0
    assert r["additive_shock_loss_pct"] > 0
    assert r["prices_may_be_stale"] is True


def test_actual_entry_engine_blocks_new_buys_when_prior_exit_overdue(tmp_trader, monkeypatch):
    tmp_path, bracket_path = tmp_trader
    brackets = json.loads(bracket_path.read_text(encoding="utf-8"))
    brackets["SQQQ"] = {"bought_qty": 1, "entry_price": 48.18,
                        "force_exit_kst": "05:40", "opened_at": "2026-11-20 03:32:05",
                        "closed": False}
    router = FakeRouter()
    trader = AutonomousTrader(policy=autotrader.execution_policy.load_policy(),
                              router=router, dry_run=False, base_dir=str(tmp_path))
    trader.entry_candidates = lambda *a, **k: [{
        "ticker": "SOXL", "source": "DIP_SWING_DAILY",
        "why": "test", "atr_pct": 8.0}]
    clock = {"phase": "REGULAR", "now_kst": "2026-11-20 23:45:00",
             "session_date": "2026-11-20"}
    st = trader.load_state("2026-11-20")
    result = trader.entries(st, brackets,
                            {"clock": clock, "quotes": {"SOXL": {"intraday_price": 50}},
                             "daily": {}, "vix": 20}, ledger())
    assert result == []
    assert router.calls == []
    assert any(e["kind"] == "ENTRY_BLOCKED_GLOBAL" and "강제청산" in e["reason"]
               for e in trader.events)
