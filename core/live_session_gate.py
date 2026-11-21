"""Fail-closed, one-US-session authorization for unattended real orders.

The config cannot silently authorize later sessions: a US Eastern session date
and a KST expiry must both be present. A disabled gate always permits dry-runs.

매도 전용(`sell_only`) 세션 (2026-11-21 사용자 지시)
---------------------------------------------------
"월요일 게이트 매도 전용 재개하고, SQQQ 1주는 11/23 개장 직후 시장가 익절로 준비해줘."

`enabled: true` + `sell_only: true`이면 그 세션에는 **매도만** 허용된다. 이때 매수
한도 3종(`max_buy_order_usd`·`max_session_buy_usd`·`max_session_buy_orders`)은
반드시 0이어야 한다 — 종전 검증식(`0 < cap`, `1 <= count`)은 0을 '잘못된 값'으로
보고 게이트 전체를 닫아 버려서, 매도까지 함께 막혔다(청산할 수 없는 게이트).
그래서 sell_only일 때만 0을 정상값으로 인정하고, 하나라도 0이 아니면 fail-closed로
거부한다(설정 실수로 매수가 열리는 경로를 만들지 않는다).

프리마켓(PRE_MARKET) 확장 (2026-11-21)
--------------------------------------
종전에는 `phase != "REGULAR"`이면 무조건 거부했다. 이제 허용 단계는 호출부가 넘기는
`premarket_policy`로 정해진다 — 스위치가 꺼져 있거나 사용자 승인 기록이 없으면
종전과 똑같이 **정규장만** 열린다(기본값이 안전한 쪽). 프리장이 승인된 세션이면
같은 세션일·같은 만료 조건 아래 `PRE_MARKET`도 통과하고, 이때 주문 호가유형은
`61`(프리마켓 지정가)로 돌려준다(`core/session_routing`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from core import session_routing

KST = timezone(timedelta(hours=9))


def check_live_gate(gate: Any, clock: Dict[str, Any],
                    now_utc: Optional[datetime] = None,
                    premarket_policy: Optional[Dict[str, Any]] = None,
                    plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """이 세션·이 단계에서 실주문이 허용되는가.

    premarket_policy를 넘기지 않으면 정규장만 허용한다(기존 호출부와 동일 동작).
    """
    if not isinstance(gate, dict) or gate.get("enabled") is not True:
        return {"ok": False, "reason": "live gate disabled"}
    plan = plan if plan is not None else (
        session_routing.status(premarket_policy) if premarket_policy is not None
        else session_routing.default_plan())
    phase = str(clock.get("phase") or "")
    if phase not in (plan.get("phases") or ["REGULAR"]):
        if phase == "PRE_MARKET":
            return {"ok": False, "phase": phase,
                    "reason": f"premarket not authorized — {plan.get('blocked_reason') or 'premarket_policy'}"}
        return {"ok": False, "phase": phase, "reason": "US regular session required"}
    order_type = session_routing.order_type_for_phase(phase, plan)
    if not order_type:
        return {"ok": False, "phase": phase, "reason": f"no order type for phase {phase}"}
    sell_only = gate.get("sell_only") is True
    session_date = str(clock.get("session_date") or "")
    if not session_date or not gate.get("session_date") or gate.get("session_date") != session_date:
        return {"ok": False, "reason": "live gate does not authorize this US session"}
    expiry = gate.get("expires_at_kst")
    if not isinstance(expiry, str):
        return {"ok": False, "reason": "live gate expiration missing"}
    try:
        end = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return {"ok": False, "reason": "live gate expiration invalid"}
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return {"ok": False, "reason": "naive UTC clock not allowed"}
    if current.astimezone(KST) >= end:
        return {"ok": False, "reason": "live gate expired"}
    try:
        cap = float(gate["max_buy_order_usd"])
        session_cap = float(gate["max_session_buy_usd"])
        count = int(gate["max_session_buy_orders"])
        if sell_only:
            if not (cap == 0 and session_cap == 0 and count == 0):
                return {"ok": False,
                        "reason": "live sell-only gate must set every buy cap to 0"}
        elif not (0 < cap <= 100 and cap <= session_cap <= 200 and 1 <= count <= 3):
            return {"ok": False, "reason": "live gate risk caps invalid"}
    except (TypeError, KeyError, ValueError, OverflowError):
        return {"ok": False, "reason": "live gate risk caps missing"}
    return {"ok": True, "session_date": session_date,
            "expires_at_kst": expiry, "max_buy_order_usd": cap,
            "max_session_buy_usd": session_cap,
            "max_session_buy_orders": count, "sell_only": sell_only,
            "phase": phase, "order_type": order_type,
            "premarket": phase == "PRE_MARKET",
            "after_hours": phase == "POST_MARKET"}
