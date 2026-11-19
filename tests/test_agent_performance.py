"""Offline feedback: only realized closes can shape bandit proposals."""
from scripts import agent_performance as ap


def test_learning_excludes_imported_trades_and_never_deploys():
    ledger = {"trades": [
        {"source": "ledger_import", "pnl_usd": 120.0, "qty": 1, "entry": 10, "exit": 130},
        {"source": "BEAR_ALPHA_HEDGE", "pnl_usd": 3.5, "qty": 1, "entry": 20, "exit": 23.5},
        {"source": "BEAR_ALPHA_HEDGE", "pnl_usd": -1.0, "qty": 1, "entry": 20, "exit": 19},
    ]}
    bus = {"agent_state": {str(i): {"line": f"agent {i}", "ts_kst": "2026-11-20 01:00"}
                           for i in range(1, 11)}}
    out = ap.summarize(ledger, bus)
    assert out["confirmed_trades"] == 2
    assert out["historical_imports_excluded"] == 1
    assert out["team_score"]["realized_pnl_usd"] == 2.5
    assert out["team_score"]["latest_reports"] == 10
    assert len(out["agents"]) == 10
    assert out["auto_deploy"] is False
    assert out["candidate"] is None
    assert out["strategies"][0]["auto_deployed"] is False


def test_empty_learning_set_never_invents_performance():
    out = ap.summarize({}, {})
    assert out["confirmed_trades"] == 0
    assert out["strategies"] == []
    assert out["candidate"] is None
    assert out["team_score"]["realized_pnl_usd"] == 0.0
