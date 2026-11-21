"""프리장 수동·지시 개입 전용(manual_intervention_only) — 2026-11-21 18:41 승인(안 A).

승인 범위: "M8 shadow와 월요일 SQQQ 매도전용은 유지하고 프리장 수동·지시 개입(61)만 승인".
그래서 프리장은 ① 세션 라우팅(phases)에는 남아 61 주문이 가능하지만,
② 무인 엔진(auto_phases)에서는 빠져 자동 매수·매도가 나가지 않아야 한다.
"""
import json

import pytest

from core import autotrader, session_routing
from core.autotrader import AutonomousTrader
from core.live_session_gate import check_live_gate
from scripts import cloud_cycle as cc
from scripts import intervene_trade as iv

from conftest import FakeRouter, ledger

SESSION = "2026-11-23"
MANUAL = {"enabled": True, "version": 3, "manual_intervention_only": True,
          "approved_by": "박동훈 (사용자)", "approved_at_kst": "2026-11-21 18:41",
          "entry_conditions": {"max_budget_usd": 60.0, "max_orders_per_session": 2,
                               "allow_sources": ["DIP_SWING_DAILY"]}}
AUTO = dict(MANUAL, manual_intervention_only=False)


def pre_clock(et="04:40", kst=f"{SESSION} 18:40:00"):
    return {"now_kst": kst, "now_et": f"{SESSION} {et}:00", "phase": "PRE_MARKET",
            "session_date": SESSION, "kst_shift_min": 0,
            "next_open_kst": f"{SESSION} 23:30 KST"}


def market(clock, price=50.0, prev_close=50.0, vix=20.0):
    return {"clock": clock, "quotes": {"SOXL": {"intraday_price": price, "prev_close": prev_close}},
            "techs": {}, "daily": {}, "snapshot": {}, "vix": vix, "integrity": {}}


def candidate(**kw):
    c = {"ticker": "SOXL", "source": "DIP_SWING_DAILY",
         "signal": {"rsi2": 12.0, "pct_b": 0.05, "rvol": 1.4}, "why": "테스트 신호",
         "atr_pct": 6.0, "prev_close": 50.0}
    c.update(kw)
    return c


@pytest.fixture()
def make_trader(tmp_trader):
    tmp_path, brackets_path = tmp_trader

    def _make(policy=MANUAL, clock=None):
        router = FakeRouter(account={"found": False})
        plan = session_routing.status(policy)
        t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=router,
                             dry_run=False, market=market(clock or pre_clock()),
                             base_dir=str(tmp_path), session_plan=plan)
        return t, json.loads(brackets_path.read_text(encoding="utf-8"))

    return _make


# ------------------------------------------------------------------ 세션 상태
def test_manual_only_keeps_premarket_routing_but_not_auto_phases():
    st = session_routing.status(MANUAL)
    assert st["premarket_enabled"] is True
    assert st["phases"] == ["REGULAR", "PRE_MARKET"]          # 수동 개입 61은 열려 있다
    assert st["auto_phases"] == ["REGULAR"]                   # 자동 사이클은 정규장만
    assert st["premarket_auto_enabled"] is False
    assert st["premarket_manual_only"] is True
    assert st["order_types"]["PRE_MARKET"] == "61"
    assert "수동·지시 개입" in (st["auto_blocked_reason"] or "")


def test_auto_scope_is_unchanged_when_policy_allows_auto():
    st = session_routing.status(AUTO)
    assert st["auto_phases"] == ["REGULAR", "PRE_MARKET"]
    assert st["premarket_auto_enabled"] is True
    assert st["premarket_manual_only"] is False


def test_default_plan_never_enables_premarket():
    plan = session_routing.default_plan()
    assert plan["phases"] == ["REGULAR"] and session_routing.auto_phases(plan) == ["REGULAR"]


def test_monday_sell_only_gate_still_allows_premarket_sell_path():
    """월요일 게이트(매도 전용) — 프리장 수동 개입 경로는 열려 있고 매수는 게이트가 막는다."""
    gate = {"enabled": True, "session_date": SESSION, "expires_at_kst": "2026-11-24 06:00:00",
            "sell_only": True, "max_buy_order_usd": 0, "max_session_buy_usd": 0,
            "max_session_buy_orders": 0}
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)   # 19:00 KST = 05:00 ET
    out = check_live_gate(gate, pre_clock(), now_utc=now, plan=session_routing.status(MANUAL))
    assert out["ok"] is True
    assert out["phase"] == "PRE_MARKET" and out["order_type"] == "61" and out["sell_only"] is True


# ------------------------------------------------------------------ 무인 엔진 정지
def test_auto_engine_places_no_premarket_entries_under_manual_only(make_trader):
    trader, brackets = make_trader()
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert trader.router.calls == []
    assert any("무인 엔진 허용 단계" in (e.get("reason") or "") or
               "수동·지시 개입" in (e.get("reason") or "") for e in trader.events)


def test_auto_engine_places_no_premarket_exits_under_manual_only(make_trader):
    trader, brackets = make_trader()
    brackets["SOXL"] = {"bought_qty": 1, "entry_price": 60.0, "sl": 55.0, "closed": False,
                        "exit_mode": "chandelier_v2", "atr_pct_at_plan": 6.0,
                        "highest_high": 60.0, "initial_sl_pct": 5.0}
    st = trader.load_state(SESSION)
    status = trader.run(execute=True)
    assert status["exits"] == []
    assert trader.router.calls == []
    assert any(e.get("kind") == "EXITS_SKIPPED" and "수동·지시 개입" in (e.get("reason") or "")
               for e in trader.events)
    assert "수동·지시 개입" in status["next_gate"]
    assert status["session_plan"]["auto_phases"] == ["REGULAR"]


def test_auto_policy_still_trades_premarket(make_trader):
    """manual_intervention_only=false인 정책에서는 종전처럼 61 자동 진입이 동작한다(경로 보존)."""
    trader, brackets = make_trader(policy=AUTO)
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    actions = trader.entries(st, brackets, trader._market, ledger())
    assert actions and actions[0]["order_type"] == "61"


# ------------------------------------------------------------------ 수동 개입 경로
GATE = {"enabled": True, "session_date": SESSION, "expires_at_kst": "2026-11-24 06:00:00",
        "sell_only": False, "max_buy_order_usd": 65.0, "max_session_buy_usd": 120.0,
        "max_session_buy_orders": 2}
QUOTES = {"SOXL": {"intraday_price": 50.0, "prev_close": 50.0,
                   "intraday_bar_time": f"{SESSION}T18:37:00"}}


def test_manual_intervention_still_places_order_type_61(tmp_path):
    out = iv.plan_intervention(ticker="SOXL", side="BUY", qty=1, clock=pre_clock(), gate=GATE,
                               plan=session_routing.status(MANUAL), quotes=QUOTES, brackets={},
                               cash_usd=426.0, equity_usd=891.0,
                               log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is True, out["reason"]
    assert out["order_type"] == "61" and out["manual_intervention"] is True
    assert out["premarket_manual_only"] is True
    assert any(c.get("check") == "premarket_manual_scope" for c in out["checks"])


def test_non_manual_intervention_is_rejected_under_manual_only(tmp_path):
    out = iv.plan_intervention(ticker="SOXL", side="BUY", qty=1, clock=pre_clock(), gate=GATE,
                               plan=session_routing.status(MANUAL), quotes=QUOTES, brackets={},
                               cash_usd=426.0, equity_usd=891.0, manual=False,
                               log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "자동 개입 금지" in out["reason"]


# ------------------------------------------------------------------ 클라우드 자동 사이클
@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.delenv("CUBE_CLOUD_DRY_RUN", raising=False)
    monkeypatch.setattr(cc, "CLOUD_PATH", str(tmp_path / "cloud_status.json"))
    monkeypatch.setattr(cc, "DATA", str(tmp_path))
    monkeypatch.setattr(cc, "LIVE_GATE_PATH", str(tmp_path / "live_execution_gate.json"))
    calls = []

    def fake_run(name, cmd, timeout=900):
        calls.append((name, list(cmd)))
        return {"name": name, "seconds": 0.1, "rc": 0, "ok": True, "tail": "ok"}

    monkeypatch.setattr(cc, "run", fake_run)
    return tmp_path, calls


def test_cloud_cycle_skips_premarket_under_manual_only(sandbox, monkeypatch):
    tmp, calls = sandbox
    (tmp / "live_execution_gate.json").write_text(json.dumps(GATE), encoding="utf-8")
    monkeypatch.setattr("core.market_clock.resolve", lambda *a, **k: pre_clock())
    out = cc.market_cycle()
    assert out["result"] == "SKIPPED_PREMARKET_MANUAL"
    assert calls == []                              # 무인 단계는 하나도 돌지 않는다
    assert out["session_plan"]["auto_phases"] == ["REGULAR"]
    assert "수동" in out["note"]
    saved = json.loads((tmp / "cloud_status.json").read_text(encoding="utf-8"))
    assert saved["market"]["result"] == "SKIPPED_PREMARKET_MANUAL"
