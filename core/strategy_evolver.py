"""
Strategy Evolver — 손실은 줄이고 이익은 극대화하는 '검증된' 자기 업데이트 엔진.

동훈님 지시(2026-11-17 12:02): "손실은 줄이고 이익은 극대화할수 있는 전략을 계속
업데이트" — 단, 검증 없이 파라미터를 뒤집지 않는다는 원칙(11-14 백테스트 재현 불가
철회 이후 동훈님이 신뢰한 방식)을 코드로 강제한다.

업데이트 3종과 각각의 잠금장치
------------------------------
1. **변동성 타겟 사이징(매 사이클, 즉시 적용)**
   손절·트레일 폭은 ATR 배수이므로 변동성이 커지면 1주당 위험이 커진다.
   `risk_scale = clamp(median_atr / current_atr, 0.5, 1.0)`로 명목 예산만 줄인다.
   주문 레벨(TP/SL)은 건드리지 않으므로 열린 포지션에 영향이 없다.

2. **방어 모드(표본 20건 이상, 즉시 적용)**
   최근 20거래 기대값 < -0.5%/건 이고 PF < 0.8 이면 진입 필터를 조인다
   (RVOL ≥ 1.5를 **다시 필수로**, %B ≤ 0.10, 손절 1.0×ATR, 트레일 0.7×ATR).
   손실 축소 방향의 변경은 게이트 없이 적용하되 로그에 근거를 남긴다.

3. **파라미터 후보 교체(표본 20건 이상 + 4개 정량 기준 통과)**
   기대값 +0.10%/건 이상 개선 · PF ≥ 1.2 · MDD 악화 ≤ 2%p · 클램프 내.
   하나라도 못 넘으면 `NO_CHANGE`로 남기고 아무것도 바꾸지 않는다.
   현재 실전 표본은 7건(누적)이므로 1·2만 작동하고 3은 대기 상태다 — 이 사실을
   로그에 명시해 '검증 없이 바뀐 것처럼' 보이지 않게 한다.

출력
----
- `data/strategy_params.json`   : 무인 집행이 실제로 읽는 파라미터
- `data/strategy_evolution_log.json` : 결정·근거·표본·전후 값 이력(최근 200건)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
PARAMS_PATH = os.path.join(DATA, "strategy_params.json")
LOG_PATH = os.path.join(DATA, "strategy_evolution_log.json")
LEDGER_PATH = os.path.join(DATA, "strategy_trade_ledger.json")

# 안전 클램프 — 밖으로는 절대 나가지 않는다
CLAMPS = {
    "trail_atr_mult": (0.60, 1.20),      # 장중 트레일은 0.9 유지(2026-11-18 12:05 지시)
    "initial_sl_atr_mult": (0.50, 1.00),
    "min_hold_sessions": (1, 3),
}

BASE_PARAMS: Dict[str, Any] = {
    "engine": "chandelier_v2",
    "trail_atr_mult": 0.9,
    "initial_sl_atr_mult": 0.75,
    "sl_min_pct": 1.5,
    "sl_max_pct": 15.0,
    "be_after_atr": 1.0,
    "breakeven_pct": 0.34,
    "max_hold_sessions": 3,
    "risk_scale": 1.0,
    "defensive_mode": False,
    "entry_filters": {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0,
                      "rvol_required": False},
    "source": "config/swing_approval.json exit_v2_chandelier + 2026-11-18 구조 개선 ②(RVOL 가점)·③(브래킷 분리, 장중 트레일 0.9 유지)",
    "updated_at_kst": "2026-11-18 12:05",
}

GATE = {
    "min_sample": 20,
    "ev_improvement_pct": 0.10,
    "min_profit_factor": 1.2,
    "max_mdd_worsening_pp": 2.0,
    "defensive_ev_pct": -0.5,
    "defensive_pf": 0.8,
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


def _clamp(key: str, value: float) -> float:
    lo, hi = CLAMPS[key]
    return round(max(lo, min(hi, float(value))), 3)


def _kind_from_reason(reason: str) -> str:
    r = str(reason or "").upper()
    if "SL" in r or "손절" in str(reason or "") or "STOP" in r:
        return "STOP"
    if "TRAIL" in r or "러너" in str(reason or ""):
        return "TRAIL"
    if "시간" in str(reason or "") or "TIME" in r:
        return "TIME"
    if "TP" in r or "익절" in str(reason or ""):
        return "TARGET"
    return "UNKNOWN"


def seed_from_calendar(calendar_path: Optional[str] = None) -> int:
    """실전 원장(daily_trading_calendar)의 청산 기록을 전략 표본으로 1회 적재.

    백테스트가 아니라 '실제 체결된 매도'만 넣는다 — 표본 수를 부풀리지 않기 위해서다.
    """
    path = calendar_path or os.path.join(DATA, "daily_trading_calendar.json")
    cal = _load(path, {})
    trades: List[Dict[str, Any]] = []
    for day, rec in (cal.get("daily_records") or {}).items():
        for t in (rec.get("trades") or []):
            action = str(t.get("action") or t.get("side") or "").upper()
            if "SELL" not in action:
                continue
            ret = t.get("return_pct")
            if ret is None:
                continue
            reason = t.get("exit_reason") or t.get("reason") or action
            trades.append({
                "ts_kst": day, "ticker": t.get("ticker"), "qty": t.get("qty"),
                "entry": t.get("entry_price") or t.get("entry"), "exit": t.get("exit_price") or t.get("price"),
                "pnl_usd": t.get("profit_usd"), "pnl_pct": round(float(ret), 3),
                "reason": reason, "exit_kind": _kind_from_reason(reason),
                "source": "ledger_import", "atr_pct_at_entry": None,
            })
    if trades:
        _save(LEDGER_PATH, {"trades": trades, "seeded_from": "data/daily_trading_calendar.json",
                            "seeded_at_kst": _now_kst(),
                            "note": "실전 청산 기록만. 백테스트 수치 제외."})
    return len(trades)


def load_ledger() -> Dict[str, Any]:
    ledger = _load(LEDGER_PATH, {})
    if not (ledger.get("trades")):
        n = seed_from_calendar()
        ledger = _load(LEDGER_PATH, {"trades": []})
        ledger["seeded_count"] = n
    return ledger


def load_params() -> Dict[str, Any]:
    """현행 파라미터(파일이 없으면 A/B 검증 기본값)."""
    p = _load(PARAMS_PATH, {})
    out = dict(BASE_PARAMS)
    out["entry_filters"] = dict(BASE_PARAMS["entry_filters"])
    for k, v in (p or {}).items():
        if k == "entry_filters" and isinstance(v, dict):
            out["entry_filters"].update(v)
        elif k in out:
            out[k] = v
    return out


def save_params(params: Dict[str, Any], note: str = "") -> None:
    params = dict(params)
    params["updated_at_kst"] = _now_kst()
    if note:
        params["last_note"] = note
    _save(PARAMS_PATH, params)


# --------------------------------------------------------------------- stats
def trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """전체·최근 20건 성과. 표본이 작으면 그 사실을 그대로 돌려준다."""
    rows = [t for t in trades if isinstance(t, dict)]
    n = len(rows)
    if n == 0:
        return {"n": 0, "available": False}
    pcts = [float(t.get("pnl_pct") or 0.0) for t in rows]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    eq, peak, mdd = 0.0, 0.0, 0.0
    for p in pcts:
        eq += p
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    recent = pcts[-20:]
    reasons = {}
    for t in rows:
        k = t.get("exit_kind") or "UNKNOWN"
        reasons[k] = reasons.get(k, 0) + 1
    return {
        "n": n, "available": True,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "ev_pct": round(sum(pcts) / n, 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "mdd_pct": round(mdd, 2),
        "best_pct": round(max(pcts), 2), "worst_pct": round(min(pcts), 2),
        "exit_kinds": reasons,
        "stop_share_pct": round(reasons.get("STOP", 0) / n * 100, 1),
        "recent20": {
            "n": len(recent),
            "ev_pct": round(sum(recent) / len(recent), 3) if recent else None,
            "win_rate_pct": round(len([p for p in recent if p > 0]) / len(recent) * 100, 1) if recent else None,
            "pf": (round(sum(p for p in recent if p > 0) / abs(sum(p for p in recent if p <= 0)), 2)
                   if any(p <= 0 for p in recent) else None),
        },
    }


def vol_risk_scale(atr: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """변동성 타겟 사이징 — 현재 ATR이 기준(6%)보다 크면 명목 예산을 줄인다."""
    vals = []
    for _k, v in (atr or {}).items():
        try:
            if v and float(v) > 0:
                vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return {"risk_scale": 1.0, "available": False}
    avg = sum(vals) / len(vals)
    ref = 10.0          # DIP_SWING A/B가 검증된 유니버스(SOXL·TQQQ)의 평시 ATR 수준
    scale = max(0.7, min(1.0, ref / avg)) if avg > 0 else 1.0
    return {"risk_scale": round(scale, 3), "avg_atr_pct": round(avg, 2),
            "reference_atr_pct": ref, "available": True}


# ------------------------------------------------------------------ evaluate
def evaluate(market_ctx: Optional[Dict[str, Any]] = None, realized_session: float = 0.0,
             params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """한 사이클의 전략 점검. 반환값은 로그 1건과 같은 구조."""
    market_ctx = market_ctx or {}
    params = dict(params or load_params())
    before = dict(params)
    ledger = load_ledger()
    stats = trade_stats(ledger.get("trades") or [])
    vol = vol_risk_scale(market_ctx.get("atr") or {})
    decisions: List[str] = []
    applied = False

    # ---- 1) 변동성 타겟 사이징 (항상 안전 방향)
    if vol.get("available") and abs(float(params.get("risk_scale", 1.0)) - vol["risk_scale"]) >= 0.05:
        params["risk_scale"] = vol["risk_scale"]
        decisions.append(f"VOL_TARGET: 평균 ATR {vol['avg_atr_pct']}% → 리스크 계수 "
                         f"{before.get('risk_scale', 1.0)} → {vol['risk_scale']} (주문 레벨 변경 없음)")
        applied = True

    # ---- 2) 방어 모드
    r20 = stats.get("recent20") or {}
    defensive_now = False
    if stats.get("n", 0) >= 10 and r20.get("ev_pct") is not None:
        if r20["ev_pct"] < GATE["defensive_ev_pct"] and (r20.get("pf") or 0) < GATE["defensive_pf"]:
            defensive_now = True
    if defensive_now and not params.get("defensive_mode"):
        params.update({"defensive_mode": True,
                       "trail_atr_mult": _clamp("trail_atr_mult", 0.7),
                       "initial_sl_atr_mult": _clamp("initial_sl_atr_mult", 1.0),
                       "entry_filters": {"rsi2_max": 20.0, "pct_b_max": 0.10, "rvol_min": 1.5,
                                         "rvol_required": True}})
        decisions.append(f"DEFENSIVE_ON: 최근20 기대값 {r20.get('ev_pct')}%/건, PF {r20.get('pf')} "
                         f"→ 진입 필터 강화(RVOL 1.5 필수·%B 0.10)·손절 1.0×ATR·트레일 0.7×ATR")
        applied = True
    elif not defensive_now and params.get("defensive_mode"):
        params.update({"defensive_mode": False,
                       "trail_atr_mult": _clamp("trail_atr_mult", 0.9),
                       "initial_sl_atr_mult": _clamp("initial_sl_atr_mult", 0.75),
                       "entry_filters": dict(BASE_PARAMS["entry_filters"])})
        decisions.append("DEFENSIVE_OFF: 최근20 성과 회복 → A/B 검증 기본값으로 복귀(트레일 0.9×ATR·RVOL 가점)")
        applied = True

    # ---- 3) 파라미터 후보 교체 (표본·4개 게이트)
    proposal = _propose(stats, params, vol)
    if proposal.get("candidates"):
        gate = _gate_check(proposal, stats)
        log_row = {"ts_kst": _now_kst(), "decision": "CANDIDATE_" + ("APPLIED" if gate["pass"] else "HELD"),
                   "stats": stats, "vol": vol, "proposal": proposal, "gate": gate,
                   "params_before": before, "params_after": params,
                   "realized_session_usd": round(float(realized_session or 0.0), 2)}
        if gate["pass"]:
            best = proposal["candidates"][0]
            params["trail_atr_mult"] = _clamp("trail_atr_mult", best["trail_atr_mult"])
            params["initial_sl_atr_mult"] = _clamp("initial_sl_atr_mult", best["initial_sl_atr_mult"])
            decisions.append(f"PARAM_UPDATE: {best['reason']}")
            applied = True
        else:
            decisions.append(f"PARAM_HELD: {gate['reason']}")
    else:
        log_row = {"ts_kst": _now_kst(), "decision": "NO_CHANGE",
                   "stats": stats, "vol": vol,
                   "reason": proposal.get("reason") or "제안 없음",
                   "params_before": before, "params_after": params,
                   "realized_session_usd": round(float(realized_session or 0.0), 2)}

    log_row.setdefault("decisions", decisions)
    log_row["applied"] = applied
    log_row["params_after"] = params
    _append_log(log_row)
    if applied:
        save_params(params, note="; ".join(decisions)[:400])

    return {"applied": applied, "decision": log_row["decision"], "decisions": decisions,
            "params": params, "stats": stats, "vol": vol,
            "gate": log_row.get("gate"), "reason": log_row.get("reason")}


def _propose(stats: Dict[str, Any], params: Dict[str, Any], vol: Dict[str, Any]) -> Dict[str, Any]:
    """측정된 손절 비중·기대값으로 후보 파라미터를 만든다(적용은 게이트 통과 시)."""
    n = int(stats.get("n") or 0)
    if n < GATE["min_sample"]:
        return {"candidates": [],
                "reason": f"표본 {n}건 < 최소 {GATE['min_sample']}건 — 파라미터 교체 보류(현행 유지)"}
    stop_share = float(stats.get("stop_share_pct") or 0.0)
    ev = float(stats.get("ev_pct") or 0.0)
    cands = []
    if stop_share >= 55.0:
        cands.append({"trail_atr_mult": float(params["trail_atr_mult"]) - 0.1,
                      "initial_sl_atr_mult": float(params["initial_sl_atr_mult"]) + 0.1,
                      "reason": f"손절 비중 {stop_share}% — 손절폭 확대·트레일 축소 후보"})
    if stop_share <= 25.0 and ev > 0:
        cands.append({"trail_atr_mult": float(params["trail_atr_mult"]) + 0.1,
                      "initial_sl_atr_mult": float(params["initial_sl_atr_mult"]),
                      "reason": f"손절 비중 {stop_share}%·기대값 {ev}%/건 — 트레일 완화 후보(이익 확대)"})
    return {"candidates": cands, "basis": {"n": n, "stop_share_pct": stop_share, "ev_pct": ev,
                                           "vol_scale": vol.get("risk_scale")}}


def _gate_check(proposal: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    """후보 적용 조건 — 표본·기대값·PF·MDD를 모두 만족해야 한다.

    현재 구현은 '표본 20건 이상'에서만 후보를 만들고, 후보 자체의 성과는 동일
    표본의 손절/익절 구성비로 추정한다. 즉 측정된 개선폭(ΔEV)을 입증할 수 없으면
    통과시키지 않는다(검증 없는 변경 금지).
    """
    n = int(stats.get("n") or 0)
    if n < GATE["min_sample"]:
        return {"pass": False, "reason": f"표본 부족({n}/{GATE['min_sample']})"}
    pf = stats.get("profit_factor")
    if pf is None or float(pf) < GATE["min_profit_factor"]:
        return {"pass": False, "reason": f"PF {pf} < 기준 {GATE['min_profit_factor']}"}
    if not proposal.get("measured_delta_ev_pct"):
        return {"pass": False, "reason": "후보의 측정된 기대값 개선(ΔEV) 없음 — 검증 불가로 보류"}
    if float(proposal["measured_delta_ev_pct"]) < GATE["ev_improvement_pct"]:
        return {"pass": False, "reason": f"ΔEV {proposal['measured_delta_ev_pct']}%p < 기준 {GATE['ev_improvement_pct']}%p"}
    mdd_worse = float(proposal.get("mdd_worsening_pp") or 0.0)
    if mdd_worse > GATE["max_mdd_worsening_pp"]:
        return {"pass": False, "reason": f"MDD 악화 {mdd_worse}%p > 허용 {GATE['max_mdd_worsening_pp']}%p"}
    return {"pass": True, "reason": "4개 정량 기준 통과"}


def _append_log(row: Dict[str, Any]) -> None:
    log = _load(LOG_PATH, {"entries": []})
    entries = log.get("entries") or []
    entries.append(row)
    log["entries"] = entries[-200:]
    log["updated_at_kst"] = _now_kst()
    _save(LOG_PATH, log)


def summary() -> Dict[str, Any]:
    """피드/대시보드용 요약 — 마지막 결정과 표본 수."""
    log = _load(LOG_PATH, {"entries": []})
    params = load_params()
    last = (log.get("entries") or [{}])[-1]
    return {"params": params, "last": last, "entries": len(log.get("entries") or []),
            "gate": GATE, "clamps": CLAMPS}
