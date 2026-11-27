"""
장중 추세 변곡(inflection) 청산 규칙 — `exit_mode="inflection_v1"`.

무엇을 하는 모듈인가
--------------------
보유 중인 포지션(인버스·레버리지 ETF 매수, 일반 롱)이 **장중에 추세가 꺾이는 지점**을
잡아 청산하는 규칙이다. 기존 두 계열의 중간에 있다.

    chandelier_v2   추세를 끝까지 끌고 가는 트레일 (반전을 세지 않는다)
    ladder          고정 TP/SL (추세를 세지 않는다)
    inflection_v1   수익이 난 뒤에는 **구조가 깨지는 신호를 세어** 청산하고,
                    수익이 나기 전에는 손절선만 지킨다

청산 판정 3층
-------------
1. **손실 방어** — `sl`(브래킷 값) 이탈 시 전량. 수익 여부와 무관.
2. **이익 보호(래칫)** — 최고가가 `be_after_atr×ATR` 또는 `be_min_pct`만큼 오르면
   손절선을 본전+왕복비용(0.34%)으로 올리고, 이후 `highest − k×ATR` 트레일을 쓴다.
3. **변곡 신호(표결)** — 아래 3개 중 `votes_required`개 이상이면 청산. 단 **평가손익이
   `struct_min_profit_pct` 이상일 때만** 표결을 연다(손실 구간에서는 손절선만 본다).

변곡 신호 3종
~~~~~~~~~~~~~
    (a) 세션 VWAP 하향 이탈 — 포지션이 VWAP 위에서 거래되다 종가가 아래로 내려온 봉
        (`vwap_confirm_bars`봉 연속). 장중 상승 추세의 지지선이 깨졌다는 뜻.
    (b) 단기 EMA 역크로스 — 10분봉 EMA5 < EMA15 (직전 `ema_cross_lookback`봉 내 발생).
        봉 주기는 **10분 고정**이다. 1분봉·5분봉으로 좁히면 노이즈에 걸려 회전만 늘어난다
        (`docs/inflection_1m_backtest_20261118.md`: 1분봉 24격자 전부 플러스 0개).
    (c) 기저지수 반등 확인 — SQQQ↔QQQ, SOXS↔SOXL, TQQQ↔QQQ, SOXL↔SOXL. 기저지수가
        자기 세션 VWAP을 `underlying_confirm_bars`봉 연속 회복하면, 헤지/숏 포지션을
        들고 있을 이유 자체가 사라진 것이다.

왜 "표결"인가
-------------
단일 신호는 장중 되돌림(휩쏘)과 구분되지 않는다. 세 신호는 서로 다른 정보를 본다
(가격구조 / 모멘텀 / 기저 상품). 2표 이상을 요구하면 한 번의 노이즈로 청산되지 않는다.
백테스트에서 1표·2표·3표를 모두 돌려 2표를 기본값으로 삼았다
(`docs/intraday_inflection_exit_20261120.md`).

설계 원칙
---------
* `evaluate()`는 **순수 함수**다. 바(bar) 배열과 인덱스만 받아 결정을 돌려주고,
  상태를 바꾸지 않는다. 백테스트와 실전이 **같은 함수**를 쓰기 때문에 "백테스트에서
  좋았던 규칙"과 "실전에서 도는 규칙"이 갈라지지 않는다.
* 실전 경로의 바 공급은 `core/entry_signals.fetch_intraday_bars()`(5분봉, 3분 캐시)를
  재사용한다. 새 네트워크 경로를 만들지 않는다.
* 이 모듈은 **주문 능력이 없다**. 결정만 돌려주고, 집행은 `core/autotrader`가 한다.
* 어떤 브래킷도 스스로 `exit_mode`를 바꾸지 않는다. `bracket_specs`/`config`에서
  명시적으로 지정한 경우에만 `exit_rules.decide()`가 이 경로로 들어온다.

규격(2026-11-20 기본값)
-----------------------
| 파라미터              | 값   | 의미                                            |
|-----------------------|------|-------------------------------------------------|
| `trail_atr_mult`      | 1.2  | 최고가 − 1.2×ATR(10분) 트레일                    |
| `be_after_atr`        | 1.0  | 1.0×ATR 상승 시 본전 래칫                       |
| `be_min_pct`          | 1.0  | 또는 +1.0% 도달 시 (둘 중 먼저)                  |
| `giveback_min_pct`    | 1.2  | 최고가 대비 1.2% 이상 되돌림 → 즉시 청산         |
| `votes_required`      | 2    | 변곡 표결 2/3                                    |
| `struct_min_profit_pct`| 0.0 | 이 이상 수익일 때만 표결                         |
| `sl_min_pct`          | 2.0  | 손절 하한(브래킷 sl이 없을 때)                   |
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

# 기저지수 매핑 — 인버스·레버리지 ETF가 무엇을 따라가는가
UNDERLYING = {
    "SQQQ": "QQQ",
    "TQQQ": "QQQ",
    "SOXS": "SOXL",
    "SOXL": "SOXL",
    "SPXU": "SPY",
    "SPXS": "SPY",
    "UPRO": "SPY",
}

DEFAULT_PARAMS: Dict[str, Any] = {
    "trail_atr_mult": 1.2,
    "be_after_atr": 1.0,
    "be_min_pct": 1.0,
    "be_buffer_pct": 0.34,          # 왕복 수수료(0.24%) + 여유
    "giveback_min_pct": 1.2,
    "sl_atr_mult": 0.75,
    "sl_min_pct": 2.0,
    "sl_max_pct": 15.0,
    "vwap_confirm_bars": 2,
    "ema_fast": 5,
    "ema_slow": 15,
    "ema_cross_lookback": 2,
    "underlying_confirm_bars": 2,
    "votes_required": 2,
    "struct_min_profit_pct": 0.0,
    "atr_period": 14,
}

# 실측으로 고른 두 가지 프리셋(2026-11-20, 10분봉 60세션 · 인버스 진입 146건)
#
#   profit_lock : 본전 잠금(+1×ATR 또는 +1% 후 손절선을 본전+0.34%) + 세션 종가 청산.
#                 트레일·변곡 표결을 끈다. EV +0.65%/건(vwap이탈 진입 89건), PF 2.46,
#                 MDD -$9.47, t=2.00 — 이 표본에서 유일하게 |t|≥2.
#   inflection  : 트레일 1.2×ATR + 변곡 2/3표. 보유시간이 278분→134분으로 줄고
#                 MDD는 -$10.2로 비슷하지만 EV는 +0.14%/건으로 내려간다.
#
# 즉 "변곡 신호만으로 승자를 자르면 기대값이 깎이고, 본전 잠금만으로도 하방은 막힌다".
PRESETS: Dict[str, Dict[str, Any]] = {
    "profit_lock": {"trail_atr_mult": None, "giveback_min_pct": 99.0,
                    "be_after_atr": 1.0, "be_min_pct": 1.0,
                    "votes_required": 9, "sl_atr_mult": 1.5, "sl_min_pct": 6.0},
    "inflection": {"trail_atr_mult": 1.2, "giveback_min_pct": 1.2,
                   "be_after_atr": 1.0, "be_min_pct": 1.0,
                   "votes_required": 2, "sl_atr_mult": 0.75, "sl_min_pct": 2.0},
}


def preset(name: str) -> Dict[str, Any]:
    """이름 있는 프리셋 파라미터(없는 이름이면 기본값)."""
    p = dict(DEFAULT_PARAMS)
    p.update(PRESETS.get(str(name), {}))
    return p


# --------------------------------------------------------------------- context
def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """지수이동평균 — 루프 구현(외부 의존 없이 numpy만)."""
    n = len(values)
    out = np.empty(n, dtype=float)
    if n == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    out[0] = values[0]
    for i in range(1, n):
        out[i] = out[i - 1] + alpha * (values[i] - out[i - 1])
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder ATR — 봉 주기 배열에서 직접 계산."""
    n = len(close)
    tr = np.empty(n, dtype=float)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.empty(n, dtype=float)
    alpha = 1.0 / float(period)
    out[0] = tr[0]
    for i in range(1, n):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def _session_vwap(close: np.ndarray, volume: np.ndarray, session_ids: List[str]) -> np.ndarray:
    """세션별 누적 VWAP(세션이 바뀌면 리셋)."""
    n = len(close)
    out = np.full(n, np.nan, dtype=float)
    cur = None
    pv = 0.0
    vv = 0.0
    for i in range(n):
        sid = session_ids[i]
        if sid != cur:
            cur, pv, vv = sid, 0.0, 0.0
        pv += float(close[i]) * float(volume[i])
        vv += float(volume[i])
        out[i] = pv / vv if vv > 0 else float(close[i])
    return out


def build_context(bars: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, np.ndarray]:
    """10분봉 시계열 → 판정용 배열 묶음.

    `bars`는 `close/high/low/volume/session` 키를 가진 dict(list)이며,
    실전에서는 `bars_from_live()`가 만들어 준다.
    """
    p = dict(DEFAULT_PARAMS)
    p.update(params or {})
    close = np.asarray(bars["close"], dtype=float)
    high = np.asarray(bars["high"], dtype=float)
    low = np.asarray(bars["low"], dtype=float)
    vol = np.asarray(bars.get("volume") or np.ones(len(close)), dtype=float)
    sess = list(bars.get("session") or ["s"] * len(close))
    atr = _atr(high, low, close, int(p["atr_period"]))
    return {
        "close": close, "high": high, "low": low, "volume": vol, "session": sess,
        "vwap": _session_vwap(close, vol, sess),
        "ema_fast": _ema(close, int(p["ema_fast"])),
        "ema_slow": _ema(close, int(p["ema_slow"])),
        "atr": atr,
        "atr_pct": np.where(close > 0, atr / np.where(close > 0, close, 1.0) * 100.0, 0.0),
    }


def _bucket_10m(ts: str) -> str:
    """'YYYY-MM-DD HH:MM...' → 10분봉 버킷 라벨(분을 10분 내림)."""
    if len(ts) < 16:
        return ts
    hour = int(ts[11:13])
    minute = int(ts[14:16]) - int(ts[14:16]) % 10
    return f"{ts[:10]} {hour:02d}:{minute:02d}"


def resample_5m_to_10m(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """5분봉 리스트 → 10분봉 리스트(세션 경계 유지). 실전 경로용."""
    out: List[Dict[str, Any]] = []
    cur_bucket = None
    agg: Optional[Dict[str, Any]] = None
    for b in bars:
        ts = str(b.get("t") or "")
        day = ts[:10]
        bucket = _bucket_10m(ts)
        if bucket != cur_bucket:
            if agg:
                out.append(agg)
            cur_bucket = bucket
            agg = {"t": bucket, "close": float(b.get("c")), "high": float(b.get("h")),
                   "low": float(b.get("l")), "volume": float(b.get("v") or 0.0), "session": day}
        else:
            agg["close"] = float(b.get("c"))
            agg["high"] = max(agg["high"], float(b.get("h")))
            agg["low"] = min(agg["low"], float(b.get("l")))
            agg["volume"] += float(b.get("v") or 0.0)
    if agg:
        out.append(agg)
    return out


def bars_from_10m(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """10분봉 리스트 → `build_context` 입력 dict."""
    return {
        "close": [r["close"] for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "volume": [r.get("volume") or 0.0 for r in rows],
        "session": [r.get("session") or "s" for r in rows],
    }


# ------------------------------------------------------------------- decisions
def _profit_pct(entry: float, price: float, side: str) -> float:
    if entry <= 0:
        return 0.0
    s = 1.0 if str(side).upper() != "SHORT" else -1.0
    return (price / entry - 1.0) * 100.0 * s


def vote_detail(ctx: Dict[str, np.ndarray], uctx: Optional[Dict[str, np.ndarray]],
                i: int, side: str, p: Dict[str, Any],
                underlying_i: Optional[int] = None) -> Dict[str, bool]:
    """변곡 신호 3종의 각 표(vote)를 계산한다."""
    s = 1.0 if str(side).upper() != "SHORT" else -1.0
    close, vwap = ctx["close"], ctx["vwap"]
    n_vwap = int(p["vwap_confirm_bars"])

    # (a) 세션 VWAP 이탈 — 직전에는 위(아래)에 있다가 n봉 연속 반대편
    def _side(i0: int) -> int:
        if i0 < 0 or np.isnan(vwap[i0]):
            return 0
        d = (close[i0] - vwap[i0]) * s
        return 1 if d > 0 else -1 if d < 0 else 0

    # _side: +1 = 포지션에 유리한 쪽(롱은 VWAP 위), -1 = 불리한 쪽(이탈), 0 = 판정불가
    broke_now = _side(i) == -1
    was_ok = any(_side(j) == 1 for j in range(max(0, i - n_vwap - 2), max(0, i - n_vwap) + 1))
    streak = all(_side(j) == -1 for j in range(max(0, i - n_vwap + 1), i + 1))
    vwap_break = bool(broke_now and was_ok and streak)

    # (b) 단기 EMA 역크로스 (10분봉 EMA5/EMA15) — 최근 lookback봉 내 발생
    lb = int(p["ema_cross_lookback"])
    ema_flip = False
    for j in range(max(1, i - lb + 1), i + 1):
        prev = (ctx["ema_fast"][j - 1] - ctx["ema_slow"][j - 1]) * s
        now = (ctx["ema_fast"][j] - ctx["ema_slow"][j]) * s
        if prev > 0 and now <= 0:
            ema_flip = True
            break

    # (c) 기저지수 반등 확인 — 기저지수가 자기 세션 VWAP을 연속 회복
    underlying_reclaim = False
    ui = int(underlying_i) if underlying_i is not None else i
    if uctx is not None and 0 <= ui < len(uctx["close"]):
        uc, uv = uctx["close"], uctx["vwap"]
        m = int(p["underlying_confirm_bars"])
        # 기저지수는 "반등"이므로 LONG 방향 기준(위로 회복)
        streak_up = all(uc[j] > uv[j] for j in range(max(0, ui - m + 1), ui + 1))
        had_below = any(uc[j] <= uv[j] for j in range(max(0, ui - 3 * m), max(0, ui - m) + 1))
        underlying_reclaim = bool(streak_up and had_below)

    return {
        "vwap_break": bool(vwap_break),
        "ema_flip": bool(ema_flip),
        "underlying_reclaim": bool(underlying_reclaim),
    }


def evaluate(bracket: Dict[str, Any], ctx: Dict[str, np.ndarray],
             i: int, params: Optional[Dict[str, Any]] = None,
             underlying_ctx: Optional[Dict[str, np.ndarray]] = None,
             entry_index: int = 0,
             underlying_i: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """`i`번 봉 종가 시점의 청산 결정(순수 함수).

    반환 `None`이면 보유 유지, dict면 `exit_rules`와 같은 형식의 매도 결정이다.
    """
    p = dict(DEFAULT_PARAMS)
    p.update(params or {})
    entry = float(bracket.get("entry_price") or 0.0)
    if entry <= 0 or i < 0 or i >= len(ctx["close"]):
        return None
    side = str(bracket.get("side") or "LONG").upper()
    price = float(ctx["close"][i])
    qty = int(bracket.get("bought_qty", 1) or 1)

    # 최고가(보유 구간)
    lo_i = max(0, int(entry_index))
    hh = float(np.max(ctx["high"][lo_i:i + 1])) if i >= lo_i else price
    hh = max(hh, float(bracket.get("highest_high") or entry))

    atr = float(ctx["atr"][i])
    atr_pct = float(ctx["atr_pct"][i]) if np.isfinite(ctx["atr_pct"][i]) else 6.0
    # 래칫·트레일 폭은 **진입 시점 ATR**(브래킷에 저장된 값)을 쓴다 — 장중 ATR이 줄면
    # 본전 잠금이 너무 일찍 걸려 승자를 자른다(2026-11-20 A/B: 보유 177→214분, EV +0.48→+0.66%).
    if bracket.get("atr_pct_at_plan"):
        atr_pct = float(bracket["atr_pct_at_plan"])
    profit = _profit_pct(entry, price, side)

    # 1) 손실 방어 — 브래킷 sl 우선, 없으면 규격 값
    sl = float(bracket.get("sl") or 0.0)
    if sl <= 0:
        sl_pct = min(max(float(p["sl_atr_mult"]) * atr_pct, float(p["sl_min_pct"])),
                     float(p["sl_max_pct"]))
        sl = round(entry * (1 - sl_pct / 100.0), 2)
    s = 1.0 if side != "SHORT" else -1.0
    if (price - sl) * s <= 0:
        return {"action": "SELL", "qty": qty, "stop": True, "close": True,
                "reason": f"변곡 STOP (${price:.2f} ≤ ${sl:.2f})",
                "post": {"sl_triggered": True, "closed": True}, "signal": "stop"}

    # 2) 이익 보호 — 본전 래칫 + (옵션) ATR 트레일
    be_trigger = max(float(bracket.get("be_after_atr", p["be_after_atr"])) * atr_pct,
                     float(p["be_min_pct"]))
    trail_stop = None
    if (hh / entry - 1.0) * 100.0 * s >= be_trigger:
        floor = entry * (1 + s * float(p["be_buffer_pct"]) / 100.0)
        k = p.get("trail_atr_mult")
        if k is None:
            stop_px = floor                     # 본전 잠금만(트레일 없음)
        else:
            trail = hh - float(k) * atr if side != "SHORT" else hh + float(k) * atr
            stop_px = max(trail, floor) if side != "SHORT" else min(trail, floor)
        trail_stop = round(stop_px, 2)
        if (price - stop_px) * s <= 0:
            label = "BREAKEVEN" if k is None else "TRAIL"
            return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                    "reason": (f"변곡 {label} (${price:.2f} ≤ ${stop_px:.2f}, "
                               f"최고 ${hh:.2f} 대비 {((hh - stop_px) * s / entry * 100):.1f}%)"),
                    "post": {"closed": True, "trail_stop": trail_stop}, "signal": "trail"}

    # 3) 되돌림 상한(최고가 대비) — 트레일보다 먼저 걸리는 경우가 있다
    giveback = max(float(p["giveback_min_pct"]), 1.0 * atr_pct) if atr_pct > 0 else float(p["giveback_min_pct"])
    if (hh / entry - 1.0) * 100.0 * s >= max(be_trigger, float(p["giveback_min_pct"])) and profit > 0:
        floor_px = hh * (1 - s * giveback / 100.0)
        if (price - floor_px) * s <= 0:
            return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                    "reason": (f"변곡 되돌림 (${price:.2f} ≤ ${max(floor_px, (trail_stop or 0)):.2f}, "
                               f"최고 ${hh:.2f} 대비 {giveback:.1f}%)"),
                    "post": {"closed": True}, "signal": "giveback"}

    # 4) 변곡 표결 — 수익 구간에서만
    if profit >= float(p["struct_min_profit_pct"]):
        votes = vote_detail(ctx, underlying_ctx, i, side, p, underlying_i=underlying_i)
        count = sum(1 for v in votes.values() if v)
        if count >= int(p["votes_required"]):
            names = [k for k, v in votes.items() if v]
            return {"action": "SELL", "qty": qty, "stop": False, "close": True,
                    "reason": (f"변곡 반전 청산 {count}/3 ({', '.join(names)}) "
                               f"— ${price:.2f}, 평가 {profit:+.2f}%"),
                    "post": {"closed": True}, "signal": "inflection",
                    "votes": votes}
    return None


# ------------------------------------------------------------------------ live
def decide(bracket: Dict[str, Any], price: float, clock: Dict[str, Any],
           params: Optional[Dict[str, Any]] = None,
           bars: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Optional[Dict[str, Any]]:
    """실전 진입점 — `core/exit_rules.decide()`가 호출한다.

    바 공급은 `core/entry_signals.fetch_intraday_bars()`(5분봉 3분 캐시)를 쓴다.
    바가 없거나 계산이 실패하면 `None`(보유)을 돌려주고, 강제청산·손절선은
    `exit_rules.chandelier_v2_exit`/`autotrader`의 기존 경로가 그대로 지킨다.

    ★ 이 함수는 **세션 종가 청산을 하지 않는다**. `force_exit_kst`는 상위 규칙이
      먼저 판정하므로(exit_rules.decide의 순서), 여기서 중복 청산하지 않는다.
    """
    from core import session_time

    fex = bracket.get("force_exit_kst")
    if fex and session_time.time_reached_close(clock, fex):
        return None                      # 세션 청산은 상위 규칙이 처리
    ticker = str(bracket.get("ticker") or "").upper()
    if not ticker:
        return None
    if params is None:
        if isinstance(bracket.get("inflection_params"), dict):
            params = dict(bracket["inflection_params"])
        elif bracket.get("inflection_preset"):
            params = preset(bracket["inflection_preset"])
    try:
        if bars is None:
            from core.entry_signals import fetch_intraday_bars
            und = UNDERLYING.get(ticker)
            syms = [ticker] + ([und] if und and und != ticker else [])
            bars = fetch_intraday_bars(syms)
        rows = resample_5m_to_10m(list(bars.get(ticker) or []))
        if len(rows) < 20:
            return None
        ctx = build_context(bars_from_10m(rows), params)
        und = UNDERLYING.get(ticker)
        uctx = None
        if und and und != ticker and bars.get(und):
            urows = resample_5m_to_10m(list(bars.get(und) or []))
            if len(urows) >= 20:
                uctx = build_context(bars_from_10m(urows), params)
        # 보유 시작(체결 시각) 이후 봉만 최고가 계산에 쓴다
        idx = len(rows) - 1
        entry_idx = 0
        filled = str(bracket.get("filled_at_kst") or bracket.get("opened_at") or "")
        if filled:
            day = filled[:10]
            hhmm = filled[11:16]
            for j, r in enumerate(rows):
                hm = str(r.get("t"))[11:16]
                if str(r.get("session")) >= day and hm >= hhmm:
                    entry_idx = j
                    break
        dec = evaluate(bracket, ctx, idx, params, uctx, entry_index=entry_idx)
        if not dec:
            return None
        dec["qty"] = min(int(dec.get("qty") or 1), int(bracket.get("bought_qty", 1) or 1))
        return dec
    except Exception:
        return None
