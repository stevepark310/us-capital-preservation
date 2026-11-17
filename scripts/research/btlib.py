"""Research backtest harness (2026-11-18 study).

- Data: yfinance, cached under data_cache/research/*.pkl
- Cost: ROUND_TRIP 0.24% default (12bp per side), adjustable
- Fills: signal-bar close +/- half the round-trip cost (documented, no look-ahead
  beyond the bar that produced the signal)
- Sizing: integer shares for a fixed $ budget (NH Open API takes whole shares)

Nothing here touches live trading state.
"""
from __future__ import annotations

import os
import pickle
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(BASE_DIR, "data_cache", "research")
os.makedirs(CACHE, exist_ok=True)

RT_COST = 0.0024          # round trip 0.24%
SIDE_COST = RT_COST / 2


def load(sym: str, interval: str, period: str) -> pd.DataFrame:
    path = os.path.join(CACHE, f"{sym}_{interval}_{period}.pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                df = pickle.load(f)
            if df is not None and len(df):
                return df
        except Exception:
            pass
    import yfinance as yf
    df = yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=False)
    if df is None:
        return pd.DataFrame()
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(how="all")
    if len(df):
        with open(path, "wb") as f:
            pickle.dump(df, f)
    return df


def et(df: pd.DataFrame) -> pd.DataFrame:
    """Index -> naive US/Eastern session times."""
    d = df.copy()
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert("America/New_York").tz_localize(None)
    return d


def sessionize(df5: pd.DataFrame) -> Dict[pd.Timestamp, pd.DataFrame]:
    out = {}
    for day, g in df5.groupby(df5.index.date):
        out[pd.Timestamp(day)] = g.copy()
    return out


# ---------------------------------------------------------------- statistics
def stats(trades: List[Dict], budget: float = 90.0, label: str = "") -> Dict:
    """Trade-level statistics net of cost. `ret_pct` is net."""
    rows = [t for t in trades if t.get("ret_pct") is not None]
    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0}
    r = np.array([float(t["ret_pct"]) for t in rows])
    wins, losses = r[r > 0], r[r <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    # equity path in % of a notional that puts one trade at `budget`
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    mdd = float((eq - peak).min()) if n else 0.0
    tstat = float(r.mean() / (r.std(ddof=1) / np.sqrt(n))) if n > 2 and r.std(ddof=1) > 0 else None
    days = sorted({t.get("date") for t in rows if t.get("date")})
    day_pnl: Dict[str, float] = {}
    for t in rows:
        d = str(t.get("date"))
        day_pnl[d] = day_pnl.get(d, 0.0) + float(t["ret_pct"])
    dp = np.array(list(day_pnl.values()))
    # per-day p&l expressed in % of `budget` notional
    streak, worst_streak = 0, 0
    for x in r:
        if x <= 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0
    return {
        "label": label, "n": n,
        "win_pct": round(100 * len(wins) / n, 1),
        "ev_pct": round(float(r.mean()), 3),
        "median_pct": round(float(np.median(r)), 3),
        "pf": round(gross_win / gross_loss, 2) if gross_loss else None,
        "best_pct": round(float(r.max()), 2), "worst_pct": round(float(r.min()), 2),
        "mdd_pct": round(mdd, 2),
        "t_stat": round(tstat, 2) if tstat is not None else None,
        "avg_win_pct": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_pct": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "days_traded": len(days),
        "pos_day_pct": round(100 * float((dp > 0).mean()), 1) if len(dp) else None,
        "ev_usd_at_budget": round(float(r.mean()) / 100 * budget, 2),
        "usd_mdd_at_budget": round(mdd / 100 * budget, 2),
        "worst_streak": worst_streak,
        "avg_hold_min": (round(float(np.mean([t["hold_min"] for t in rows])), 0)
                         if all(t.get("hold_min") is not None for t in rows) else None),
    }


def fmt_row(s: Dict) -> str:
    keys = ["label", "n", "win_pct", "ev_pct", "pf", "t_stat", "mdd_pct", "pos_day_pct",
            "ev_usd_at_budget", "worst_streak"]
    return " | ".join(f"{s.get(k)}" for k in keys)
