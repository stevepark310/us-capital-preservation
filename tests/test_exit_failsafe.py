"""강제청산 페일세이프 — 05:40 사이클 미발화로 청산이 사라진 사고 재발방지."""
import json
from datetime import datetime

import pytest

from core import exit_failsafe


def clock(hhmm, phase="REGULAR", session="2026-11-19"):
    return {"now_kst": f"2026-11-20 {hhmm}:00", "phase": phase, "session_date": session,
            "kst_shift_min": 0, "session_times_kst": {"regular_close": "06:00"}}


def sqqq():
    return {"bought_qty": 1, "entry_price": 48.18, "sl": 45.99, "closed": False,
            "exit_mode": "chandelier_v2", "force_exit_kst": "05:40",
            "last_session_date": "2026-11-19"}


def test_overdue_after_force_exit_time():
    doc = exit_failsafe.overdue({"SQQQ": sqqq()}, clock("05:45"))
    assert len(doc) == 1
    assert doc[0]["ticker"] == "SQQQ" and doc[0]["minutes_late"] == 5
    assert doc[0]["same_session"] is True


def test_not_overdue_before_early_buffer():
    assert exit_failsafe.overdue({"SQQQ": sqqq()}, clock("05:20")) == []


def test_early_buffer_fires_at_0530():
    assert exit_failsafe.overdue({"SQQQ": sqqq()}, clock("05:29")) == []
    assert len(exit_failsafe.overdue({"SQQQ": sqqq()}, clock("05:31"))) == 1


def test_closed_or_carry_brackets_excluded():
    b = sqqq()
    b["closed"] = True
    assert exit_failsafe.overdue({"SQQQ": b}, clock("05:50")) == []
    sgov = {"bought_qty": 1, "entry_price": 100.53, "carry_parking": True}
    assert exit_failsafe.overdue({"SGOV": sgov}, clock("05:50")) == []


def test_overdue_visible_outside_regular_session():
    """정규장이 끝난 뒤에도 '연체'를 알 수 있어야 한다(다음 세션 첫 사이클이 닫는다)."""
    doc = exit_failsafe.overdue({"SQQQ": sqqq()}, clock("09:00", phase="POST_MARKET"))
    assert len(doc) == 1
    assert "다음 정규장" in doc[0]["note"]


def test_cloud_gap_critical(tmp_path, monkeypatch):
    p = tmp_path / "cloud_status.json"
    p.write_text(json.dumps({"market": {"last_cycle_kst": "2026-11-20 03:32:05"}}),
                 encoding="utf-8")
    monkeypatch.setattr(exit_failsafe, "CLOUD_PATH", str(p))
    doc = exit_failsafe.cloud_gap(now=datetime(2026, 11, 20, 5, 40), clock=clock("05:40"))
    assert doc["age_min"] > 45 and doc["critical"] is True
    ok = exit_failsafe.cloud_gap(now=datetime(2026, 11, 20, 3, 45), clock=clock("03:45"))
    assert ok["critical"] is False and ok["warn"] is False


def test_record_persists_and_flags_action(tmp_path, monkeypatch):
    out = tmp_path / "exit_failsafe.json"
    monkeypatch.setattr(exit_failsafe, "OUT_PATH", str(out))
    monkeypatch.setattr(exit_failsafe, "CLOUD_PATH", str(tmp_path / "missing.json"))
    doc = exit_failsafe.record({"SQQQ": sqqq()}, clock("05:45"))
    assert doc["action_required"] is True
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["overdue"][0]["ticker"] == "SQQQ"


def test_early_min_can_be_disabled_by_bracket_field():
    b = sqqq()
    b["force_exit_early_min"] = 0
    assert exit_failsafe.overdue({"SQQQ": b}, clock("05:35")) == []
    assert len(exit_failsafe.overdue({"SQQQ": b}, clock("05:41"))) == 1


def test_autotrader_marks_overdue_outside_session_and_closes_on_open(tmp_path, monkeypatch):
    """11-19 사고 재현: 05:40을 놓친 SQQQ가 다음 정규장 첫 사이클에 즉시 청산된다."""
    from core import autotrader
    from execution.order_bridge import OrderRouter

    brackets_file = tmp_path / "active_brackets.json"
    brackets_file.write_text(json.dumps({"SQQQ": sqqq()}), encoding="utf-8")
    monkeypatch.setattr(autotrader, "DATA", str(tmp_path))
    monkeypatch.setattr(autotrader, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(autotrader, "STATUS_PATH", str(tmp_path / "status.json"))
    monkeypatch.setattr(autotrader, "LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(autotrader, "LEASE_PATH", str(tmp_path / "lease.json"))
    monkeypatch.setattr(autotrader, "TRADE_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(exit_failsafe, "CLOUD_PATH", str(tmp_path / "cloud.json"))
    monkeypatch.setattr(exit_failsafe, "OUT_PATH", str(tmp_path / "failsafe.json"))

    class FakeRouter(OrderRouter):
        def __init__(self):
            super().__init__(dry_run=True)
            self.calls = []

        def place(self, ticker, side, qty, price, reason, **kw):
            self.calls.append({"ticker": ticker, "side": side, "qty": qty, "price": price,
                               "reason": reason})
            return {"status": "DRY_RUN", "orr_no": None, "run_id": None}

    # (1) 장 마감 후 사이클 — 주문 없이 '연체'만 브래킷에 표시
    post = {"now_kst": "2026-11-20 09:00:00", "phase": "POST_MARKET", "session_date": "2026-11-19",
            "kst_shift_min": 0, "session_times_kst": {"regular_close": "06:00"}}
    trader = autotrader.AutonomousTrader(router=FakeRouter(), dry_run=False)
    trader.market = lambda: {"clock": post, "snapshot": {}, "quotes": {}, "techs": {},
                             "daily": {}, "vix": 27.0, "integrity": {}, "broker_marks": {}}
    monkeypatch.setattr(trader, "_ledger", lambda: {"equity_usd": 878.0, "cash_usd": 600.0})
    monkeypatch.setattr(trader, "entries", lambda *a, **k: [])
    monkeypatch.setattr(trader, "evolve", lambda *a, **k: {"decision": "NO_CHANGE"})
    trader.run(execute=True)
    saved = json.loads(brackets_file.read_text(encoding="utf-8"))
    assert saved["SQQQ"]["force_exit_due"] is True
    assert saved["SQQQ"]["force_exit_kst"] == "05:40"

    # (2) 다음 정규장 첫 사이클 — 시각과 무관하게 연체분을 먼저 닫는다
    reg = {"now_kst": "2026-11-20 23:35:00", "phase": "REGULAR", "session_date": "2026-11-20",
           "kst_shift_min": 0, "session_times_kst": {"regular_close": "06:00"}}
    trader2 = autotrader.AutonomousTrader(router=FakeRouter(), dry_run=True)
    monkeypatch.setattr(trader2, "_ledger", lambda: {"equity_usd": 878.0, "cash_usd": 600.0})
    st2 = trader2.load_state("2026-11-20")
    brackets = json.loads(brackets_file.read_text(encoding="utf-8"))
    actions = trader2.exits(st2, brackets, {"SQQQ": {"intraday_price": 49.7}}, reg)
    assert len(actions) == 1 and actions[0]["side"] == "SELL"
    assert "연체 강제청산" in actions[0]["reason"]
