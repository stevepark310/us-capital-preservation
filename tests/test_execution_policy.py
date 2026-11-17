"""가드레일·예산·세션 시각 계산 검증 (주문 없음, 순수 함수)."""
import json
import os

import pytest

from core import execution_policy as ep
from core import session_time


def test_safe_caps_override_policy_file(tmp_path):
    """정책 파일이 절대 상한을 넘어도 코드 상한이 이긴다."""
    cfg = {"autonomous_execution": {"enabled": True,
                                    "guardrails": {"entry_budget_cap_usd": 9000,
                                                   "risk_per_trade_pct": 50,
                                                   "max_entries_per_session": 99}}}
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    policy = ep.load_policy(str(p))
    g = ep.guardrails(policy)
    assert g["entry_budget_cap_usd"] == ep.SAFE_CAPS["entry_budget_cap_usd"]
    assert g["risk_per_trade_pct"] == ep.SAFE_CAPS["risk_per_trade_pct"]
    assert g["max_entries_per_session"] == ep.SAFE_CAPS["max_entries_per_session"]
    assert policy["enabled"] is True


def test_missing_policy_block_is_disabled(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"universe": ["SOXL"]}), encoding="utf-8")
    policy = ep.load_policy(str(p))
    assert policy["enabled"] is False
    assert policy["ask_before_trade"] is True


def test_entry_budget_binds_to_cash_pct():
    policy = ep.load_policy()
    info = ep.entry_budget(891.63, 426.63, policy)
    # 20% of 426.63 = 85.33 이 가장 작은 제약
    assert info["binding"] == "cash_pct"
    assert info["budget_usd"] == pytest.approx(85.33, abs=0.01)
    assert info["components"]["abs_cap"] == 120.0


def test_entry_budget_respects_cash_reserve():
    policy = ep.load_policy()
    info = ep.entry_budget(900, 160, policy)   # 현금 160 - 보존 150 = 10
    assert info["budget_usd"] == pytest.approx(10.0, abs=0.01)
    assert info["binding"] == "cash_after_reserve"
    assert info["below_floor"] is True


def test_entry_budget_vol_scale_cuts_notional():
    policy = ep.load_policy()
    base = ep.entry_budget(891.63, 426.63, policy, 1.0)
    half = ep.entry_budget(891.63, 426.63, policy, 0.5)
    assert half["budget_usd"] == pytest.approx(base["budget_usd"] * 0.5, abs=0.01)


def test_loss_limit_uses_stricter_of_pct_and_usd():
    policy = ep.load_policy()
    # 자산 891.63의 3% = 26.75 > $25 → $25
    assert ep.loss_limit_usd(891.63, policy) == pytest.approx(25.0)
    # 자산 400의 3% = 12 < $25 → $12
    assert ep.loss_limit_usd(400, policy) == pytest.approx(12.0)


def test_vix_scale_thresholds():
    policy = ep.load_policy()
    assert ep.vix_scale(18, policy) == 1.0
    assert ep.vix_scale(24.0, policy) == 0.5
    assert ep.vix_scale(29.0, policy) == 0.0


def test_universe_and_banned_tickers():
    policy = ep.load_policy()
    assert ep.allowed_ticker("SOXL", policy)
    assert ep.allowed_ticker("SOXS", policy)
    assert not ep.allowed_ticker("TSLA", policy)
    assert not ep.allowed_ticker("SOUN", policy)


def test_locked_nvda_levels_are_fixed():
    assert ep.locked_levels("NVDA") == {"tp": 173.0, "sl": 150.0}


def test_session_window_crosses_midnight():
    c = {"now_kst": "2026-11-17 23:45:00"}
    assert session_time.in_window(c, "23:30-00:00") is True
    c2 = {"now_kst": "2026-11-17 23:29:00"}
    assert session_time.in_window(c2, "23:30-00:00") is False
    c3 = {"now_kst": "2026-11-18 00:01:00"}
    assert session_time.in_window(c3, "23:30-00:00") is False


def test_time_reached_after_midnight():
    c = {"now_kst": "2026-11-18 00:10:00"}
    assert session_time.time_reached(c, "23:45") is True
    assert session_time.time_reached(c, "04:30") is False
    assert session_time.time_reached(c, "05:40") is False
    c2 = {"now_kst": "2026-11-18 05:41:00"}
    assert session_time.time_reached(c2, "05:40") is True
