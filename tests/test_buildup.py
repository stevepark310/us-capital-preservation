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
    # 2026-11-26 자동 반영 확장 — 브래킷 규격·모델 스위치도 쓰이므로 실제 정본을 건드리지 않게 샌드박스.
    monkeypatch.setattr(buildup, "POLICY_PATH", str(tmp_path / "swing_approval.json"))
    monkeypatch.setattr(buildup, "REGIME_POLICY_PATH", str(tmp_path / "regime_policy.json"))
    (tmp_path / "swing_approval.json").write_text(json.dumps({
        "entry_conditions": {"rvol_required": True, "rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0},
        "autonomous_execution": {"bracket_specs": {"DIP_SWING_DAILY": {
            "initial_sl_atr_mult": 1.5, "tp_atr_mult": 1.5, "max_hold_sessions": 3}}}},
        ensure_ascii=False), encoding="utf-8")
    (tmp_path / "regime_policy.json").write_text(json.dumps({
        "enabled": False, "approved_by": None, "approved_at_kst": None}), encoding="utf-8")
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
    lambda s: s.update({"universe": ["SOXL", "TQQQ"]}),                      # 코어 누락
    lambda s: s.update({"universe": ["SOXL", "TQQQ", "SOXS", "SQQQ", "ZZZZ"]}),  # 미선정 종목
    lambda s: s.update({"vix_riskoff_28": 0.5}),
])
def test_hard_lock_catches_relaxation(mutate):
    snap = buildup.policy_snapshot()
    mutate(snap)
    rep = buildup.hard_lock_report(snap)
    assert rep["ok"] is False and rep["violations"]


def test_universe_allows_core_plus_screened_selection():
    """유니버스는 '코어 필수 + 스크리너 선정만 추가' — 고정 4종목 락은 풀렸다(2026-11-26)."""
    snap = buildup.policy_snapshot()
    base = list(snap["universe"])
    assert set(buildup.CORE_UNIVERSE).issubset(set(base))
    allowed = buildup.hard_lock_report(snap)
    assert allowed["ok"] is True, allowed["violations"]

    grown = buildup.policy_snapshot()
    grown["universe_allowed"] = list(grown["universe_allowed"]) + ["PLTR"]
    grown["universe"] = base + ["PLTR"]
    assert buildup.hard_lock_report(grown)["ok"] is True

    dropped = buildup.policy_snapshot()
    dropped["universe"] = ["SOXL", "TQQQ", "SOXS"]
    rep = buildup.hard_lock_report(dropped)
    assert rep["ok"] is False and any(v["key"] == "universe.core" for v in rep["violations"])


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


def test_apply_writes_bracket_spec_to_approval_config(sandbox):
    """게이트를 통과한 브래킷 규격은 승인 정본에 자동 반영된다(2026-11-26 지시)."""
    doc = buildup.load()
    h = _hypothesis(key="bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult", frm=1.5, to=1.8)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is True, res
    cfg = json.loads((sandbox / "swing_approval.json").read_text(encoding="utf-8"))
    assert cfg["autonomous_execution"]["bracket_specs"]["DIP_SWING_DAILY"]["initial_sl_atr_mult"] == 1.8
    assert doc["improvements"][-1]["targets"]["swing_approval"]["before"][
        "bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult"] == 1.5


def test_apply_activates_whitelisted_model(sandbox):
    doc = buildup.load()
    h = _hypothesis(key="models.REGIME_ADAPTIVE_DIP_V1.enabled", frm=False, to=True)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is True, res
    pol = json.loads((sandbox / "regime_policy.json").read_text(encoding="utf-8"))
    assert pol["enabled"] is True and pol["approved_by"] and pol["approved_at_kst"]


def test_apply_rejects_model_outside_whitelist(sandbox):
    doc = buildup.load()
    h = _hypothesis(key="models.SOME_OTHER_MODEL.enabled", frm=False, to=True)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is False
    assert any(("화이트리스트" in r) or ("자동 적용 키가 아닙니다" in r) for r in res["reasons"])


def test_apply_refuses_when_gate_fails(sandbox):
    doc = buildup.load()
    h = _hypothesis(delta=0.01)
    res = buildup.apply(doc, h, by=10)
    assert res["applied"] is False and h["status"] == "REJECTED"


def test_bidirectional_apply_stays_inside_hard_lock(sandbox):
    """진입 필터는 양방향(2026-11-26) — 단 자동 적용 클램프·하드락 밖은 거부한다."""
    doc = buildup.load()
    doc["ratchet"] = {"rsi2_max": 15.0, "pct_b_max": 0.10, "rvol_min": 1.2}
    ok = _hypothesis(key="entry_filters.pct_b_max", frm=0.10, to=0.15)
    assert buildup.apply(doc, ok, by=10)["applied"] is True

    doc2 = buildup.load()
    too_loose = _hypothesis(key="entry_filters.pct_b_max", frm=0.15, to=0.20)
    res = buildup.apply(doc2, too_loose, by=10)
    assert res["applied"] is False
    assert any("자동 적용 범위" in r for r in res["reasons"])

    doc3 = buildup.load()
    rsi_loose = _hypothesis(key="entry_filters.rsi2_max", frm=20.0, to=25.0)
    res = buildup.apply(doc3, rsi_loose, by=10)
    assert res["applied"] is False


def test_recommend_leaves_approval_with_the_user(sandbox):
    doc = buildup.load()
    h = _hypothesis(key="bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult", frm=1.5, to=1.8, auto=False)
    out = buildup.recommend(doc, h, by=8)
    assert out["status"] == "RECOMMENDED"
    assert "승인 필요" in h["decisions"][-1]["reasons"][0]


def test_candidate_grid_covers_every_axis_within_clamps():
    grid = buildup.candidate_grid({"entry_filters": {"rsi2_max": 15.0, "pct_b_max": 0.10, "rvol_min": 1.4},
                                  "sl_max_pct": 15.0, "max_hold_sessions": 3})
    axes = {g["axis"] for g in grid}
    assert {"ENTRY_FILTER", "EXIT_SPEC", "BRACKET_SPEC"}.issubset(axes)
    assert all(g["auto_apply"] for g in grid), "게이트 통과 시 전 축이 자동 반영 대상이다"
    for g in grid:
        for key, ch in g["changes"].items():
            assert key in buildup.AUTO_APPLY, key
            rule = buildup.AUTO_APPLY[key]
            if rule.get("direction") == "bool":
                assert isinstance(ch["to"], bool)
                continue
            lo, hi = float(rule["min"]), float(rule["max"])
            assert lo - 1e-9 <= float(ch["to"]) <= hi + 1e-9, (key, ch)
    # 양방향 후보가 실제로 만들어진다(2026-11-26)
    assert any("완화" in g["title"] for g in grid if g["axis"] == "ENTRY_FILTER")
