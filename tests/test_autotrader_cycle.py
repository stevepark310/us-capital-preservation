"""엔드투엔드 사이클 — 매수 접수 → 체결 확인 → 손절 매도 (모의 라우터, 실주문 없음)."""
import json

import pytest

from core import autotrader, execution_policy
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at, ledger


def market_at(hhmm, price, vix=20.0):
    return {"clock": clock_at(hhmm, session_date="2026-11-17"),
            "quotes": {"SOXL": {"intraday_price": price}}, "techs": {}, "daily": {},
            "snapshot": {}, "vix": vix}


def test_full_cycle_buy_then_fill_then_stop(tmp_trader, monkeypatch):
    tmp_path, brackets_path = tmp_trader
    router = FakeRouter()
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                         market=market_at("01:00", 50.0), base_dir=str(tmp_path))
    monkeypatch.setattr(t, "entry_candidates", lambda *a, **k: [{
        "ticker": "SOXL", "source": "DIP_SWING_INTRADAY", "why": "테스트", "atr_pct": 10.0}])
    monkeypatch.setattr(t, "_ledger", lambda: ledger())

    status = t.run(execute=True)
    assert status["entries"], "매수 주문이 나가야 한다"
    bracket = json.loads(brackets_path.read_text(encoding="utf-8"))["SOXL"]
    assert bracket["bought_qty"] == 0 and bracket["pending_entry"] is True
    assert status["pending_orders"] and status["pending_orders"][0]["side"] == "BUY"

    # 2사이클: NH 잔고에 1주가 생겼다 → 체결 확정
    order = status["pending_orders"][0]
    account = {"found": True,
               "positions": {"SOXL": {"qty": 1, "avg_price": 49.98, "sellable_qty": 1}},
               "recent_executions": {"20261118": {"Output_0": [
                   {"orr_no": order["order_no"], "cns_qty": 1, "cns_pr": 49.98}]}}}
    t2 = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                          market=market_at("01:10", 49.5), account=account, base_dir=str(tmp_path))
    monkeypatch.setattr(t2, "entry_candidates", lambda *a, **k: [])
    monkeypatch.setattr(t2, "_ledger", lambda: ledger())
    status2 = t2.run(execute=True)
    assert status2["reconcile"]["confirmed"] == 1
    bracket = json.loads(brackets_path.read_text(encoding="utf-8"))["SOXL"]
    assert bracket["bought_qty"] == 1
    assert bracket["entry_price"] == 49.98
    assert status2["pending_orders"] == []

    # 3사이클: 가격이 v2 손절(49.98 × 1-7.5%) 아래로 → 매도 주문
    t3 = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                          market=market_at("02:00", 45.0), account=account, base_dir=str(tmp_path))
    monkeypatch.setattr(t3, "entry_candidates", lambda *a, **k: [])
    monkeypatch.setattr(t3, "_ledger", lambda: ledger())
    status3 = t3.run(execute=True)
    sells = [a for a in status3["exits"] if a["side"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["qty"] == 1
    assert any(c["side"] == "SELL" for c in router.calls)


def test_status_file_written(tmp_trader):
    tmp_path, _ = tmp_trader
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=FakeRouter(), dry_run=False,
                         market=market_at("01:00", 50.0), base_dir=str(tmp_path))
    t.run(execute=True)
    status = json.loads((tmp_path / "autotrader_status.json").read_text(encoding="utf-8"))
    assert status["engine"] == "AUTONOMOUS_INTRADAY"
    assert status["policy"]["enabled"] is True
    assert status["policy"]["ask_before_trade"] is False
    assert status["policy"]["daily_loss_limit_usd"] > 0
    # 로그 1줄
    lines = (tmp_path / "autotrader_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_next_gate_reports_opening_blackout(tmp_trader):
    tmp_path, _ = tmp_trader
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=FakeRouter(), dry_run=False,
                         market=market_at("23:35", 50.0), base_dir=str(tmp_path))
    status = t.run(execute=True)
    assert "개장 유예" in status["next_gate"]
