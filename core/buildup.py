"""
Strategy Build-Up — 가설 → 실측 검증 → 승인/보류 → 적용의 누적 장부.

동훈님 지시(2026-11-19): "모든 직원이 상시로 계속 일하면서 서로 업무 결과를 주고 받고
지속 빌드업 하면서 투자 수익을 지속 향상시킬 수 있도록."

이 모듈이 지키는 규칙
--------------------
1. **측정 없이는 아무것도 바뀌지 않는다.** 후보는 `core/desk_lab`의 In-Sample /
   Out-of-Sample 실측을 통과해야 한다(표본·OOS 기대값·PF·MDD 네 관문).
2. **자동 적용 범위는 진입 필터의 '조이기'뿐이다.** `rsi2_max`·`pct_b_max`는 내려가고
   `rvol_min`은 올라가는 방향만 자동 적용한다(래칫). 손절·익절 폭처럼 브래킷 규격을
   바꾸는 후보는 **측정·기록만 하고 승인 대기(RECOMMENDED)** 로 남긴다 — 승인 정본
   (`config/swing_approval.json`)을 코드가 임의로 고치지 않는다.
3. **핵심 가드레일은 하드 락.** RVOL 필수, RSI2/%B/RVOL 하한, 손절 상한 15%,
   3세션 상한, 현금 마지노선 $150, 1회 예산 상한 $220, 일일 손실한도 -$25,
   NVDA 잠금(TP $173.0 / SL $150.0), 물타기 금지, 프리마켓 자동매매 금지.
   어떤 경로로도 이 값을 느슨하게 바꿀 수 없다(`hard_lock_report`가 위반을 돌려준다).

파일
----
`data/strategy_buildup.json` — 가설·측정·게이트·적용 이력의 정본(누적).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core import desk_lab, strategy_evolver

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
BUILDUP_PATH = os.path.join(DATA, "strategy_buildup.json")
POLICY_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")

MAX_HYPOTHESES = 160
MAX_IMPROVEMENTS = 80

# ------------------------------------------------------------------ hard lock
# (연산자, 값) — 위반이면 어떤 쓰기도 거부된다.
HARD_LOCK: Dict[str, Any] = {
    "entry_filters.rvol_required": ("==", True),
    "entry_filters.rsi2_max": ("<=", 20.0),
    "entry_filters.pct_b_max": ("<=", 0.15),
    "entry_filters.rvol_min": (">=", 1.0),
    "sl_max_pct": ("<=", 15.0),
    "sl_min_pct": (">=", 1.5),
    "max_hold_sessions": ("<=", 3),
    "guardrails.cash_reserve_floor_usd": ("==", 150.0),
    "guardrails.entry_budget_cap_usd": ("<=", 220.0),
    "guardrails.entry_budget_floor_usd": ("<=", 220.0),
    "guardrails.daily_realized_loss_limit_usd": ("<=", 25.0),
    "guardrails.no_averaging_down": ("==", True),
    "vix_riskoff_28": ("==", 0.0),
    "nvda_levels_locked": ("==", {"tp": 173.0, "sl": 150.0}),
    "premarket_enabled": ("==", False),
    "universe": ("==", ["SOXL", "TQQQ", "SOXS", "SQQQ"]),
}

# 자동 적용이 허용되는 키(진입 필터 조이기 전용)
AUTO_APPLY: Dict[str, Dict[str, Any]] = {
    "entry_filters.rsi2_max": {"direction": "down", "min": 10.0, "max": 20.0},
    "entry_filters.pct_b_max": {"direction": "down", "min": 0.05, "max": 0.15},
    "entry_filters.rvol_min": {"direction": "up", "min": 1.0, "max": 2.0},
}

GATE: Dict[str, Any] = {
    "min_sample": 20,
    "min_oos_sample": 8,
    "ev_improvement_pct": 0.10,
    "min_oos_profit_factor": 1.2,
    "max_mdd_worsening_pp": 2.0,
    "max_is_regression_pct": 0.50,
    "apply_cooldown_hours": 12,
}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load() -> Dict[str, Any]:
    doc = _load(BUILDUP_PATH, {}) or {}
    doc.setdefault("version", "1.0")
    doc.setdefault("hypotheses", [])
    doc.setdefault("improvements", [])
    doc.setdefault("recent_measurements", [])
    doc.setdefault("ratchet", {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0})
    return doc


def save(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["hypotheses"] = (doc.get("hypotheses") or [])[-MAX_HYPOTHESES:]
    doc["improvements"] = (doc.get("improvements") or [])[-MAX_IMPROVEMENTS:]
    doc["recent_measurements"] = (doc.get("recent_measurements") or [])[-40:]
    doc["updated_at_kst"] = _now_kst()
    doc["counters"] = counters(doc)
    doc["hard_lock"] = hard_lock_snapshot()
    _save(BUILDUP_PATH, doc)
    return doc


def counters(doc: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    doc = doc or load()
    hs = doc.get("hypotheses") or []
    st = {}
    for h in hs:
        st[h.get("status")] = st.get(h.get("status"), 0) + 1
    return {
        "hypotheses": len(hs),
        "applied": st.get("APPLIED", 0),
        "recommended": st.get("RECOMMENDED", 0),
        "held": st.get("HELD", 0),
        "rejected": st.get("REJECTED", 0),
        "open": st.get("PROPOSED", 0) + st.get("MEASURED", 0),
        "improvements": len(doc.get("improvements") or []),
        "by_status": st,
    }


# ------------------------------------------------------------------ hard lock
def _get_path(d: Dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def policy_snapshot(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """하드락 검사용 현재 값 묶음(가드레일 + NVDA 잠금 + 프리마켓 + 유니버스)."""
    from core import execution_policy
    pol = policy if policy is not None else execution_policy.load_policy()
    cfg = _load(POLICY_PATH, {}) or {}
    pre = _load(os.path.join(BASE_DIR, "config", "premarket_policy.json"), {}) or {}
    g = execution_policy.guardrails(pol)
    return {
        "guardrails": g,
        "vix_riskoff_28": (g.get("vix_riskoff_scale") or {}).get("28.0"),
        "nvda_levels_locked": (g.get("nvda_levels_locked") or {}),
        "premarket_enabled": bool(pre.get("enabled")),
        "universe": list(pol.get("universe") or []),
        "entry_filters": dict((_load(os.path.join(DATA, "strategy_params.json"), {}) or {}).get("entry_filters") or {}),
        "sl_max_pct": (strategy_evolver.load_params() or {}).get("sl_max_pct"),
        "sl_min_pct": (strategy_evolver.load_params() or {}).get("sl_min_pct"),
        "max_hold_sessions": (strategy_evolver.load_params() or {}).get("max_hold_sessions"),
        "approval_entry_conditions": ((cfg.get("entry_conditions") or {}) if isinstance(cfg, dict) else {}),
    }


def _cmp(value: Any, op: str, target: Any) -> bool:
    if op == "==":
        if isinstance(target, dict) and isinstance(value, dict):
            # 잠금 레벨은 핵심 키만 비교한다(설명 필드가 덧붙어도 잠금은 유효).
            return all(value.get(k) == v for k, v in target.items())
        return value == target
    if value is None:
        return False
    try:
        if op == "<=":
            return float(value) <= float(target)
        if op == ">=":
            return float(value) >= float(target)
    except (TypeError, ValueError):
        return False
    return False


def hard_lock_report(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """하드 락 위반 목록. 비어 있으면 가드레일이 온전하다."""
    snap = snapshot if snapshot is not None else policy_snapshot()
    violations = []
    for dotted, (op, target) in HARD_LOCK.items():
        value = _get_path(snap, dotted)
        if value is None and dotted == "nvda_levels_locked":
            value = snap.get("nvda_levels_locked")
        if not _cmp(value, op, target):
            violations.append({"key": dotted, "expected": f"{op} {target}", "actual": value})
    # 승인 조건(entry_conditions)이 전략 파라미터보다 느슨해지지 않았는지도 확인한다.
    appr = snap.get("approval_entry_conditions") or {}
    if appr.get("rvol_required") is not True:
        violations.append({"key": "approval.entry_conditions.rvol_required",
                           "expected": "== True", "actual": appr.get("rvol_required")})
    return {"ok": not violations, "violations": violations, "checked_at_kst": _now_kst()}


def hard_lock_snapshot() -> Dict[str, Any]:
    rep = hard_lock_report()
    return {"ok": rep["ok"], "violations": rep["violations"], "rules": {k: f"{v[0]} {v[1]}" for k, v in HARD_LOCK.items()}}


# ------------------------------------------------------------------ hypotheses
def _next_id(doc: Dict[str, Any], prefix: str) -> str:
    key = "hypotheses" if prefix == "H" else "improvements"
    n = len(doc.get(key) or []) + 1
    while True:
        cand = f"{prefix}-{n:04d}"
        if not any((x or {}).get("id") == cand for x in (doc.get(key) or [])):
            return cand
        n += 1


def candidate_grid(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """현재 파라미터에서 만들어지는 후보 목록(진입 필터=자동 적용, 브래킷=승인 대기)."""
    p = params or strategy_evolver.load_params()
    f = dict(p.get("entry_filters") or {})
    rsi = float(f.get("rsi2_max") or 20.0)
    pctb = float(f.get("pct_b_max") or 0.15)
    rvol = float(f.get("rvol_min") or 1.0)
    grid: List[Dict[str, Any]] = []
    if rsi > 15.0:
        grid.append({"axis": "ENTRY_FILTER", "auto_apply": True,
                     "title": f"RSI2 상한 {rsi:g} → {rsi - 5:g}",
                     "claim": "RSI2를 더 조이면 눌림 진입의 질이 올라가 기대값이 개선되는가",
                     "candidate": {"entry_filters": {"rsi2_max": max(10.0, rsi - 5)}},
                     "changes": {"entry_filters.rsi2_max": {"from": rsi, "to": max(10.0, rsi - 5)}}})
    if pctb > 0.10:
        grid.append({"axis": "ENTRY_FILTER", "auto_apply": True,
                     "title": f"볼린저 %B 상한 {pctb:g} → {pctb - 0.05:g}",
                     "claim": "%B를 더 낮춰야 과열 진입을 걸러 기대값이 올라가는가",
                     "candidate": {"entry_filters": {"pct_b_max": round(pctb - 0.05, 3)}},
                     "changes": {"entry_filters.pct_b_max": {"from": pctb, "to": round(pctb - 0.05, 3)}}})
    if rvol < 1.6:
        grid.append({"axis": "ENTRY_FILTER", "auto_apply": True,
                     "title": f"RVOL 하한 {rvol:g} → {rvol + 0.2:g}",
                     "claim": "거래량 배수를 더 요구하면 신호 빈도는 줄지만 건당 기대값이 오르는가",
                     "candidate": {"entry_filters": {"rvol_min": round(rvol + 0.2, 2)}},
                     "changes": {"entry_filters.rvol_min": {"from": rvol, "to": round(rvol + 0.2, 2)}}})
    # 브래킷 규격 후보 — 측정만 하고 승인 대기로 남긴다(승인 정본은 사용자 몫).
    spec = desk_lab.SWING_SPEC
    grid += [
        {"axis": "BRACKET_SPEC", "auto_apply": False,
         "title": f"일봉 스윙 손절 {spec['sl_atr_mult']:g}×ATR → 1.2×ATR",
         "claim": "손절을 1.2×ATR로 좁히면 손실은 줄지만 노이즈 손절이 늘어 기대값이 어떻게 되는가",
         "candidate": {"spec": {"sl_atr_mult": 1.2}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult": {"from": spec["sl_atr_mult"], "to": 1.2}}},
        {"axis": "BRACKET_SPEC", "auto_apply": False,
         "title": f"일봉 스윙 손절 {spec['sl_atr_mult']:g}×ATR → 1.8×ATR",
         "claim": "손절을 1.8×ATR로 넓히면 3세션 안의 흔들림을 견디는가",
         "candidate": {"spec": {"sl_atr_mult": 1.8}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult": {"from": spec["sl_atr_mult"], "to": 1.8}}},
        {"axis": "BRACKET_SPEC", "auto_apply": False,
         "title": f"일봉 스윙 익절 {spec['tp_atr_mult']:g}×ATR → 2.0×ATR",
         "claim": "익절을 2.0×ATR로 넓히면 큰 날을 더 먹는가, 아니면 되돌림에 잃는가",
         "candidate": {"spec": {"tp_atr_mult": 2.0}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.tp_atr_mult": {"from": spec["tp_atr_mult"], "to": 2.0}}},
        {"axis": "BRACKET_SPEC", "auto_apply": False,
         "title": "일봉 스윙 보유 3세션 → 2세션",
         "claim": "보유를 2세션으로 줄이면 회전이 빨라져 기대값이 오르는가",
         "candidate": {"spec": {"max_hold_sessions": 2}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.max_hold_sessions": {"from": 3, "to": 2}}},
    ]
    return grid


def record_hypothesis(doc: Dict[str, Any], item: Dict[str, Any], owner: int, owner_name: str,
                      data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    h = {
        "id": _next_id(doc, "H"),
        "created_at_kst": _now_kst(),
        "owner": owner, "owner_name": owner_name,
        "axis": item.get("axis"), "auto_apply": bool(item.get("auto_apply")),
        "title": item.get("title"), "claim": item.get("claim"),
        "candidate": item.get("candidate"), "changes": item.get("changes") or {},
        "status": "PROPOSED", "decisions": [],
        "data_fingerprint": (data or {}).get("fingerprint"),
        "data_as_of": (data or {}).get("as_of"),
    }
    doc.setdefault("hypotheses", []).append(h)
    return h


# ------------------------------------------------------------------ measure + gate
def measure(h: Dict[str, Any], daily: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    daily = daily or desk_lab.load_daily()
    params = strategy_evolver.load_params()
    ev = desk_lab.evaluate_variant(daily, h.get("candidate") or {}, params)
    h["measurement"] = ev
    h["gate"] = gate(ev)
    h["status"] = "MEASURED"
    return ev


def gate(ev: Dict[str, Any]) -> Dict[str, Any]:
    oos = (ev.get("candidate") or {}).get("oos") or {}
    base_oos = (ev.get("baseline") or {}).get("oos") or {}
    is_ = (ev.get("candidate") or {}).get("is") or {}
    base_is = (ev.get("baseline") or {}).get("is") or {}
    checks = []

    def add(name: str, value: Any, ok: bool, rule: str) -> None:
        checks.append({"check": name, "value": value, "rule": rule, "ok": bool(ok)})

    add("표본", oos.get("n") and (is_.get("n") or 0) + (oos.get("n") or 0),
        int((is_.get("n") or 0) + (oos.get("n") or 0)) >= GATE["min_sample"], f"전체 ≥ {GATE['min_sample']}건")
    add("OOS 표본", oos.get("n"), int(oos.get("n") or 0) >= GATE["min_oos_sample"],
        f"OOS ≥ {GATE['min_oos_sample']}건")
    d_ev = float(ev.get("delta_oos_ev_pct") or 0.0)
    add("OOS 기대값 개선", round(d_ev, 3), d_ev >= GATE["ev_improvement_pct"],
        f"ΔEV ≥ +{GATE['ev_improvement_pct']}%p")
    pf = oos.get("profit_factor")
    add("OOS PF", pf, pf is not None and float(pf) >= GATE["min_oos_profit_factor"],
        f"≥ {GATE['min_oos_profit_factor']}")
    worse = float(ev.get("mdd_worsening_pp") or 0.0)
    add("OOS MDD 악화", worse, worse <= GATE["max_mdd_worsening_pp"],
        f"≤ +{GATE['max_mdd_worsening_pp']}%p")
    is_delta = round(float(is_.get("ev_pct") or 0.0) - float(base_is.get("ev_pct") or 0.0), 3)
    add("IS 후퇴 폭", is_delta, is_delta >= -GATE["max_is_regression_pct"],
        f"≥ -{GATE['max_is_regression_pct']}%p")
    passed = all(c["ok"] for c in checks)
    failed = [c for c in checks if not c["ok"]]
    return {"pass": passed, "checks": checks,
            "reason": ("4개 정량 기준 + 표본 통과" if passed
                       else "; ".join(f"{c['check']} {c['value']} ({c['rule']})" for c in failed)),
            "thresholds": GATE}


# ------------------------------------------------------------------ apply
def _set_path(params: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = params
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _ratchet_violation(doc: Dict[str, Any], key: str, new_value: Any) -> Optional[str]:
    ratchet = doc.get("ratchet") or {}
    old = ratchet.get(key.split(".")[-1])
    rule = AUTO_APPLY.get(key)
    if not rule or old is None:
        return None
    try:
        new_v, old_v = float(new_value), float(old)
    except (TypeError, ValueError):
        return None
    if rule["direction"] == "down" and new_v > old_v + 1e-9:
        return f"{key} 래칫 위반 — {old_v:g}에서 {new_v:g}로 느슨해질 수 없습니다"
    if rule["direction"] == "up" and new_v < old_v - 1e-9:
        return f"{key} 래칫 위반 — {old_v:g}에서 {new_v:g}로 느슨해질 수 없습니다"
    return None


def can_apply(doc: Dict[str, Any], h: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    g = h.get("gate") or {}
    if not g.get("pass"):
        reasons.append(f"게이트 미통과 — {g.get('reason')}")
    if not h.get("auto_apply"):
        reasons.append("자동 적용 범위 밖(브래킷 규격은 사용자 승인 필요) — 측정·기록만")
    changes = h.get("changes") or {}
    if not changes:
        reasons.append("변경 항목 없음")
    for key, val in changes.items():
        if key not in AUTO_APPLY:
            reasons.append(f"{key}는 자동 적용 키가 아닙니다")
            continue
        rule = AUTO_APPLY[key]
        new_v = val.get("to")
        try:
            if float(new_v) < float(rule["min"]) or float(new_v) > float(rule["max"]):
                reasons.append(f"{key} {new_v:g}이 허용 범위({rule['min']:g}~{rule['max']:g}) 밖")
        except (TypeError, ValueError):
            reasons.append(f"{key} 값이 숫자가 아닙니다")
        rv = _ratchet_violation(doc, key, new_v)
        if rv:
            reasons.append(rv)
    # 적용 쿨다운(같은 키 반복 변경 방지)
    last = (doc.get("improvements") or [{}])[-1] if doc.get("improvements") else {}
    if last.get("applied_at_kst"):
        try:
            t = datetime.strptime(str(last["applied_at_kst"])[:19], "%Y-%m-%d %H:%M:%S")
            now = datetime.strptime(_now_kst(), "%Y-%m-%d %H:%M:%S")
            if (now - t) < timedelta(hours=float(GATE["apply_cooldown_hours"])) and (
                    set((last.get("changes") or {}).keys()) & set(changes.keys())):
                reasons.append(f"쿨다운 — 같은 항목을 {GATE['apply_cooldown_hours']}시간 안에 다시 바꾸지 않습니다")
        except Exception:
            pass
    return {"ok": not reasons, "reasons": reasons}


def apply(doc: Dict[str, Any], h: Dict[str, Any], by: int = 10) -> Dict[str, Any]:
    """게이트·래칫·하드락을 모두 통과한 가설만 런타임 파라미터에 반영한다."""
    verdict = can_apply(doc, h)
    if not verdict["ok"]:
        h["status"] = "HELD" if (h.get("gate") or {}).get("pass") else "REJECTED"
        h["decisions"].append({"at_kst": _now_kst(), "decision": h["status"],
                               "by": by, "reasons": verdict["reasons"]})
        return {"applied": False, "status": h["status"], "reasons": verdict["reasons"]}

    params = strategy_evolver.load_params()
    before = json.loads(json.dumps(params))
    for key, val in (h.get("changes") or {}).items():
        _set_path(params, key, val.get("to"))
    # 하드락 재검사 — 변경 후에도 가드레일이 온전해야 한다.
    lock = hard_lock_report(policy_snapshot())
    if not lock["ok"]:
        h["status"] = "REJECTED"
        h["decisions"].append({"at_kst": _now_kst(), "decision": "REJECTED_HARD_LOCK",
                               "by": by, "violations": lock["violations"]})
        return {"applied": False, "status": h["status"], "reasons": [v["key"] for v in lock["violations"]]}

    strategy_evolver.save_params(params, note=f"빌드업 {h['id']} {h['title']} — OOS 게이트 통과")
    imp = {
        "id": _next_id(doc, "I"), "hypothesis": h["id"], "applied_at_kst": _now_kst(),
        "title": h["title"], "changes": h.get("changes"),
        "before": {"entry_filters": before.get("entry_filters")},
        "after": {"entry_filters": params.get("entry_filters")},
        "gate": h.get("gate"), "evidence": {
            "data_as_of": (h.get("measurement") or {}).get("data_as_of"),
            "data_fingerprint": (h.get("measurement") or {}).get("data_fingerprint"),
            "cut_date": (h.get("measurement") or {}).get("cut_date"),
            "oos": ((h.get("measurement") or {}).get("candidate") or {}).get("oos"),
            "baseline_oos": ((h.get("measurement") or {}).get("baseline") or {}).get("oos"),
        }, "by": by,
    }
    doc.setdefault("improvements", []).append(imp)
    ratchet = doc.setdefault("ratchet", {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0})
    for key, val in (h.get("changes") or {}).items():
        ratchet[key.split(".")[-1]] = val.get("to")
    h["status"] = "APPLIED"
    h["decisions"].append({"at_kst": _now_kst(), "decision": "APPLIED", "by": by,
                           "improvement": imp["id"], "changes": h.get("changes")})
    return {"applied": True, "status": "APPLIED", "improvement": imp}


def recommend(doc: Dict[str, Any], h: Dict[str, Any], by: int = 9) -> Dict[str, Any]:
    """브래킷 규격처럼 승인 정본을 건드려야 하는 후보 — 측정 결과만 남기고 대기."""
    h["status"] = "RECOMMENDED" if (h.get("gate") or {}).get("pass") else "REJECTED"
    h["decisions"].append({
        "at_kst": _now_kst(), "decision": h["status"], "by": by,
        "reasons": (["사용자 승인 필요 — config/swing_approval.json bracket_specs 변경은 자동 적용 금지"]
                    if h["status"] == "RECOMMENDED" else [(h.get("gate") or {}).get("reason")]),
    })
    return {"status": h["status"]}


def find_duplicate(doc: Dict[str, Any], item: Dict[str, Any], fingerprint: Optional[str]) -> Optional[Dict[str, Any]]:
    for h in reversed(doc.get("hypotheses") or []):
        if h.get("title") == item.get("title") and h.get("data_fingerprint") == fingerprint:
            return h
    return None


def pending_measurement(doc: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    doc = doc or load()
    return [h for h in (doc.get("hypotheses") or []) if h.get("status") == "PROPOSED"]


def summary(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = doc or load()
    imps = doc.get("improvements") or []
    hs = doc.get("hypotheses") or []
    latest = hs[-1] if hs else None
    return {
        "counters": counters(doc),
        "ratchet": doc.get("ratchet"),
        "hard_lock": hard_lock_snapshot(),
        "last_hypothesis": ({k: latest.get(k) for k in ("id", "title", "status", "owner_name", "created_at_kst")}
                            if latest else None),
        "last_improvement": ({k: imps[-1].get(k) for k in ("id", "title", "applied_at_kst", "changes",
                                                           "evidence", "hypothesis")} if imps else None),
        "gate": GATE,
        "recommendations": [{k: h.get(k) for k in ("id", "title", "status", "measurement", "created_at_kst")}
                            for h in hs if h.get("status") == "RECOMMENDED"][-5:],
    }


if __name__ == "__main__":
    import sys
    s = summary()
    print(f"가설 {s['counters']['hypotheses']}건 · 적용 {s['counters']['applied']} · "
          f"승인대기 {s['counters']['recommended']} · 보류 {s['counters']['held'] + s['counters']['rejected']}")
    print(f"래칫 {s['ratchet']} · 하드락 {'정상' if s['hard_lock']['ok'] else s['hard_lock']['violations']}")
    if s.get("last_improvement"):
        i = s["last_improvement"]
        print(f"최근 적용 {i['id']} {i['title']} @ {i['applied_at_kst']}")
    for r in s.get("recommendations") or []:
        m = (r.get("measurement") or {})
        print(f"승인대기 {r['id']} {r['title']} — OOS Δ{m.get('delta_oos_ev_pct')}%p "
              f"(데이터 {m.get('data_as_of')})")
    if "--json" in sys.argv[1:]:
        import json
        print(json.dumps(s, ensure_ascii=False, indent=2))
