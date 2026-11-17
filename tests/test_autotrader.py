"""무인 집행 엔진 — 게이트·사이징·체결 조정·청산 (모의 라우터, 실주문 없음)."""
import json

import pytest

from core import autotrader
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at, ledger


def make_market(clock, quotes=None, vix=20.0, daily=None):
    return {"clock": clock, "quotes": quotes or {}, "techs": {}, "daily": daily or {},
            "snapshot": {}, "vix": vix}


def candidate(ticker="SOXL", source="DIP_SWING_DAILY", atr=10.0, **kw):
    c = {"ticker": ticker, "source": source, "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4},
         "why": "테스트 신호", "atr_pct": atr}
    c.update(kw)
    return c


@pytest.fixture()
def trader(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    router = FakeRouter(account={"found": False})
    t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=router,
                         dry_run=False, market=make_market(clock_at("01:00")),
                         base_dir=str(tmp_path))
    t._brackets_path = brackets_path
    return t


def load_brackets(brackets_path):
    return json.loads(brackets_path.read_text(encoding="utf-8"))


def test_entries_halted_by_daily_loss_limit(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    st["realized_pnl_usd"] = -26.0
    trader.entry_candidates = lambda *a, **k: [candidate()]
    actions = trader.entries(st, brackets, make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}}), ledger())
    assert actions == []
    assert st["halted"] is True
    assert "손실" in (st["halt_reason"] or "")


def test_entries_blocked_in_opening_blackout(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    trader.entry_candidates = lambda *a, **k: [candidate()]
    market = make_market(clock_at("23:35"), {"SOXL": {"intraday_price": 50.0}})
    assert trader.entries(st, brackets, market, ledger()) == []


def test_entries_blocked_after_0430(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    trader.entry_candidates = lambda *a, **k: [candidate()]
    market = make_market(clock_at("04:45"), {"SOXL": {"intraday_price": 50.0}})
    assert trader.entries(st, brackets, market, ledger()) == []


def test_no_averaging_down_on_existing_position(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    brackets["SOXL"] = {"bought_qty": 2, "entry_price": 50.43, "sl": 49.0, "tp1": 52.0,
                        "tp1_qty": 1, "tp1_filled": False, "closed": False}
    st = trader.load_state("2026-11-17")
    trader.entry_candidates = lambda *a, **k: [candidate()]
    actions = trader.entries(st, brackets, make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}}), ledger())
    assert actions == []
    assert trader.router.calls == []


def test_entry_sizes_within_budget_and_cash_reserve(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    trader.entry_candidates = lambda *a, **k: [candidate()]
    market = make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}})
    actions = trader.entries(st, brackets, market, ledger())
    assert len(actions) == 1
    buy = actions[0]
    assert buy["side"] == "BUY" and buy["qty"] == 1          # 예산 85.33 → 1주
    assert buy["sizing"]["notional_usd"] <= 85.33 + 0.01
    call = trader.router.calls[0]
    assert call["price"] == pytest.approx(50.10)            # +20bp 지정가
    assert brackets["SOXL"]["bought_qty"] == 0              # 체결 확인 전에는 0
    assert brackets["SOXL"]["pending_entry"] is True
    assert len(st["pending"]) == 1


def test_entry_respects_risk_per_trade(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    # ATR 30% → 손절 8% 클램프: 1주당 위험 $4 → 리스크 한도 $13.4로 3주까지 가능하지만
    # 예산 85.33 → 1주. 리스크 상한 계산값을 확인한다.
    trader.entry_candidates = lambda *a, **k: [candidate(atr=30.0)]
    market = make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}})
    actions = trader.entries(st, brackets, market, ledger())
    sizing = actions[0]["sizing"]
    assert sizing["risk_usd"] <= sizing["risk_cap_usd"]
    assert sizing["sl_pct"] == pytest.approx(8.0)


def test_vix_riskoff_blocks_entries(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    trader.entry_candidates = lambda *a, **k: [candidate()]
    market = make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}}, vix=29.0)
    assert trader.entries(st, brackets, market, ledger()) == []


def test_max_entries_per_session(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    st = trader.load_state("2026-11-17")
    st["entries"] = [{"ticker": "TQQQ"}] * 3
    trader.entry_candidates = lambda *a, **k: [candidate()]
    market = make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 50.0}})
    assert trader.entries(st, brackets, market, ledger()) == []


def test_reconcile_buy_fill_uses_actual_fill_price(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    brackets["SOXL"] = {"bought_qty": 0, "entry_price": 50.0, "planned_price": 50.0,
                        "initial_sl_pct": 8.0, "sl": 46.0, "exit_mode": "chandelier_v2",
                        "pending_entry": True, "sl_active_after_kst": "23:45", "closed": False}
    st = trader.load_state("2026-11-17")
    st["pending"] = [{"ticker": "SOXL", "side": "BUY", "qty": 1, "limit_price": 50.1,
                      "order_no": 249712, "ts_kst": "2026-11-17 23:40:00", "source": "DIP_SWING_DAILY"}]
    account = {"found": True, "positions": {"SOXL": {"qty": 1, "avg_price": 50.02}},
               "recent_executions": {"20261117": {"Output_0": [
                   {"orr_no": 249712, "cns_qty": 1, "cns_pr": 50.02}]}}}
    trader._account = account
    res = trader.reconcile(st, brackets, account=account)
    assert res["confirmed"] == 1
    b = brackets["SOXL"]
    assert b["bought_qty"] == 1
    assert b["entry_price"] == 50.02
    assert b["sl"] == pytest.approx(46.02, abs=0.01)     # 50.02 × (1-8%)
    assert b["pending_entry"] is False
    assert st["pending"] == []


def test_reconcile_sell_fill_records_realized_pnl(trader, tmp_trader):
    tmp_path, brackets_path = tmp_trader
    brackets = load_brackets(brackets_path)
    brackets["SOXL"] = {"bought_qty": 2, "entry_price": 50.43, "tp1": 51.94, "tp1_qty": 1,
                        "tp1_filled": False, "tp2": 53.46, "tp2_qty": 1, "tp2_filled": False,
                        "sl": 49.17, "breakeven_stop": 50.60, "closed": False}
    st = trader.load_state("2026-11-17")
    st["pending"] = [{"ticker": "SOXL", "side": "SELL", "qty": 1, "limit_price": 51.9,
                      "order_no": 249800, "post": {"tp1_filled": True, "sl": 50.60},
                      "ts_kst": "2026-11-18 00:30:00", "reason": "TP1"}]
    account = {"found": True, "positions": {"SOXL": {"qty": 1, "avg_price": 50.43}},
               "recent_executions": {"20261118": {"Output_0": [
                   {"orr_no": 249800, "cns_qty": 1, "cns_pr": 51.94}]}}}
    trader._account = account
    trader.reconcile(st, brackets, account=account)
    b = brackets["SOXL"]
    assert b["tp1_filled"] is True
    assert b["sl"] == 50.60
    assert exit_remaining(b) == 1
    assert st["realized_pnl_usd"] == pytest.approx(1.51, abs=0.02)
    trade_ledger = json.loads((tmp_path / "strategy_trade_ledger.json").read_text(encoding="utf-8"))
    assert trade_ledger["trades"][-1]["ticker"] == "SOXL"


def exit_remaining(b):
    from core import exit_rules
    return exit_rules.remaining_qty(b)


def test_exit_stop_places_sell_order(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    brackets["SOXL"] = {"bought_qty": 2, "entry_price": 50.43, "tp1": 51.94, "tp1_qty": 1,
                        "tp1_filled": False, "tp2": 53.46, "tp2_qty": 1, "tp2_filled": False,
                        "sl": 49.17, "breakeven_stop": 50.60, "sl_active_after_kst": "23:45",
                        "closed": False}
    st = trader.load_state("2026-11-17")
    clock = clock_at("01:00")
    actions = trader.exits(st, brackets, {"SOXL": {"intraday_price": 48.90}}, clock)
    assert len(actions) == 1
    assert actions[0]["side"] == "SELL" and actions[0]["qty"] == 2
    assert trader.router.calls[0]["side"] == "SELL"
    assert trader.router.calls[0]["price"] == pytest.approx(48.75, abs=0.01)   # -30bp
    assert len(st["pending"]) == 1 and st["pending"][0]["side"] == "SELL"


def test_exit_skips_when_pending_sell_exists(trader, tmp_trader):
    brackets = load_brackets(tmp_trader[1])
    brackets["SOXL"] = {"bought_qty": 2, "entry_price": 50.43, "sl": 49.17, "closed": False}
    st = trader.load_state("2026-11-17")
    st["pending"] = [{"ticker": "SOXL", "side": "SELL", "qty": 2, "limit_price": 49.0}]
    assert trader.exits(st, brackets, {"SOXL": {"intraday_price": 48.0}}, clock_at("01:00")) == []


def test_dry_run_does_not_place_or_persist(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    before = brackets_path.read_text(encoding="utf-8")
    router = FakeRouter()
    market = make_market(clock_at("01:00"), {"SOXL": {"intraday_price": 48.0}})
    t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=router,
                         dry_run=True, market=market, base_dir=str(tmp_path))
    status = t.run(execute=False)
    assert status["mode"] == "DRY_RUN"
    assert all(c["dry_run"] for c in router.calls)
    assert brackets_path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "autotrader_state.json").exists()


def test_no_orders_outside_regular_session(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    data = json.loads(brackets_path.read_text(encoding="utf-8"))
    data["SOXL"] = {"bought_qty": 2, "entry_price": 50.43, "sl": 49.17, "closed": False}
    brackets_path.write_text(json.dumps(data), encoding="utf-8")
    router = FakeRouter()
    market = make_market(clock_at("12:00", phase="OFF_HOURS"), {"SOXL": {"intraday_price": 40.0}})
    t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=router,
                         dry_run=False, market=market, base_dir=str(tmp_path))
    status = t.run(execute=True)
    assert status["exits"] == []
    assert router.calls == []


def test_lease_blocks_second_engine(trader):
    assert trader.acquire_lease("autotrader") is True
    other = AutonomousTrader(policy=trader.policy, router=FakeRouter(), dry_run=True)
    assert other.acquire_lease("monitor_and_exit") is False
    trader.release_lease("autotrader")
    assert other.acquire_lease("monitor_and_exit") is True
