"""신중 모드(11-18 06:20까지) 강제청산 가드 — 신규 v2 포지션도 실적 전에 정리한다."""
import json

import pytest

from core import autotrader, execution_policy
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at, ledger


def v2_market(hhmm, price):
    return {"clock": clock_at(hhmm, session_date="2026-11-17"),
            "quotes": {"SOXL": {"intraday_price": price}}, "techs": {}, "daily": {},
            "snapshot": {}, "vix": 20.0}


def trader_with(tmp_path, hhmm, price, policy=None):
    return AutonomousTrader(policy=policy or execution_policy.load_policy(),
                            router=FakeRouter(), dry_run=False,
                            market=v2_market(hhmm, price), base_dir=str(tmp_path))


def put_v2(brackets_path, extra=None):
    data = json.loads(brackets_path.read_text(encoding="utf-8"))
    bracket = {"bought_qty": 1, "entry_price": 50.0, "exit_mode": "chandelier_v2",
               "sl": 46.0, "trail_atr_mult": 0.9, "atr_pct_at_plan": 10.0, "be_after_atr": 1.0,
               "breakeven_pct": 0.34, "max_hold_sessions": 3, "sl_active_after_kst": "23:45",
               "highest_high": 50.0, "closed": False, "sl_triggered": False,
               "sessions_held": 1, "last_session_date": "2026-11-17"}
    bracket.update(extra or {})
    data["SOXL"] = bracket
    brackets_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_cautious_force_exit_closes_v2_before_earnings(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    # 신중 모드는 2026-11-18 06:20 KST에 만료됐다 — 시험은 만료 시각을 미래로 고정해
    # 가드 로직 자체를 검증한다(벽시계에 의존하면 하루 뒤부터 항상 실패한다).
    policy = execution_policy.load_policy()
    policy["cautious_mode"] = dict(policy.get("cautious_mode") or {})
    policy["cautious_mode"]["until_kst"] = "2099-12-31 06:20"
    t = trader_with(tmp_path, "05:41", 50.2, policy=policy)
    st = t.load_state("2026-11-17")
    data = put_v2(brackets_path)
    actions = t.exits(st, data, {"SOXL": {"intraday_price": 50.2}}, t.market()["clock"])
    assert len(actions) == 1
    assert "강제청산" in actions[0]["reason"]
    assert actions[0]["qty"] == 1


def test_cautious_force_exit_off_after_expiry(tmp_trader):
    """만료 이후에는 05:40을 넘겨도 강제청산하지 않는다(청산은 브래킷 규칙만)."""
    tmp_path, brackets_path = tmp_trader
    policy = execution_policy.load_policy()
    policy["cautious_mode"] = dict(policy.get("cautious_mode") or {})
    policy["cautious_mode"]["until_kst"] = "2020-01-01 00:00"
    t = trader_with(tmp_path, "05:41", 50.2, policy=policy)
    st = t.load_state("2026-11-17")
    data = put_v2(brackets_path)
    assert t.exits(st, data, {"SOXL": {"intraday_price": 50.2}}, t.market()["clock"]) == []


def test_no_force_exit_before_0540(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    t = trader_with(tmp_path, "05:30", 50.2)
    st = t.load_state("2026-11-17")
    data = put_v2(brackets_path)
    assert t.exits(st, data, {"SOXL": {"intraday_price": 50.2}}, t.market()["clock"]) == []


def test_locked_nvda_not_force_exited(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    t = trader_with(tmp_path, "05:50", 160.0)
    st = t.load_state("2026-11-17")
    data = put_v2(brackets_path, {"exit_mode": "ladder", "levels_locked": True, "sl": 150.0,
                                  "tp1": 173.0, "tp1_qty": 1, "tp1_filled": False})
    data["SOXL"] = {**data["SOXL"], "ticker": "NVDA"}
    t2 = AutonomousTrader(policy=t.policy, router=t.router, dry_run=False,
                          market={"clock": clock_at("05:50"), "quotes": {"NVDA": {"intraday_price": 160.0}},
                                  "techs": {}, "daily": {}, "snapshot": {}, "vix": 20.0},
                          base_dir=str(tmp_path))
    assert t2.exits(st, data, {"NVDA": {"intraday_price": 160.0}}, t2.market()["clock"]) == []


def test_force_exit_inactive_after_cautious_window(tmp_trader):
    tmp_path, brackets_path = tmp_trader
    policy = execution_policy.load_policy()
    policy["cautious_mode"] = {"until_kst": "2026-11-17 00:00"}   # 이미 종료된 모드
    t = AutonomousTrader(policy=policy, router=FakeRouter(), dry_run=False,
                         market=v2_market("05:41", 50.2), base_dir=str(tmp_path))
    st = t.load_state("2026-11-17")
    data = put_v2(brackets_path)
    assert t.exits(st, data, {"SOXL": {"intraday_price": 50.2}}, t.market()["clock"]) == []
