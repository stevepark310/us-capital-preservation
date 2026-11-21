"""
실시간 시세·VIX 정합성 검증 — 틀린 숫자로는 주문하지 않는다(fail-closed).

왜 필요한가 (2026-11-17~19 사고 실측)
-------------------------------------
* 11-19 세션 03:32 KST `BEAR_ALPHA_HEDGE`는 스냅샷 `macro.vix.last = 27.92`로 게이트를
  통과했다. 06:05 KST에 다시 만든 `avg_anchor_plan.json`의 VIX는 27.14, 같은 세션
  `^VIX` 일봉 고가는 28.99였다. 즉 **한 세션 안에서 VIX가 27.1~29.0 사이를 오갔고,
  엔진은 그중 한 시점의 단일 소스 값 하나만 봤다.** VIX ≥ 28은 승인된 `vix_riskoff_scale`
  에서 신규 진입 계수 0(전면 금지)인데, 27.x로 읽히는 순간 계수 0.5가 적용됐다.
* 시세도 같은 구조다. `market_snapshot`은 일봉(전일 종가)과 5분봉 `intraday_price`를
  같이 들고 있는데, 엔진은 `price_of()`로 먼저 잡히는 값을 그대로 썼다. 두 값이
  같은 세션 것인지, 몇 분 전 것인지 확인하는 코드가 없었다.

설계 원칙
---------
1. **보수적 숫자만 쓴다.** VIX는 관측된 값 중 가장 높은 것(일봉 종가 · 대체 소스 ·
   같은 세션 고가)으로 게이팅하고, 가격은 청산 판정에 가장 불리한(낮은) 값으로 본다.
2. **모르면 막는다.** 스냅샷이 오래됐거나(stale) 세션 날짜가 어긋나면 VIX를 '위기'
   수준으로 올려 신규 진입을 막고, 그 사실을 `data/live_integrity.json`에 남긴다.
3. **판단과 기록을 분리한다.** 이 모듈은 아무 주문도 내지 않는다. `core/autotrader.py`가
   돌려받은 값으로 진입/청산을 결정한다.

이 모듈이 만들어진 뒤로 신규 롱 진입은 `vix_gate >= 28`이면 하드 차단된다
(종전에는 `vix_scale` 표에만 의존해 27.x 구간에서 0.5배 진입이 가능했다).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
OUT_PATH = os.path.join(DATA, "live_integrity.json")

KST = timedelta(hours=9)

# 코드에 박아 둔 절대값 — 설정 파일로도 이보다 느슨하게 만들 수 없다.
HARD_LIMITS: Dict[str, float] = {
    "max_snapshot_age_min": 8.0,          # 스냅샷 자체가 이보다 오래되면 stale
    "max_intraday_bar_age_min": 30.0,     # 5분봉 마지막 봉이 이보다 오래되면 stale
    "vix_cross_source_tolerance": 1.0,    # 소스 간 VIX 허용 괴리(포인트)
    "price_divergence_max_pct": 1.5,      # 시세 소스 간 허용 괴리(%)
    "vix_new_long_block": 28.0,           # 이 값 이상이면 신규 롱 하드 차단
    "stale_vix_substitute": 99.0,         # stale/판독불가일 때 게이팅에 쓰는 대체 VIX
}

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "max_snapshot_age_min": 6.0,
    "max_intraday_bar_age_min": 25.0,
    "vix_cross_source_tolerance": 0.75,
    "price_divergence_max_pct": 1.0,
    "vix_new_long_block": 28.0,
    "price_sanity_pct": 35.0,             # 전일 종가 대비 이 이상 움직이면 이상치로 본다
    # 주문 가격 오프셋은 `_limit_price()`가 이미 적용한다 — 여기서 또 깎으면 이중 보수.
    "buy_price_buffer_bps": 0.0,          # 매수 판정 기준가 추가 버퍼(bps)
    "sell_price_buffer_bps": 0.0,         # 청산 판정 기준가 추가 버퍼(bps)
}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + KST


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def settings(policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """정책 파일의 `risk_guardrails.live_integrity` + 기본값. 하드 한도로만 조인다."""
    cfg = dict(DEFAULTS)
    block = ((policy or {}).get("risk_guardrails") or {}).get("live_integrity") or {}
    if isinstance(block, dict):
        for k, v in block.items():
            if k in cfg:
                cfg[k] = v
    for key, hard in HARD_LIMITS.items():
        cfg.setdefault(key, hard)
        try:
            # '허용 오차'는 작을수록 엄격, '대체 VIX'는 클수록 엄격 — 방향이 다르다.
            if key == "stale_vix_substitute":
                cfg[key] = max(float(cfg[key]), float(hard))
            else:
                cfg[key] = min(float(cfg[key]), float(hard))
        except (TypeError, ValueError):
            cfg[key] = hard
    return cfg


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        dt = dt + KST
    return dt


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ VIX
def vix_view(snapshot: Dict[str, Any], clock: Dict[str, Any],
             alt_vix: Optional[float] = None,
             cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """VIX를 여러 값에서 읽어 **가장 높은(보수적인) 값**으로 게이팅한다.

    입력
    ----
    * `snapshot.macro.vix`: {last, prev_close, day_high, day_low, last_bar_date}
    * `alt_vix`: 대체 소스(브로커/장중 시계열/2차 API). 없으면 None.
    """
    cfg = cfg or settings()
    vix = ((snapshot.get("macro") or {}).get("vix") or {}) if isinstance(snapshot, dict) else {}
    last = _num(vix.get("last"))
    day_high = _num(vix.get("day_high"))
    prev_close = _num(vix.get("prev_close"))
    bar_date = str(vix.get("last_bar_date") or "")
    session_date = str((clock or {}).get("session_date") or "")
    phase = str((clock or {}).get("phase") or "")

    reasons = []
    stale = False
    if last is None:
        stale = True
        reasons.append("VIX 값 없음")
    # 정규장·장마감 후에는 오늘 세션 봉이 있어야 한다(전일 봉이면 최소 하루 지연).
    if phase in ("REGULAR", "POST_MARKET") and session_date and bar_date and bar_date != session_date:
        stale = True
        reasons.append(f"VIX 일봉 기준일 {bar_date} ≠ 세션 {session_date}")

    candidates = [v for v in (last, _num(alt_vix)) if v is not None]
    same_session_high = None
    if day_high is not None and (not session_date or bar_date == session_date):
        same_session_high = day_high
        candidates.append(day_high)
    observed_max = max(candidates) if candidates else None
    source_max = max([v for v in (last, _num(alt_vix)) if v is not None], default=None)

    divergence = None
    if last is not None and _num(alt_vix) is not None:
        divergence = round(abs(float(last) - float(alt_vix)), 3)
        if divergence > float(cfg["vix_cross_source_tolerance"]):
            reasons.append(f"VIX 소스 괴리 {divergence:.2f}p > {cfg['vix_cross_source_tolerance']}p "
                           f"(스냅샷 {last} / 대체 {alt_vix})")
    if same_session_high is not None and last is not None:
        swing = float(same_session_high) - float(last)
        if swing > float(cfg["vix_cross_source_tolerance"]):
            reasons.append(f"세션 중 VIX 고가 {same_session_high:.2f} > 현재 {last:.2f} "
                           f"(+{swing:.2f}p) — 고가로 게이팅")

    if stale:
        gate = float(cfg["stale_vix_substitute"])
    else:
        gate = float(observed_max if observed_max is not None else cfg["stale_vix_substitute"])

    return {
        "last": last, "prev_close": prev_close, "day_high": day_high,
        "alt": _num(alt_vix), "bar_date": bar_date or None,
        "same_session_high": same_session_high,
        "source_max": source_max, "observed_max": observed_max,
        "divergence": divergence,
        "stale": stale, "reasons": reasons,
        "gate": round(gate, 3),
        "block_new_long": bool(gate >= float(cfg["vix_new_long_block"])),
    }


# ------------------------------------------------------------------ prices
def _bar_age_min(bar_time: Any, now_kst: datetime) -> Optional[float]:
    dt = _parse_ts(bar_time)
    if dt is None:
        return None
    return round((now_kst - dt).total_seconds() / 60.0, 1)


def price_view(quotes: Dict[str, Any], ticker: str, clock: Dict[str, Any],
               broker_mark: Optional[float] = None,
               cfg: Optional[Dict[str, Any]] = None,
               policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """한 종목의 시세를 읽어 보수적 기준가와 신뢰도를 돌려준다.

    * `conservative`  — 청산 판정용(낮은 쪽), 매수 판정용(높은 쪽)의 기준가.
    * `trusted`       — 같은 세션의 5분봉이 신선하고 소스 간 괴리가 허용치 이내인가.
    """
    cfg = cfg or settings(policy)
    q = (quotes or {}).get(ticker) or {}
    now = _now_kst()
    intraday = _num(q.get("intraday_price"))
    last = _num(q.get("last"))
    mark = _num(broker_mark)
    prev_close = _num(q.get("prev_close"))
    bar_age = _bar_age_min(q.get("intraday_bar_time"), now)
    phase = str((clock or {}).get("phase") or "")

    reasons = []
    values = [v for v in (intraday, last, mark) if v is not None and v > 0]
    if not values:
        return {"ticker": ticker, "trusted": False, "price": None, "conservative": None,
                "reasons": ["시세 없음"], "bar_age_min": bar_age,
                "stale": True, "divergence_pct": None}

    stale = False
    if phase == "REGULAR":
        if intraday is None:
            stale = True
            reasons.append("장중 5분봉 없음(일봉 종가만)")
        elif bar_age is not None and bar_age > float(cfg["max_intraday_bar_age_min"]):
            stale = True
            reasons.append(f"5분봉 {bar_age:.0f}분 지연 > {cfg['max_intraday_bar_age_min']:.0f}분")
    divergence = None
    broker_ignored = False
    if intraday and mark:
        divergence = round(abs(intraday / mark - 1.0) * 100.0, 3)
        if divergence > float(cfg["price_divergence_max_pct"]):
            reasons.append(f"시세 괴리 {divergence:.2f}% > {cfg['price_divergence_max_pct']}% "
                           f"(5분봉 {intraday} / 브로커 {mark})")
            # 브로커 잔고 시세는 **직전 세션 종가**인 경우가 많다(2026-11-21 SQQQ:
            # 브로커 $49.68 = 11-19 종가 vs 실제 $58대). 5분봉이 신선한데 괴리가 이렇게
            # 크면 가격 기준에서 브로커 값을 빼야 한다 — 종전에는 '보수적 = 낮은 쪽'
            # 규칙이 이 유령 가격을 집어 청산 판정가를 16% 낮게 계산했다($49.53).
            bar_fresh = (intraday is not None
                         and (bar_age is None or bar_age <= float(cfg["max_intraday_bar_age_min"])))
            if bar_fresh:
                broker_ignored = True
                reasons.append("브로커 시세 무시 — 5분봉이 신선해 오래된 잔고 시세를 배제")
    if broker_ignored:
        values = [v for v in (intraday, last) if v is not None and v > 0]
    if prev_close and phase == "REGULAR":
        move = abs(max(values) / prev_close - 1.0) * 100.0
        if move > float(cfg["price_sanity_pct"]):
            reasons.append(f"전일 종가 대비 {move:.1f}% 이동 — 이상치 여부 확인 필요")

    trusted = not stale and not (divergence is not None
                                 and divergence > float(cfg["price_divergence_max_pct"]))
    low, high = min(values), max(values)
    return {
        "ticker": ticker, "trusted": trusted, "stale": stale,
        "intraday": intraday, "last": last, "broker": mark,
        "broker_ignored": broker_ignored,
        "bar_age_min": bar_age, "divergence_pct": divergence,
        "price": intraday if intraday is not None else last,
        "conservative": round(low, 4), "aggressive": round(high, 4),
        "reasons": reasons,
    }


def price_for(quotes: Dict[str, Any], ticker: str, clock: Dict[str, Any], side: str,
              broker_mark: Optional[float] = None,
              cfg: Optional[Dict[str, Any]] = None,
              policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """주문 판정에 쓸 기준가. 매도(청산)는 낮은 쪽, 매수는 높은 쪽 + 버퍼."""
    cfg = cfg or settings(policy)
    view = price_view(quotes, ticker, clock, broker_mark=broker_mark, cfg=cfg)
    if view["price"] is None:
        return {**view, "side_price": None}
    base = float(view["conservative"] if side.upper() in ("SELL", "EXIT")
                 else view["aggressive"])
    bps = float(cfg["sell_price_buffer_bps"] if side.upper() in ("SELL", "EXIT")
                else cfg["buy_price_buffer_bps"])
    factor = (1 - bps / 10000.0) if side.upper() in ("SELL", "EXIT") else (1 + bps / 10000.0)
    return {**view, "side_price": round(base * factor, 4), "buffer_bps": bps}


# ------------------------------------------------------------------ aggregate
def evaluate(snapshot: Dict[str, Any], clock: Dict[str, Any],
             tickers: Optional[list] = None,
             broker_marks: Optional[Dict[str, float]] = None,
             alt_vix: Optional[float] = None,
             policy: Optional[Dict[str, Any]] = None,
             persist: bool = False) -> Dict[str, Any]:
    """한 사이클의 정합성 판정. 주문은 하지 않는다(호출한 쪽이 결정한다)."""
    cfg = settings(policy)
    clock = clock or {}
    broker_marks = broker_marks or {}

    snapshot_age = None
    generated = _parse_ts((snapshot or {}).get("generated_at_utc"))
    if generated is not None:
        snapshot_age = round((_now_kst() - generated).total_seconds() / 60.0, 1)
    snapshot_stale = snapshot_age is not None and snapshot_age > float(cfg["max_snapshot_age_min"])

    vix = vix_view(snapshot, clock, alt_vix=alt_vix, cfg=cfg)
    if snapshot_stale:
        vix["reasons"].append(f"스냅샷 {snapshot_age:.1f}분 경과 > {cfg['max_snapshot_age_min']:.1f}분")
        vix["stale"] = True
        vix["gate"] = max(float(vix["gate"]), float(cfg["stale_vix_substitute"]))
        vix["block_new_long"] = True

    quotes = (snapshot or {}).get("quotes") or {}
    prices = {}
    for tk in (tickers or []):
        prices[tk] = price_view(quotes, tk, clock, broker_mark=broker_marks.get(tk), cfg=cfg)

    block_entries = bool(cfg.get("enabled", True)) and bool(vix["block_new_long"])
    result = {
        "checked_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "phase": clock.get("phase"), "session_date": clock.get("session_date"),
        "enabled": bool(cfg.get("enabled", True)),
        "config": {k: cfg[k] for k in ("max_snapshot_age_min", "max_intraday_bar_age_min",
                                       "vix_cross_source_tolerance", "price_divergence_max_pct",
                                       "vix_new_long_block")},
        "snapshot_age_min": snapshot_age, "snapshot_stale": snapshot_stale,
        "vix": vix, "prices": prices,
        "block_new_long": block_entries,
        "block_reasons": (list(vix["reasons"]) + (["VIX 하드 차단"]
                                                  if vix["block_new_long"] else []))[:8],
        "note": ("보수적 판정 — VIX는 관측 최고값, 청산 가격은 소스 중 최저값. "
                 "stale/판독불가면 신규 진입 차단(fail-closed)."),
    }
    if persist:
        _save(OUT_PATH, result)
    return result


def entry_gate(integrity: Dict[str, Any], ticker: str) -> Optional[str]:
    """신규 진입 사유가 있으면 차단 사유 문자열, 없으면 None."""
    if not integrity or not integrity.get("enabled", True):
        return None
    if integrity.get("block_new_long"):
        v = integrity.get("vix") or {}
        return (f"VIX 정합성 하드 차단 — 게이트 VIX {v.get('gate')} "
                f"(현재 {v.get('last')} / 세션고가 {v.get('same_session_high')})")
    p = (integrity.get("prices") or {}).get(ticker) or {}
    if p and not p.get("trusted", True):
        return "시세 신뢰 불가 — " + "; ".join(p.get("reasons") or ["stale"])
    return None
