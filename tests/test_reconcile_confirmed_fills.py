"""Pure mocked NH fill reconciliation; no network or orders."""
import json
import pytest

from scripts import reconcile_confirmed_fills as rf


class FakeTrader:
    def __init__(self, pending=True):
        self.pending = pending
        self.events = []
        self.saved = False
        self.brackets_saved = False
        self.lease_released = False

    def acquire_lease(self, name):
        return True

    def release_lease(self, name):
        self.lease_released = True

    def load_state(self, session_date):
        return {"pending": [{"ticker": "SQQQ", "side": "BUY", "qty": 1}] if self.pending else []}

    def _open_brackets(self):
        return {"SQQQ": {"pending_entry": True}}

    def reconcile(self, state, brackets, account):
        state["pending"] = []
        self.events.append({"kind": "FILL_CONFIRMED", "ticker": "SQQQ",
                            "side": "BUY", "qty": 1, "price": 48.18})
        return {"checked": 1, "confirmed": 1, "open": 0}

    def save_state(self, state):
        self.saved = True

    def _save_brackets(self, brackets):
        self.brackets_saved = True


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "LIVE", str(tmp_path / "live.json"))
    (tmp_path / "live.json").write_text(json.dumps({
        "api_status": "CONNECTED", "cash_buffer_usd": 400.0,
        "positions": [{"ticker": "NVDA", "qty": 1}, {"ticker": "SGOV", "qty": 1}]
    }), encoding="utf-8")
    return tmp_path


def test_pending_confirmed_updates_holdings_and_preserves_cash(sandbox):
    t = FakeTrader()
    acct = {"found": True, "rsp_cd": "00166", "equity_usd": 878.0, "cash_usd": 600,
            "positions": {"SQQQ": {"qty": 1, "price": 48.2, "avg_price": 48.18},
                          "SGOV": {"qty": 1, "price": 100.6, "avg_price": 100.53}}}
    result = rf.reconcile_pending(trader=t, account=acct)
    assert result["confirmed"] == 1
    assert t.saved and t.brackets_saved and t.lease_released
    live = json.loads((sandbox / "live.json").read_text(encoding="utf-8"))
    assert {p["ticker"] for p in live["positions"]} == {"SQQQ", "SGOV"}
    assert live["cash_buffer_usd"] == 400.0
    assert live["nh_balance_cash_usd"] == 600
    assert len(live["fills_confirmed"]) == 1


def test_account_failure_must_not_save_any_fill(sandbox):
    t = FakeTrader()
    with pytest.raises(RuntimeError):
        rf.reconcile_pending(trader=t,
                             account={"found": False, "rsp_cd": "ERROR", "positions": {}})
    assert t.lease_released is True
    assert t.saved is False
    assert t.brackets_saved is False


def test_empty_pending_does_not_call_broker(sandbox):
    t = FakeTrader(pending=False)
    result = rf.reconcile_pending(trader=t)
    assert result["confirmed"] == 0
    assert not t.saved and t.lease_released
