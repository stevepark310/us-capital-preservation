"""매도 전용 게이트 + 개장 청산 지시 — 2026-11-21 10:41 지시의 집행 경로 검증.

지시: "월요일 게이트 매도 전용 재개하고, SQQQ 1주는 11/23 개장 직후 시장가 익절로
준비해줘. 3주 확대는 보류할게."

검증하는 것
-----------
1. `check_live_gate`가 sell_only(매수 한도 0)를 '정상 게이트'로 인정하는가
   — 종전에는 `0 < cap` 검증 때문에 매도까지 함께 닫혔다.
2. sell_only에서 매수는 게이트·주문 브리지·진입 엔진 세 곳에서 모두 막히는가
   (매도는 통과).
3. 2026-11-23 세션 개장 뒤 첫 사이클에 SQQQ 1주가 즉시 체결되는 가격으로 매도
   주문이 나가고, 그 기록이 `data/open_exit_state.json`에 남는가.
4. 지시가 없는 세션·시각·만료 뒤에는 아무 주문도 나가지 않는가.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeRouter, clock_at, ledger
from core import execution_policy, live_integrity, open_exit
from core.autotrader import AutonomousTrader
from core.live_session_gate import check_live_gate

NOV23 = "2026-11-23"
NOW = datetime(2026, 11, 23, 14, 40, tzinfo=timezone.utc)      # 23:40 KST
CLOCK = {"phase": "REGULAR", "session_date": NOV23, "now_kst": f"{NOV23} 23:40:00",
         "kst_shift_min": 0}

SELL_ONLY_GATE = {"enabled": True, "session_date": NOV23,
                  "expires_at_kst": "2026-11-24 06:00:00", "sell_only": True,
                  "max_buy_order_usd": 0, "max_session_buy_usd": 0,
                  "max_session_buy_orders": 0}

PLAN = {"enabled": True, "session_date": NOV23, "open_kst": "23:30",
        "expires_at_kst": "2026-11-24 06:00:00", "max_attempts": 3,
        "orders": [{"ticker": "SQQQ", "qty": 1, "mode": "market", "reason": "개장 익절"}]}


# ------------------------------------------------------------------ gate
def test_sell_only_gate_is_authorized_with_zero_buy_caps():
    out = check_live_gate(SELL_ONLY_GATE, CLOCK, NOW)
    assert out["ok"] is True
    assert out["sell_only"] is True
    assert out["max_buy_order_usd"] == 0 and out["max_session_buy_orders"] == 0


def test_sell_only_gate_rejects_any_nonzero_buy_allowance():
    """sell_only인데 매수 한도가 열려 있으면 게이트 전체를 닫는다(fail-closed)."""
    for key, value in (("max_buy_order_usd", 1), ("max_session_buy_usd", 1),
                       ("max_session_buy_orders", 1)):
        bad = {**SELL_ONLY_GATE, key: value}
        out = check_live_gate(bad, CLOCK, NOW)
        assert out["ok"] is False and "buy cap" in out["reason"], key


def test_zero_caps_without_sell_only_stay_invalid():
    gate = {k: v for k, v in SELL_ONLY_GATE.items() if k != "sell_only"}
    gate["max_buy_order_usd"] = 0
    assert check_live_gate(gate, CLOCK, NOW)["ok"] is False


def test_sell_only_gate_still_requires_the_authorized_session_and_expiry():
    assert check_live_gate(SELL_ONLY_GATE, {**CLOCK, "session_date": "2026-11-24"},
                           NOW)["ok"] is False
    assert check_live_gate(SELL_ONLY_GATE, CLOCK,
                           datetime(2026, 11, 23, 21, 30, tzinfo=timezone.utc))["ok"] is False


def test_order_bridge_blocks_buys_but_allows_sells_in_sell_only(tmp_path, monkeypatch):
    from execution import order_bridge as ob, nh_trader

    class FakeNH:
        def get_order_capacity(self, ticker, side, price):
            return {"rsp_cd": "00166", "currency": "USD", "orderable_usd": 500,
                    "sellable_qty": 1}

        def get_portfolio_state(self, include_raw=False):
            return {"rsp_cd": "00166", "equity": 880, "cash": 620,
                    "positions": {"SQQQ": {"qty": 1, "sellable_qty": 1}}}

    monkeypatch.setattr(ob, "LIVE_GATE_PATH", str(tmp_path / "gate.json"))
    (tmp_path / "gate.json").write_text(
        '{"enabled":true,"session_date":"2026-11-23","expires_at_kst":"2099-01-01 06:00:00",'
        '"sell_only":true,"max_buy_order_usd":0,"max_session_buy_usd":0,'
        '"max_session_buy_orders":0}', encoding="utf-8")
    from core import market_clock
    monkeypatch.setattr(market_clock, "resolve", lambda: dict(CLOCK))
    monkeypatch.setattr(nh_trader, "NHBroker", lambda dry_run=False: FakeNH())
    router = ob.OrderRouter(dry_run=False)
    buy = router._live_pretrade_check("SQQQ", "BUY", 1, 58.0)
    assert buy["ok"] is False and "sell-only" in buy["reason"]
    sell = router._live_pretrade_check("SQQQ", "SELL", 1, 56.0)
    assert sell["ok"] is True and sell["sellable_qty"] == 1


# ------------------------------------------------------------------ open exit
def _plan_state(tmp_path, monkeypatch, plan=None):
    monkeypatch.setattr(open_exit, "CONFIG_PATH", str(tmp_path / "plan.json"))
    monkeypatch.setattr(open_exit, "STATE_PATH", str(tmp_path / "state.json"))
    if plan is not None:
        import json
        (tmp_path / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return open_exit.load_plan()


def test_intent_only_inside_the_authorized_session_window(tmp_path, monkeypatch):
    plan = _plan_state(tmp_path, monkeypatch, PLAN)
    state = {}
    assert open_exit.intent_for("SQQQ", plan, state, CLOCK, NOW)["qty"] == 1
    # 개장 전
    before = {**CLOCK, "now_kst": f"{NOV23} 23:20:00"}
    assert open_exit.intent_for("SQQQ", plan, state, before, NOW) is None
    # 다른 세션·다른 종목
    assert open_exit.intent_for("SQQQ", plan, state, {**CLOCK, "session_date": "2026-11-24"}, NOW) is None
    assert open_exit.intent_for("SOXL", plan, state, CLOCK, NOW) is None
    # 정규장 밖
    assert open_exit.intent_for("SQQQ", plan, state, {**CLOCK, "phase": "POST_MARKET"}, NOW) is None


def test_intent_expires_and_stops_after_submission(tmp_path, monkeypatch):
    plan = _plan_state(tmp_path, monkeypatch, PLAN)
    late = datetime(2026, 11, 23, 21, 30, tzinfo=timezone.utc)   # 11-24 06:30 KST
    assert open_exit.intent_for("SQQQ", plan, {}, CLOCK, late) is None
    state = {"SQQQ": {"last_status": "SUBMITTED", "done": True}}
    assert open_exit.intent_for("SQQQ", plan, state, CLOCK, NOW) is None
    # 접수 실패(거부)는 재시도한다 — 최대 3회까지
    rejected = {"SQQQ": {"last_status": "REJECTED", "attempts": 1}}
    assert open_exit.intent_for("SQQQ", plan, rejected, CLOCK, NOW)["attempt"] == 2
    exhausted = {"SQQQ": {"last_status": "REJECTED", "attempts": 3}}
    assert open_exit.intent_for("SQQQ", plan, exhausted, CLOCK, NOW) is None


def test_plan_disabled_or_missing_is_silent(tmp_path, monkeypatch):
    for plan in ({}, {**PLAN, "enabled": False}):
        loaded = _plan_state(tmp_path, monkeypatch, plan)
        assert open_exit.intent_for("SQQQ", loaded, {}, CLOCK, NOW) is None


def _sqqq_brackets(tmp_path):
    import json
    (tmp_path / "active_brackets.json").write_text(json.dumps({
        "SQQQ": {"bought_qty": 1, "entry_price": 48.18, "sl": 45.99, "closed": False,
                 "exit_mode": "chandelier_v2", "bracket_class": "INTRADAY",
                 "atr_pct_at_plan": 6.06, "trail_atr_mult": 0.9,
                 "force_exit_kst": "05:40", "highest_high": 48.18,
                 "entry_source": "BEAR_ALPHA_HEDGE"},
        "SGOV": {"bought_qty": 1, "entry_price": 100.53, "carry_parking": True,
                 "note": "CARRY_PARKING"},
    }), encoding="utf-8")


def _market(price=58.0, hhmm="23:40"):
    bar = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    return {"clock": {**clock_at(hhmm, session_date=NOV23), "kst_shift_min": 0},
            "quotes": {"SQQQ": {"intraday_price": price, "last": price,
                                "intraday_bar_time": bar, "prev_close": 49.68}},
            "techs": {}, "daily": {}, "snapshot": {}, "vix": 20.0}


def test_open_exit_sells_marketable_limit_and_records_state(tmp_trader, monkeypatch):
    tmp_path, _ = tmp_trader
    _sqqq_brackets(tmp_path)
    _plan_state(tmp_path, monkeypatch, PLAN)
    router = FakeRouter()
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                         market=_market(58.0), base_dir=str(tmp_path), sell_only=True)
    monkeypatch.setattr(t, "_ledger", lambda: ledger())

    status = t.run(execute=True)

    sell = [a for a in status["exits"] if a["ticker"] == "SQQQ"][0]
    assert sell["side"] == "SELL" and sell["qty"] == 1
    assert sell["market"] is True
    assert "개장 청산" in sell["reason"]
    # 시장가 대응: 최우선 매수호가 아래(-300bp)로 걸어 즉시 체결시킨다
    assert sell["price"] == pytest.approx(round(58.0 * (1 - 300 / 10000), 2))
    assert sell["price"] < 58.0
    assert router.calls and router.calls[0]["side"] == "SELL"
    assert status["open_exit"]["orders"][0]["done"] is True
    state = open_exit.load_state()
    assert state["SQQQ"]["last_status"] == "SUBMITTED" and state["SQQQ"]["attempts"] == 1


def test_open_exit_does_not_repeat_after_submission(tmp_trader, monkeypatch):
    tmp_path, _ = tmp_trader
    _sqqq_brackets(tmp_path)
    _plan_state(tmp_path, monkeypatch, PLAN)
    router = FakeRouter()
    for _ in range(2):
        t = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                             market=_market(58.0), base_dir=str(tmp_path), sell_only=True)
        monkeypatch.setattr(t, "_ledger", lambda: ledger())
        t.run(execute=True)
    sells = [c for c in router.calls if c["side"] == "SELL"]
    assert len(sells) == 1


def test_open_exit_is_skipped_when_gate_closed(tmp_trader, monkeypatch):
    """게이트가 닫힌 날(dry-run 사이클)에는 주문 없이 판단만 기록한다."""
    tmp_path, _ = tmp_trader
    _sqqq_brackets(tmp_path)
    _plan_state(tmp_path, monkeypatch, PLAN)
    router = FakeRouter()
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=True,
                         market=_market(58.0), base_dir=str(tmp_path))
    monkeypatch.setattr(t, "_ledger", lambda: ledger())
    status = t.run(execute=False)
    assert status["mode"] == "DRY_RUN"
    # 주문은 '미리보기'로만 기록된다(dry_run=True) — 접수되지 않는다.
    assert router.calls and all(c["dry_run"] is True for c in router.calls)
    sell = [a for a in status["exits"] if a["ticker"] == "SQQQ"][0]
    assert sell["status"] == "DRY_RUN"
    state = open_exit.load_state()
    assert state["SQQQ"].get("dry_run_at_kst") and state["SQQQ"]["last_status"] == "DRY_RUN"
    assert int(state["SQQQ"].get("attempts") or 0) == 0


def test_sell_only_trader_creates_no_buy_orders(tmp_trader, monkeypatch):
    tmp_path, _ = tmp_trader
    router = FakeRouter()
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=router, dry_run=False,
                         market=_market(58.0), base_dir=str(tmp_path), sell_only=True)
    monkeypatch.setattr(t, "entry_candidates", lambda *a, **k: [{
        "ticker": "SOXL", "source": "DIP_SWING_INTRADAY", "why": "테스트", "atr_pct": 10.0}])
    monkeypatch.setattr(t, "_ledger", lambda: ledger())
    status = t.run(execute=True)
    assert status["entries"] == []
    assert status["sell_only_gate"] is True
    assert not [c for c in router.calls if c["side"] == "BUY"]
    blocked = [e for e in status["events"] if e["kind"] == "ENTRY_BLOCKED_GLOBAL"]
    assert blocked and "매도 전용" in blocked[0]["reason"]


# ------------------------------------------------------------------ stale broker mark
def test_stale_broker_mark_does_not_drive_the_sell_price():
    """2026-11-21 05:57 사고: 브로커 잔고 시세(직전 세션 종가 $49.68)로 청산가를 계산."""
    bar = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    quotes = {"SQQQ": {"intraday_price": 58.055, "last": 58.1436,
                       "intraday_bar_time": bar, "prev_close": 49.68}}
    view = live_integrity.price_for(quotes, "SQQQ", CLOCK, "SELL", broker_mark=49.68)
    assert view["divergence_pct"] > 10
    assert view["broker_ignored"] is True
    assert view["side_price"] == pytest.approx(58.055)
    assert view["trusted"] is False        # 신호 품질 경고는 그대로 남는다


def test_broker_mark_still_used_when_intraday_bar_is_stale():
    quotes = {"SQQQ": {"intraday_price": 58.055,
                       "intraday_bar_time": "2026-11-19 09:35:00-05:00",
                       "prev_close": 49.68}}
    view = live_integrity.price_for(quotes, "SQQQ", CLOCK, "SELL", broker_mark=49.68)
    assert view["broker_ignored"] is False
    assert view["side_price"] == pytest.approx(49.68)
