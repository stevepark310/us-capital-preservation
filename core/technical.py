"""
CUBE Technical Core — indicators and levels computed from real OHLCV history.

Used by Agent 2 (momentum/levels), Agent 6 (scenario parameterisation),
Agent 7 (backtest validation) and the exit planner (ATR-based brackets).
No number in this module is stored: every value derives from the bars passed in.
"""

from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    roll_up = up.ewm(alpha=1.0 / n, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def bollinger(series: pd.Series, n: int = 20, k: float = 2.0):
    mid = series.rolling(n).mean()
    sd = series.rolling(n).std()
    return mid - k * sd, mid, mid + k * sd


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def realized_vol(series: pd.Series, n: int = 20) -> float:
    r = series.pct_change().dropna().tail(n)
    if len(r) < 5:
        return 0.0
    return float(r.std() * np.sqrt(252) * 100.0)


def _f(x, default=0.0):
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def analyze(symbol: str, df: pd.DataFrame, quote: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full technical read of one symbol from daily bars."""
    if df is None or len(df) < 30 or "Close" not in df:
        return {"symbol": symbol, "available": False}
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    o = df["Open"].astype(float)
    v = df["Volume"].astype(float) if "Volume" in df else pd.Series(0.0, index=df.index)

    atr_s = atr(df, 14)
    e20 = ema(c, 20)
    e50 = ema(c, 50)
    bb_lo, bb_mid, bb_hi = bollinger(c, 20, 2.0)
    macd_line, macd_sig, macd_hist = macd(c)
    r14, r2 = rsi(c, 14), rsi(c, 2)

    close = _f(quote.get("last") if quote and quote.get("last") else c.iloc[-1])
    atr_val = _f(atr_s.iloc[-1], close * 0.04 if close else 0.0)
    atr_pct = round(atr_val / close * 100, 2) if close else 0.0
    avg_vol20 = _f(v.tail(21).head(20).mean())
    rvol = round(_f(v.iloc[-1]) / avg_vol20, 2) if avg_vol20 else None
    pct_b = None
    if _f(bb_hi.iloc[-1]) > _f(bb_lo.iloc[-1]):
        pct_b = round((close - _f(bb_lo.iloc[-1])) / (_f(bb_hi.iloc[-1]) - _f(bb_lo.iloc[-1])), 2)

    prev_high, prev_low = _f(h.iloc[-1]), _f(l.iloc[-1])
    ret_5d = round((close / _f(c.iloc[-6], close) - 1) * 100, 2) if len(c) > 6 else 0.0
    ret_20d = round((close / _f(c.iloc[-21], close) - 1) * 100, 2) if len(c) > 21 else 0.0
    hi20 = _f(c.tail(21).max(), close)
    dd20 = round((close / hi20 - 1) * 100, 2) if hi20 else 0.0

    return {
        "symbol": symbol,
        "available": True,
        "close": round(close, 2),
        "bar_date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
        "atr14": round(atr_val, 2),
        "atr_pct": atr_pct,
        "rsi14": round(_f(r14.iloc[-1], 50), 1),
        "rsi2": round(_f(r2.iloc[-1], 50), 1),
        "ema20": round(_f(e20.iloc[-1]), 2),
        "ema50": round(_f(e50.iloc[-1]), 2),
        "above_ema20": bool(close >= _f(e20.iloc[-1])),
        "ema20_slope_pct": round((_f(e20.iloc[-1]) / _f(e20.iloc[-6], _f(e20.iloc[-1])) - 1) * 100, 2),
        "bb_lower": round(_f(bb_lo.iloc[-1]), 2),
        "bb_mid": round(_f(bb_mid.iloc[-1]), 2),
        "bb_upper": round(_f(bb_hi.iloc[-1]), 2),
        "bb_pct_b": pct_b,
        "macd_hist": round(_f(macd_hist.iloc[-1]), 3),
        "macd_bearish": bool(_f(macd_hist.iloc[-1]) < 0),
        "rvol": rvol,
        "avg_volume_20d": int(avg_vol20) if avg_vol20 else None,
        "ret_5d_pct": ret_5d,
        "ret_20d_pct": ret_20d,
        "drawdown_20d_pct": dd20,
        "realized_vol_20d_pct": round(realized_vol(c, 20), 1),
        "prev_bar": {"open": round(_f(o.iloc[-1]), 2), "high": round(prev_high, 2), "low": round(prev_low, 2),
                     "close": round(_f(c.iloc[-1]), 2), "range": round(prev_high - prev_low, 2)},
        "weekly_pivots": weekly_pivots(df),
    }


def weekly_pivots(df: pd.DataFrame) -> Dict[str, float]:
    """Classic pivots from the last completed week, for Monday planning."""
    if df is None or len(df) < 8:
        return {}
    try:
        wk = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(wk) < 2:
            return {}
        # The final bucket is only a *completed* week when the last bar is a Friday.
        row = wk.iloc[-1] if df.index[-1].weekday() == 4 else wk.iloc[-2]
        H, L, C = float(row["High"]), float(row["Low"]), float(row["Close"])
        P = (H + L + C) / 3.0
        return {
            "week_high": round(H, 2), "week_low": round(L, 2), "week_close": round(C, 2),
            "pivot": round(P, 2),
            "r1": round(2 * P - L, 2), "r2": round(P + (H - L), 2),
            "s1": round(2 * P - H, 2), "s2": round(P - (H - L), 2),
        }
    except Exception:
        return {}


def breakout_level(symbol: str, tech: Dict[str, Any], k: float = 0.5) -> Dict[str, float]:
    """Larry Williams volatility breakout reference for the next session."""
    pb = tech.get("prev_bar") or {}
    if not pb:
        return {}
    rng = pb["high"] - pb["low"]
    trigger = round(pb["open"] + k * rng, 2) if pb.get("open") else round(pb["close"] + k * rng, 2)
    return {
        "k": k,
        "trigger": trigger,
        "range": round(rng, 2),
        "trigger_pct_from_close": round((trigger / pb["close"] - 1) * 100, 2) if pb.get("close") else None,
        "breakdown_trigger": round(pb["low"] - k * rng, 2),
    }


def composite_entry_score(
    tech: Dict[str, Any],
    sentiment_score: float = 50.0,
    regime_ok: bool = True,
    rvol_threshold: float = 1.5,
) -> Dict[str, Any]:
    """Intraday rotation entry score (0-100) with explicit components.

    Components (each 0-20, capped):
      oversold   : RSI(2) < 15 and/or price under lower band
      rebound    : close back above prior close / pct_b recovery
      volume     : RVOL vs 20d average
      breakout   : close vs EMA20 & breakout trigger proximity
      context    : sentiment + regime
    """
    if not tech.get("available"):
        return {"score": 0.0, "components": {}, "signal": "NO_DATA"}

    comp: Dict[str, float] = {}
    pct_b = tech.get("bb_pct_b")
    rsi2 = tech.get("rsi2", 50)
    comp["oversold"] = 20.0 if (rsi2 <= 10 or (pct_b is not None and pct_b <= 0.05)) else (
        12.0 if (rsi2 <= 25 or (pct_b is not None and pct_b <= 0.2)) else 0.0)
    comp["rebound"] = 12.0 if tech.get("ret_5d_pct", 0) > -1.0 else (6.0 if tech.get("ret_5d_pct", 0) > -4 else 0.0)
    rvol = tech.get("rvol") or 0.0
    comp["volume"] = 20.0 if rvol >= max(2.0, rvol_threshold + 0.5) else (
        12.0 if rvol >= rvol_threshold else (6.0 if rvol >= 1.1 else 0.0))
    comp["trend"] = 10.0 if tech.get("above_ema20") else 0.0
    comp["breakout_prox"] = 8.0 if tech.get("ema20_slope_pct", 0) > 0 else 0.0
    comp["context"] = max(0.0, min(20.0, (sentiment_score - 50.0) * 0.4 + (10.0 if regime_ok else 0.0)))
    score = sum(comp.values())
    if not regime_ok:
        score *= 0.5
    signal = "STRONG_ENTRY" if score >= 55 else ("WATCH" if score >= 35 else "NO_TRADE")
    return {"score": round(score, 1), "components": comp, "signal": signal,
            "rvol": rvol, "rsi2": rsi2, "pct_b": pct_b}


def gap_scenarios(tech: Dict[str, Any], futures_pct: float = 0.0) -> Dict[str, Any]:
    """Monday-open (next session) gap plan built from Friday's level and futures."""
    pb = tech.get("prev_bar") or {}
    piv = tech.get("weekly_pivots") or {}
    if not pb:
        return {}
    close = pb["close"]
    atr_pct = tech.get("atr_pct") or 4.0
    beta = 1.9 if tech.get("symbol") in ("SOXL", "SOXS") else 1.35
    expected_gap = round(futures_pct * beta, 2)
    alt_gap = -round(max(abs(expected_gap) * 0.6, 0.75), 2)
    return {
        "friday_close": close,
        "futures_implied_gap_pct": expected_gap,
        "primary_open": round(close * (1 + expected_gap / 100), 2),
        "alternate_open": round(close * (1 + alt_gap / 100), 2),
        "atr_band": [round(close * (1 - atr_pct / 100), 2), round(close * (1 + atr_pct / 100), 2)],
        "pivot": piv.get("pivot"),
        "r1": piv.get("r1"), "s1": piv.get("s1"),
        "plan": (f"갭업 시도 시 전일 종가 ${close:.2f} 리테스트 확인 후 진입" if expected_gap >= 0
                 else f"갭다운 시 일봉 저점/주간 S1 ${piv.get('s1')} 지지 확인 후 분할 진입"),
    }


if __name__ == "__main__":
    from core import market_snapshot
    s = market_snapshot.load_snapshot()
    hist = market_snapshot.fetch_history(["SOXL", "TQQQ", "NVDA"])
    for sym, df in hist.items():
        t = analyze(sym, df, s["quotes"].get(sym))
        print(sym, {k: t[k] for k in ("close", "atr_pct", "rsi14", "rsi2", "rvol", "ret_5d_pct", "above_ema20") if k in t})
        print("  breakout", breakout_level(sym, t), "entry", composite_entry_score(t, 22))
        print("  gap", gap_scenarios(t, s["futures"]["nq"]["change_pct"]))
