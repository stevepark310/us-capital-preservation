"""
Exit decision rules — shared by the 10-minute monitor and the autonomous trader.

한 곳에서만 청산 규칙을 정의한다. `scripts/monitor_and_exit.py`(감시·리포트)와
`core/autotrader.py`(무인 집행)가 같은 함수를 호출하므로, 두 엔진이 서로 다른
손절 시각·다른 트레일 폭으로 주문하는 일이 생기지 않는다.

규칙 3종
--------
1. `swing_exit_decision`  — DIP_SWING 오버나이트(사용자 승인 2026-11-14 21:29).
   검증된 ATR×1.5 대칭 브래킷, 전량 청산, 최대 3세션. **일봉 브래킷 전용**
   (`bracket_class=DAILY_SWING`).
2. `chandelier_v2_exit`   — 장중 진입 기본값(2026-11-17 A/B · 2026-11-18 트레일 상향).
   샹들리에 트레일(브래킷의 `trail_atr_mult`, 신규분 1.5×ATR) + 초기손절 0.75×ATR +
   본전 하한 + 세션 종가 청산(`force_exit_kst`, 05:40 KST) + 3세션 시간청산. 부분익절 없음.
3. `ladder_exit_decision` — 평단 기준 사다리(TP1/TP2/SL) + 시간·실적 가드 청산.
   개장 15분 손절 유예(`sl_active_after_kst`)와 강제청산을 그대로 지킨다. C5 파일럿의
   15:40 ET 시간청산도 이 경로(`force_exit_kst`)를 쓴다.

각 함수는 브래킷을 '읽고' 결정만 돌려준다. 상태 변경은 호출한 쪽이
`decision["post"]`를 적용해 수행한다(테스트에서 부작용 없이 검증하기 위해서).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core import session_time
from core.session_time import late_session, time_reached

# 강제청산은 정책 시각보다 이만큼 먼저 발동한다(사이클 1회 누락을 흡수, 2026-11-19 사고).
# 브래킷이 `force_exit_early_min`을 들고 있으면 그 값이 우선한다.
FORCE_EXIT_EARLY_MIN = 10


def _force_due(bracket: Dict[str, Any], clock: Dict[str, Any]) -> bool:
    """`force_exit_kst` + 앞당김 버퍼가 지났는가."""
    fex = bracket.get("force_exit_kst")
    if not fex:
        return False
    try:
        early = int(bracket.get("force_exit_early_min", FORCE_EXIT_EARLY_MIN))
    except (TypeError, ValueError):
        early = FORCE_EXIT_EARLY_MIN
    early = max(0, min(30, early))
    if early <= 0:
        return time_reached(clock, fex)
    now = session_time.session_minutes(session_time.kst_hhmm(clock or {}))
    gate = session_time.shift_minutes(clock or {}, session_time.session_minutes(fex)) - early
    if now < 0 or gate < 0:
        return time_reached(clock, fex)
    return now >= gate


def remaining_qty(bracket: Dict[str, Any]) -> int:
    """아직 팔지 않은 수량(부분익절 체결분 제외)."""
    total = int(bracket.get("bought_qty", 0) or 0)
    used = 0
    if bracket.get("tp1_filled"):
        used += int(bracket.get("tp1_qty", 0) or 0)
    if bracket.get("tp2_filled"):
        used += int(bracket.get("tp2_qty", 0) or 0)
    return max(0, total - used)


def is_open(bracket: Dict[str, Any]) -> bool:
    """청산 대상 브래킷인가(캐리 파킹·종료·미체결 제외)."""
    if not isinstance(bracket, dict) or "entry_price" not in bracket:
        return False
    if bracket.get("carry_parking") or bracket.get("note") == "CARRY_PARKING":
        return False
    if bracket.get("closed"):
        return False
    if int(bracket.get("bought_qty", 0) or 0) <= 0:
        return False
    return True


def _late(clock: Dict[str, Any]) -> bool:
    """정규장 마감 15분 전 이후 — 마감 시각은 clock의 서머타임 반영값을 쓴다."""
    return late_session(clock, 15)


def _roll_session(bracket: Dict[str, Any], clock: Dict[str, Any]) -> None:
    """세션이 바뀌면 보유 세션 수를 +1 (같은 세션에서 반복 호출돼도 1회만)."""
    session = clock.get("session_date")
    if bracket.get("last_session_date") and bracket["last_session_date"] != session:
        bracket["sessions_held"] = int(bracket.get("sessions_held", 1) or 1) + 1
    bracket["last_session_date"] = session
    bracket.setdefault("sessions_held", 1)


def swing_exit_decision(bracket: Dict[str, Any], price: float,
                        clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """DIP_SWING 오버나이트 — 승인된 조건 그대로(전량, 3세션 상한)."""
    _roll_session(bracket, clock)
    max_hold = int(bracket.get("max_hold_sessions", 3) or 3)
    qty = remaining_qty(bracket)

    if float(price) <= float(bracket.get("sl", 0)):
        return {"action": "SELL", "qty": qty, "stop": True, "close": False,
                "reason": f"DIP_SWING STOP (${price:.2f} ≤ ${bracket['sl']:.2f})",
                "post": {"sl_triggered": True}}
    if bracket.get("tp") and float(price) >= float(bracket["tp"]):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": f"DIP_SWING TARGET (${price:.2f} ≥ ${bracket['tp']:.2f})",
                "post": {"closed": True}}
    if int(bracket.get("sessions_held", 1)) >= max_hold and _late(clock):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": f"DIP_SWING TIME EXIT ({max_hold}세션 종가 청산)",
                "post": {"closed": True}}
    return None


def chandelier_v2_exit(bracket: Dict[str, Any], price: float,
                       clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """v2 청산 — 샹들리에 트레일 + 초기손절 + 세션 종가 청산 + 시간청산.

    근거(2026-11-17 A/B, 2년 60분봉 39~51건): 고정 사다리(v1)는 SOXL ATR 27%에서
    손절이 2.5%로 클램프돼 39건 중 31건이 손절(-1.31%/건)이었다. 트레일 v2는
    SOXL +2.48%/건, TQQQ +0.70%/건으로 두 종목 모두 개선됐다. 부분익절 없이
    전량 1회 청산한다(계좌 1~2주 단위에서 분할은 수수료만 늘린다).

    트레일 폭은 **브래킷에 등록된 값**을 쓴다(신규 장중 진입 1.5×ATR, 기존 0.9×ATR 유지).
    장중 규격에 `force_exit_kst`가 있으면 그 시각에 세션 종가 청산 — 장중 브래킷이
    다음 세션으로 넘어가 "3세션 방치"가 되는 것을 막는다(2026-11-18 구조 개선 ③).
    """
    entry = float(bracket.get("entry_price") or 0)
    if entry <= 0:
        return None
    _roll_session(bracket, clock)
    atr_pct = float(bracket.get("atr_pct_at_plan") or (bracket.get("ladder_params") or {}).get("atr_pct") or 6.0)
    atr_abs = entry * atr_pct / 100.0
    k = float(bracket.get("trail_atr_mult", 0.9))
    hh = max(float(bracket.get("highest_high") or entry), float(price))
    bracket["highest_high"] = hh
    trail = hh - k * atr_abs
    if hh >= entry + float(bracket.get("be_after_atr", 1.0)) * atr_abs:
        trail = max(trail, entry * (1 + float(bracket.get("breakeven_pct", 0.34)) / 100))
    bracket["trail_stop"] = round(trail, 2)
    max_hold = int(bracket.get("max_hold_sessions", 3) or 3)
    qty = remaining_qty(bracket)

    if not time_reached(clock, bracket.get("sl_active_after_kst")):
        return None                       # 개장 15분 유예
    if float(price) <= float(bracket.get("sl", 0)):
        return {"action": "SELL", "qty": qty, "stop": True, "close": True,
                "reason": f"v2 STOP (${price:.2f} ≤ ${bracket['sl']:.2f})",
                "post": {"sl_triggered": True, "closed": True}}
    fex = bracket.get("force_exit_kst")
    if fex and not bracket.get("force_exited") and _force_due(bracket, clock):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": (f"v2 SESSION EXIT ({fex} KST 종가 청산, 장중 브래킷"
                           f"{'' if time_reached(clock, fex) else ' · 페일세이프 선행'})"),
                "post": {"force_exited": True, "closed": True}}
    if float(price) <= trail:
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": f"v2 TRAIL (${price:.2f} ≤ ${trail:.2f}, 최고 ${hh:.2f} − {k}×ATR)",
                "post": {"closed": True}}
    if int(bracket.get("sessions_held", 1)) >= max_hold and _late(clock):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": f"v2 TIME EXIT ({max_hold}세션 종가 청산)",
                "post": {"closed": True}}
    return None


def ladder_exit_decision(bracket: Dict[str, Any], price: float,
                         clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """평단 사다리 — 실적 가드 → 손절(유예 후) → TP1 → TP2 순서.

    손절이 익절보다 우선한다(같은 틱에서 둘 다 닿는 경우 방어 우선).
    `post`에는 손절선 상향(TP1 후 본전, TP2 후 +1.5%)이 담긴다.
    """
    if bracket.get("closed") or bracket.get("sl_triggered"):
        return None
    qty = remaining_qty(bracket)
    if qty <= 0:
        return None

    # 실적 가드: 지정 시각 이후 잔여 수량 시장가 청산 (3배 레버리지로 실적을 넘기지 않는다)
    if (bracket.get("force_exit_kst") and not bracket.get("force_exited")
            and _force_due(bracket, clock)):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": (f"실적 가드 강제청산 ({bracket['force_exit_kst']} KST, NVDA 실적 전 레버리지 정리"
                           f"{'' if time_reached(clock, bracket['force_exit_kst']) else ' · 페일세이프 선행'})"),
                "post": {"force_exited": True, "closed": True}}

    sl = float(bracket.get("sl", 0) or 0)
    if sl and float(price) <= sl and time_reached(clock, bracket.get("sl_active_after_kst")):
        return {"action": "SELL", "qty": qty, "stop": True, "close": True,
                "reason": f"STOP/TRAIL (${price:.2f} ≤ ${sl:.2f}, {bracket.get('runner_trail_source', 'stop')})",
                "post": {"sl_triggered": True, "closed": True}}

    if not bracket.get("tp1_filled") and bracket.get("tp1") and float(price) >= float(bracket["tp1"]):
        take = min(int(bracket.get("tp1_qty", 1) or 1), qty)
        entry = float(bracket.get("entry_price") or 0)
        new_sl = max(sl, float(bracket.get("breakeven_stop") or entry))
        return {"action": "SELL", "qty": take, "stop": False, "close": take >= qty,
                "reason": f"TP1 (+{((price / entry) - 1) * 100:.1f}%)" if entry else "TP1",
                "post": {"tp1_filled": True, "sl": round(new_sl, 2), "closed": take >= qty}}

    if (bracket.get("tp1_filled") and bracket.get("tp2")
            and not bracket.get("tp2_filled") and float(price) >= float(bracket["tp2"])):
        take = min(int(bracket.get("tp2_qty", 1) or 1), qty)
        entry = float(bracket.get("entry_price") or 0)
        new_sl = max(sl, entry * 1.015)
        return {"action": "SELL", "qty": take, "stop": False, "close": take >= qty,
                "reason": f"TP2 (+{((price / entry) - 1) * 100:.1f}%)" if entry else "TP2",
                "post": {"tp2_filled": True, "sl": round(new_sl, 2), "closed": take >= qty}}
    return None


def decide(bracket: Dict[str, Any], price: float,
           clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """브래킷 종류를 보고 알맞은 규칙 하나만 적용한다.

    `bracket_class`(core/bracket_specs.py)가 규격의 정체를 남긴다:
    DAILY_SWING → swing_hold, INTRADAY/HEDGE → chandelier_v2, PILOT_C5/HEDGE_EVENT → time_exit,
    `exit_mode="inflection_v1"`(옵트인) → core/intraday_inflection_exit.
    """
    if not is_open(bracket):
        return None
    if bracket.get("swing_hold"):
        return swing_exit_decision(bracket, price, clock)
    if bracket.get("exit_mode") == "inflection_v1":
        return inflection_exit_decision(bracket, price, clock)
    if bracket.get("exit_mode") == "chandelier_v2":
        return chandelier_v2_exit(bracket, price, clock)
    return ladder_exit_decision(bracket, price, clock)


def inflection_exit_decision(bracket: Dict[str, Any], price: float,
                            clock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """장중 변곡 청산(옵트인) — 세션 강제청산은 여기서 먼저 지킨다.

    변곡 모듈은 "추세가 깨졌는가"만 판단하고 **세션 종가 청산을 하지 않는다**(중복 방지).
    그래서 `force_exit_kst`는 이 함수가 직접 처리해야 한다 — 변곡 신호가 한 번도
    나오지 않는 조용한 세션에서도 포지션이 다음 세션으로 넘어가지 않도록.
    """
    from core import intraday_inflection_exit as inflection

    _roll_session(bracket, clock)
    qty = remaining_qty(bracket)
    fex = bracket.get("force_exit_kst")
    if fex and not bracket.get("force_exited") and _force_due(bracket, clock):
        return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                "reason": (f"변곡 SESSION EXIT ({fex} KST 종가 청산, 장중 브래킷"
                           f"{'' if time_reached(clock, fex) else ' · 페이세이프 선행'})"),
                "post": {"force_exited": True, "closed": True}}
    decision = inflection.decide(bracket, price, clock)
    if decision:
        decision["qty"] = min(int(decision.get("qty") or qty), qty)
    return decision


def apply_post(bracket: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """결정에 담긴 상태 변경(손절선 상향·플래그)을 브래킷에 적용."""
    for k, v in (decision.get("post") or {}).items():
        bracket[k] = v
    return bracket
