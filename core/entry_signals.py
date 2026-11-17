"""
Entry signals — 무인 집행이 실제로 '살 수 있는' 신호만 계산한다.

여기 있는 신호는 전부 사용자가 이미 승인한 것들이다.

1. `dip_daily`      — DIP_SWING(11-14 21:29 승인): RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0
   이면 다음 세션 시가 진입, ATR×1.5 대칭 브래킷, 최대 3세션. 1년 +19.19%/PF 1.98,
   2년 +38.17%(60분봉·왕복 0.24% 마찰).
2. `dip_intraday`   — 신중 모드(11-16 23:43 승인)의 장중 규칙: 00:00 KST 이후
   DIP_SWING 등급 신호가 나올 때만 현금 15~20%로 1회 진입. 5분봉 VWAP 돌파 단독
   진입은 60일 실측 건당 -0.29%(SOXL)/-0.30%(TQQQ)로 폐기됐다.
3. `bear_alpha`     — 하락장 숏(11-13 수립·11-14 게이트 상향): VIX ≥ 24.0 + 확인 2개.
   3개년 시뮬 승률 71.4%·손익비 2.21:1·MDD -5.8%.
4. `nvda_gap_soxs`  — 11-18 NVDA 실적 반응일(11-17 12:00 사용자 승인): 갭 ≥ +2%면
   SOXS 1주 시가 매수·05:55 종가 청산. 19회 건당 +2.77%(승률 47.4%, t=1.37).

신호는 '점수'가 아니라 조건 충족 여부로만 돌려준다. 조건이 애매하면 진입하지 않는다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
INTRADAY_CACHE = os.path.join(DATA, "intraday_5m_cache.json")

DIP_THRESHOLDS = {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0}
BEAR_GATE = {"vix_min": 24.0, "confirmations_required": 2,
             "confirmations": {"dxy_min": 104.5, "sentiment_max": 40.0}}
GAP_SOXS = {"reference_close_usd": 156.14, "gap_min_pct": 2.0, "ticker": "SOXS",
            "disaster_stop_pct": -8.0, "exit_kst": "05:55"}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _load(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# --------------------------------------------------------------- intraday bars
def fetch_intraday_bars(symbols: List[str], cache_min: float = 3.0,
                        force: bool = False) -> Dict[str, List[Dict[str, float]]]:
    """5분봉(최근 2일) 종가·고가·저가·거래량. 캐시 TTL 기본 3분."""
    cache = _load(INTRADAY_CACHE)
    if cache.get("bars") and not force and cache.get("fetched_ts_utc"):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cache["fetched_ts_utc"])).total_seconds() / 60
            if age <= cache_min and set(symbols).issubset(set((cache.get("bars") or {}).keys())):
                return cache["bars"]
        except Exception:
            pass
    bars: Dict[str, List[Dict[str, float]]] = {}
    try:
        import yfinance as yf
        for sym in symbols:
            try:
                df = yf.download(sym, period="2d", interval="5m", progress=False, auto_adjust=False)
                if df is None or len(df) == 0:
                    continue
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                    df.columns = [c[0] for c in df.columns]
                rows = []
                for idx, row in df.tail(120).iterrows():
                    try:
                        c = float(row["Close"])
                        if c != c:
                            continue
                        rows.append({"t": str(idx), "c": c,
                                     "h": float(row["High"]), "l": float(row["Low"]),
                                     "v": float(row.get("Volume") or 0)})
                    except Exception:
                        continue
                if len(rows) >= 12:
                    bars[sym] = rows
            except Exception:
                continue
    except Exception:
        return bars
    if bars:
        cache.update({"bars": bars, "fetched_ts_utc": datetime.now(timezone.utc).isoformat(),
                      "fetched_at_kst": _now_kst().strftime("%Y-%m-%d %H:%M:%S")})
        try:
            with open(INTRADAY_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return bars


def _rsi_last(closes: List[float], n: int = 2) -> Optional[float]:
    import numpy as np
    import pandas as pd
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    return float(rsi.iloc[-1])


def intraday_dip_metrics(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """5분봉 기준 RSI2 · 볼린저 %B · RVOL · ATR% (DIP 등급 판정용)."""
    import numpy as np
    import pandas as pd
    if not bars or len(bars) < 21:
        return None
    df = pd.DataFrame(bars)
    c = df["c"].astype(float)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    lo, hi = mid - 2 * sd, mid + 2 * sd
    pct_b = float(((c - lo) / (hi - lo).replace(0, np.nan)).iloc[-1])
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    vol_avg = float(df["v"].rolling(20).mean().shift(1).iloc[-1] or 0)
    rvol = float(df["v"].iloc[-1] / vol_avg) if vol_avg > 0 else None
    return {"rsi2": round(_rsi_last(list(c), 2), 1), "pct_b": round(pct_b, 3),
            "rvol": round(rvol, 2) if rvol is not None else None,
            "atr_pct": round(atr / float(c.iloc[-1]) * 100, 2),
            "price": round(float(c.iloc[-1]), 2), "bars": int(len(c))}


def dip_intraday(symbols: List[str], cache_min: float = 3.0,
                 thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    th = dict(DIP_THRESHOLDS)
    th.update({k: float(v) for k, v in (thresholds or {}).items() if k in th})
    bars = fetch_intraday_bars(symbols, cache_min=cache_min)
    out = {"signals": [], "watch": [], "thresholds": th}
    for sym in symbols:
        m = intraday_dip_metrics(bars.get(sym) or [])
        if not m:
            out["watch"].append({"symbol": sym, "why_not": "5분봉 부족"})
            continue
        m["symbol"] = sym
        ok = (m["rsi2"] <= th["rsi2_max"] and m["pct_b"] <= th["pct_b_max"]
              and m["rvol"] is not None and m["rvol"] >= th["rvol_min"])
        if ok:
            m["signal"] = "DIP_SWING_INTRADAY"
            out["signals"].append(m)
        else:
            m["why_not"] = (f"RSI2 {m['rsi2']}>{th['rsi2_max']:.0f} 또는 "
                            f"%B {m['pct_b']}>{th['pct_b_max']:.2f} 또는 RVOL {m['rvol']}<{th['rvol_min']}")
            out["watch"].append(m)
    return out


# --------------------------------------------------------------- daily signals
def dip_daily(daily: Dict[str, Any], universe: Optional[List[str]] = None,
              thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """완료된 세션 일봉 기준 DIP_SWING 신호(다음 세션 시가 진입용)."""
    try:
        from core import swing_engine
        params = dict(swing_engine.DIP_PARAMS)
        if universe:
            params["universe"] = list(universe)
        for k, v in (thresholds or {}).items():
            if k in ("rsi2_max", "pct_b_max", "rvol_min"):
                params[k] = float(v)
        scan = swing_engine.scan_latest(daily, params)
        return {"signals": scan.get("signals", []), "watch": scan.get("watch", []),
                "as_of": scan.get("as_of"), "params": params}
    except Exception as e:
        return {"signals": [], "watch": [], "error": str(e)}


# --------------------------------------------------------------- bear alpha
def bear_alpha_gate(snapshot: Dict[str, Any], sentiment_avg: Optional[float] = None) -> Dict[str, Any]:
    """VIX ≥ 24.0 + 확인 신호 2개 (DXY ≥ 104.5 / 평균 센티먼트 < 40)."""
    macro = snapshot.get("macro") or {}
    vix = ((macro.get("vix") or {}).get("last"))
    dxy = ((macro.get("dxy") or {}).get("last"))
    confirmations = []
    if dxy is not None and float(dxy) >= BEAR_GATE["confirmations"]["dxy_min"]:
        confirmations.append(f"DXY {float(dxy):.2f} ≥ {BEAR_GATE['confirmations']['dxy_min']}")
    if sentiment_avg is not None and float(sentiment_avg) < BEAR_GATE["confirmations"]["sentiment_max"]:
        confirmations.append(f"센티먼트 {float(sentiment_avg):.1f} < {BEAR_GATE['confirmations']['sentiment_max']}")
    armed = (vix is not None and float(vix) >= BEAR_GATE["vix_min"]
             and len(confirmations) >= BEAR_GATE["confirmations_required"])
    return {"vix": vix, "dxy": dxy, "confirmations": confirmations, "armed": armed,
            "gate": f"VIX ≥ {BEAR_GATE['vix_min']} + 확인 {BEAR_GATE['confirmations_required']}개"}


# --------------------------------------------------------------- NVDA gap → SOXS
def nvda_gap_soxs(nvda_prev_close: Optional[float], nvda_open: Optional[float],
                  reference_close: float = GAP_SOXS["reference_close_usd"]) -> Dict[str, Any]:
    """11-18 실적 반응일 규칙: NVDA 갭 ≥ +2% → SOXS 1주 시가 매수, 05:55 청산."""
    ref = float(reference_close or GAP_SOXS["reference_close_usd"])
    if not nvda_open or not nvda_prev_close:
        return {"armed": False, "reason": "개장가/직전 종가 미확인", "reference_close_usd": ref}
    gap = (float(nvda_open) / float(nvda_prev_close) - 1.0) * 100.0
    armed = gap >= GAP_SOXS["gap_min_pct"]
    return {"armed": armed, "gap_pct": round(gap, 2), "threshold_pct": GAP_SOXS["gap_min_pct"],
            "reference_close_usd": ref, "ticker": GAP_SOXS["ticker"],
            "exit_kst": GAP_SOXS["exit_kst"], "disaster_stop_pct": GAP_SOXS["disaster_stop_pct"],
            "reason": (f"NVDA 갭 {gap:+.2f}% ≥ +{GAP_SOXS['gap_min_pct']}% → SOXS 1주" if armed
                       else f"NVDA 갭 {gap:+.2f}% < +{GAP_SOXS['gap_min_pct']}% → 미진입")}


def news_sentiment_avg() -> Optional[float]:
    rep = _load(os.path.join(DATA, "news_sentiment_report.json"))
    v = rep.get("average_sentiment_score")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
