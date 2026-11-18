"""13F 스마트머니 계층 검증 — 원문 파싱 데이터 → Agent 5 블록 → 정책 반영.

네트워크를 타지 않는다. 합성 JSON으로만 계산하고, 백테스트 오버레이 훅이
'아무것도 사지 않으면 0건'이 되는지도 확인한다.
"""
import json

import pandas as pd
import pytest

from agents.agent5_superinvestor import build_superinvestor_block, load_13f
from core import execution_policy as ep
from core import swing_engine


def _si(**over):
    base = {
        "quarter": "2026Q3", "as_of_kst": "2026-11-17 14:00:00", "age_days": 0.1, "stale": False,
        "investors": [
            {"key": "BERKSHIRE", "name": "버크셔 해서웨이", "pm": "워런 버핏", "data_available": True,
             "positions_q2": 29, "positions_q3": 30, "value_q3_usd": 304e9, "net_flow_pct_of_value": -0.23,
             "counts": {"new": 1, "exits": 0, "increased": 4, "reduced": 6, "unchanged": 19, "converted": 0},
             "stance": "HOLD", "top_holdings": [{"ticker": "AAPL"}, {"ticker": "GOOGL"}]},
            {"key": "PERSHING", "name": "퍼싱 스퀘어", "pm": "빌 애크먼", "data_available": False,
             "form": "13F-NT", "note_ko": "13F-NT"},
        ],
        "cashization": {"index": 85.4, "regime_ko": "현금 확보 우위", "net_flow_usd": -18.74e9,
                        "components": {"flow": 100.0, "breadth": 70.8}, "formula": "f",
                        "reading_ko": "원문 5인 순주식흐름 -18.74B"},
        "sector_consensus": [
            {"sector": "GOLD_MATERIALS", "sector_ko": "금·소재(방어)", "score": 100.0,
             "avg_flow_pct_per_manager": 0.496, "breadth": "2매수 / 0매도"},
            {"sector": "SEMI_AI_HW", "sector_ko": "반도체·AI 하드웨어", "score": 11.5,
             "avg_flow_pct_per_manager": 1.736, "breadth": "3매수 / 2매도"},
            {"sector": "AI_POWER_DC", "sector_ko": "AI 전력·데이터센터", "score": -60.1,
             "avg_flow_pct_per_manager": -1.04, "breadth": "1매수 / 1매도"},
        ],
        "ticker_flows": [
            {"ticker": "LEN", "sector": "VALUE_HOUSING_FIN", "net_flow_usd": 1074.75e6,
             "buyers": ["BERKSHIRE"], "sellers": []},
            {"ticker": "STX", "sector": "SEMI_AI_HW", "net_flow_usd": 202.7e6,
             "buyers": ["DUQUESNE"], "sellers": []},
        ],
        "nvidia_governance": {"note_ko": "젠슨 황 복귀 확률 ~10%", "jensen_return_prob_pct": 10},
    }
    base.update(over)
    return base


def test_block_available_and_reads_original_filings():
    b = build_superinvestor_block(_si(), [{"ticker": "SOXX", "signal": True, "pct_b": 0.12}])
    assert b["available"] is True
    assert b["covered"] == 1 and b["requested"] == 2
    assert b["excluded"] == ["퍼싱 스퀘어"]
    assert b["cashization"]["index"] == 85.4
    assert b["cashization"]["applied_to_sizing"] is False  # 보고 전용
    assert b["sector_consensus"][0]["sector_ko"] == "금·소재(방어)"
    assert b["ticker_consensus"][0]["ticker"] == "LEN"
    assert b["candidate_signals"][0]["ticker"] == "SOXX"
    assert "현금화 압력 85.4/100" in b["reading_ko"]


def test_block_without_dataset_is_unavailable():
    b = build_superinvestor_block({})
    assert b["available"] is False
    assert "13F" in b["note_ko"]


def test_load_13f_missing_file_returns_empty(monkeypatch, tmp_path):
    import agents.agent5_superinvestor as a5
    monkeypatch.setattr(a5, "THIRTEENF_PATH", str(tmp_path / "none.json"))
    assert load_13f() == {}


def test_load_13f_marks_stale(monkeypatch, tmp_path):
    import agents.agent5_superinvestor as a5
    p = tmp_path / "si.json"
    p.write_text(json.dumps({"quarter": "2025Q1", "as_of_kst": "2025-02-01 09:00:00"}), encoding="utf-8")
    monkeypatch.setattr(a5, "THIRTEENF_PATH", str(p))
    d = load_13f(max_age_days=90)
    assert d["stale"] is True and d["age_days"] > 90


def test_vix_tier_20_added_but_caps_respected():
    policy = ep.load_policy()
    assert ep.vix_scale(19.9, policy) == 1.0
    assert ep.vix_scale(22.9, policy) == pytest.approx(0.75)
    assert ep.vix_scale(24.0, policy) == 0.5
    assert ep.vix_scale(28.0, policy) == 0.0
    # 예산이 실제로 줄어드는지(구간 계수 곱)
    full = ep.entry_budget(900, 600, policy, 1.0)["budget_usd"]
    tiered = ep.entry_budget(900, 600, policy, ep.vix_scale(22.9, policy))["budget_usd"]
    assert tiered == pytest.approx(full * 0.75, abs=0.02)


def test_policy_records_13f_ab_decisions():
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(base, "config", "swing_approval.json"), encoding="utf-8"))
    review = cfg["smart_money_13f_review"]
    assert any("VIX" in a["item"] for a in review["adopted"])
    assert any("유니버스 확장" in r["item"] for r in review["rejected"])


def _daily(n=160, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    px = 50 * (1 + pd.Series(rng.normal(0, 0.01, n), index=idx).cumsum())
    df = pd.DataFrame({"Open": px * 0.999, "High": px * 1.01, "Low": px * 0.99,
                       "Close": px, "Volume": rng.integers(5e6, 2e7, n).astype(float)}, index=idx)
    return df


def test_backtest_overlay_hook_zero_scale_trades_nothing():
    daily = {"SOXL": _daily(seed=1), "TQQQ": _daily(seed=2)}
    params = dict(swing_engine.DIP_PARAMS)
    base = swing_engine.portfolio_backtest(daily, params)
    none = swing_engine.portfolio_backtest(daily, params, size_scale_fn=lambda d, s, g: 0.0)
    assert none["total_trades"] == 0
    assert base["total_trades"] >= none["total_trades"]


def test_backtest_overlay_identity_matches_baseline():
    daily = {"SOXL": _daily(seed=3), "TQQQ": _daily(seed=4)}
    params = dict(swing_engine.DIP_PARAMS)
    base = swing_engine.portfolio_backtest(daily, params)
    same = swing_engine.portfolio_backtest(daily, params, size_scale_fn=lambda d, s, g: 1.0)
    assert same["total_trades"] == base["total_trades"]
    assert same["total_return_pct"] == pytest.approx(base["total_return_pct"], abs=1e-6)
