"""개장 청산 지시 — 사용자가 특정 세션·종목을 지정한 "개장 직후 정리" 주문.

왜 필요한가 (2026-11-21 10:41 지시)
-----------------------------------
"월요일 게이트 매도 전용 재개하고, SQQQ 1주는 11/23 개장 직후 시장가 익절로
준비해줘. 3주 확대는 보류할게."

`core/exit_rules`는 손절·트레일·종가청산만 판단한다. SQQQ 1주는
`BEAR_ALPHA_HEDGE` 브래킷(chandelier 0.9×ATR · SL $45.99 · 05:40 종가청산)이라
개장가가 평단($48.18) 위에서 열려도 "매도 사유"가 없어 그대로 들고 있게 된다.
그래서 브래킷 규칙은 그대로 두고(전략 엔진이 계속 관리한다), 사용자가 승인한
"이 세션 개장에 정리" 지시만 따로 둔다 — 한 세션·한 종목 한정, 집행되면 끝난다.

파일 2개
---------
    config/open_exit_plan.json   지시(세션·종목·수량·승인 근거). 사람이 쓴다.
    data/open_exit_state.json    집행 기록(시도·주문번호·상태). 엔진이 쓴다.

안전 규칙
---------
* 지시가 없거나(`enabled: false`) 세션·시각이 맞지 않거나 만료되면 아무 일도 하지 않는다.
* 이 모듈은 주문을 내지 않는다. `core/autotrader.exits()`가 결정을 받아 **기존 주문
  경로 하나**(`OrderRouter`)로 낸다. 05:40 종가청산·손절 규칙은 그대로 살아 있다.
* 시도는 최대 `max_attempts`(기본 3)회. 정규장 중 한 번 접수되면 다시 시도하지 않는다.
* NH 해외주식 API는 지정가만 받는다(시장가 코드 없음). "시장가에 준비"는 즉시 체결되는
  공격적 지정가로 구현한다 — 오프셋은 `core/autotrader.OPEN_EXIT_LIMIT_OFFSET_BPS`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core import session_time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "open_exit_plan.json")
STATE_PATH = os.path.join(BASE_DIR, "data", "open_exit_state.json")

DEFAULT_OPEN_KST = "23:30"          # EST 정규장 개장(서머타임은 clock.kst_shift_min이 보정)
DEFAULT_MAX_ATTEMPTS = 3
# 접수 이후 상태 — 더 이상 재시도하지 않는다(미체결분은 05:40 종가청산이 닫는다).
LIVE_STATUSES = {"SUBMITTED", "DISPATCHED", "PENDING",
                 "UNKNOWN_NEEDS_RECONCILE", "RUN_NOT_FOUND", "UNPARSED"}

KST_OFFSET = timedelta(hours=9)


def now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST_OFFSET


def _now_str() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


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


# ------------------------------------------------------------------ plan/state
def load_plan(path: Optional[str] = None) -> Dict[str, Any]:
    plan = _load(path or CONFIG_PATH, {})
    return plan if isinstance(plan, dict) else {}


def load_state(path: Optional[str] = None) -> Dict[str, Any]:
    state = _load(path or STATE_PATH, {})
    return state if isinstance(state, dict) else {}


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> None:
    try:
        _save(path or STATE_PATH, state)
    except Exception:
        pass


def orders_of(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in ((plan or {}).get("orders") or []) if isinstance(o, dict)]


def _expires_at(plan: Dict[str, Any]) -> Optional[datetime]:
    raw = str(plan.get("expires_at_kst") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _kst_naive(dt: datetime) -> datetime:
    """aware/naive 혼용을 KST 민시각으로 통일(만료 비교 단일 기준)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone(KST_OFFSET)).replace(tzinfo=None)
    return dt


def expired(plan: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    end = _expires_at(plan)
    if end is None:
        return False
    return _kst_naive(now or now_kst()) >= end


def intent_for(ticker: str, plan: Dict[str, Any], state: Dict[str, Any],
               clock: Dict[str, Any], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """이 세션에서 이 종목에 대해 지금 집행해야 할 개장 청산 1건(없으면 None)."""
    if not isinstance(plan, dict) or plan.get("enabled") is not True:
        return None
    if (clock or {}).get("phase") != "REGULAR":
        return None
    session = str((clock or {}).get("session_date") or "")
    if not session or str(plan.get("session_date") or "") != session:
        return None
    if expired(plan, now):
        return None
    if not session_time.time_reached(clock, plan.get("open_kst") or DEFAULT_OPEN_KST):
        return None
    tk = str(ticker or "").upper()
    for order in orders_of(plan):
        if str(order.get("ticker") or "").upper() != tk:
            continue
        qty = int(order.get("qty") or 0)
        if qty <= 0:
            return None
        rec = (state or {}).get(tk) or {}
        if rec.get("done"):
            return None
        if str(rec.get("last_status") or "").upper() in LIVE_STATUSES:
            return None            # 이미 접수된 주문이 살아 있다 — 중복 매도 금지
        attempts = int(rec.get("attempts") or 0)
        if attempts >= int(plan.get("max_attempts") or DEFAULT_MAX_ATTEMPTS):
            return None
        return {
            "ticker": tk,
            "qty": qty,
            "mode": str(order.get("mode") or "market"),
            "session_date": session,
            "attempt": attempts + 1,
            "reason": str(order.get("reason")
                          or f"{session} 개장 청산 지시 ({tk} {qty}주)"),
        }
    return None


def mark_attempt(ticker: str, status: str, order_no: Any = None, price: Any = None,
                 dry_run: bool = False, note: Optional[str] = None,
                 path: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """집행 시도를 기록한다. DRY RUN은 시도 횟수를 소모하지 않는다."""
    doc = dict(state) if isinstance(state, dict) else load_state(path)
    tk = str(ticker or "").upper()
    rec = dict(doc.get(tk) or {})
    stamp = _now_str()
    rec["last_status"] = str(status or "").upper()
    rec["last_at_kst"] = stamp
    if note:
        rec["note"] = note
    if dry_run or rec["last_status"] == "DRY_RUN":
        rec["dry_run_at_kst"] = stamp
        rec["dry_run_price"] = price
        doc[tk] = rec
        save_state(doc, path)
        return doc
    rec["attempts"] = int(rec.get("attempts") or 0) + 1
    rec["order_no"] = order_no
    rec["price"] = price
    if rec["last_status"] in LIVE_STATUSES:
        rec["done"] = True         # 개장 청산 지시는 1회 접수로 종결(잔여는 05:40이 닫는다)
        rec["submitted_at_kst"] = stamp
    doc[tk] = rec
    save_state(doc, path)
    return doc


def report(plan: Optional[Dict[str, Any]] = None,
           state: Optional[Dict[str, Any]] = None,
           clock: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """상태 보고용 요약(주문 없음)."""
    plan = plan if isinstance(plan, dict) else load_plan()
    state = state if isinstance(state, dict) else load_state()
    clock = clock or {}
    pending = []
    for order in orders_of(plan):
        tk = str(order.get("ticker") or "").upper()
        rec = state.get(tk) or {}
        pending.append({"ticker": tk, "qty": order.get("qty"), "mode": order.get("mode") or "market",
                        "done": bool(rec.get("done")), "attempts": int(rec.get("attempts") or 0),
                        "last_status": rec.get("last_status"), "order_no": rec.get("order_no"),
                        "last_at_kst": rec.get("last_at_kst")})
    return {
        "enabled": bool(plan.get("enabled")),
        "session_date": plan.get("session_date"),
        "open_kst": plan.get("open_kst") or DEFAULT_OPEN_KST,
        "expired": expired(plan),
        "authorized_by": plan.get("authorized_by"),
        "authorized_at_kst": plan.get("authorized_at_kst"),
        "orders": pending,
        "clock_session_date": clock.get("session_date"),
        "clock_phase": clock.get("phase"),
    }
