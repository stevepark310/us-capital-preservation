"""
Strategy Build-Up — 가설 → 실측 검증 → 승인/보류 → 적용의 누적 장부.

동훈님 지시(2026-11-19): "모든 직원이 상시로 계속 일하면서 서로 업무 결과를 주고 받고
지속 빌드업 하면서 투자 수익을 지속 향상시킬 수 있도록."

이 모듈이 지키는 규칙
--------------------
1. **측정 없이는 아무것도 바뀌지 않는다.** 후보는 `core/desk_lab`의 In-Sample /
   Out-of-Sample 실측을 통과해야 한다(표본·OOS 기대값·PF·MDD 네 관문).
2. **자동 적용 범위 = 게이트를 통과한 모든 축.** 진입 필터는 양방향(`rsi2_max`·`pct_b_max`·
   `rvol_min`), 손절·트레일·보유기간(`initial_sl_atr_mult`·`trail_atr_mult`·`sl_max_pct`·
   `max_hold_sessions`), 브래킷 규격(`bracket_specs.DIP_SWING_DAILY.*`), 모델 활성화
   (`config/regime_policy.json`)까지 **안전 클램프(`AUTO_APPLY` 상·하한) 안에서 자동 반영**한다
   (동훈님 지시 2026-11-26 20:58: "게이트 통과 전략 전부 자동 반영").
   경계는 여전히 두 겹이다 — ① 4개 정량 게이트(표본·OOS ΔEV·OOS PF·MDD 악화 한도·IS 후퇴)
   ② 하드 락. 게이트 미통과 후보는 어떤 축도 바꾸지 않는다.
3. **핵심 가드레일은 하드 락.** RVOL 필수, RSI2/%B/RVOL 한도, 손절 상한 15%,
   3세션 상한, 현금 마지노선 $150, 1회 예산 상한 $220, 일일 손실한도 -$25,
   NVDA 잠금(TP $173.0 / SL $150.0), 물타기 금지, 프리마켓 수동개입 정책.
   어떤 경로로도 이 값을 느슨하게 바꿀 수 없다(`hard_lock_report`가 위반을 돌려준다).
   유니버스는 **코어 4종목 필수 + 동적 스크리너 선정 종목만 추가 허용**으로 바뀌었다
   (2026-11-26) — 고정 4종목 락을 풀되, 근거 없는 임의 종목 편입은 여전히 위반이다.

파일
----
`data/strategy_buildup.json` — 가설·측정·게이트·적용 이력의 정본(누적).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core import desk_lab, strategy_evolver, universe_screener

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
BUILDUP_PATH = os.path.join(DATA, "strategy_buildup.json")
POLICY_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")
REGIME_POLICY_PATH = os.path.join(BASE_DIR, "config", "regime_policy.json")

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
}

# 유니버스 규칙 (2026-11-26 — 고정 4종목 락 → '코어 필수 + 스크리닝 선정만 추가')
# 예전에는 universe 가 정확히 ["SOXL","TQQQ","SOXS","SQQQ"] 여야 했다. 동훈님이
# "ETF·나스닥100 대형주까지 발굴"을 지시해 고정 락을 풀되, 아무 종목이나 들어오지
# 못하도록 ① 코어 4종목 필수 ② 추가분은 `core/universe_screener`의 선정 목록에만 한정
# ③ 금지 종목(ENVX·IBRX·SOUN) 차단 — 세 조건을 하드락으로 검사한다.
CORE_UNIVERSE: List[str] = list(universe_screener.CORE_UNIVERSE)
UNIVERSE_RULE: Dict[str, Any] = {
    "key": "universe",
    "expected": "코어 4종목 포함 + 스크리너 선정 종목만 추가(금지 종목 제외)",
    "core": CORE_UNIVERSE,
    "allowed_source": "data/dynamic_universe.json (core/universe_screener)",
    "note": ("선정 목록은 실측 지표(거래대금·ATR·RSI2·%B·RVOL·모멘텀)로 매시간 다시 계산되고, "
             "`sync_policy_universe`가 config/swing_approval.json 유니버스에 반영한다."),
}

# 프리장 자동매매 잠금 (2026-11-21 재설계)
# ------------------------------------------------
# 종전에는 `premarket_enabled == False`가 무조건 참이어야 했다. 그런데 동훈님이 "프리장에
# 개입해 바로바로 매매"를 지시했고(2026-11-21 13:53), `core/session_routing`은 승인 기록이
# 있을 때만 프리장을 여는 구조가 됐다. 그래서 '무조건 금지'를 '승인 없는 활성화 금지'로
# 바꿔 둔다 — 코드가 스스로는 열 수 없고, 사용자 승인(`approved_by`·`approved_at_kst`)이
# 있는 파일만 통과한다(하드락 충돌 제거, 잠금 자체는 유지).
PREMATURE_PREMARKET_RULE: Dict[str, Any] = {
    "key": "premarket_enabled",
    "expected": "승인 기록(approved_by·approved_at_kst)과 함께 true",
    "note": ("스위치만 켜고 승인 기록이 없으면 하드락 위반으로 보고한다. "
             "승인 범위가 manual_intervention_only=true면 무인 엔진은 프리장에 주문하지 않고 "
             "intervene_trade.py(사용자 지시)만 61 주문을 낸다 — 이 구분은 hard_lock 스냅샷의 "
             "premarket.mode(closed/manual_only/auto)에 남긴다."),
}

# 자동 적용 키 — 게이트 통과 시 아래 클램프 안에서만 쓴다.
#   direction: "any" = 양방향(게이트가 판정) · "down"/"up" = 래칫(한 방향만)
#   target:    strategy_params(data/strategy_params.json) | swing_approval(config/swing_approval.json)
#              | regime_policy(config/regime_policy.json)
#   min/max:   자동 적용 클램프(하드 락보다 좁다 — 이중 방어)
AUTO_APPLY: Dict[str, Dict[str, Any]] = {
    # 진입 필터 — 양방향(2026-11-26). 상한 자체는 HARD_LOCK이 잡는다.
    "entry_filters.rsi2_max": {"direction": "any", "min": 10.0, "max": 20.0, "target": "strategy_params"},
    "entry_filters.pct_b_max": {"direction": "any", "min": 0.05, "max": 0.15, "target": "strategy_params"},
    "entry_filters.rvol_min": {"direction": "any", "min": 1.0, "max": 2.0, "target": "strategy_params"},
    # 손절·트레일·보유 — strategy_evolver.CLAMPS와 같은 구간
    "initial_sl_atr_mult": {"direction": "any", "min": 0.50, "max": 1.00, "target": "strategy_params"},
    "trail_atr_mult": {"direction": "any", "min": 0.60, "max": 1.20, "target": "strategy_params"},
    "sl_max_pct": {"direction": "any", "min": 1.5, "max": 15.0, "target": "strategy_params"},
    "max_hold_sessions": {"direction": "any", "min": 1, "max": 3, "target": "strategy_params"},
    # 승인 정본의 브래킷 규격(일봉 스윙)
    "bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult": {"direction": "any", "min": 1.0, "max": 2.5, "target": "swing_approval"},
    "bracket_specs.DIP_SWING_DAILY.tp_atr_mult": {"direction": "any", "min": 1.0, "max": 3.0, "target": "swing_approval"},
    "bracket_specs.DIP_SWING_DAILY.max_hold_sessions": {"direction": "any", "min": 1, "max": 3, "target": "swing_approval"},
    # 모델 활성화 — 화이트리스트만(게이트 통과 + 하드락 통과 시 자동 on)
    "models.REGIME_ADAPTIVE_DIP_V1.enabled": {"direction": "bool", "target": "regime_policy"},
}

# 모델 활성화가 자동으로 켤 수 있는 모델(그 외 이름은 거부)
MODEL_WHITELIST = {"REGIME_ADAPTIVE_DIP_V1"}

GATE: Dict[str, Any] = {
    "min_sample": 20,
    "min_oos_sample": 8,
    "ev_improvement_pct": 0.10,
    "min_oos_profit_factor": 1.2,
    "max_mdd_worsening_pp": 2.0,
    "max_all_mdd_worsening_pp": 3.0,   # 전체 표본 MDD도 못 나빠진다(2026-11-19 추가)
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
    from core import execution_policy, session_routing
    pol = policy if policy is not None else execution_policy.load_policy()
    cfg = _load(POLICY_PATH, {}) or {}
    pre = session_routing.load_policy()
    approval = session_routing.approval_state(pre)
    g = execution_policy.guardrails(pol)
    return {
        "guardrails": g,
        "vix_riskoff_28": (g.get("vix_riskoff_scale") or {}).get("28.0"),
        "nvda_levels_locked": (g.get("nvda_levels_locked") or {}),
        "premarket_enabled": bool(approval.get("approved")),
        "premarket_switch": bool(pre.get("enabled")),
        "premarket_manual_only": session_routing.manual_intervention_only(pre),
        "premarket_approval": approval,
        "universe": list(pol.get("universe") or []),
        "universe_core": list(CORE_UNIVERSE),
        "universe_allowed": list(universe_screener.allowed_symbols()),
        "universe_banned": sorted(set(universe_screener.BANNED)),
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


def _universe_violations(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """유니버스 하드락 — 코어 필수 · 선정 종목만 추가 · 금지 종목 차단."""
    out: List[Dict[str, Any]] = []
    uni = [str(x).upper() for x in (snap.get("universe") or [])]
    core = [str(x).upper() for x in (snap.get("universe_core") or CORE_UNIVERSE)]
    allowed = {str(x).upper() for x in (snap.get("universe_allowed") or core)}
    banned = {str(x).upper() for x in (snap.get("universe_banned") or universe_screener.BANNED)}
    missing = [c for c in core if c not in uni]
    if missing:
        out.append({"key": "universe.core", "expected": f"코어 포함 {core}", "actual": missing})
    hit = [t for t in uni if t in banned]
    if hit:
        out.append({"key": "universe.banned", "expected": "금지 종목 미포함", "actual": hit})
    extra = [t for t in uni if t not in allowed and t not in banned]
    if extra:
        out.append({"key": "universe.screened",
                    "expected": "추가는 스크리너 선정 종목만", "actual": extra})
    return out


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
    violations.extend(_universe_violations(snap))
    # 승인 조건(entry_conditions)이 전략 파라미터보다 느슨해지지 않았는지도 확인한다.
    appr = snap.get("approval_entry_conditions") or {}
    if appr.get("rvol_required") is not True:
        violations.append({"key": "approval.entry_conditions.rvol_required",
                           "expected": "== True", "actual": appr.get("rvol_required")})
    # 프리장: 스위치가 켜졌는데 사용자 승인 기록이 없으면 위반(코드가 스스로 여는 경로 차단).
    if snap.get("premarket_switch") and not snap.get("premarket_enabled"):
        violations.append({"key": PREMATURE_PREMARKET_RULE["key"],
                           "expected": PREMATURE_PREMARKET_RULE["expected"],
                           "actual": (snap.get("premarket_approval") or {}).get("reason")})
    return {"ok": not violations, "violations": violations, "checked_at_kst": _now_kst()}


def hard_lock_snapshot() -> Dict[str, Any]:
    snap = policy_snapshot()
    rep = hard_lock_report(snap)
    approved = bool(snap.get("premarket_enabled"))
    manual_only = bool(snap.get("premarket_manual_only"))
    return {"ok": rep["ok"], "violations": rep["violations"],
            "rules": {**{k: f"{v[0]} {v[1]}" for k, v in HARD_LOCK.items()},
                      "universe": f"코어 포함 {CORE_UNIVERSE} + 스크리너 선정만 추가"},
            "universe_rule": UNIVERSE_RULE,
            "universe": {"current": list(snap.get("universe") or []),
                         "core": list(CORE_UNIVERSE),
                         "allowed": list(snap.get("universe_allowed") or CORE_UNIVERSE)},
            "premarket_rule": PREMATURE_PREMARKET_RULE,
            "premarket": {"switch": bool(snap.get("premarket_switch")),
                          "approved": approved,
                          "manual_intervention_only": manual_only,
                          "auto_enabled": bool(approved and not manual_only),
                          "mode": ("closed" if not approved else
                                   ("manual_only" if manual_only else "auto")),
                          "approved_by": (snap.get("premarket_approval") or {}).get("approved_by"),
                          "approved_at_kst": (snap.get("premarket_approval") or {}).get("approved_at_kst")}}


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
    """현재 파라미터에서 만들어지는 후보 목록.

    모든 후보가 `auto_apply=True`다 — 게이트를 통과하면 그 축이 실제로 바뀐다
    (2026-11-26 지시). 게이트 미통과 후보는 `HELD`로 남고 아무것도 바뀌지 않는다.
    """
    p = params or strategy_evolver.load_params()
    f = dict(p.get("entry_filters") or {})
    rsi = float(f.get("rsi2_max") or 20.0)
    pctb = float(f.get("pct_b_max") or 0.15)
    rvol = float(f.get("rvol_min") or 1.0)
    sl_max = float(p.get("sl_max_pct") or 15.0)
    hold = int(p.get("max_hold_sessions") or 3)
    spec = dict(desk_lab.SWING_SPEC)
    spec.update({k: v for k, v in (_bracket_specs().get("DIP_SWING_DAILY") or {}).items()
                 if k in ("initial_sl_atr_mult", "tp_atr_mult", "max_hold_sessions",
                          "sl_min_pct", "sl_max_pct", "tp_min_pct", "tp_max_pct")})
    sl_mult = float(spec.get("initial_sl_atr_mult") or 1.5)
    tp_mult = float(spec.get("tp_atr_mult") or 1.5)
    b_hold = int(spec.get("max_hold_sessions") or 3)
    grid: List[Dict[str, Any]] = []

    def entry_item(field: str, new: float, label: str, claim: str) -> Dict[str, Any]:
        cur = {"rsi2_max": rsi, "pct_b_max": pctb, "rvol_min": rvol}[field]
        return {"axis": "ENTRY_FILTER", "auto_apply": True, "title": f"{label} {cur:g} → {new:g}",
                "claim": claim,
                "candidate": {"entry_filters": {field: new}},
                "changes": {f"entry_filters.{field}": {"from": cur, "to": new}}}

    # ---- 진입 필터: 조이기
    if rsi > 15.0:
        grid.append(entry_item("rsi2_max", max(10.0, rsi - 5), "RSI2 상한",
                               "RSI2를 더 조이면 눌림 진입의 질이 올라가 기대값이 개선되는가"))
    if pctb > 0.10:
        grid.append(entry_item("pct_b_max", round(pctb - 0.05, 3), "볼린저 %B 상한",
                               "%B를 더 낮춰 과열 진입을 걸러 기대값이 올라가는가"))
    if rvol < 1.6:
        grid.append(entry_item("rvol_min", round(rvol + 0.2, 2), "RVOL 하한",
                               "거래량 배수를 더 요구하면 신호 빈도는 줄지만 건당 기대값이 오르는가"))
    # ---- 진입 필터: 풀기(게이트가 기대값을 입증해야 적용 — 2026-11-26 양방향)
    if rsi < 20.0:
        grid.append(entry_item("rsi2_max", min(20.0, rsi + 5), "RSI2 상한(완화)",
                               "상한을 넓히면 표본이 늘어 기대값이 오르는가(하드락 20 이하 유지)"))
    if pctb < 0.15:
        grid.append(entry_item("pct_b_max", round(min(0.15, pctb + 0.05), 3), "볼린저 %B 상한(완화)",
                               "%B 상한을 넓혀 진입 기회를 늘리면 기대값이 오르는가(하드락 0.15 이하 유지)"))
    if rvol > 1.0:
        grid.append(entry_item("rvol_min", round(max(1.0, rvol - 0.2), 2), "RVOL 하한(완화)",
                               "RVOL 요구를 낮춰 표본을 늘리면 기대값이 오르는가(RVOL 필수·1.0 이상 유지)"))

    # ---- 종료·손절 파라미터(data/strategy_params.json) — 측정은 일봉 스펙으로 갈음
    if sl_max > 8.0:
        new_sl = round(max(8.0, sl_max - 5.0), 1)
        grid.append({"axis": "EXIT_SPEC", "auto_apply": True,
                     "title": f"손절 하드캡 {sl_max:g}% → {new_sl:g}%",
                     "claim": "손절 상한을 좁히면 건당 손실이 줄어 기대값·MDD가 개선되는가",
                     "candidate": {"spec": {"sl_max_pct": new_sl}},
                     "changes": {"sl_max_pct": {"from": sl_max, "to": new_sl}}})
    if hold > 2:
        grid.append({"axis": "EXIT_SPEC", "auto_apply": True,
                     "title": f"보유 상한 {hold}세션 → {hold - 1}세션",
                     "claim": "보유를 줄이면 회전이 빨라져 기대값이 오르는가",
                     "candidate": {"spec": {"max_hold_sessions": hold - 1}},
                     "changes": {"max_hold_sessions": {"from": hold, "to": hold - 1}}})

    # ---- 브래킷 규격(config/swing_approval.json → autonomous_execution.bracket_specs)
    grid += [
        {"axis": "BRACKET_SPEC", "auto_apply": True,
         "title": f"일봉 스윙 손절 {sl_mult:g}×ATR → 1.2×ATR",
         "claim": "손절을 1.2×ATR로 좁히면 손실은 줄지만 노이즈 손절이 늘어 기대값이 어떻게 되는가",
         "candidate": {"spec": {"sl_atr_mult": 1.2}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult": {"from": sl_mult, "to": 1.2}}},
        {"axis": "BRACKET_SPEC", "auto_apply": True,
         "title": f"일봉 스윙 손절 {sl_mult:g}×ATR → 1.8×ATR",
         "claim": "손절을 1.8×ATR로 넓히면 3세션 안의 흔들림을 견디는가",
         "candidate": {"spec": {"sl_atr_mult": 1.8}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.initial_sl_atr_mult": {"from": sl_mult, "to": 1.8}}},
        {"axis": "BRACKET_SPEC", "auto_apply": True,
         "title": f"일봉 스윙 익절 {tp_mult:g}×ATR → 2.0×ATR",
         "claim": "익절을 2.0×ATR로 넓히면 큰 날을 더 먹는가, 아니면 되돌림에 잃는가",
         "candidate": {"spec": {"tp_atr_mult": 2.0}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.tp_atr_mult": {"from": tp_mult, "to": 2.0}}},
        {"axis": "BRACKET_SPEC", "auto_apply": True,
         "title": f"일봉 스윙 보유 {b_hold}세션 → 2세션",
         "claim": "보유를 2세션으로 줄이면 회전이 빨라져 기대값이 오르는가",
         "candidate": {"spec": {"max_hold_sessions": 2}},
         "changes": {"bracket_specs.DIP_SWING_DAILY.max_hold_sessions": {"from": b_hold, "to": 2}}},
    ]
    out = []
    for g in grid:
        first = g["changes"][next(iter(g["changes"]))]
        if first.get("from") != first.get("to"):
            out.append(g)
    return out


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
    all_ = (ev.get("candidate") or {}).get("all") or {}
    base_all = (ev.get("baseline") or {}).get("all") or {}
    worse_all = round(float(base_all.get("mdd_pct") or 0.0) - float(all_.get("mdd_pct") or 0.0), 2)
    add("전체 MDD 악화", worse_all, worse_all <= GATE["max_all_mdd_worsening_pp"],
        f"≤ +{GATE['max_all_mdd_worsening_pp']}%p")
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
    """게이트·클램프·냉각을 모두 통과해야 쓸 수 있다(하드락은 적용 직전에 한 번 더)."""
    reasons: List[str] = []
    g = h.get("gate") or {}
    if not g.get("pass"):
        reasons.append(f"게이트 미통과 — {g.get('reason')}")
    changes = h.get("changes") or {}
    if not changes:
        reasons.append("변경 항목 없음")
    for key, val in changes.items():
        rule = AUTO_APPLY.get(key)
        if not rule:
            reasons.append(f"{key}는 자동 적용 키가 아닙니다")
            continue
        new_v = val.get("to")
        if rule.get("direction") == "bool" or key.startswith("models."):
            name = key.split(".")[1] if key.startswith("models.") else ""
            if name not in MODEL_WHITELIST:
                reasons.append(f"{name or key}는 자동 활성화 화이트리스트 밖입니다")
            if not isinstance(new_v, bool):
                reasons.append(f"{key} 값이 bool이 아닙니다({new_v!r})")
            continue
        try:
            fv = float(new_v)
        except (TypeError, ValueError):
            reasons.append(f"{key} 값이 숫자가 아닙니다")
            continue
        if fv < float(rule["min"]) - 1e-9 or fv > float(rule["max"]) + 1e-9:
            reasons.append(f"{key} {fv:g}이 자동 적용 범위({rule['min']:g}~{rule['max']:g}) 밖")
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


# ------------------------------------------------------------------ 쓰기 경로
# 키마다 어느 파일에 쓰는지가 다르다. 한 번의 적용이 여러 파일에 걸칠 수 있으므로
# '전부 미리 계산 → 하드락 검사 → 기록' 순서로 쓴다(중간 상태로 정본이 깨지지 않게).
def _target_path(target: str) -> str:
    if target == "swing_approval":
        return POLICY_PATH
    if target == "regime_policy":
        return REGIME_POLICY_PATH
    return strategy_evolver.PARAMS_PATH


def _split_changes(changes: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for key, val in (changes or {}).items():
        rule = AUTO_APPLY.get(key) or {}
        target = rule.get("target") or "strategy_params"
        groups.setdefault(target, {})[key] = val.get("to")
    return groups


def _current_value(target: str, doc: Dict[str, Any], key: str) -> Any:
    if target == "swing_approval":
        parts = key.split(".")
        if len(parts) >= 3 and parts[0] == "bracket_specs":
            block = ((((doc.get("autonomous_execution") or {}).get("bracket_specs") or {})
                      .get(parts[1])) or {})
            return block.get(parts[2])
        return _get_path(doc, key)
    if target == "regime_policy":
        return doc.get("enabled")
    val = _get_path(doc, key)
    return val


def _write_value(target: str, doc: Dict[str, Any], key: str, value: Any, h: Dict[str, Any]) -> None:
    if target == "swing_approval":
        parts = key.split(".")
        if len(parts) >= 3 and parts[0] == "bracket_specs":
            ae = doc.setdefault("autonomous_execution", {})
            specs = ae.setdefault("bracket_specs", {})
            block = specs.setdefault(parts[1], {})
            block[parts[2]] = value
            block["updated_by"] = f"buildup {h.get('id')} (OOS 게이트 통과)"
            block["updated_at_kst"] = _now_kst()
            return
        _set_path(doc, key, value)
        return
    if target == "regime_policy":
        doc["enabled"] = bool(value)
        doc["mode"] = "active" if value else "shadow"
        if value:
            doc["approved_by"] = "자동 반영 — 빌드업 OOS 게이트 통과(CUBE)"
            doc["approved_at_kst"] = _now_kst()
            doc["approval_source"] = {"hypothesis": h.get("id"), "title": h.get("title"),
                                      "gate": (h.get("gate") or {}).get("reason"),
                                      "policy": "2026-11-26 사용자 지시(게이트 통과 전략 자동 반영)"}
        else:
            doc["approved_by"] = None
            doc["approved_at_kst"] = None
        doc["decided_at_kst"] = _now_kst()
        return
    _set_path(doc, key, value)


def _project_snapshot(snap: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    """변경 예정 값을 하드락 스냅샷에 미리 반영(쓰기 전 검사용)."""
    for key, val in (changes or {}).items():
        to = val.get("to")
        if key.startswith("entry_filters."):
            snap.setdefault("entry_filters", {})[key.split(".")[-1]] = to
        elif key in ("sl_max_pct", "sl_min_pct", "max_hold_sessions", "initial_sl_atr_mult",
                     "trail_atr_mult"):
            snap[key] = to
        elif key.startswith("bracket_specs."):
            snap.setdefault("approval_bracket_specs", {})[key] = to
        elif key.startswith("models."):
            snap["regime_enabled"] = bool(to)
            snap["regime_approved_by"] = "auto" if to else None
    return snap


def _commit_changes(h: Dict[str, Any]) -> Dict[str, Any]:
    """변경을 대상 파일별로 기록하고 before/after를 돌려준다."""
    groups = _split_changes(h.get("changes") or {})
    out: Dict[str, Any] = {}
    for target, kv in groups.items():
        path = _target_path(target)
        doc = _load(path, {}) or {}
        if target == "strategy_params" and not doc:
            doc = dict(strategy_evolver.load_params())
        doc = json.loads(json.dumps(doc, ensure_ascii=False))
        before = {key: _current_value(target, doc, key) for key in kv}
        for key, value in kv.items():
            _write_value(target, doc, key, value, h)
        if target == "strategy_params":
            strategy_evolver.save_params(doc, note=f"빌드업 {h.get('id')} {h.get('title')} — OOS 게이트 통과")
        else:
            _save(path, doc)
        out[target] = {"path": os.path.relpath(path, BASE_DIR), "before": before, "after": kv}
    return out


def apply(doc: Dict[str, Any], h: Dict[str, Any], by: int = 10) -> Dict[str, Any]:
    """게이트·클램프·하드락을 모두 통과한 가설만 실제 설정에 반영한다."""
    verdict = can_apply(doc, h)
    if not verdict["ok"]:
        gate_pass = (h.get("gate") or {}).get("pass")
        # 게이트를 통과했는데 자동 적용 키가 아니면 '승인 대기'로, 게이트 미통과면 REJECTED.
        if gate_pass and not all(k in AUTO_APPLY for k in (h.get("changes") or {})):
            rec = recommend(doc, h, by=by)
            return {"applied": False, "status": rec["status"], "reasons": verdict["reasons"],
                    "execution_link": rec.get("execution_link")}
        h["status"] = "HELD" if gate_pass else "REJECTED"
        h["decisions"].append({"at_kst": _now_kst(), "decision": h["status"],
                               "by": by, "reasons": verdict["reasons"]})
        return {"applied": False, "status": h["status"], "reasons": verdict["reasons"]}

    # 하드락 재검사 — 파일에 쓰기 전에 '바뀐 상태'를 미리 만들어 본다.
    snap = _project_snapshot(policy_snapshot(), h.get("changes") or {})
    lock = hard_lock_report(snap)
    if not lock["ok"]:
        h["status"] = "REJECTED"
        h["decisions"].append({"at_kst": _now_kst(), "decision": "REJECTED_HARD_LOCK",
                               "by": by, "violations": lock["violations"]})
        return {"applied": False, "status": h["status"],
                "reasons": [v["key"] for v in lock["violations"]]}

    before_params = strategy_evolver.load_params()
    try:
        written = _commit_changes(h)
    except Exception as e:                     # 쓰기 실패는 조용히 삼키지 않는다
        h["status"] = "HELD"
        h["decisions"].append({"at_kst": _now_kst(), "decision": "WRITE_FAILED", "by": by,
                               "reasons": [f"{type(e).__name__}: {e}"]})
        return {"applied": False, "status": h["status"], "reasons": [str(e)]}

    after_params = strategy_evolver.load_params()
    imp = {
        "id": _next_id(doc, "I"), "hypothesis": h["id"], "applied_at_kst": _now_kst(),
        "title": h["title"], "changes": h.get("changes"),
        "before": {"entry_filters": before_params.get("entry_filters")},
        "after": {"entry_filters": after_params.get("entry_filters")},
        "targets": written,
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
        if key.startswith("entry_filters."):
            ratchet[key.split(".")[-1]] = val.get("to")
    doc.setdefault("apply_history", []).append(
        {"hypothesis": h["id"], "at_kst": imp["applied_at_kst"], "changes": h.get("changes"),
         "targets": {t: v["path"] for t, v in written.items()}})
    doc["apply_history"] = doc["apply_history"][-80:]
    h["status"] = "APPLIED"
    h["decisions"].append({"at_kst": _now_kst(), "decision": "APPLIED", "by": by,
                           "improvement": imp["id"], "changes": h.get("changes"),
                           "targets": {t: v["path"] for t, v in written.items()}})
    return {"applied": True, "status": "APPLIED", "improvement": imp, "targets": written}


def recommend(doc: Dict[str, Any], h: Dict[str, Any], by: int = 9) -> Dict[str, Any]:
    """게이트는 통과했지만 자동 적용 키 밖인 후보 — 측정 결과만 남기고 승인 대기."""
    h["status"] = "RECOMMENDED" if (h.get("gate") or {}).get("pass") else "REJECTED"
    h["execution_link"] = execution_link(h)
    h["decisions"].append({
        "at_kst": _now_kst(), "decision": h["status"], "by": by,
        "reasons": (["자동 적용 키가 아님 — 승인 필요: " + str(h["execution_link"].get("switch") or "승인 정본(config)")]
                    if h["status"] == "RECOMMENDED" else [(h.get("gate") or {}).get("reason")]),
    })
    return {"status": h["status"], "execution_link": h["execution_link"]}


# ------------------------------------------------------------------ 실행 연계
# OOS 게이트를 통과한 안건이 '피드에만' 남지 않도록, 각 축이 실제로 바꾸는
# 스위치 위치를 정본으로 둔다. 파일·키를 글로만 적어 두면 사람이 다시 찾아야 하지만,
# 이 표가 있으면 `reconcile_execution()`이 스위치 상태를 읽어 APPLIED/RECOMMENDED를
# 저절로 맞춘다(하드락 충돌·HELD 교착 해소, 2026-11-21).
EXECUTION_LINKS: Dict[str, Dict[str, Any]] = {
    "ENTRY_FILTER": {
        "switch": "data/strategy_params.json → entry_filters",
        "auto": True,
        "effect": "진입 필터(RSI2·%B·RVOL) 조이기 — 다음 10분 사이클부터 집행",
        "approval": "불필요(자동 적용 범위)",
    },
    "BRACKET_SPEC": {
        "switch": "config/swing_approval.json → autonomous_execution.bracket_specs",
        "auto": True,
        "effect": "손절·익절·보유기간 — 게이트 통과 시 자동 반영, 다음 재진입부터 적용",
        "approval": "게이트 통과 시 자동(2026-11-26 지시)",
    },
    "EXIT_SPEC": {
        "switch": "data/strategy_params.json → initial_sl_atr_mult·trail_atr_mult·sl_max_pct·max_hold_sessions",
        "auto": True,
        "effect": "장중 트레일·손절 폭·보유 상한 — 게이트 통과 시 자동 반영",
        "approval": "게이트 통과 시 자동(2026-11-26 지시)",
    },
    "MODEL": {
        "switch": "config/regime_policy.json → enabled (+ approved_by)",
        "auto": True,
        "effect": "V2 레짐 차단·사이징 — 게이트 통과 시 자동 활성화(approved_by에 근거 기록)",
        "approval": "게이트 통과 시 자동(2026-11-26 지시, 화이트리스트 모델만)",
    },
}


def _bracket_specs() -> Dict[str, Any]:
    cfg = _load(POLICY_PATH, {}) or {}
    return (((cfg.get("autonomous_execution") or {}).get("bracket_specs"))
            or (cfg.get("bracket_specs") or {}))


def _switch_state(h: Dict[str, Any]) -> Dict[str, Any]:
    """이 안건이 가리키는 스위치의 **현재** 값과 실효 여부."""
    axis = h.get("axis")
    if axis == "MODEL":
        pol = _load(os.path.join(BASE_DIR, "config", "regime_policy.json"), {}) or {}
        on = bool(pol.get("enabled") is True and str(pol.get("approved_by") or "").strip())
        return {"path": "config/regime_policy.json", "key": "enabled",
                "value": pol.get("enabled"), "approved_by": pol.get("approved_by"),
                "in_effect": on}
    if axis == "BRACKET_SPEC":
        specs = _bracket_specs()
        hits, checked = [], []
        for key, val in (h.get("changes") or {}).items():
            parts = key.split(".")
            if len(parts) < 3:
                continue
            block, field = parts[1], parts[2]
            actual = (specs.get(block) or {}).get(field)
            try:
                same = actual is not None and abs(float(actual) - float(val.get("to"))) < 1e-9
            except (TypeError, ValueError):
                same = actual == val.get("to")
            checked.append({"key": key, "actual": actual, "target": val.get("to"), "same": same})
            hits.append(same)
        return {"path": "config/swing_approval.json", "key": "autonomous_execution.bracket_specs",
                "value": {c["key"]: c["actual"] for c in checked},
                "in_effect": bool(hits) and all(hits), "checked": checked}
    if axis == "ENTRY_FILTER":
        ef = (strategy_evolver.load_params() or {}).get("entry_filters") or {}
        checked, hits = [], []
        for key, val in (h.get("changes") or {}).items():
            field = key.split(".")[-1]
            actual = ef.get(field)
            try:
                same = actual is not None and abs(float(actual) - float(val.get("to"))) < 1e-9
            except (TypeError, ValueError):
                same = actual == val.get("to")
            checked.append({"key": key, "actual": actual, "target": val.get("to"), "same": same})
            hits.append(same)
        return {"path": "data/strategy_params.json", "key": "entry_filters",
                "value": ef, "in_effect": bool(hits) and all(hits), "checked": checked}
    if axis == "EXIT_SPEC":
        params = strategy_evolver.load_params() or {}
        checked, hits = [], []
        for key, val in (h.get("changes") or {}).items():
            field = key.split(".")[-1]
            actual = params.get(field)
            try:
                same = actual is not None and abs(float(actual) - float(val.get("to"))) < 1e-9
            except (TypeError, ValueError):
                same = actual == val.get("to")
            checked.append({"key": key, "actual": actual, "target": val.get("to"), "same": same})
            hits.append(same)
        return {"path": "data/strategy_params.json", "key": "exit_spec",
                "value": {c["key"]: c["actual"] for c in checked},
                "in_effect": bool(hits) and all(hits), "checked": checked}
    return {"in_effect": False, "reason": f"알 수 없는 축 {axis}"}


def execution_link(h: Dict[str, Any]) -> Dict[str, Any]:
    """안건 → 실행 스위치. 게이트 통과 후 '어디를 켜면 되는가'를 수치와 함께 남긴다."""
    meta = dict(EXECUTION_LINKS.get(str(h.get("axis")), {}))
    state = _switch_state(h)
    meta.update(state)
    meta["hypothesis"] = h.get("id")
    meta["gate_pass"] = bool((h.get("gate") or {}).get("pass"))
    if meta.get("gate_pass") and not state.get("in_effect"):
        meta["next_action_ko"] = ("스위치 변경 대기 — " + str(meta.get("switch"))
                                  + f" (승인: {meta.get('approval')})")
    elif state.get("in_effect"):
        meta["next_action_ko"] = "이미 실행 정책에 반영됨"
    else:
        meta["next_action_ko"] = "게이트 미통과 — 적용하지 않는다"
    return meta


def reconcile_execution(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """스위치 상태와 장부 상태를 맞춘다 — HELD/MEASURED 교착을 풀고 실제 적용을 기록.

    * 게이트 통과 + 스위치가 이미 켜져 있으면 → APPLIED(개선 이력 기록)
    * 게이트 통과 + 스위치가 꺼져 있으면 → RECOMMENDED(스위치 경로·변경값 표기)
    * 게이트 미통과 → 건드리지 않는다(측정 결과 보존)
    """
    doc = doc if doc is not None else load()
    changed: List[Dict[str, Any]] = []
    for h in doc.get("hypotheses") or []:
        if h.get("axis") not in EXECUTION_LINKS:
            continue
        if not (h.get("gate") or {}).get("pass"):
            continue
        link = execution_link(h)
        h["execution_link"] = link
        if link.get("in_effect"):
            if h.get("status") == "APPLIED":
                continue
            h["status"] = "APPLIED"
            imp = {
                "id": _next_id(doc, "I"), "hypothesis": h.get("id"), "applied_at_kst": _now_kst(),
                "title": h.get("title"), "changes": h.get("changes"),
                "before": {}, "after": {link.get("key"): link.get("value")},
                "gate": h.get("gate"), "execution_link": link,
                "evidence": {"data_as_of": (h.get("measurement") or {}).get("data_as_of"),
                             "oos": ((h.get("measurement") or {}).get("candidate") or {}).get("oos")},
                "by": 0, "sync": "execution_switch_observed",
            }
            doc.setdefault("improvements", []).append(imp)
            h.setdefault("decisions", []).append(
                {"at_kst": _now_kst(), "decision": "APPLIED", "by": 0,
                 "improvement": imp["id"], "switch": link.get("path"),
                 "note": "스위치가 켜진 것을 확인해 장부를 APPLIED로 맞췄습니다."})
            changed.append({"id": h.get("id"), "to": "APPLIED"})
        elif h.get("status") in ("MEASURED", "HELD", "PROPOSED"):
            h["status"] = "RECOMMENDED"
            h.setdefault("decisions", []).append(
                {"at_kst": _now_kst(), "decision": "RECOMMENDED", "by": 0,
                 "reasons": [f"게이트 통과 — 실행 스위치 {link.get('path')} 승인 대기"],
                 "switch": link.get("path")})
            changed.append({"id": h.get("id"), "to": "RECOMMENDED"})
    if changed:
        doc["execution_reconciled_at_kst"] = _now_kst()
    return {"changed": changed, "count": len(changed),
            "pending": pending_approvals(doc)}


def pending_approvals(doc: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """게이트는 통과했는데 스위치가 꺼져 있어 기다리는 안건 — 대시보드·피드용."""
    doc = doc or load()
    out = []
    for h in doc.get("hypotheses") or []:
        if not (h.get("gate") or {}).get("pass"):
            continue
        link = h.get("execution_link") or execution_link(h)
        if link.get("in_effect") or h.get("status") == "APPLIED":
            continue
        out.append({"id": h.get("id"), "title": h.get("title"), "axis": h.get("axis"),
                    "status": h.get("status"), "switch": link.get("switch"),
                    "path": link.get("path"), "current": link.get("value"),
                    "changes": h.get("changes"),
                    "oos_ev_pct": (((h.get("measurement") or {}).get("candidate") or {})
                                   .get("oos") or {}).get("ev_pct")})
    return out


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
        # 게이트는 통과했는데 실행 스위치가 꺼져 있어 기다리는 안건(스위치 경로 포함).
        "execution_pending": pending_approvals(doc)[-5:],
        "execution_links": EXECUTION_LINKS,
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
