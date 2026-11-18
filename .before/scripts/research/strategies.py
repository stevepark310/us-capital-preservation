"""Strategy implementations for the 2026-11-18 research study.

Each function returns a list of trades:
  {"date": session date, "entry_ts", "exit_ts", "entry", "exit", "ret_pct", "hold_min", "reason"}
`ret_pct` is NET of the round-trip cost (default 0.24%).

No look-ahead: every decision uses bars up to and including the decision bar, and the
fill is that bar's close (or the next bar's open where the paper says so).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from btlib import RT_COST, SIDE_COST, et, load, sessionize


def _net(entry: float, exit_px: float, rt_cost: float = RT_COST) -> float:
    return (exit_px / entry - 1) * 100 - rt_cost * 100


def _trade(day, entry_ts, exit_ts, entry, exit_px, reason, rt_cost=RT_COST) -> Dict:
    hold = (pd.Timestamp(exit_ts) - pd.Timestamp(entry_ts)).total_seconds() / 60.0
    return {"date": str(pd.Timestamp(day).date()), "entry_ts": str(entry_ts), "exit_ts": str(exit_ts),
            "entry": round(float(entry), 4), "exit": round(float(exit_px), 4),
            "ret_pct": round(_net(entry, exit_px, rt_cost), 3),
            "hold_min": round(hold, 0), "reason": reason}


# --------------------------------------------------------------- helpers
def atr14(daily: pd.DataFrame) -> pd.Series:
    h, l, c = daily["High"], daily["Low"], daily["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean()


def daily_frame(sym: str, period: str = "5y") -> pd.DataFrame:
    d = et(load(sym, "1d", period))
    d = d.copy()
    d.index = pd.to_datetime([str(x)[:10] for x in d.index])
    d["ATR14"] = atr14(d)
    return d


# --------------------------------------------------------------- 1) ORB 5m
def orb5(sym: str, direction: str = "both", stop_mode: str = "range",
         rvol_min: Optional[float] = None, target_r: Optional[float] = None,
         eod: str = "15:55", rt_cost: float = RT_COST,
         start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
    """Zarattini & Aziz (2023) 5-minute ORB.

    direction: 'both' (paper baseline, synthetic short) | 'long' (implementable long-only)
    stop_mode: 'range' = opposite extreme of the first 5-min bar (paper baseline)
               'atr'   = 10% of ATR14 below entry (Barbon/Zarattini/Aziz 2024)
    rvol_min: filter on first-bar relative volume vs prior 14 sessions (Barbon et al.)
    target_r: profit target in R (paper baseline 10R); None = hold to EOD
    """
    bars = et(load(sym, "5m", "60d"))
    daily = daily_frame(sym)
    sess = sessionize(bars)
    days = sorted(sess.keys())
    atr_map = {}
    for i, day in enumerate(days):
        prev = days[i - 1] if i else None
        if prev is not None and prev in daily.index:
            atr_map[day] = float(daily.loc[prev, "ATR14"])
        elif len(daily):
            atr_map[day] = float(daily["ATR14"].iloc[-1])

    # first-bar volume history for RVOL
    first_vol = {d: float(sess[d]["Volume"].iloc[0]) for d in days}
    trades: List[Dict] = []
    for i, day in enumerate(days):
        g = sess[day]
        if len(g) < 6:
            continue
        if start and day < pd.Timestamp(start):
            continue
        if end and day > pd.Timestamp(end):
            continue
        o, c = float(g["Open"].iloc[0]), float(g["Close"].iloc[0])
        hi, lo = float(g["High"].iloc[0]), float(g["Low"].iloc[0])
        if o == c:
            continue
        bull = c > o
        if direction == "long" and not bull:
            continue
        if rvol_min is not None:
            hist = [first_vol[days[j]] for j in range(max(0, i - 14), i)]
            if not hist:
                continue
            avg = float(np.mean(hist))
            if avg <= 0 or first_vol[day] / avg < rvol_min:
                continue
        entry = float(g["Open"].iloc[1])                 # paper: open of 2nd 5-min candle
        entry_ts = g.index[1]
        if stop_mode == "range":
            stop = lo if bull else hi
        else:
            atr = atr_map.get(day) or 0.0
            dist = 0.10 * atr
            if dist <= 0:
                continue
            stop = entry - dist if bull else entry + dist
        risk = abs(entry - stop)
        if risk <= 0 or risk / entry > 0.08:             # nonsensical / gap-risk guard
            continue
        tgt = (entry + target_r * risk) if (target_r and bull) else \
              (entry - target_r * risk) if target_r else None
        exit_px, exit_ts, reason = None, g.index[-1], "EOD"
        for j in range(1, len(g)):
            bar = g.iloc[j]
            if bull:
                if float(bar["Low"]) <= stop:
                    exit_px, exit_ts, reason = stop, g.index[j], "STOP"
                    break
                if tgt and float(bar["High"]) >= tgt:
                    exit_px, exit_ts, reason = tgt, g.index[j], "TARGET"
                    break
            else:
                if float(bar["High"]) >= stop:
                    exit_px, exit_ts, reason = stop, g.index[j], "STOP"
                    break
                if tgt and float(bar["Low"]) <= tgt:
                    exit_px, exit_ts, reason = tgt, g.index[j], "TARGET"
                    break
        if exit_px is None:
            idx = [k for k in g.index if k.strftime("%H:%M") <= eod]
            exit_ts = idx[-1] if idx else g.index[-1]
            exit_px = float(g.loc[exit_ts, "Close"])
        if not bull:                                     # synthetic short
            ret = (1 - exit_px / entry) * 100 - rt_cost * 100
            tr = _trade(day, entry_ts, exit_ts, entry, entry, "SHORT", 0.0)
            tr.update({"ret_pct": round(ret, 3), "exit": round(exit_px, 4),
                       "short": True, "reason": reason})
            trades.append(tr)
        else:
            trades.append(_trade(day, entry_ts, exit_ts, entry, exit_px, reason, rt_cost))
    return trades


# ------------------------------------------------ 2) intraday momentum (late)
def intraday_momentum(sym: str, entry_hhmm: str = "15:30", exit_hhmm: str = "15:55",
                      direction: str = "long", include_r12: bool = False,
                      rt_cost: float = RT_COST) -> List[Dict]:
    """Gao, Han, Li & Zhou (2018): sign of the first half-hour return (from the prior
    close) predicts the last half-hour return. Entry at `entry_hhmm`, exit at `exit_hhmm`.
    """
    bars = et(load(sym, "5m", "60d"))
    sess = sessionize(bars)
    days = sorted(sess.keys())
    trades = []
    for i, day in enumerate(days):
        g = sess[day]
        if i == 0 or len(g) < 40:
            continue
        prev_close = float(sess[days[i - 1]]["Close"].iloc[-1])
        first = g[g.index.strftime("%H:%M") <= "10:00"]
        if len(first) == 0:
            continue
        r1 = float(g.loc[first.index[-1], "Close"]) / prev_close - 1
        r12 = None
        if include_r12:
            b = g[g.index.strftime("%H:%M") == "15:00"]
            b2 = g[g.index.strftime("%H:%M") == "15:30"]
            if len(b) and len(b2):
                r12 = float(b2["Close"].iloc[0]) / float(b["Close"].iloc[0]) - 1
        ent = g[g.index.strftime("%H:%M") == entry_hhmm]
        ext = g[g.index.strftime("%H:%M") == exit_hhmm]
        if len(ent) == 0 or len(ext) == 0:
            continue
        long_ok = r1 > 0
        if include_r12 and r12 is not None:
            long_ok = (r1 + r12) > 0
        if direction == "long" and not long_ok:
            continue
        entry = float(ent["Close"].iloc[0])
        exit_px = float(ext["Close"].iloc[0])
        if direction == "both" and not long_ok:
            ret = (1 - exit_px / entry) * 100 - rt_cost * 100
            tr = _trade(day, ent.index[0], ext.index[0], entry, entry, "SHORT", 0.0)
            tr.update({"ret_pct": round(ret, 3), "exit": round(exit_px, 4), "short": True})
            trades.append(tr)
        else:
            trades.append(_trade(day, ent.index[0], ext.index[0], entry, exit_px, "EOD", rt_cost))
    return trades


# ------------------------------------------------------- 3) overnight drift
def overnight_drift(sym: str, long_only: bool = True, rt_cost: float = RT_COST,
                    period: str = "5y", start: Optional[str] = None) -> List[Dict]:
    """Buy at the close, sell at the next open (close-to-open drift)."""
    d = daily_frame(sym, period)
    if start:
        d = d[d.index >= pd.Timestamp(start)]
    trades = []
    idx = list(d.index)
    for i in range(len(idx) - 1):
        row = d.loc[idx[i]]
        nxt = d.loc[idx[i + 1]]
        entry = float(row["Close"])
        exit_px = float(nxt["Open"])
        trades.append(_trade(idx[i + 1], idx[i], idx[i + 1], entry, exit_px, "OPEN", rt_cost))
    return trades


def overnight_drift_hour(sym: str, buy_hhmm: str = "15:00", sell_hhmm: str = "09:35") -> List[Dict]:
    """Intraday-executable overnight: buy the 15:00 5-min bar, sell the 09:35 bar next day."""
    bars = et(load(sym, "5m", "60d"))
    sess = sessionize(bars)
    days = sorted(sess.keys())
    trades = []
    for i in range(len(days) - 1):
        g, nx = sess[days[i]], sess[days[i + 1]]
        b = g[g.index.strftime("%H:%M") == buy_hhmm]
        s = nx[nx.index.strftime("%H:%M") == sell_hhmm]
        if len(b) == 0 or len(s) == 0:
            continue
        trades.append(_trade(days[i + 1], b.index[0], s.index[0],
                             float(b["Close"].iloc[0]), float(s["Open"].iloc[0]), "OVERNIGHT"))
    return trades


# ------------------------------------------------- 4) VWAP-band reversion 5m
def vwap_reversion(sym: str, k: float = 2.0, rsi2_max: float = 10.0,
                   stop_pct: float = 1.5, eod: str = "15:55",
                   rt_cost: float = RT_COST) -> List[Dict]:
    """Intraday mean reversion: buy when price is k sigma below the session VWAP band
    with RSI2 <= rsi2_max; exit at VWAP touch, stop, or EOD."""
    bars = et(load(sym, "5m", "60d"))
    sess = sessionize(bars)
    trades = []
    for day, g in sess.items():
        if len(g) < 30:
            continue
        tp = (g["High"] + g["Low"] + g["Close"]) / 3
        cum_v = g["Volume"].cumsum().replace(0, np.nan)
        vwap = (tp * g["Volume"]).cumsum() / cum_v
        dev = (g["Close"] - vwap)
        sd = dev.expanding(20).std()
        delta = g["Close"].diff()
        up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
        dn = (-delta).clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
        rsi2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
        pos = None
        for j in range(20, len(g)):
            bar = g.iloc[j]
            tstr = g.index[j].strftime("%H:%M")
            if pos is None:
                if tstr >= "13:00":
                    continue
                if (float(bar["Close"]) < float(vwap.iloc[j]) - k * float(sd.iloc[j] or 0)
                        and float(rsi2.iloc[j] or 50) <= rsi2_max):
                    pos = {"entry": float(bar["Close"]), "ts": g.index[j],
                           "stop": float(bar["Close"]) * (1 - stop_pct / 100),
                           "vwap_at": j}
                    continue
            else:
                if float(bar["Low"]) <= pos["stop"]:
                    trades.append(_trade(day, pos["ts"], g.index[j], pos["entry"], pos["stop"], "STOP", rt_cost))
                    pos = None
                    continue
                if float(bar["Close"]) >= float(vwap.iloc[j]) or tstr >= eod:
                    px = float(vwap.iloc[j]) if float(bar["Close"]) >= float(vwap.iloc[j]) else float(bar["Close"])
                    trades.append(_trade(day, pos["ts"], g.index[j], pos["entry"], px, "VWAP" if px != float(bar["Close"]) else "EOD", rt_cost))
                    pos = None
        if pos is not None:
            trades.append(_trade(day, pos["ts"], g.index[-1], pos["entry"], float(g["Close"].iloc[-1]), "EOD", rt_cost))
    return trades


# --------------------------------------- 5) ORB 15m (Barbon/Zarattini/Aziz)
def orb15(sym: str, direction: str = "long", stop_atr_frac: float = 0.10,
          rvol_min: Optional[float] = None, rt_cost: float = RT_COST) -> List[Dict]:
    bars = et(load(sym, "15m", "60d"))
    daily = daily_frame(sym)
    sess = sessionize(bars)
    days = sorted(sess.keys())
    first_vol = {d: float(sess[d]["Volume"].iloc[0]) for d in days}
    trades = []
    for i, day in enumerate(days):
        g = sess[day]
        if len(g) < 6:
            continue
        o, c = float(g["Open"].iloc[0]), float(g["Close"].iloc[0])
        hi, lo = float(g["High"].iloc[0]), float(g["Low"].iloc[0])
        if o == c:
            continue
        bull = c > o
        if direction == "long" and not bull:
            continue
        if rvol_min is not None:
            hist = [first_vol[days[j]] for j in range(max(0, i - 14), i)]
            if not hist or first_vol[day] / max(1e-9, float(np.mean(hist))) < rvol_min:
                continue
        prev = days[i - 1] if i else None
        atr = float(daily.loc[prev, "ATR14"]) if prev is not None and prev in daily.index else 0.0
        entry = float(g["Open"].iloc[1])
        dist = stop_atr_frac * atr
        if dist <= 0:
            continue
        stop = entry - dist if bull else entry + dist
        exit_px, exit_ts, reason = None, g.index[-1], "EOD"
        for j in range(1, len(g)):
            bar = g.iloc[j]
            if bull and float(bar["Low"]) <= stop:
                exit_px, exit_ts, reason = stop, g.index[j], "STOP"
                break
            if (not bull) and float(bar["High"]) >= stop:
                exit_px, exit_ts, reason = stop, g.index[j], "STOP"
                break
        if exit_px is None:
            idx = [x for x in g.index if x.strftime("%H:%M") <= "15:55"]
            exit_ts = idx[-1] if idx else g.index[-1]
            exit_px = float(g.loc[exit_ts, "Close"])
        if not bull:
            ret = (1 - exit_px / entry) * 100 - rt_cost * 100
            tr = _trade(day, g.index[1], exit_ts, entry, entry, "SHORT", 0.0)
            tr.update({"ret_pct": round(ret, 3), "exit": round(exit_px, 4), "short": True, "reason": reason})
            trades.append(tr)
        else:
            trades.append(_trade(day, g.index[1], exit_ts, entry, exit_px, reason, rt_cost))
    return trades
