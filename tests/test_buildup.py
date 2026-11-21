"""빌드업 게이트·하드락 — 측정 없이는 바뀌지 않고, 가드레일은 어떤 경로로도 풀리지 않는다."""
import json

import pytest

from core import buildup, desk_lab, strategy_evolver


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(buildup, "BUILDUP_PATH", str(tmp_path / "strategy_buildup.json"))
    monkeypatch.setattr(strategy_evolver, "PARAMS_PATH", str(tmp_path / "strategy_params.json"))
    monkeypatch.setattr(strategy_evolver, "LOG_PATH", str(tmp_path / "strategy_evolution_log.json"))
    monkeypatch.setattr(strategy_evolver, "LEDGER_PATH", str(tmp_path / "strategy_trade_ledger.json"))
    return tmp_path


def measurement(delta=0.5, oos_n=12, pf=1.8, mdd_worse=0.0, is_delta=-0.1):
    oos = {"n": oos_n, "available": True, "ev_pct": 1.0 + delta, "profit_factor": pf, "mdd_pct": -10.0}
    base_oos = {"n": oos_n, "available": True, "ev_pct": 1.0, "profit_factor": 1.5, "mdd_pct": -10.0}
    iss = {"n": 20, "available": True, "ev_pct": 1.0 + is_delta, "profit_factor": 1.5}
    base_is = {"n": 20, "available": True, "ev_pct": 1.0, "profit_factor": 1.5}
    return {"baseline": {"all": base_is, "is": base_is, "oos": base_oos},
            "candidate": {"all": iss, "is": iss, "oos": oos},
            "delta_oos_ev_pct": delta, "mdd_worsening_pp": mdd_worse,
            "data_as_of": "2026-11-13", "data_fingerprint": "fp"}


# ------------------------------------------------------------------ hard lock
def test_hard_lock_passes_on_approved_policy():
    rep = buildup.hard_lock_report()
    assert rep["ok"] is True, rep["violations"]


@pytest.mark.parametrize("mutate", [
    lambda s: s["entry_filters"].update({"rvol_required": False}),
    lambda s: s["entry_filters"].update({"pct_b_max": 0.30}),
    lambda s: s["entry_filters"].update({"rsi2_max": 30.0}),
    lambda s: s["entry_filters"].update({"rvol_min": 0.5}),
    lambda s: s.update({"sl_max_pct": 25.0}),
    lambda s: s.update({"max_hold_sessions": 5}),
    lambda s: s["guardrails"].update({"cash_reserve_floor_usd": 50.0}),
    lambda s: s["guardrails"].update({"entry_budget_cap_usd": 400.0}),
    lambda s: s["guardrails"].update({"daily_realized_loss_limit_usd": 60.0}),
    lambda s: s["guardrails"].update({"no_averaging_down": False}),
    lambda s: s.update({"nvda_levels_locked": {"tp": 190.0, "sl": 150.0}}),
    lambda s: s.update({"premarket_switch": True, "premarket_enabled": False}),
    lambda s: s.update({"universe": ["SOXL", "TQQQ", "SOXS", "SQQQ", "ENVX"]}),
    lambda s: s.update({"vix_riskoff_28": 0.5}),
])
def test_hard_lock_catches_relaxation(mutate):
    snap = buildup.policy_snapshot()
    mutate(snap)
    rep = buildup.hard_lock_report(snap)
    assert rep["ok"] is False and rep["violations"]


def test_premarket_lock_allows_only_approved_switch():
    """프리장은 '무조건 금지'가 아니라 '승인 없는 활성화 금지'다(2026-11-21 재설계).

    동훈님의 프리장 개입 지시를 코드로 받되, 코드가 스스로 프리장을 여는 길은 막는다.
    """
    opened = buildup.policy_snapshot()
    opened.update({"premarket_switch": True, "premarket_enabled": True,
                   "premarket_approval": {"approved": True}})
    assert buildup.hard_lock_report(opened)["ok"] is True

    unapproved = buildup.policy_snapshot()
    unapproved.update({"premarket_switch": True, "premarket_enabled": False,
                       "premarket_approval": {"approved": False, "reason": "승인 기록 없음"}})
    rep = buildup.hard_lock_report(unapproved)
    assert rep["ok"] is False
    assert any(v["key"] == "premarket_enabled" for v in rep["violations"])


# ------------------------------------------------------------------ gate
def test_gate_passes_only_with_all_criteria():
    g = buildup.gate(measurement())
    assert g["pass"] is True
    assert all(c["ok"] for c in g["checks"])


@pytest.mark.parametrize("kw,failed", [
    ({"delta": 0.05}, "OOS 기대값 개선"),
    ({"oos_n": 5}, "OOS 표본"),
    ({"pf": 1.05}, "OOS PF"),
    ({"mdd_worse": 5.0}, "OOS MDD 악화"),
    ({"is_delta": -3.0}, "IS 후퇴 폭"),
])
def test_gate_rejects_each_missing_criterion(kw, failed):
    g = buildup.gate(measurement(**kw))
    assert g["pass"] is False
    assert any(c["check"] == failed and not c["ok"] for c in g["checks"])


# ------------------------------------------------------------------ apply
def _hypothesis(delta=0.5, key="entry_filters.pct_b_max", frm=0.15, to=0.10, auto=True):
    h = {"id": "H-0001", "title": "t", "auto_apply": auto, "decisions": [],
         "changes": {key: {"from": frm, "to": to}}, "measurement": measurement(delta)}
    h["gate"] = buildup.gate(h["measurement"])
    h["status"] = "MEASURED"
    return h


def test_apply_writes_runtime_params_and_records_improvement(sandbox):
    doc = buildup.load()
    h = _hypothesis()
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is True
    params = json.loads((sandbox / "strategy_params.json").read_text(encoding="utf-8"))
    assert params["entry_filters"]["pct_b_max"] == 0.10
    assert doc["ratchet"]["pct_b_max"] == 0.10
    assert doc["improvements"][0]["hypothesis"] == "H-0001"
    assert h["status"] == "APPLIED"


def test_apply_refuses_bracket_spec_changes(sandbox):
    doc = buildup.load()
    h = _hypothesis(key="bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult", frm=1.5, to=1.8, auto=False)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is False
    assert any("자동 적용" in r for r in res["reasons"])
    assert not (sandbox / "strategy_params.json").exists() or "entry_filters" not in json.loads(
        (sandbox / "strategy_params.json").read_text(encoding="utf-8"))


def test_apply_refuses_when_gate_fails(sandbox):
    doc = buildup.load()
    h = _hypothesis(delta=0.01)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is False and h["status"] == "REJECTED"


def test_ratchet_prevents_loosening(sandbox):
    doc = buildup.load()
    doc["ratchet"] = {"rsi2_max": 20.0, "pct_b_max": 0.10, "rvol_min": 1.2}
    h = _hypothesis(key="entry_filters.pct_b_max", frm=0.10, to=0.15)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is False
    assert any("래칫" in r for r in res["reasons"])


def test_recommend_leaves_approval_with_the_user(sandbox):
    doc = buildup.load()
    h = _hypothesis(key="bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult", frm=1.5, to=1.8, auto=False)
    out = buildup.recommend(doc, h, by=8)
    assert out["status"] == "RECOMMENDED"
    assert "사용자 승인" in h["decisions"][-1]["reasons"][0]


def test_candidate_grid_only_tightens_entry_filters():
    grid = buildup.candidate_grid({"entry_filters": {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0}})
    auto = [g for g in grid if g["auto_apply"]]
    assert auto, "자동 적용 후보가 있어야 한다"
    for g in auto:
        for key, ch in g["changes"].items():
            assert key in buildup.AUTO_APPLY
            if buildup.AUTO_APPLY[key]["direction"] == "down":
                assert ch["to"] < ch["from"]
            else:
                assert ch["to"] > ch["from"]
    # 브래킷 규격 후보는 절대 자동 적용이 아니다
    assert all(not g["auto_apply"] for g in grid if g["axis"] == "BRACKET_SPEC")
