"""협업 사이클 엔드투엔드 — 리서치 → 검증 → 총괄 → 집행 핸드오프가 실제로 이어지는가."""
import json

import numpy as np
import pandas as pd
import pytest

from core import buildup, collab_bus, desk_lab, strategy_evolver, universe_screener
from scripts import run_collab_cycle as rc


def frame(seed):
    rng = np.random.default_rng(seed)
    n = 160
    closes = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, n))
    idx = pd.bdate_range("2025-06-02", periods=n)
    return pd.DataFrame({"Open": closes * 1.001, "High": closes * 1.02, "Low": closes * 0.98,
                         "Close": closes, "Volume": rng.integers(800_000, 2_000_000, n).astype(float)},
                        index=idx)


CUBE_REPORT = {
    "agents": [
        {"agent_id": 1, "report": {"average_sentiment_score": 55.9, "overall_market_mood": "RISK_ON",
                                   "data_sources": {"items_last_24h": 248},
                                   "sector_sentiment": {"BONDS_FED": {"sentiment_score": 3.4}}}},
        {"agent_id": 2, "report": {"levels": {"SOXL": {"close": 49.26, "atr_pct": 23.84, "rsi2": 18.3,
                                                       "pct_b": 0.25, "rvol": 1.03}},
                                   "actionable_symbols": []}},
        {"agent_id": 3, "report": {"earnings_calendar": [{"ticker": "AVGO", "d_day": 21,
                                                          "report_kst": "2026-12-10 06:00"}],
                                   "macro_calendar": [1, 2, 3]}},
        {"agent_id": 4, "report": {"vix_level": 24.56, "macro_regime": "BEAR_VOLATILE",
                                   "leading_indicators": {"cube_lei_index": 101.0, "regime_ko": "후기 사이클 둔화",
                                                          "recession_probit": {"probability_used": 11.5}}}},
        {"agent_id": 5, "report": {"qqq_put_call_oi": 1.24, "institutional_bias": "PUT_HEAVY",
                                   "credit_flow": {"hy_oas_bp": 343.0, "credit_level": "NORMAL"}}},
        {"agent_id": 6, "report": {"leading_indicator_filter": {"regime": "LATE_CYCLE_SLOWDOWN",
                                                                "leverage_cap_factor": 0.7},
                                   "approved_now": ["SCENARIO_10"]}},
        {"agent_id": 8, "report": {"profitable_scenarios": [{}], "rejected_scenarios": [{}, {}],
                                   "sgov_vs_cash": {"annual_yield_pct": 4.19}}},
        {"agent_id": 9, "report": {"market_stance": "CAUTIOUS_ROTATION", "composite_score": -9.5}},
    ]
}


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", str(tmp_path))
    monkeypatch.setattr(rc, "CYCLE_PATH", str(tmp_path / "collab_cycle.json"))
    monkeypatch.setattr(buildup, "BUILDUP_PATH", str(tmp_path / "strategy_buildup.json"))
    monkeypatch.setattr(collab_bus, "BUS_PATH", str(tmp_path / "agent_collab_bus.json"))
    monkeypatch.setattr(strategy_evolver, "PARAMS_PATH", str(tmp_path / "strategy_params.json"))
    monkeypatch.setattr(strategy_evolver, "LOG_PATH", str(tmp_path / "strategy_evolution_log.json"))
    monkeypatch.setattr(strategy_evolver, "LEDGER_PATH", str(tmp_path / "strategy_trade_ledger.json"))
    # 빌드업이 실제 승인 정본·레짐 스위치를 건드리지 않게 샌드박스로 돌린다(2026-11-26 자동 반영 확장).
    monkeypatch.setattr(buildup, "POLICY_PATH", str(tmp_path / "swing_approval.json"))
    monkeypatch.setattr(buildup, "REGIME_POLICY_PATH", str(tmp_path / "regime_policy.json"))
    (tmp_path / "swing_approval.json").write_text(json.dumps({
        "entry_conditions": {"rvol_required": True, "rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0},
        "autonomous_execution": {"bracket_specs": {}}}), encoding="utf-8")
    (tmp_path / "regime_policy.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
    # 동적 유니버스 스캔은 네트워크를 쓰므로 테스트에서는 고정 응답으로 대체한다.
    monkeypatch.setattr(universe_screener, "refresh_if_stale",
                        lambda max_age_h=None, force=False: ({"generated_at_kst": "2026-11-26 10:00:00",
                                                             "as_of": "2025-11-14", "source": "test",
                                                             "counts": {"pool": 2, "scored": 2},
                                                             "selected_long": ["TQQQ"], "selected_hedge": [],
                                                             "selected": []}, False))
    monkeypatch.setattr(universe_screener, "summary",
                        lambda max_age_h=6.0: {"selected_long": ["TQQQ"], "selected_hedge": [],
                                               "selected": [], "stale": False})
    daily = {"SOXL": frame(1), "TQQQ": frame(2)}
    monkeypatch.setattr(desk_lab, "load_daily", lambda force=False, period="2y": daily)
    (tmp_path / "cube_report.json").write_text(json.dumps(CUBE_REPORT), encoding="utf-8")
    (tmp_path / "autotrader_status.json").write_text(json.dumps({
        "mode": "EXECUTE", "phase": "OFF_HOURS", "generated_at_kst": "2026-11-19 10:58:41",
        "positions": {"NVDA": {"qty": 1, "entry": 173.3, "sl": 150.0}},
        "pending_orders": [], "realized_pnl_today_usd": 0.0,
        "policy": {"entry_budget": {"budget_usd": 149.32, "binding": "cash_pct"},
                   "guardrails": {"cash_reserve_floor_usd": 150.0}},
        "next_gate": "다음 정규장 2026-11-19 23:30 KST"}), encoding="utf-8")
    (tmp_path / "live_portfolio.json").write_text(json.dumps({
        "api_status": "CONNECTED", "current_total_equity_usd": 878.6, "cash_buffer_usd": 426.63,
        "realized_pnl_usd": 1.85, "unrealized_pnl_usd": -22.09, "win_rate_pct": 55.6,
        "trades_summary": "9전 5승 4패", "positions": [{"ticker": "NVDA", "qty": 1, "avg_price": 173.3}]}),
        encoding="utf-8")
    (tmp_path / "avg_anchor_plan.json").write_text(json.dumps({"ladders": [{"ticker": "NVDA", "qty": 1}]}),
                                                   encoding="utf-8")
    (tmp_path / "active_brackets.json").write_text(json.dumps({
        "NVDA": {"entry_price": 173.3, "bought_qty": 1, "sl": 150.0, "closed": False}}), encoding="utf-8")
    return tmp_path


def test_cycle_creates_handoff_chain_and_measured_hypothesis(sandbox):
    out = rc.run_cycle()
    bus = json.loads((sandbox / "agent_collab_bus.json").read_text(encoding="utf-8"))
    kinds = [m["kind"] for m in bus["messages"]]
    for k in ("DUTY_REPORT", "INSIGHT", "HYPOTHESIS", "VALIDATION_REQUEST",
              "VALIDATION_RESULT", "EXECUTION_REVIEW"):
        assert k in kinds, k
    assert kinds.count("INSIGHT") == 5
    assert out["validation"]["hypothesis"].startswith("H-")
    assert out["validation"]["gate"] in (True, False)
    # 검증 결과에는 실제 측정값이 들어간다(문장만 있는 보고가 아니다)
    result = [m for m in bus["messages"] if m["kind"] == "VALIDATION_RESULT"][0]
    assert "OOS" in result["body"] and result["metrics"]["ΔEV_OOS"] is not None
    assert result["refs"]["measurement"]["data_as_of"]


def test_duty_reports_only_on_configured_every(sandbox):
    rc.run_cycle(every=3)
    rc.run_cycle(every=3)
    bus = json.loads((sandbox / "agent_collab_bus.json").read_text(encoding="utf-8"))
    duties = [m for m in bus["messages"] if m["kind"] == "DUTY_REPORT"]
    assert len(duties) == 10          # 2사이클에서는 업무보고를 반복하지 않는다
    assert len(bus["agent_state"]) == 10   # 현황은 매 사이클 갱신


def test_no_new_candidate_is_reported_once(sandbox):
    rc.run_cycle()
    rc.run_cycle()
    rc.run_cycle()
    bus = json.loads((sandbox / "agent_collab_bus.json").read_text(encoding="utf-8"))
    idle = [m for m in bus["messages"] if m["subject"].startswith("재측정 불필요")]
    assert len(idle) <= 1


def test_cycle_never_relaxes_hard_locked_guardrails(sandbox):
    for _ in range(4):
        rc.run_cycle()
    lock = buildup.hard_lock_report()
    assert lock["ok"] is True, lock["violations"]
    params = json.loads((sandbox / "strategy_params.json").read_text(encoding="utf-8")) \
        if (sandbox / "strategy_params.json").exists() else {}
    f = params.get("entry_filters") or {}
    assert f.get("rvol_required", True) is True
    assert f.get("pct_b_max", 0.15) <= 0.15 and f.get("rsi2_max", 20.0) <= 20.0


def test_cycle_refreshes_dynamic_universe_and_reports(sandbox, monkeypatch):
    """1시간 이상 묵으면 후보 풀을 다시 스캔하고, 선정 결과를 버스·출력에 남긴다(2026-11-26)."""
    call = {}

    def fake_refresh(max_age_h=None, force=False):
        call["max_age_h"] = max_age_h
        return ({"generated_at_kst": "2026-11-26 21:00:00", "as_of": "2026-11-25",
                 "source": "test", "counts": {"pool": 79, "scored": 73},
                 "selected": [{"ticker": "LRCX"}],
                 "selected_long": ["LRCX"], "selected_hedge": ["SPXS"], "allowed": []}, True)

    def fake_sync(doc=None):
        call["synced"] = doc
        return {"before": ["SOXL", "TQQQ", "SOXS", "SQQQ"],
                "universe": ["SOXL", "TQQQ", "SOXS", "SQQQ", "LRCX", "SPXS"], "changed": True,
                "added": ["LRCX", "SPXS"], "removed": []}

    monkeypatch.setattr(universe_screener, "refresh_if_stale", fake_refresh)
    monkeypatch.setattr(universe_screener, "sync_policy_universe", fake_sync)
    out = rc.run_cycle()
    assert call["max_age_h"] == 1.0 and call.get("synced")
    assert out["dynamic_universe"]["refreshed"] is True
    assert out["dynamic_universe"]["selected_long"] == ["LRCX"]
    bus = json.loads((sandbox / "agent_collab_bus.json").read_text(encoding="utf-8"))
    uni = [m for m in bus["messages"] if m["subject"].startswith("동적 유니버스")]
    assert uni and "LRCX" in uni[-1]["body"] and "SPXS" in uni[-1]["body"]
