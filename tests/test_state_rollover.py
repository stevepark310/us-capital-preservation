"""세션 전환 시 상태 이월 — 마감 직전 주문이 사라지지 않는지 확인."""
import json

import pytest

from core import autotrader, execution_policy
from core.autotrader import AutonomousTrader

from conftest import FakeRouter, clock_at


def test_pending_orders_survive_session_rollover(tmp_trader):
    tmp_path, _ = tmp_trader
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=FakeRouter(), dry_run=False,
                         market={"clock": clock_at("01:00"), "quotes": {}, "techs": {}, "daily": {},
                                 "snapshot": {}, "vix": 20.0}, base_dir=str(tmp_path))
    st = t.load_state("2026-11-17")
    st["pending"] = [{"ticker": "SOXL", "side": "SELL", "qty": 1, "limit_price": 50.0,
                      "ts_kst": autotrader._now_str()}]
    st["entries"] = [{"ticker": "SOXL"}]
    st["halted"] = True
    t.save_state(st)

    nxt = t.load_state("2026-11-20")          # 새 세션
    assert len(nxt["pending"]) == 1           # 미확인 주문은 이월
    assert nxt["entries"] == []               # 일일 카운터는 리셋
    assert nxt["halted"] is False             # 서킷브레이커도 일일 단위


def test_old_pending_orders_dropped(tmp_trader):
    tmp_path, _ = tmp_trader
    t = AutonomousTrader(policy=execution_policy.load_policy(), router=FakeRouter(), dry_run=False,
                         market={"clock": clock_at("01:00"), "quotes": {}, "techs": {}, "daily": {},
                                 "snapshot": {}, "vix": 20.0}, base_dir=str(tmp_path))
    st = t.load_state("2026-11-17")
    st["pending"] = [{"ticker": "SOXL", "side": "SELL", "qty": 1, "ts_kst": "2026-11-01 01:00:00"}]
    t.save_state(st)
    assert t.load_state("2026-11-18")["pending"] == []
