"""Second A/B batch: 60-min ORB over 2y, 1x vs 3x friction, cost sensitivity,
BEAR-ALPHA short-ETF check, combined portfolio daily P&L, and the arithmetic of
'profit every day'.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from btlib import RT_COST, SIDE_COST, stats, et, load, sessionize
from strategies import daily_frame, atr14
from run_ab import prep, vix_series, dip_trades, portfolio, DIP

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "research_ab2_20261118.json")


def orb60(sym: str, budget: float = 90.0, stop_atr_frac: float = 0.10,
          direction: str = "long", rt_cost: float = RT_COST,
          rvol_min: Optional[float] = None) -> List[Dict]:
    """60-minute ORB (Barbon/Zarattini/Aziz 2024 tested 15/30/60-min variants).

    Entry at the open of the 10:30 bar (after the first 60-min range), stop at
    `stop_atr_frac` x ATR14 below entry, exit at the 15:30 bar close.
    """
    bars = et(load(sym, "1h", "730d"))
    daily = daily_frame(sym)
    sess = sessionize(bars)
    days = sorted(sess.keys())
    first_vol = {d: float(sess[d]["Volume"].iloc[0]) for d in days}
    trades = []
    for i, day in enumerate(days):
        g = sess[day]
        if len(g) < 5:
            continue
        o, c = float(g["Open"].iloc[0]), float(g["Close"].iloc[0])
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
        dist = stop_atr_frac * atr
        if dist <= 0:
            continue
        entry = float(g["Open"].iloc[1])
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
            exit_px = float(g["Close"].iloc[-1])
            exit_ts = g.index[-1]
        if not bull:
            ret = (1 - exit_px / entry) * 100 - rt_cost * 100
            trades.append({"date": str(day.date()), "ticker": sym, "short": True,
                           "ret_pct": round(ret, 3), "hold_min": 300, "reason": reason})
        else:
            trades.append({"date": str(day.date()), "ticker": sym,
                           "ret_pct": round((exit_px / entry - 1) * 100 - rt_cost * 100, 3),
                           "hold_min": 300, "reason": reason})
    return trades


def bear_alpha(vix_min: float = 24.0, tickers=("SOXS", "SQQQ"), hold: int = 1,
               vix_max: Optional[None] = None, budget: float = 90.0) -> List[Dict]:
    """Existing BEAR ALPHA idea: when VIX >= 24 buy the inverse ETF at the next open."""
    vix = vix_series()
    out = []
    for sym in tickers:
        d = prep(sym, "2y")
        i = 30
        while i < len(d) - 1:
            day = d.index[i]
            try:
                v = float(vix.loc[:day].iloc[-1])
            except Exception:
                i += 1
                continue
            if v < vix_min or (vix_max is not None and v > vix_max):
                i += 1
                continue
            entry = float(d.iloc[i + 1]["Open"]) * (1 + SIDE_COST)
            exit_i = min(len(d) - 1, i + hold)
            exit_px = float(d.iloc[exit_i]["Close"]) * (1 - SIDE_COST)
            out.append({"date": str(d.index[i + 1].date()), "ticker": sym,
                        "ret_pct": round((exit_px / entry - 1) * 100, 3), "hold_min": hold * 390,
                        "reason": "TIME", "vix": round(v, 1)})
            i = exit_i + 1
    return out


def cost_sensitivity(sym: str = "TQQQ", vix_max: float = 28.0) -> Dict:
    res = {}
    for rt in (0.0010, 0.0024, 0.0040):
        t = dip_trades(sym, 90.0, tp_atr=1.5, sl_atr=1.5, max_hold=3, vix_max=vix_max, rt_cost=rt)
        res[f"rt={rt*100:.2f}%"] = stats(t, 90.0, f"{sym} rt={rt*100:.2f}%")
    return res


def dip_loose(sym: str, budget: float = 90.0, hold: int = 3, rt_cost: float = RT_COST,
              period: str = "2y", z: float = 0.15) -> List[Dict]:
    """Same 'buy the panic close' idea with only a Bollinger condition, so that 1x
    index ETFs also produce signals -> lets us compare 1x vs 3x friction directly."""
    d = prep(sym, period)
    trades, i = [], 30
    while i < len(d) - 1:
        row = d.iloc[i]
        if not (float(row["PCT_B"]) <= z):
            i += 1
            continue
        entry = float(d.iloc[i + 1]["Open"]) * (1 + rt_cost / 2)
        exit_i = min(len(d) - 1, i + hold)
        exit_px = float(d.iloc[exit_i]["Close"]) * (1 - rt_cost / 2)
        trades.append({"date": str(d.index[i + 1].date()), "exit_date": str(d.index[exit_i].date()),
                       "ticker": sym, "entry": round(entry, 2), "exit": round(exit_px, 2), "qty": 1,
                       "ret_pct": round((exit_px / entry - 1) * 100, 3),
                       "pnl_usd": round(exit_px - entry, 2), "reason": "TIME", "hold_min": hold * 390})
        i = exit_i + 1
    return trades


def one_x_vs_three_x() -> Dict:
    res = {}
    for sym in ("SPY", "QQQ", "TQQQ", "SOXL"):
        t = dip_loose(sym)
        res[sym] = stats(t, 90.0, f"dip_loose {sym}")
        res[sym]["gross_ev_pct"] = (round(res[sym].get("ev_pct", 0) + RT_COST * 100, 3)
                                    if res[sym].get("ev_pct") is not None else None)
    return res


def combined(equity0: float = 891.63, budget: float = 90.0, vix_max: float = 28.0,
             period: str = "2y", max_concurrent: int = 2) -> Dict:
    """SOXL + TQQQ dip system, one position each, max_concurrent cap, daily equity."""
    all_trades = []
    for sym in ("SOXL", "TQQQ"):
        all_trades += dip_trades(sym, budget, tp_atr=1.5, sl_atr=1.5, max_hold=3, vix_max=vix_max, period=period)
    all_trades.sort(key=lambda x: x["date"])
    # cap concurrency
    active: List[Dict] = []
    kept = []
    for t in all_trades:
        d0, d1 = pd.Timestamp(t["date"]), pd.Timestamp(t["exit_date"])
        active = [a for a in active if a["exit_date_ts"] >= d0]
        if len(active) >= max_concurrent:
            continue
        t["exit_date_ts"] = d1
        active.append(t)
        kept.append(t)
    start = all_trades[0]["date"] if all_trades else "2024-11-18"
    end = max(t["exit_date"] for t in all_trades) if all_trades else "2026-11-17"
    pf = portfolio(kept, equity0, start, end)
    pf["per_trade"] = stats(kept, budget, "combined")
    pf["usd_total"] = round(sum(t["pnl_usd"] for t in kept), 2)
    pf["usd_avg_per_trade"] = round(np.mean([t["pnl_usd"] for t in kept]), 2)
    pf["usd_best"] = round(max(t["pnl_usd"] for t in kept), 2)
    pf["usd_worst"] = round(min(t["pnl_usd"] for t in kept), 2)
    pf["trades"] = len(kept)
    # daily distribution of realised P&L
    days = pd.bdate_range(start, end)
    pnl = pd.Series(0.0, index=days)
    for t in kept:
        d = pd.Timestamp(t["exit_date"])
        if d in pnl.index:
            pnl.loc[d] += t["pnl_usd"]
    pf["days_total"] = len(days)
    pf["days_with_trade"] = int((pnl != 0).sum())
    pf["days_positive"] = int((pnl > 0).sum())
    pf["days_negative"] = int((pnl < 0).sum())
    pf["days_flat"] = int((pnl == 0).sum())
    pf["pct_days_positive_of_all"] = round(100 * (pnl > 0).mean(), 2)
    pf["pct_days_negative_of_all"] = round(100 * (pnl < 0).mean(), 2)
    pf["longest_losing_day_streak"] = _streak((pnl < 0).values)
    return pf


def _streak(flags) -> int:
    best = cur = 0
    for f in flags:
        if f:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def daily_income_math() -> Dict:
    """What would 'profit every day' require?"""
    out = {}
    equity = 891.63
    for target in (1.0, 5.0, 10.0):
        out[f"${target}/day"] = {
            "annual_usd": round(target * 252, 2),
            "annual_pct_of_equity": round(target * 252 / equity * 100, 1),
            "required_sharpe_at_15pct_vol": round((target * 252 / equity) / 0.15, 2),
        }
    # P(all 20 days positive) for a strategy with daily mu/sigma drawn from our best system
    for mu, sd, label in ((0.0006, 0.0100, "EV 0.06%/day, 1.0% daily vol"),
                          (0.0010, 0.0110, "EV 0.10%/day, 1.1% daily vol"),
                          (0.0020, 0.0200, "EV 0.20%/day, 2.0% daily vol")):
        p_pos = 1 - _norm_cdf(0, mu, sd)
        out[label] = {"p_positive_day": round(p_pos * 100, 1),
                      "p_20_consecutive": round(p_pos ** 20, 4),
                      "expected_positive_days_per_year": round(p_pos * 252, 0)}
    return out


def _norm_cdf(x, mu, sd):
    import math
    return 0.5 * (1 + math.erf((x - mu) / (sd * math.sqrt(2))))


if __name__ == "__main__":
    res = {}
    res["orb60"] = {}
    for sym in ("QQQ", "TQQQ", "SOXL"):
        for rv in (None, 1.0):
            t = orb60(sym, rvol_min=rv)
            res["orb60"][f"{sym}|rv={rv}"] = stats(t, 90.0, f"ORB60 {sym} rv={rv}")
    res["bear_alpha"] = {f"vix>={m}": stats(bear_alpha(vix_min=m, hold=1), 90.0, f"BEAR vix>={m}")
                         for m in (22.0, 24.0, 26.0)}
    res["cost"] = cost_sensitivity()
    res["x1_vs_x3"] = one_x_vs_three_x()
    res["combined_90"] = combined(891.63, 90.0)
    res["combined_150"] = combined(891.63, 150.0)
    res["combined_44"] = combined(891.63, 44.8)
    res["income_math"] = daily_income_math()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    for k, v in res["orb60"].items():
        print("ORB60", k, "|n", v["n"], "|win", v["win_pct"], "|ev", v["ev_pct"], "|pf", v["pf"], "|t", v["t_stat"], "|mdd", v["mdd_pct"])
    for k, v in res["bear_alpha"].items():
        print("BEAR", k, "|n", v["n"], "|win", v["win_pct"], "|ev", v["ev_pct"], "|t", v["t_stat"])
    for k, v in res["cost"].items():
        print("COST TQQQ", k, "|n", v["n"], "|ev", v["ev_pct"], "|pf", v["pf"])
    for k, v in res["x1_vs_x3"].items():
        print("X1X3", k, "|n", v.get("n"), "|ev_net", v.get("ev_pct"), "|ev_gross", v.get("gross_ev_pct"),
          "|win", v.get("win_pct"), "|pf", v.get("pf"), "|t", v.get("t_stat"))
    for k in ("combined_44", "combined_90", "combined_150"):
        print(k, {x: res[k].get(x) for x in ("trades", "usd_total", "total_pct", "mdd_pct", "positive_day_pct",
                                              "active_day_pct", "sharpe", "days_positive", "days_negative",
                                              "days_flat", "pct_days_positive_of_all", "longest_losing_day_streak",
                                              "usd_avg_per_trade", "usd_worst", "usd_best")})
    print(json.dumps(res["income_math"], ensure_ascii=False, indent=1))
