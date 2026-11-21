"""프리장 집행 — 세션 계획·호가유형 61·진입 조건·판단 근거(2026-11-21)."""
import json

import pytest

from core import autotrader, session_routing
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, ledger

SESSION = "2026-11-23"
APPROVED = {"enabled": True, "version": 2, "approved_by": "박동훈 (사용자)",
            "approved_at_kst": "2026-11-22 09:00:00",
            "entry_conditions": {"max_budget_usd": 60.0, "max_orders_per_session": 2,
                                 "allow_sources": ["DIP_SWING_DAILY", "BEAR_ALPHA_HEDGE"]}}
CLOSED = {"enabled": False, "reason": "백테스트 미승인"}


def pre_clock(et="04:40", kst=f"{SESSION} 18:40:00", session=SESSION):
    return {"now_kst": kst, "now_et": f"{session} {et}:00", "phase": "PRE_MARKET",
            "session_date": session, "kst_shift_min": 0,
            "next_open_kst": f"{session} 23:30 KST"}


def market(clock, price=50.0, prev_close=50.0, vix=20.0):
    return {"clock": clock, "quotes": {"SOXL": {"intraday_price": price,
                                                "prev_close": prev_close}},
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

    def _make(policy=APPROVED, clock=None):
        router = FakeRouter(account={"found": False})
        plan = session_routing.status(policy)
        t = AutonomousTrader(policy=autotrader.execution_policy.load_policy(), router=router,
                             dry_run=False, market=market(clock or pre_clock()),
                             base_dir=str(tmp_path), session_plan=plan)
        return t, json.loads(brackets_path.read_text(encoding="utf-8"))

    return _make


def test_premarket_entries_blocked_when_policy_closed(make_trader):
    trader, brackets = make_trader(policy=CLOSED)
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert any("세션 아님" in (e.get("reason") or "") for e in trader.events)


def test_premarket_entry_places_order_type_61(make_trader):
    trader, brackets = make_trader()
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    actions = trader.entries(st, brackets, trader._market, ledger())
    assert actions and actions[0]["order_type"] == "61"
    call = trader.router.calls[-1]
    assert call["order_type"] == "61" and call["qty"] == 1
    # 판단 근거가 주문 컨텍스트에 실린다
    attribution = call["extra"]["attribution"]
    assert attribution["session"]["phase"] == "PRE_MARKET"
    assert attribution["session"]["order_type"] == "61"
    assert attribution["macro8"]["applied_scale"] == 1.0
    assert attribution["regime_v2"]["mode"] == "shadow"
    assert attribution["buildup"]["hard_lock_ok"] is True


def test_premarket_entry_skipped_outside_window(make_trader):
    trader, brackets = make_trader()
    trader._market = market(pre_clock(et="09:20", kst=f"{SESSION} 23:20:00"))
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert any("진입 창" in (e.get("reason") or "") for e in trader.events)


def test_premarket_gap_chase_is_blocked(make_trader):
    trader, brackets = make_trader()
    trader._market = market(pre_clock(), price=51.2, prev_close=49.5)
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert any("추격" in (e.get("reason") or "") for e in trader.events)


def test_premarket_signal_must_be_allowed_source(make_trader):
    trader, brackets = make_trader()
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate(source="C5_OPEN30_DIRECTION")]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert any("허용 신호" in (e.get("reason") or "") for e in trader.events)


def test_premarket_budget_is_capped_below_regular(make_trader):
    trader, brackets = make_trader()
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    actions = trader.entries(st, brackets, trader._market, ledger())
    assert actions, "1주는 예산 안에서 집행돼야 한다"
    assert actions[0]["sizing"]["budget_usd"] == 60.0


def test_exits_run_in_premarket_with_order_type_61(make_trader):
    trader, brackets = make_trader()
    brackets["SOXL"] = {"bought_qty": 1, "entry_price": 60.0, "sl": 55.0, "closed": False,
                        "exit_mode": "chandelier_v2", "atr_pct_at_plan": 6.0,
                        "highest_high": 60.0, "initial_sl_pct": 5.0}
    st = trader.load_state(SESSION)
    clock = pre_clock()
    actions = trader.exits(st, brackets, {"SOXL": {"intraday_price": 50.0}}, clock, integrity={})
    assert actions and actions[0]["side"] == "SELL"
    assert trader.router.calls[-1]["order_type"] == "61"


def test_regime_gate_is_shadow_until_approved(tmp_path, monkeypatch, make_trader):
    pol = tmp_path / "regime_policy.json"
    pol.write_text(json.dumps({"enabled": True, "block_regimes": ["CRISIS"],
                               "size_scale": {"RISK_OFF": 0.5}}), encoding="utf-8")
    monkeypatch.setattr(autotrader, "REGIME_POLICY_PATH", str(pol))
    trader, _ = make_trader()
    gate = trader.regime_gate(market(pre_clock(), vix=30.0))
    assert gate["mode"] == "shadow" and gate["block_long"] is False
    assert "approved_by" in gate["reason"]


def test_regime_gate_blocks_crisis_longs_once_approved(tmp_path, monkeypatch, make_trader):
    pol = tmp_path / "regime_policy.json"
    pol.write_text(json.dumps({"enabled": True, "approved_by": "박동훈",
                               "block_regimes": ["CRISIS"],
                               "size_scale": {"RISK_OFF": 0.5}}), encoding="utf-8")
    monkeypatch.setattr(autotrader, "REGIME_POLICY_PATH", str(pol))
    trader, brackets = make_trader()
    trader._market = market(pre_clock(), vix=30.0)      # VIX 30 → CRISIS
    st = trader.load_state(SESSION)
    trader.entry_candidates = lambda *a, **k: [candidate()]
    assert trader.entries(st, brackets, trader._market, ledger()) == []
    assert any(e.get("kind") == "ENTRY_BLOCKED_REGIME" for e in trader.events)


def test_decision_attribution_block_is_in_status(make_trader):
    trader, _ = make_trader()
    trader._market = market(pre_clock())
    trader.entry_candidates = lambda *a, **k: []
    status = trader.run(execute=False)
    attr = status["decision_attribution"]
    assert attr["session"]["phase"] == "PRE_MARKET"
    assert set(attr) >= {"macro8", "regime_v2", "buildup", "risk", "session"}
    assert status["session_plan"]["premarket_enabled"] is True
    assert status["order_type"] == "61"
