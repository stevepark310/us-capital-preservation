"""세션 단계별 주문 라우팅 — 정규장(00)·프리마켓(61)·애프터마켓(62)을 한 곳에서 정한다.

왜 필요한가 (2026-11-21 동훈님 지시)
------------------------------------
"실전 주문에도 이러한 판단의 결과. 업무의 결과들이 적용되어야해. 필요시 프리장이나
본장에 개입해서 바로바로 매매할 수 있게 해줘"

종전에는 네 겹으로 프리장이 막혀 있었다.
  1. `config/premarket_policy.json` enabled=false
  2. `core/buildup.HARD_LOCK`의 `premarket_enabled == False`
  3. `core/live_session_gate` 의 `phase != "REGULAR"` 차단
  4. `core/autotrader` 의 `phase != "REGULAR"` 차단

이 모듈은 ③④의 판정 규칙을 **한 곳**으로 모은다. 스위치(`premarket_policy.enabled`)와
사용자 승인 기록(`approved_by`·`approved_at_kst`)이 있으면 `PRE_MARKET`이 정상 세션이
되고, 없으면 종전과 똑같이 정규장만 열린다(fail-closed).

NH 해외주식 호가유형
--------------------
* `00` 정규장 지정가 — 기본값(기존 동작)
* `61` 프리마켓 지정가 — ET 04:00~09:30, **price > 0 필수**
* `62` 애프터마켓 지정가 — ET 16:00~20:00 (`after_hours.enabled`를 켜야 사용)

프리장 얇은 호가 방어
---------------------
프리장은 호가 공백·스프레드 확대가 일상이다. 그래서 프리장 주문에는
① 지정가 슬리피지 상한(`max_slippage_bps`, 기본 15bp) ② 1회/세션 예산 상한
③ 세션 주문 건수 상한 ④ 추격 금지 밴드(전일 종가 대비 +`max_gap_chase_pct` 초과면 매수 금지)
⑤ 09:00 ET 이후 신규 진입 금지(개장 동시호가 회피)를 건다. 정규장 주문은 종전 규칙 그대로다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREMARKET_POLICY_PATH = os.path.join(BASE_DIR, "config", "premarket_policy.json")

KST = timedelta(hours=9)

# 단계 → NH 호가유형 코드
SESSION_ORDER_TYPES: Dict[str, str] = {"REGULAR": "00", "PRE_MARKET": "61", "POST_MARKET": "62"}
ORDER_TYPE_NAMES: Dict[str, str] = {"00": "정규장 지정가", "61": "프리마켓 지정가", "62": "애프터마켓 지정가"}

# 프리장 자동 진입 조건 기본값 — `config/premarket_policy.json entry_conditions`가 정본이고
# 이 값은 파일에 키가 없을 때의 폴백이다. 승인 전에는 어차피 enabled=false라 쓰이지 않는다.
DEFAULT_ENTRY_CONDITIONS: Dict[str, Any] = {
    # 2026-11-21 백테스트: 헤지(SOXS·SQQQ)의 프리장 진입은 정규장 시가 대비 OOS -0.89%p
    # (t -2.02)로 유의하게 나빴다 → 헤지는 개장 후에만 진입한다. DIP 신호만 허용.
    "allow_sources": ["DIP_SWING_DAILY"],
    "block_regimes": ["CRISIS"],
    "allow_risk_off": False,
    "max_gap_chase_pct": 1.0,
    "window_et": ["04:00", "09:00"],
    "max_budget_usd": 60.0,
    "max_orders_per_session": 2,
    "max_slippage_bps": 15,
    "min_price_usd": 5.0,
    "max_quote_age_min": 6.0,
}

DEFAULT_AFTER_HOURS: Dict[str, Any] = {
    "enabled": False,
    "window_et": ["16:00", "20:00"],
    "max_budget_usd": 40.0,
    "max_orders_per_session": 1,
    "max_slippage_bps": 25,
}


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """`config/premarket_policy.json` — 없거나 깨졌으면 '닫힘' 기본값."""
    try:
        with open(path or PREMARKET_POLICY_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
        return doc
    except Exception as exc:
        return {"enabled": False, "load_error": type(exc).__name__}


def _parse_kst(ts: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(ts or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.fromisoformat(str(ts))
        except Exception:
            return None


def approval_state(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """프리장 실주문을 켜도 되는지 — 스위치 + **사용자 승인 기록**이 모두 있어야 한다.

    `enabled: true`만 있고 `approved_by`/`approved_at_kst`가 없으면 켜지지 않는다.
    코드가 스스로 프리장을 여는 경로를 만들지 않기 위한 잠금이다(2026-11-21).
    """
    pol = policy if policy is not None else load_policy()
    if pol.get("enabled") is not True:
        return {"approved": False, "enabled": False,
                "reason": pol.get("reason") or "premarket_policy.enabled=false"}
    approver = str(pol.get("approved_by") or "").strip()
    at = _parse_kst(pol.get("approved_at_kst"))
    if not approver or at is None:
        return {"approved": False, "enabled": True,
                "reason": "프리장 enabled=true 이지만 사용자 승인 기록(approved_by·approved_at_kst)이 없습니다."}
    return {"approved": True, "enabled": True, "approved_by": approver,
            "approved_at_kst": pol.get("approved_at_kst")}


def entry_conditions(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pol = policy if policy is not None else load_policy()
    cond = dict(DEFAULT_ENTRY_CONDITIONS)
    cond.update(pol.get("entry_conditions") or {})
    return cond


def after_hours(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pol = policy if policy is not None else load_policy()
    cfg = dict(DEFAULT_AFTER_HOURS)
    cfg.update(pol.get("after_hours") or {})
    return cfg


def status(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """세션 라우팅 상태 요약 — 게이트·집행기·대시보드가 같은 판정을 쓴다."""
    pol = policy if policy is not None else load_policy()
    appr = approval_state(pol)
    phases: List[str] = ["REGULAR"]
    reasons: List[str] = []
    if appr["approved"]:
        phases.append("PRE_MARKET")
    else:
        reasons.append(appr["reason"])
    ah = after_hours(pol)
    if appr["approved"] and ah.get("enabled") is True:
        phases.append("POST_MARKET")
    return {
        "premarket_enabled": bool(appr["approved"]),
        "premarket_switch": bool(pol.get("enabled") is True),
        "after_hours_enabled": bool(ah.get("enabled") is True),
        "phases": phases,
        "order_types": {p: SESSION_ORDER_TYPES[p] for p in phases},
        "approved_by": appr.get("approved_by"),
        "approved_at_kst": appr.get("approved_at_kst"),
        "blocked_reason": "; ".join(reasons) or None,
        "entry_conditions": entry_conditions(pol),
        "after_hours": ah,
        "policy_path": PREMARKET_POLICY_PATH if policy is None else None,
        "version": pol.get("version"),
        "validation": pol.get("validation"),
    }


def default_plan() -> Dict[str, Any]:
    """정규장만 허용하는 기본 계획(프리장 파일을 읽지 않는 안전 기본값)."""
    return {"premarket_enabled": False, "after_hours_enabled": False,
            "phases": ["REGULAR"], "order_types": {"REGULAR": "00"},
            "entry_conditions": dict(DEFAULT_ENTRY_CONDITIONS),
            "after_hours": dict(DEFAULT_AFTER_HOURS),
            "blocked_reason": "정규장만 허용(기본 계획)"}


def allowed_phases(policy: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    return tuple(status(policy)["phases"])


def order_type_for_phase(phase: Optional[str], plan: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """이 단계에서 쓸 NH 호가유형. 이 계획이 허용하지 않는 단계면 None."""
    plan = plan if plan is not None else default_plan()
    ph = str(phase or "")
    if ph not in (plan.get("phases") or []):
        return None
    return (plan.get("order_types") or {}).get(ph)


# ------------------------------------------------------------------ 세션 분
def _et_minutes(clock: Dict[str, Any]) -> Optional[int]:
    raw = str((clock or {}).get("now_et") or "")[11:16]
    if ":" not in raw:
        return None
    try:
        h, m = raw.split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _kst_minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    total = int(h) * 60 + int(m)
    return total + 24 * 60 if int(h) < 12 else total


def _hm_minutes(hhmm: str) -> int:
    """'HH:MM' → 그날의 분(자정 보정 없음 — ET 창 판정용)."""
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


def phase_minutes(clock: Dict[str, Any]) -> Optional[int]:
    """현재 단계의 '개시 이후 경과 분'.

    REGULAR: 23:30 KST(EST 기준) 개장부터, 서머타임은 `kst_shift_min`으로 보정.
    PRE_MARKET: ET 04:00부터(서머타임 무관, ET 벽시계 사용).
    """
    phase = str((clock or {}).get("phase") or "")
    if phase == "REGULAR":
        now = _kst_minutes(str((clock or {}).get("now_kst") or "")[11:16] or "00:00")
        shift = int((clock or {}).get("kst_shift_min") or 0)
        return now - (_kst_minutes("23:30") + shift)
    if phase == "PRE_MARKET":
        et = _et_minutes(clock)
        return None if et is None else et - 4 * 60
    if phase == "POST_MARKET":
        et = _et_minutes(clock)
        return None if et is None else et - 16 * 60
    return None


def _in_et_window(clock: Dict[str, Any], window: Optional[List[str]]) -> bool:
    if not window or len(window) < 2:
        return False
    et = _et_minutes(clock)
    if et is None:
        return False
    try:
        start = _hm_minutes(window[0])
        end = _hm_minutes(window[1])
    except ValueError:
        return False
    return start <= et < end


def premarket_entry_gate(clock: Dict[str, Any], plan: Dict[str, Any],
                         regime: Optional[str] = None,
                         source: Optional[str] = None,
                         prev_close: Optional[float] = None,
                         price: Optional[float] = None) -> Dict[str, Any]:
    """프리장 자동 진입 허용 판정 — 통과하지 못하면 이유를 돌려준다(주문 없음)."""
    if str((clock or {}).get("phase")) != "PRE_MARKET":
        return {"ok": True, "reason": None}
    if not plan.get("premarket_enabled"):
        return {"ok": False, "reason": "premarket_policy 미승인 — 프리장 진입 금지"}
    cond = plan.get("entry_conditions") or dict(DEFAULT_ENTRY_CONDITIONS)
    if not _in_et_window(clock, cond.get("window_et")):
        return {"ok": False, "reason": f"프리장 진입 창 밖 {cond.get('window_et')} ET"}
    if source and source not in set(cond.get("allow_sources") or []):
        return {"ok": False, "reason": f"프리장 허용 신호 아님({source})"}
    reg = str(regime or "")
    if reg and reg in set(cond.get("block_regimes") or []):
        return {"ok": False, "reason": f"국면 {reg} — 프리장 진입 금지"}
    if reg == "RISK_OFF" and not bool(cond.get("allow_risk_off")):
        return {"ok": False, "reason": "RISK_OFF 국면 — 프리장 진입 금지(정규장 대기)"}
    if price is not None and prev_close:
        try:
            gap = (float(price) / float(prev_close) - 1.0) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            gap = 0.0
        cap = float(cond.get("max_gap_chase_pct") or 0.0)
        if gap > cap:
            return {"ok": False, "gap_pct": round(gap, 2),
                    "reason": f"프리장 추격 금지 — 전일 종가 대비 +{gap:.2f}% > +{cap:.2f}%"}
    if price is not None and price < float(cond.get("min_price_usd") or 0.0):
        return {"ok": False, "reason": f"프리장 최소가 ${cond.get('min_price_usd')} 미달"}
    return {"ok": True, "reason": None}


def slippage_bps(phase: Optional[str], plan: Optional[Dict[str, Any]],
                 fallback_bps: float = 20.0) -> float:
    """단계별 지정가 슬리피지 상한(bp). 프리장은 호가가 얇아 정책값으로 조인다."""
    plan = plan or default_plan()
    if str(phase) == "PRE_MARKET":
        return float((plan.get("entry_conditions") or {}).get("max_slippage_bps")
                     or DEFAULT_ENTRY_CONDITIONS["max_slippage_bps"])
    if str(phase) == "POST_MARKET":
        return float((plan.get("after_hours") or {}).get("max_slippage_bps")
                     or DEFAULT_AFTER_HOURS["max_slippage_bps"])
    return float(fallback_bps)


def limit_price(price: float, side: str, phase: Optional[str],
                plan: Optional[Dict[str, Any]], fallback_bps: float = 20.0,
                price_round: int = 2) -> Tuple[float, Dict[str, Any]]:
    """단계별 지정가. 매수는 위로, 매도는 아래로 슬리피지 상한만큼만 밀어 준다."""
    side = str(side or "BUY").upper()
    cap = slippage_bps(phase, plan, fallback_bps if side == "BUY" else -abs(fallback_bps))
    if str(phase) in ("PRE_MARKET", "POST_MARKET"):
        bps = cap if side == "BUY" else -cap
    else:
        bps = float(fallback_bps) if side == "BUY" else -abs(float(fallback_bps))
    rounded = round(float(price) * (1.0 + bps / 10000.0), int(price_round))
    return max(0.01, rounded), {"offset_bps": bps, "phase": phase,
                                "cap_bps": abs(cap), "capped": str(phase) in ("PRE_MARKET", "POST_MARKET")}


def budget(phase: Optional[str], plan: Optional[Dict[str, Any]],
           equity_budget_usd: float) -> Dict[str, Any]:
    """단계별 1회 예산 — 정규장 예산과 프리장/애프터마켓 상한 중 작은 쪽."""
    plan = plan or default_plan()
    base = float(equity_budget_usd or 0.0)
    if str(phase) == "PRE_MARKET":
        cap = float((plan.get("entry_conditions") or {}).get("max_budget_usd")
                    or DEFAULT_ENTRY_CONDITIONS["max_budget_usd"])
        return {"budget_usd": round(min(base, cap), 2), "phase_cap_usd": cap,
                "binding": "premarket_cap" if cap < base else "engine_budget"}
    if str(phase) == "POST_MARKET":
        cap = float((plan.get("after_hours") or {}).get("max_budget_usd")
                    or DEFAULT_AFTER_HOURS["max_budget_usd"])
        return {"budget_usd": round(min(base, cap), 2), "phase_cap_usd": cap,
                "binding": "after_hours_cap" if cap < base else "engine_budget"}
    return {"budget_usd": round(base, 2), "phase_cap_usd": None, "binding": "engine_budget"}


def session_order_cap(phase: Optional[str], plan: Optional[Dict[str, Any]],
                      default_cap: int = 8) -> int:
    plan = plan or default_plan()
    if str(phase) == "PRE_MARKET":
        return int((plan.get("entry_conditions") or {}).get("max_orders_per_session")
                   or DEFAULT_ENTRY_CONDITIONS["max_orders_per_session"])
    if str(phase) == "POST_MARKET":
        return int((plan.get("after_hours") or {}).get("max_orders_per_session")
                   or DEFAULT_AFTER_HOURS["max_orders_per_session"])
    return int(default_cap)


def quote_fresh(quote: Dict[str, Any], clock: Dict[str, Any],
                max_age_min: Optional[float] = None) -> Dict[str, Any]:
    """프리장 시세 신선도 — 얇은 호가에서 오래된 가격으로 주문하지 않는다."""
    if not isinstance(quote, dict):
        return {"ok": False, "reason": "시세 없음"}
    limit = float(max_age_min if max_age_min is not None
                  else DEFAULT_ENTRY_CONDITIONS["max_quote_age_min"])
    stamp = quote.get("intraday_bar_time") or quote.get("quote_time") or quote.get("as_of")
    if not stamp:
        # 시각이 없으면 스냅샷 생성 시각으로 대체 판정한다(호출부가 넘기는 clock 문자열).
        return {"ok": True, "age_min": None, "reason": "시각 미표기 — 스냅샷 신선도 판정에 위임"}
    raw = str(stamp).replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(raw)
        if t.tzinfo is not None:
            # 러너의 시스템 시간대에 기대지 않고 KST로 고정 변환한다(UTC 러너에서 9시간 오차 방지).
            t = t.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
        now = datetime.strptime(str(clock.get("now_kst") or "")[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return {"ok": True, "age_min": None, "reason": "시각 해석 불가 — 스냅샷 판정에 위임"}
    age = (now - t).total_seconds() / 60.0
    if age > limit:
        return {"ok": False, "age_min": round(age, 1),
                "reason": f"프리장 시세 {age:.0f}분 경과 > {limit:.0f}분"}
    return {"ok": True, "age_min": round(age, 1), "reason": None}
