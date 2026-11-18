"""A/B backtests for the 2026-11-18 study — daily-bar dip/trend systems with
integer-share sizing at a real $ budget, plus an intraday ORB overlay.

All returns NET of the round-trip cost (default 0.24%).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from btlib import RT_COST, SIDE_COST, stats
from strategies import daily_frame, atr14

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "research_ab_20261118.json")

DIP = {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0}


def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)


def prep(sym: str, period: str = "5y") -> pd.DataFrame:
    d = daily_frame(sym, period).copy()
    c = d["Close"]
    d["RSI2"] = _rsi(c, 2)
    d["RSI14"] = _rsi(c, 14)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    d["BB_L"] = mid - 2 * sd
    d["BB_U"] = mid + 2 * sd
    d["PCT_B"] = ((c - d["BB_L"]) / (d["BB_U"] - d["BB_L"]).replace(0, np.nan)).fillna(0.5)
    d["RVOL"] = d["Volume"] / d["Volume"].rolling(20).mean().shift(1)
    d["ATR_PCT"] = d["ATR14"] / c * 100
    return d


def vix_series() -> pd.Series:
    v = daily_frame("^VIX", "5y")
    return v["Close"]


def dip_trades(sym: str, budget: float, params: Optional[Dict] = None,
               tp_atr: float = 1.5, sl_atr: float = 1.5, max_hold: int = 3,
               vix_max: Optional[float] = None, vix_min: Optional[float] = None,
               rt_cost: float = RT_COST, period: str = "5y",
               vix_tier: bool = False, start: Optional[str] = None) -> List[Dict]:
    """Buy the next open after a dip signal; exit at TP/SL/time; 1 contract = 1 share.

    Sizing: shares = floor(budget / entry) but at least 1 only if budget >= price.
    """
    p = dict(DIP)
    p.update(params or {})
    d = prep(sym, period)
    vix = vix_series()
    trades = []
    i = 30
    n = len(d)
    while i < n - 1:
        row = d.iloc[i]
        if start and d.index[i] < pd.Timestamp(start):
            i += 1
            continue
        sig = (float(row["RSI2"]) <= p["rsi2_max"] and float(row["PCT_B"]) <= p["pct_b_max"]
               and np.isfinite(row["RVOL"]) and float(row["RVOL"]) >= p["rvol_min"])
        if not sig:
            i += 1
            continue
        day = d.index[i + 1]
        v = None
        try:
            v = float(vix.loc[:day].iloc[-1])
        except Exception:
            pass
        if vix_max is not None and v is not None and v > vix_max:
            i += 1
            continue
        if vix_min is not None and v is not None and v < vix_min:
            i += 1
            continue
        entry = float(d.iloc[i + 1]["Open"]) * (1 + rt_cost / 2)
        atr = float(row["ATR14"])
        qty = int(budget // entry)
        if qty < 1:
            i += 1
            continue
        tp = entry + tp_atr * atr
        sl = entry - sl_atr * atr
        exit_px, exit_day, reason = None, None, "TIME"
        for k in range(i + 1, min(n, i + 1 + max_hold)):
            bar = d.iloc[k]
            if float(bar["Low"]) <= sl:
                exit_px, exit_day, reason = sl, d.index[k], "STOP"
                break
            if float(bar["High"]) >= tp:
                exit_px, exit_day, reason = tp, d.index[k], "TARGET"
                break
        if exit_px is None:
            k = min(n - 1, i + max_hold)
            exit_px, exit_day, reason = float(d.iloc[k]["Close"]) * (1 - rt_cost / 2), d.index[k], "TIME"
        ret = (exit_px / entry - 1) * 100
        trades.append({
            "date": str(day.date()), "exit_date": str(exit_day.date()), "ticker": sym,
            "entry": round(entry, 2), "exit": round(exit_px, 2), "qty": qty,
            "ret_pct": round(ret, 3), "pnl_usd": round((exit_px - entry) * qty, 2),
            "reason": reason, "vix": round(v, 1) if v else None,
            "hold_min": (exit_day - day).days * 390,
        })
        i = max(i + 1, d.index.get_loc(exit_day) if exit_day in d.index else i + 1)
    return trades


def portfolio(trades: List[Dict], equity0: float, start: str, end: str,
              carry_apr: float = 0.0452) -> Dict:
    """Daily equity path: trade P&L on entry/exit days, SGOV carry on free cash."""
    days = pd.bdate_range(start, end)
    pnl = pd.Series(0.0, index=days)
    used = pd.Series(0.0, index=days)
    for t in trades:
        d = pd.Timestamp(t["exit_date"])
        if d in pnl.index:
            pnl.loc[d] += float(t["pnl_usd"])
    eq = equity0 + pnl.cumsum()
    daily_ret = eq.pct_change().fillna(0.0) + carry_apr / 252 * 0.6
    curve = equity0 * (1 + daily_ret).cumprod()
    dd = (curve / curve.cummax() - 1) * 100
    active = (pnl != 0)
    return {
        "days": len(days), "trades": len(trades),
        "total_usd": round(float(pnl.sum() + equity0 * (carry_apr / 252 * 0.6) * len(days)), 2),
        "total_pct": round(float(curve.iloc[-1] / equity0 - 1) * 100, 2),
        "mdd_pct": round(float(dd.min()), 2),
        "positive_day_pct": round(100 * float((daily_ret > 0).mean()), 1),
        "active_day_pct": round(100 * float(active.mean()), 1),
        "sharpe": round(float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)), 2) if daily_ret.std() else None,
        "avg_usd_per_active_day": round(float(pnl[active].mean()), 2) if active.any() else 0.0,
    }


def run_dip_matrix() -> Dict:
    out = {}
    variants = {
        "DIP_SWING_A0_baseline_1x1.5_hold3": dict(tp_atr=1.5, sl_atr=1.5, max_hold=3),
        "DIP_SWING_A1_VIXmax28": dict(tp_atr=1.5, sl_atr=1.5, max_hold=3, vix_max=28),
        "DIP_SWING_A2_VIXtier_24": dict(tp_atr=1.5, sl_atr=1.5, max_hold=3, vix_max=28, vix_min=0),
        "DIP_SWING_A3_2.0ATR_hold3": dict(tp_atr=2.0, sl_atr=2.0, max_hold=3),
        "DIP_SWING_A4_1.5ATR_hold5": dict(tp_atr=1.5, sl_atr=1.5, max_hold=5),
        "DIP_SWING_A5_1.5ATR_hold2": dict(tp_atr=1.5, sl_atr=1.5, max_hold=2),
    }
    for period in ("5y", "2y", "1y"):
        for name, kw in variants.items():
            for sym in ("SOXL", "TQQQ"):
                t = dip_trades(sym, 90.0, period=period, **kw)
                out[f"{period}|{name}|{sym}"] = stats(t, 90.0, f"{period} {name} {sym}")
    return out


def run_size_study() -> Dict:
    """Same signal, three budgets, integer shares — the small-account reality."""
    out = {}
    for budget in (44.8, 60, 90, 150):
        for sym in ("SOXL", "TQQQ", "TSLL", "NVDL"):
            t = dip_trades(sym, budget, period="2y")
            s = stats(t, budget, f"budget={budget} {sym}")
            s["budget"] = budget
            s["shares_used"] = sorted({x["qty"] for x in t}) if t else []
            s["total_usd"] = round(sum(x["pnl_usd"] for x in t), 2)
            out[f"{budget}|{sym}"] = s
    return out


def run_intraday_overlay() -> Dict:
    from strategies import orb5
    out = {}
    for sym in ("SOXL", "TQQQ", "QQQ"):
        for rv in (None, 1.0, 1.5):
            t = orb5(sym, direction="long", rvol_min=rv)
            out[f"ORB5|{sym}|rvol={rv}"] = stats(t, 90.0, f"ORB5 {sym} rvol={rv}")
    return out


if __name__ == "__main__":
    res = {"dip_matrix": run_dip_matrix(), "size": run_size_study(),
           "intraday": run_intraday_overlay()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    for k, v in res["dip_matrix"].items():
        print(k, "|n", v.get("n"), "|win", v.get("win_pct"), "|ev", v.get("ev_pct"),
              "|pf", v.get("pf"), "|t", v.get("t_stat"))
    print("--- size ---")
    for k, v in res["size"].items():
        print(k, "|n", v.get("n"), "|win", v.get("win_pct"), "|ev", v.get("ev_pct"),
              "|usd", v.get("total_usd"), "|shares", v.get("shares_used"))
