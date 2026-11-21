"""즉시 개입 경로 — 게이트·세션·얇은 호가·물타기·예산 방어(2026-11-21)."""
import json

import pytest

from scripts import intervene_trade as iv

SESSION = "2026-11-23"
CLOCK = {"phase": "PRE_MARKET", "session_date": SESSION, "now_kst": f"{SESSION} 18:40:00",
         "now_et": f"{SESSION} 04:40:00", "kst_shift_min": 0}
REGULAR = {"phase": "REGULAR", "session_date": SESSION, "now_kst": f"{SESSION} 23:45:00",
           "now_et": f"{SESSION} 09:45:00", "kst_shift_min": 0}
GATE = {"enabled": True, "session_date": SESSION, "expires_at_kst": "2026-11-24 06:00:00",
        "sell_only": False, "max_buy_order_usd": 65.0, "max_session_buy_usd": 120.0,
        "max_session_buy_orders": 2}
APPROVED = {"enabled": True, "version": 2, "approved_by": "박동훈 (사용자)",
            "approved_at_kst": "2026-11-22 09:00:00",
            "entry_conditions": {"max_budget_usd": 60.0, "max_orders_per_session": 2}}
QUOTES = {"SOXL": {"intraday_price": 50.0, "prev_close": 50.0,
                   "intraday_bar_time": f"{SESSION}T18:37:00"}}


def plan(**kw):
    from core import session_routing
    return session_routing.status(kw.pop("policy", APPROVED))


def call(**kw):
    base = dict(clock=kw.pop("clock", CLOCK), gate=kw.pop("gate", GATE),
                plan=plan(**{k: kw.pop(k) for k in ("policy",) if k in kw}),
                quotes=kw.pop("quotes", QUOTES), brackets=kw.pop("brackets", {}),
                cash_usd=kw.pop("cash_usd", 426.0), equity_usd=kw.pop("equity_usd", 891.0),
                log_path=kw.pop("log_path", None), ticker="SOXL", side="BUY")
    base.update(kw)
    return iv.plan_intervention(**base)


def test_closed_gate_blocks_intervention(tmp_path):
    closed = dict(GATE, enabled=False)
    out = call(gate=closed, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "게이트" in out["reason"]


def test_premarket_requires_approval_even_with_gate(tmp_path):
    out = call(policy={"enabled": False}, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "premarket" in out["reason"]


def test_premarket_buy_uses_order_type_61_with_slippage_cap(tmp_path):
    out = call(qty=1, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is True, out["reason"]
    assert out["order_type"] == "61"
    assert out["limit_price"] == pytest.approx(50.08, abs=0.01)     # +15bp 상한
    assert out["phase"] == "PRE_MARKET"


def test_regular_session_buy_uses_order_type_00(tmp_path):
    regular_quotes = {"SOXL": {"intraday_price": 50.0, "prev_close": 49.5}}
    out = call(clock=REGULAR, quotes=regular_quotes, qty=1, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is True
    assert out["order_type"] == "00"


def test_sell_only_gate_blocks_new_buy(tmp_path):
    gate = {**GATE, "sell_only": True, "max_buy_order_usd": 0.0,
            "max_session_buy_usd": 0.0, "max_session_buy_orders": 0}
    out = call(gate=gate, qty=1, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "매도 전용" in out["reason"]


def test_averaging_down_is_blocked(tmp_path):
    brackets = {"SOXL": {"bought_qty": 1, "entry_price": 49.0, "closed": False}}
    out = call(qty=1, brackets=brackets, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "물타기" in out["reason"]


def test_sell_cannot_exceed_holdings(tmp_path):
    brackets = {"SOXL": {"bought_qty": 1, "entry_price": 49.0, "closed": False}}
    out = call(side="SELL", qty=2, brackets=brackets, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "보유" in out["reason"]
    ok = call(side="SELL", qty=1, brackets=brackets, log_path=str(tmp_path / "log.jsonl"))
    assert ok["ok"] is True and ok["order_type"] == "61"


def test_budget_cap_and_cash_reserve_are_enforced(tmp_path):
    out = call(notional=200.0, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "예산 상한" in out["reason"]
    low_cash = call(qty=1, cash_usd=140.0, log_path=str(tmp_path / "log.jsonl"))
    assert low_cash["ok"] is False


def test_gap_chase_band_blocks_premarket_chasing(tmp_path):
    chase = {"SOXL": {"intraday_price": 51.0, "prev_close": 49.5,
                      "intraday_bar_time": f"{SESSION}T18:37:00"}}
    out = call(quotes=chase, qty=1, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "추격" in out["reason"]


def test_premarket_session_order_cap_is_enforced(tmp_path):
    log = tmp_path / "log.jsonl"
    for _ in range(2):
        iv._append_log({"session_date": SESSION, "side": "BUY", "status": "SUBMITTED"}, str(log))
    out = call(qty=1, log_path=str(log))
    assert out["ok"] is False and "한도" in out["reason"]


def test_qty_and_notional_together_are_rejected(tmp_path):
    out = call(qty=1, notional=40.0, log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "하나만" in out["reason"]


# ------------------------------------------------------------------ 집행·기록
class FakeRouter:
    def __init__(self, status="SUBMITTED"):
        self.status = status
        self.calls = []

    def place(self, ticker, side, qty, price, reason, session_date="", dry_run=None,
              extra=None, order_type="00"):
        self.calls.append({"ticker": ticker, "side": side, "qty": qty, "price": price,
                           "order_type": order_type, "dry_run": dry_run, "extra": extra})
        return {"status": self.status if not dry_run else "DRY_RUN",
                "orr_no": 4321, "run_id": 99}


def test_dry_run_does_not_place_order_and_logs(tmp_path):
    log = tmp_path / "log.jsonl"
    rec = iv.run_intervention(execute=False, clock=CLOCK, gate=GATE, plan=plan(), quotes=QUOTES,
                              brackets={}, cash_usd=426.0, equity_usd=891.0, log_path=str(log),
                              ticker="SOXL", side="BUY", qty=1)
    assert rec["status"] == "DRY_RUN"
    saved = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert saved["plan"]["order_type"] == "61" and saved["status"] == "DRY_RUN"


def test_execute_dispatches_with_order_type_and_records(tmp_path):
    log = tmp_path / "log.jsonl"
    router = FakeRouter()
    rec = iv.run_intervention(execute=True, router=router, clock=CLOCK, gate=GATE, plan=plan(),
                              quotes=QUOTES, brackets={}, cash_usd=426.0, equity_usd=891.0,
                              log_path=str(log), ticker="SOXL", side="BUY", qty=1,
                              reason="M8 판단 반영")
    assert router.calls and router.calls[0]["order_type"] == "61"
    assert rec["status"] == "SUBMITTED" and rec["order_no"] == 4321
    saved = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert saved["ticker"] == "SOXL" and saved["qty"] == 1


def test_blocked_plan_never_reaches_router(tmp_path):
    router = FakeRouter()
    closed = dict(GATE, enabled=False)
    rec = iv.run_intervention(execute=True, router=router, clock=CLOCK, gate=closed, plan=plan(),
                              quotes=QUOTES, brackets={}, cash_usd=426.0, equity_usd=891.0,
                              log_path=str(tmp_path / "log.jsonl"), ticker="SOXL", side="BUY", qty=1)
    assert rec["status"] == "BLOCKED" and router.calls == []


# ------------------------------------------------------------------ VIX 리스크오프
def test_vix_riskoff_blocks_new_long_but_allows_hedge_and_sell(tmp_path):
    """VIX 28 이상은 신규 롱 하드 차단 — 수동 개입도 무인 엔진과 같은 규칙(가드레일)."""
    log = str(tmp_path / "log.jsonl")
    blocked = call(qty=1, vix=30.0, log_path=log)
    assert blocked["ok"] is False and "리스크오프" in blocked["reason"]

    hedge = call(ticker="SQQQ", qty=1, vix=30.0, log_path=log,
                 quotes={"SQQQ": {"intraday_price": 60.0, "prev_close": 60.0,
                                  "intraday_bar_time": f"{SESSION}T18:37:00"}})
    assert hedge["ok"] is True and hedge["ticker"] == "SQQQ"

    sell = call(side="SELL", qty=1, vix=30.0, log_path=log,
                brackets={"SOXL": {"bought_qty": 1, "entry_price": 55.0, "closed": False}})
    assert sell["ok"] is True


def test_vix_gate_uses_conservative_integrity_value(tmp_path):
    out = call(qty=1, vix=18.0, integrity={"vix": {"gate": 29.5}},
               log_path=str(tmp_path / "log.jsonl"))
    assert out["ok"] is False and "리스크오프" in out["reason"]
