"""판단 근거 기록(decision attribution) — 어떤 연구·업무 결과가 실제 주문에 반영됐는가.

동훈님 지시(2026-11-21 13:53): "실전 주문에도 이러한 판단의 결과. 업무의 결과들이
적용되어야해."  그러려면 주문 1건마다 **어떤 판단이 수치를 움직였는지**가 남아야 한다.
그동안 CUBE-M8·V2 레짐·10인 협업 결과는 문서·피드·대시보드에만 적히고, 주문에는
"예산 × VIX 계수"만 남았다(사후에 '왜 이 수량인가'를 재구성할 수 없었다).

이 모듈은 판단 사슬을 다음 다섯 갈래로 나눠 한 딕셔너리에 담는다.

1. `macro8`   — CUBE-M8 복합 z·국면·노출계수. `applied_scale`이 1.0이면 shadow(주문 불변).
2. `regime_v2`— REGIME_ADAPTIVE_DIP_V1의 국면·플레이북. `mode`가 shadow면 참고만.
3. `buildup`  — 10인 빌드업이 바꾼 진입 필터(래칫)·적용 이력·하드락 상태.
4. `risk`     — VIX 게이트·일일 손실한도·현금 마지노선·예산 구성(binding).
5. `session`  — 단계(정규장/프리장)·호가유형·프리장 방어 조건.

각 갈래는 `applied`(실제로 수량을 바꿨는가)를 명시한다 — 참고용(shadow)과 실제 반영을
섞지 않기 위해서다. 주문 레코드의 `context.attribution`과 `data/autotrader_status.json`
`decision_attribution`이 같은 구조를 쓴다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")

KST = timedelta(hours=9)

SOURCES: Dict[str, str] = {
    "macro8": "config/macro_leading_policy.json + data/macro_leading.json",
    "regime_v2": "config/regime_policy.json + core/regime_model.py",
    "buildup": "data/strategy_buildup.json + data/strategy_params.json",
    "approval": "config/swing_approval.json (entry_conditions·guardrails)",
    "risk": "config/risk_guardrails.json + config/live_execution_gate.json",
    "session": "config/premarket_policy.json + core/session_routing.py",
}


def _now() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def buildup_block(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """빌드업·승인 조건이 지금 진입 필터에 어떻게 반영돼 있는가."""
    doc = _load(os.path.join(DATA, "strategy_buildup.json"), {}) or {}
    imps = doc.get("improvements") or []
    last = imps[-1] if imps else {}
    lock = doc.get("hard_lock") or {}
    entry_filters = ((params or {}).get("entry_filters") or {}) if isinstance(params, dict) else {}
    return {
        "entry_filters": entry_filters,
        "entry_filters_applied": bool(entry_filters),
        "ratchet": doc.get("ratchet"),
        "hypotheses": (doc.get("counters") or {}).get("hypotheses"),
        "applied_count": (doc.get("counters") or {}).get("applied"),
        "last_improvement": ({k: last.get(k) for k in ("id", "title", "applied_at_kst")} if last else None),
        "hard_lock_ok": lock.get("ok"),
        "hard_lock_violations": lock.get("violations") or [],
        "source": SOURCES["buildup"],
    }


def macro8_block(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    meta = meta or {}
    applied = float(meta.get("applied_scale", 1.0) or 1.0)
    return {
        "mode": meta.get("mode") or "unavailable",
        "z": meta.get("z"),
        "regime": meta.get("regime"),
        "regime_ko": meta.get("regime_ko"),
        "shadow_scale": meta.get("shadow_scale"),
        "applied_scale": applied,
        "used_in_sizing": bool(abs(applied - 1.0) > 1e-9),
        "as_of": meta.get("as_of"),
        "error": meta.get("error"),
        "source": SOURCES["macro8"],
    }


def regime_block(v2: Optional[Dict[str, Any]], gate: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    v2 = v2 or {}
    gate = gate or {}
    scale = float(gate.get("scale", 1.0) or 1.0)
    blocked = bool(gate.get("block_long"))
    return {
        "model": v2.get("model"),
        "regime": v2.get("regime") or v2.get("error"),
        "vix": v2.get("vix"),
        "spy_trend": v2.get("spy_trend"),
        "playbook": v2.get("playbook"),
        "mode": gate.get("mode") or "shadow",
        "applied_scale": scale,
        "block_long": blocked,
        "used_in_sizing": bool(abs(scale - 1.0) > 1e-9),
        "used_in_gating": blocked,
        "reason": gate.get("reason"),
        "source": SOURCES["regime_v2"],
    }


def risk_block(budget_info: Optional[Dict[str, Any]], *, vix: Any, vix_scale: float,
               strategy_scale: float, loss_limit_usd: Any, cash_floor_usd: Any,
               sell_only: bool = False, integrity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    integrity = integrity or {}
    return {
        "vix_gate": ((integrity.get("vix") or {}).get("gate") if isinstance(integrity.get("vix"), dict)
                     else None) or vix,
        "vix_scale": round(float(vix_scale or 0.0), 3),
        "strategy_risk_scale": round(float(strategy_scale or 1.0), 3),
        "budget": budget_info or {},
        "daily_loss_limit_usd": loss_limit_usd,
        "cash_floor_usd": cash_floor_usd,
        "sell_only": bool(sell_only),
        "integrity_ok": integrity.get("ok"),
        "source": SOURCES["risk"],
    }


def session_block(plan: Optional[Dict[str, Any]], clock: Dict[str, Any],
                  order_type: Optional[str]) -> Dict[str, Any]:
    plan = plan or {}
    phase = str((clock or {}).get("phase") or "")
    return {
        "phase": phase,
        "order_type": order_type,
        "order_type_name": {"00": "정규장 지정가", "61": "프리마켓 지정가",
                            "62": "애프터마켓 지정가"}.get(order_type or "", None),
        "premarket_enabled": bool(plan.get("premarket_enabled")),
        "after_hours_enabled": bool(plan.get("after_hours_enabled")),
        "allowed_phases": plan.get("phases") or ["REGULAR"],
        "entry_conditions": plan.get("entry_conditions") or {},
        "source": SOURCES["session"],
    }


def build(*, clock: Dict[str, Any], params: Optional[Dict[str, Any]], plan: Optional[Dict[str, Any]],
          order_type: Optional[str], macro_meta: Optional[Dict[str, Any]],
          regime_v2: Optional[Dict[str, Any]], regime_gate: Optional[Dict[str, Any]],
          budget_info: Optional[Dict[str, Any]], vix: Any, vix_scale: float,
          loss_limit_usd: Any = None, cash_floor_usd: Any = None,
          sell_only: bool = False, integrity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """한 사이클의 판단 근거 묶음(주문 없음)."""
    r = risk_block(budget_info, vix=vix, vix_scale=vix_scale,
                   strategy_scale=float((params or {}).get("risk_scale") or 1.0),
                   loss_limit_usd=loss_limit_usd, cash_floor_usd=cash_floor_usd,
                   sell_only=sell_only, integrity=integrity)
    return {
        "generated_at_kst": _now(),
        "session": session_block(plan, clock, order_type),
        "macro8": macro8_block(macro_meta),
        "regime_v2": regime_block(regime_v2, regime_gate),
        "buildup": buildup_block(params),
        "risk": r,
        "sources": SOURCES,
        "summary_ko": _summary(clock, order_type, macro_meta, regime_v2, regime_gate, r, params),
    }


def _summary(clock: Dict[str, Any], order_type: Optional[str],
             macro_meta: Optional[Dict[str, Any]], regime_v2: Optional[Dict[str, Any]],
             regime_gate: Optional[Dict[str, Any]], risk: Dict[str, Any],
             params: Optional[Dict[str, Any]]) -> str:
    m8 = macro8_block(macro_meta)
    rg = regime_block(regime_v2, regime_gate)
    ef = ((params or {}).get("entry_filters") or {})
    phase = (clock or {}).get("phase")
    parts = [f"세션 {phase}·호가유형 {order_type or '-'}"]
    parts.append(f"M8 {m8['mode']}(z {m8['z']}, 계수 {m8['applied_scale']})")
    parts.append(f"V2 {rg['regime']}({rg['mode']}"
                 + (f", 롱차단" if rg["block_long"] else "")
                 + (f", 계수 {rg['applied_scale']}" if rg["used_in_sizing"] else "") + ")")
    if ef:
        parts.append(f"진입필터 RSI2≤{ef.get('rsi2_max')}/%B≤{ef.get('pct_b_max')}/RVOL≥{ef.get('rvol_min')}")
    parts.append(f"예산 ${(risk.get('budget') or {}).get('budget_usd')}"
                 f" (VIX {risk.get('vix_gate')} × {risk.get('vix_scale')}"
                 f" × 전략 {risk.get('strategy_risk_scale')})")
    return " · ".join(parts)


def sizing_trace(cand: Dict[str, Any], sizing: Dict[str, Any],
                 quant: Optional[Dict[str, Any]] = None,
                 gates: Optional[List[str]] = None) -> Dict[str, Any]:
    """후보 1건이 왜 그 수량이 됐는가 — 주문 레코드에 붙인다."""
    q = quant or {}
    return {
        "ticker": cand.get("ticker"),
        "source": cand.get("source"),
        "qty": sizing.get("qty"),
        "notional_usd": sizing.get("notional_usd"),
        "budget_usd": sizing.get("budget_usd"),
        "sl_pct": sizing.get("sl_pct"),
        "risk_usd": sizing.get("risk_usd"),
        "risk_cap_usd": sizing.get("risk_cap_usd"),
        "min_one_share": sizing.get("min_one_share"),
        "quant_approved_qty": q.get("approved_qty"),
        "quant_reasons": q.get("reasons"),
        "gate_notes": gates or [],
    }


def write(path: Optional[str] = None) -> str:
    p = path or os.path.join(DATA, "decision_attribution.json")
    return p
