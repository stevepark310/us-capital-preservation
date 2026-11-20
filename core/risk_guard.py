"""
포지션 단위 손실 하드캡 + 이벤트 리스크 필터.

왜 필요한가 (2026-11-19 실측)
-----------------------------
NVDA 1주를 평단 $173.30에 보유한 채 실적(2026-11-18 06:20 KST)을 넘겼고, 11-19 세션에
$149.20으로 청산됐다 — **-$24.10 / -13.91%**, 계좌 최대 단일 손실. 손절선 $150.0은
사용자가 직접 지정한 '잠금 레벨'이라 엔진이 그대로 지켰지만(그건 옳았다), 그 결과
한 포지션이 자산의 2.7%($878 기준)를 한 번에 잃었다. 기존 코드에는 **포지션 단위
손실 상한**이 없었다. 있었던 것은 ① 건당 리스크 한도(`risk_per_trade_pct` 3%)와
② 일일 실현손실 서킷(3%/$25)뿐이라, 이미 열린 포지션의 손실 폭은 아무도 자르지 않았다.

이 모듈이 추가하는 것
---------------------
1. `max_position_loss_pct` / `max_position_loss_usd` — **모든 자동 포지션**에 걸리는
   하드캡. 가격이 캡을 넘으면 청산 규칙보다 먼저 전량 청산한다(`-13.9%` 같은 대형
   손절을 -8%에서 자른다). 두 상한 중 **먼저 닿는 쪽**이 발동한다.
2. 사용자 잠금 레벨(`levels_locked`, 예: NVDA TP 173 / SL 150)은 엔진이 임의로 바꾸지
   않는다(위임 밖). 대신 손실이 경고선(`locked_warn_pct`, 5%)을 넘으면 매 사이클
   `LOCKED_LOSS_ALERT`로 표면화하고, 이벤트 리스크(실적)가 겹치면 확인 요청 대상으로
   표시한다. 잠금 포지션에도 **재난 캡**(`max_locked_loss_pct`, 기본 15%)만은 건다 —
   사용자가 지정한 손절선보다 더 넓은 캡이므로 평시에는 절대 닿지 않고, 지정 손절선이
   어떤 이유로든 집행되지 못한 경우(크론 무발화 등)의 마지막 방어선이다.
3. 이벤트 리스크 필터 — 실적 발표 전후 창(`event_blackout_hours_before/after`)에는
   **신규 진입 금지**, 보유 포지션은 `event_risk` 플래그로 보고한다.
4. `cap_sl_pct()` — 진입 시 손절 폭 자체를 캡 이하로 조인다. 손절 폭이 캡보다 넓은
   신호는 애초에 그 크기로 들어오지 못한다(원인이 진입 시점에 제거된다).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
EARNINGS_PATH = os.path.join(DATA, "earnings_calendar.json")
KST = timedelta(hours=9)

# 설정 파일로도 넘을 수 없는 절대 상한. `strict`는 '엄격한 쪽'의 방향이다.
#   low  : 값이 작을수록 엄격(손실 상한·경고선)
#   high : 값이 클수록 엄격(이벤트 차단 창)
HARD_LIMITS: Dict[str, Dict[str, Any]] = {
    "max_position_loss_pct": {"min": 2.0, "max": 12.0, "strict": "low"},
    "max_position_loss_usd": {"min": 5.0, "max": 40.0, "strict": "low"},
    "max_locked_loss_pct": {"min": 10.0, "max": 20.0, "strict": "low"},
    "locked_warn_pct": {"min": 2.0, "max": 10.0, "strict": "low"},
    "event_blackout_hours_before": {"min": 6.0, "max": 72.0, "strict": "high"},
    "event_blackout_hours_after": {"min": 2.0, "max": 24.0, "strict": "high"},
}

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "max_position_loss_pct": 8.0,
    "max_position_loss_usd": 25.0,
    "max_locked_loss_pct": 15.0,
    "locked_warn_pct": 5.0,
    "event_blackout_hours_before": 24.0,
    "event_blackout_hours_after": 6.0,
    "event_watch_tickers": ["NVDA", "TSLA", "AVGO", "TSM", "MU", "AMD", "SOXL", "TQQQ",
                            "SOXS", "SQQQ"],
    "cap_enabled_for_sources": ["DIP_SWING_DAILY", "DIP_SWING_INTRADAY", "BEAR_ALPHA_HEDGE",
                                "C5_OPEN30_DIRECTION", "NVDA_EARNINGS_GAP_SOXS"],
}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def settings(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    block = ((policy or {}).get("risk_guardrails") or {}).get("loss_cap") or {}
    if isinstance(block, dict):
        for k, v in block.items():
            if k in cfg:
                cfg[k] = v
    for key, bounds in HARD_LIMITS.items():
        lo, hi = float(bounds["min"]), float(bounds["max"])
        try:
            value = max(lo, min(hi, float(cfg[key])))
        except (TypeError, ValueError):
            value = float(DEFAULTS[key])
        # '엄격한 쪽'으로만 조인다: 느슨하게 하는 값은 절대 채택하지 않는다.
        default = float(DEFAULTS[key])
        if bounds["strict"] == "low":
            value = min(value, default)
        else:
            value = max(value, default)
        cfg[key] = max(lo, min(hi, value))
    return cfg


# ------------------------------------------------------------------ loss cap
def loss_of(bracket: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    """브래킷 1건의 평가 손익(USD/%)."""
    entry = float(bracket.get("entry_price") or 0.0)
    qty = int(bracket.get("bought_qty") or 0)
    if entry <= 0 or qty <= 0 or price is None:
        return {"entry": entry, "qty": qty, "price": price, "usd": None, "pct": None}
    usd = round((float(price) - entry) * qty, 2)
    return {"entry": entry, "qty": qty, "price": round(float(price), 4), "usd": usd,
            "pct": round((float(price) / entry - 1.0) * 100.0, 3)}


def check_position(ticker: str, bracket: Dict[str, Any], price: Optional[float],
                   equity: Optional[float], policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """포지션 한 건의 하드캡 판정.

    반환 `action`:
      * `SELL_ALL`   — 캡 초과, 전량 청산(엔진이 집행)
      * `ALERT`      — 잠금(사용자 지정) 포지션의 경고선 초과 — 청산은 사용자 결정
      * `NONE`       — 정상
    """
    cfg = settings(policy)
    info = loss_of(bracket, price)
    pct, usd = info.get("pct"), info.get("usd")
    locked = bool(bracket.get("levels_locked"))
    cap_pct = float(cfg["max_locked_loss_pct"] if locked else cfg["max_position_loss_pct"])
    cap_usd = float(cfg["max_position_loss_usd"])
    equity_cap_usd = None
    try:
        if equity:
            # 자산 대비 2% — 금액 캡과 함께 걸어 소액 계좌에서 % 캡만으로는 늦게 잘리는 것을 막는다.
            equity_cap_usd = round(float(equity) * 0.02, 2)
    except (TypeError, ValueError):
        equity_cap_usd = None
    binding = cap_usd if equity_cap_usd is None else min(cap_usd, equity_cap_usd)

    out = {
        "ticker": ticker, "locked": locked, "loss": info,
        "cap_pct": cap_pct, "cap_usd": round(binding, 2),
        "source": bracket.get("entry_source") or bracket.get("bracket_version"),
        "action": "NONE", "reason": None,
    }
    if pct is None:
        out["reason"] = "가격 없음 — 판정 보류"
        return out

    breach_pct = pct <= -cap_pct
    breach_usd = usd is not None and usd <= -binding
    if breach_pct or breach_usd:
        why = []
        if breach_pct:
            why.append(f"{pct:+.2f}% ≤ -{cap_pct:.1f}%")
        if breach_usd:
            why.append(f"${usd:+.2f} ≤ -${binding:.2f}")
        if locked:
            # 잠금 포지션은 사용자 레벨이 우선 — 재난 캡(max_locked_loss_pct)만 집행한다.
            if pct <= -float(cfg["max_locked_loss_pct"]):
                out["action"] = "SELL_ALL"
                out["reason"] = "잠금 포지션 재난 캡 발동 — " + ", ".join(why)
            else:
                out["action"] = "ALERT"
                out["reason"] = ("잠금 포지션 손실 캡 도달 — 사용자 지정 손절선이 더 가깝습니다 "
                                 f"({', '.join(why)}). 청산은 사용자 결정.")
        else:
            out["action"] = "SELL_ALL"
            out["reason"] = "포지션 손실 하드캡 — " + ", ".join(why)
        return out

    if locked and pct <= -float(cfg["locked_warn_pct"]):
        out["action"] = "ALERT"
        out["reason"] = (f"잠금 포지션 손실 경고선 -{cfg['locked_warn_pct']:.1f}% 초과 "
                         f"({pct:+.2f}%) — 실적·거버넌스 이벤트 확인 필요")
        return out
    near_pct = pct <= -0.8 * cap_pct
    near_usd = usd is not None and usd <= -0.8 * binding
    if near_pct or near_usd:
        out["action"] = "WARN"
        parts = []
        if near_pct:
            parts.append(f"{pct:+.2f}% / -{cap_pct:.1f}%")
        if near_usd:
            parts.append(f"${usd:+.2f} / -${binding:.2f}")
        out["reason"] = "손실 캡 80% 근접 (" + ", ".join(parts) + ")"
    return out


def cap_sl_pct(sl_pct: float, equity: Optional[float], price: float, qty: int,
               policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """진입 시 손절 폭을 캡 이하로 조인다(원인을 진입 시점에 제거).

    손절 폭이 `max_position_loss_pct`보다 넓으면 그 폭으로 진입하지 않는다.
    """
    cfg = settings(policy)
    cap = float(cfg["max_position_loss_pct"])
    capped = min(float(sl_pct), cap)
    usd_at_cap = None
    if equity and price and qty:
        usd_at_cap = round(float(price) * float(capped) / 100.0 * int(qty), 2)
    return {"sl_pct_in": round(float(sl_pct), 3), "sl_pct": round(capped, 3),
            "capped": capped < float(sl_pct) - 1e-9, "cap_pct": cap,
            "loss_at_cap_usd": usd_at_cap}


# ------------------------------------------------------------------ events
def _parse_event_ts(text: Any) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def event_risk(ticker: str, now: Optional[datetime] = None,
               policy: Optional[Dict[str, Any]] = None,
               calendar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """실적 이벤트 창 판정 — 신규 진입 금지 / 보유 보고용."""
    cfg = settings(policy)
    now = now or _now_kst()
    cal = calendar if calendar is not None else _load(EARNINGS_PATH, {})
    entry = ((cal.get("entries") or {}).get(ticker.upper()) or {})
    at = _parse_event_ts(entry.get("report_at_kst"))
    if at is None:
        return {"ticker": ticker.upper(), "available": False, "block_entry": False,
                "hours_to_event": None, "reason": None}
    hours = round((at - now).total_seconds() / 3600.0, 2)
    before = float(cfg["event_blackout_hours_before"])
    after = float(cfg["event_blackout_hours_after"])
    in_window = (-after) <= hours <= before
    if hours > before:
        reason = f"실적 {hours:.1f}시간 전 (창 {before:.0f}h 이내 아님)"
    elif hours >= 0:
        reason = f"실적 {hours:.1f}시간 전 — 신규 진입 금지"
    else:
        reason = f"실적 {abs(hours):.1f}시간 경과 — 반응일 변동성 구간"
    return {"ticker": ticker.upper(), "available": True, "report_at_kst": entry.get("report_at_kst"),
            "hours_to_event": hours, "block_entry": bool(in_window),
            "reason": reason if in_window else None,
            "window": {"before_h": before, "after_h": after}}


def portfolio_alerts(brackets: Dict[str, Any], prices: Dict[str, Optional[float]],
                     equity: Optional[float], policy: Optional[Dict[str, Any]] = None,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """모든 열린 포지션에 대해 캡·이벤트 판정을 한 번에. (주문 없음)"""
    from core import exit_rules  # 순환 import 방지(exit_rules는 이 모듈을 참조하지 않는다)

    checks: List[Dict[str, Any]] = []
    for tk, b in (brackets or {}).items():
        if not isinstance(b, dict) or not exit_rules.is_open(b):
            continue
        c = check_position(tk, b, prices.get(tk), equity, policy)
        ev = event_risk(tk, now=now, policy=policy)
        c["event_risk"] = ev
        if ev.get("block_entry") and c["action"] == "NONE":
            c["action"] = "WARN"
            c["reason"] = ev.get("reason")
        checks.append(c)
    sell = [c["ticker"] for c in checks if c["action"] == "SELL_ALL"]
    alerts = [c for c in checks if c["action"] in ("ALERT", "WARN")]
    return {"checked_at_kst": now.strftime("%Y-%m-%d %H:%M:%S") if now else _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "checks": checks, "sell_all": sell, "alerts": alerts,
            "config": {k: settings(policy)[k] for k in ("max_position_loss_pct",
                                                        "max_position_loss_usd",
                                                        "max_locked_loss_pct",
                                                        "locked_warn_pct")}}
