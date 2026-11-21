"""세션 라우팅 — 프리장 승인 게이트·호가유형·얇은 호가 방어(2026-11-21)."""
import json
from datetime import datetime

import pytest

from core import session_routing as sr


APPROVED = {
    "enabled": True,
    "version": 2,
    "approved_by": "박동훈 (사용자)",
    "approved_at_kst": "2026-11-22 09:00:00",
    "entry_conditions": {"max_budget_usd": 60.0, "max_orders_per_session": 2,
                         "max_gap_chase_pct": 1.5, "allow_sources": ["DIP_SWING_DAILY"]},
}
CLOSED = {"enabled": False, "reason": "백테스트 미승인"}
SWITCH_ONLY = {"enabled": True, "reason": "스위치만 켬"}


def pre_clock(et="04:30", kst="2026-11-23 18:30:00"):
    return {"phase": "PRE_MARKET", "session_date": "2026-11-23", "now_kst": kst,
            "now_et": f"2026-11-23 {et}:00", "kst_shift_min": 0}


# ------------------------------------------------------------------ 승인 게이트
def test_switch_alone_does_not_open_premarket():
    """enabled=true 만으로는 열리지 않는다 — 사용자 승인 기록이 필요하다."""
    st = sr.status(SWITCH_ONLY)
    assert st["premarket_switch"] is True
    assert st["premarket_enabled"] is False
    assert "PRE_MARKET" not in st["phases"]
    assert "승인 기록" in (st["blocked_reason"] or "")


def test_approved_policy_opens_premarket_and_after_hours_only_when_enabled():
    st = sr.status(APPROVED)
    assert st["premarket_enabled"] is True
    assert st["phases"] == ["REGULAR", "PRE_MARKET"]
    assert st["order_types"] == {"REGULAR": "00", "PRE_MARKET": "61"}
    with_after = dict(APPROVED, after_hours={"enabled": True})
    assert "POST_MARKET" in sr.status(with_after)["phases"]


def test_closed_policy_keeps_regular_only():
    st = sr.status(CLOSED)
    assert st["phases"] == ["REGULAR"] and st["order_types"] == {"REGULAR": "00"}


# ------------------------------------------------------------------ 호가유형
def test_order_type_mapping_is_phase_specific():
    plan = sr.status(APPROVED)
    assert sr.order_type_for_phase("REGULAR", plan) == "00"
    assert sr.order_type_for_phase("PRE_MARKET", plan) == "61"
    assert sr.order_type_for_phase("POST_MARKET", plan) is None      # 애프터마켓 미승인
    assert sr.order_type_for_phase("PRE_MARKET", sr.default_plan()) is None


def test_limit_price_caps_slippage_in_premarket_only():
    plan = sr.status(APPROVED)
    buy, meta = sr.limit_price(100.0, "BUY", "PRE_MARKET", plan, fallback_bps=20)
    assert buy == pytest.approx(100.15, abs=0.001) and meta["capped"] is True
    sell, _ = sr.limit_price(100.0, "SELL", "PRE_MARKET", plan, fallback_bps=-30)
    assert sell == pytest.approx(99.85, abs=0.001)
    regular, rmeta = sr.limit_price(100.0, "BUY", "REGULAR", plan, fallback_bps=20)
    assert regular == pytest.approx(100.20, abs=0.001) and rmeta["capped"] is False


def test_phase_minutes_uses_et_clock_in_premarket():
    assert sr.phase_minutes(pre_clock("04:00")) == 0
    assert sr.phase_minutes(pre_clock("06:30")) == 150
    assert sr.phase_minutes({"phase": "REGULAR", "now_kst": "2026-11-23 23:40:00",
                             "kst_shift_min": 0}) == 10


def test_budget_and_order_cap_are_tighter_in_premarket():
    plan = sr.status(APPROVED)
    assert sr.budget("PRE_MARKET", plan, 150.0)["budget_usd"] == 60.0
    assert sr.budget("REGULAR", plan, 150.0)["budget_usd"] == 150.0
    assert sr.session_order_cap("PRE_MARKET", plan, 8) == 2
    assert sr.session_order_cap("REGULAR", plan, 8) == 8


# ------------------------------------------------------------------ 진입 조건
def test_premarket_entry_gate_checks_window_source_regime_and_chase():
    plan = sr.status(APPROVED)
    assert sr.premarket_entry_gate(pre_clock(), plan, regime="NEUTRAL",
                                   source="DIP_SWING_DAILY", prev_close=50.0,
                                   price=50.2)["ok"] is True
    # 진입 창(04:00~09:00 ET) 밖
    late = sr.premarket_entry_gate(pre_clock("09:30"), plan, regime="NEUTRAL",
                                   source="DIP_SWING_DAILY")
    assert late["ok"] is False and "창" in late["reason"]
    # 허용 신호 아님
    assert sr.premarket_entry_gate(pre_clock(), plan, source="C5_OPEN30_DIRECTION")["ok"] is False
    # 위기 국면 / 리스크오프
    assert sr.premarket_entry_gate(pre_clock(), plan, regime="CRISIS",
                                   source="DIP_SWING_DAILY")["ok"] is False
    assert sr.premarket_entry_gate(pre_clock(), plan, regime="RISK_OFF",
                                   source="DIP_SWING_DAILY")["ok"] is False
    # 추격 금지 밴드
    chase = sr.premarket_entry_gate(pre_clock(), plan, regime="NEUTRAL",
                                    source="DIP_SWING_DAILY", prev_close=50.0, price=51.0)
    assert chase["ok"] is False and "추격" in chase["reason"]


def test_premarket_entry_gate_is_noop_in_regular_session():
    plan = sr.status(APPROVED)
    assert sr.premarket_entry_gate({"phase": "REGULAR"}, plan, source="C5_OPEN30_DIRECTION")["ok"] is True


def test_quote_freshness_rejects_stale_premarket_prints():
    clock = pre_clock("04:30", kst="2026-11-23 18:30:00")
    fresh = sr.quote_fresh({"intraday_bar_time": "2026-11-23T18:27:00"}, clock)
    assert fresh["ok"] is True
    stale = sr.quote_fresh({"intraday_bar_time": "2026-11-23T17:30:00"}, clock)
    assert stale["ok"] is False and "경과" in stale["reason"]


def test_repo_policy_file_stays_closed_until_approved():
    """저장소의 정책 파일은 승인 전이므로 프리장이 닫혀 있어야 한다(주문 금지)."""
    with open(sr.PREMARKET_POLICY_PATH, "r", encoding="utf-8") as f:
        pol = json.load(f)
    st = sr.status(pol)
    assert st["premarket_enabled"] is False
    assert st["phases"] == ["REGULAR"]
