"""Fourth batch: the only structure that produces a P&L *every day* — a trend-
filtered leveraged core (Gayed & Bilello 2016 style, SMA200 risk switch), measured
over 5 years so the daily distribution is comparable with the dip overlay.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from btlib import RT_COST, SIDE_COST
from strategies import daily_frame

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "research_trend_20261118.json")


def sma_system(sym: str, signal_sym: str = "SPY", sma: int = 200,
               cash_sym: str = "SGOV", period: str = "5y", rt_cost: float = RT_COST,
               leverage_signal: bool = True) -> dict:
    """Hold `sym` while signal_sym (or sym) closes above its own SMA; else hold cash."""
    d = daily_frame(sym, period)
    sig = daily_frame(signal_sym, period)
    idx = d.index.intersection(sig.index)
    d, sig = d.loc[idx], sig.loc[idx]
    ref = sig["Close"] if leverage_signal else d["Close"]
    s = ref.rolling(sma).mean()
    pos = (ref > s).astype(int)
    ret = d["Close"].pct_change().fillna(0)
    cash_ret = pd.Series(0.0452 / 252, index=d.index)
    strat = pos.shift(1).fillna(0) * ret + (1 - pos.shift(1).fillna(0)) * cash_ret
    # count switches for cost
    switches = int((pos.diff().abs() > 0).sum())
    strat = strat - pos.shift(1).diff().abs().fillna(0) * rt_cost
    # buy & hold
    bh = ret + (1 - ret) * 0  # raw
    def _m(x):
        eq = (1 + x).cumprod()
        dd = (eq / eq.cummax() - 1).min() * 100
        return {
            "total_pct": round(float(eq.iloc[-1] - 1) * 100, 2),
            "cagr_pct": round(float(eq.iloc[-1] ** (252 / len(eq)) - 1) * 100, 2),
            "mdd_pct": round(float(dd), 2),
            "sharpe": round(float(x.mean() / x.std() * np.sqrt(252)), 2) if x.std() else None,
            "pos_day_pct": round(float((x > 0).mean()) * 100, 1),
            "worst_day_pct": round(float(x.min()) * 100, 2),
            "best_day_pct": round(float(x.max()) * 100, 2),
            "avg_day_pct": round(float(x.mean()) * 100, 3),
        }
    return {"symbol": sym, "period": f"{d.index[0].date()}~{d.index[-1].date()}", "days": len(d),
            "exposure_pct": round(float(pos.shift(1).fillna(0).mean()) * 100, 1),
            "switches": switches, "switch_cost_pct": round(switches * rt_cost * 100, 2),
            "sma_system": _m(strat), "buy_hold": _m(ret)}


if __name__ == "__main__":
    out = {}
    for sym, lev in (("TQQQ", True), ("SOXL", True), ("QQQ", True), ("SPY", True)):
        out[f"{sym}_SMA200_on_underlying"] = sma_system(sym, "SPY", 200, leverage_signal=True)
        out[f"{sym}_SMA200_on_self"] = sma_system(sym, sym, 200, leverage_signal=False)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for k, v in out.items():
        print(k, v["period"], "| exposure", v["exposure_pct"], "| switches", v["switches"],
              "|", v["sma_system"], "| BH", v["buy_hold"])
