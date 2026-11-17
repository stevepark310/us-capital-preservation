"""Third batch: the live engine's own bracket vs the validated DIP bracket, the
entry-budget deadlock table, ATR levels of the universe, and the cost-of-daily-
trading arithmetic.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from btlib import RT_COST, SIDE_COST, stats
from run_ab import prep, vix_series
from strategies import daily_frame

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "data", "research_ab3_20261118.json")


def chandelier_sim(sym: str, budget: float = 90.0, sl_atr_mult: float = 0.75,
                   sl_min_pct: float = 1.5, sl_max_pct: Optional[float] = 8.0,
                   trail_atr_mult: float = 0.9, be_after_atr: float = 1.0,
                   breakeven_pct: float = 0.34, max_hold: int = 3,
                   vix_max: Optional[float] = None, period: str = "2y",
                   rsi2_max: float = 20.0, pct_b_max: float = 0.15, rvol_min: float = 1.0,
                   rt_cost: float = RT_COST, tp_atr: Optional[float] = None) -> List[Dict]:
    """Entry = next open after the dip signal. Exit logic chosen by the arguments:
    * tp_atr not None -> symmetric ATR bracket (validated DIP spec)
    * else            -> chandelier trail with an initial stop clamped to sl_max_pct
                         (what core/autotrader.py actually does today)
    Path assumption: daily bars; the stop is checked against the daily low and the
    trail is ratcheted on the daily close (conservative for a 3-session hold).
    """
    d = prep(sym, period)
    vix = vix_series()
    trades, i = [], 30
    while i < len(d) - 1:
        row = d.iloc[i]
        sig = (float(row["RSI2"]) <= rsi2_max and float(row["PCT_B"]) <= pct_b_max
               and np.isfinite(row["RVOL"]) and float(row["RVOL"]) >= rvol_min)
        if not sig:
            i += 1
            continue
        day = d.index[i + 1]
        if vix_max is not None:
            try:
                if float(vix.loc[:day].iloc[-1]) > vix_max:
                    i += 1
                    continue
            except Exception:
                pass
        atr_pct = float(row["ATR_PCT"])
        entry = float(d.iloc[i + 1]["Open"]) * (1 + rt_cost / 2)
        qty = int(budget // entry)
        if qty < 1:
            i += 1
            continue
        atr_abs = entry * atr_pct / 100.0
        if tp_atr is not None:
            tp = entry + tp_atr * atr_abs
            sl = entry - tp_atr * atr_abs
        else:
            sl_pct = max(sl_min_pct, min(sl_max_pct, sl_atr_mult * atr_pct)) if sl_max_pct else sl_atr_mult * atr_pct
            sl = entry * (1 - sl_pct / 100)
            tp = None
        hh = entry
        exit_px, exit_day, reason = None, None, "TIME"
        for k in range(i + 1, min(len(d), i + 1 + max_hold)):
            bar = d.iloc[k]
            lo, hi, cl = float(bar["Low"]), float(bar["High"]), float(bar["Close"])
            if lo <= sl:
                exit_px, exit_day, reason = sl, d.index[k], "STOP"
                break
            if tp is not None and hi >= tp:
                exit_px, exit_day, reason = tp, d.index[k], "TARGET"
                break
            if tp is None:
                hh = max(hh, hi)
                trail = hh - trail_atr_mult * atr_abs
                if hh >= entry + be_after_atr * atr_abs:
                    trail = max(trail, entry * (1 + breakeven_pct / 100))
                if cl <= trail:
                    exit_px, exit_day, reason = cl * (1 - rt_cost / 2), d.index[k], "TRAIL"
                    break
                sl = max(sl, trail)          # ratchet
        if exit_px is None:
            k = min(len(d) - 1, i + max_hold)
            exit_px, exit_day, reason = float(d.iloc[k]["Close"]) * (1 - rt_cost / 2), d.index[k], "TIME"
        trades.append({"date": str(day.date()), "exit_date": str(exit_day.date()), "ticker": sym,
                       "entry": round(entry, 2), "exit": round(exit_px, 2), "qty": qty,
                       "ret_pct": round((exit_px / entry - 1) * 100, 3),
                       "pnl_usd": round((exit_px - entry) * qty, 2), "reason": reason,
                       "atr_pct": round(atr_pct, 2), "hold_min": (exit_day - day).days * 390})
        i = max(i + 1, d.index.get_loc(exit_day) if exit_day in d.index else i + 1)
    return trades


def atr_table() -> Dict:
    out = {}
    for sym in ("SOXL", "TQQQ", "SOXS", "SQQQ", "SPXS", "SPXU", "QQQ", "SPY", "TSLL", "NVDL", "FNGU", "QID"):
        d = prep(sym, "1y")
        last = d.tail(30)
        cur = float(last["ATR_PCT"].iloc[-1])
        med = float(last["ATR_PCT"].median())
        px = float(last["Close"].iloc[-1])
        out[sym] = {
            "price": round(px, 2), "atr_pct_now": round(cur, 2), "atr_pct_30d_median": round(med, 2),
            "engine_sl_pct_clamped": round(max(1.5, min(8.0, 0.75 * cur)), 2),
            "validated_sl_pct_1.5atr": round(1.5 * cur, 2),
            "sl_in_atr_units": round(max(1.5, min(8.0, 0.75 * cur)) / cur, 2) if cur else None,
            "one_share_usd": round(px, 2),
        }
    return out


def deadlock_table() -> List[Dict]:
    """How many shares the live sizing buys at each budget (whole shares only)."""
    prices = {"SOXL": 47.91, "TQQQ": 51.49, "SOXS": 60.08, "SQQQ": 47.23,
              "SPXS": 27.75, "SPXU": 39.00, "QID": 16.47, "FNGU": 21.24,
              "TSLL": 9.85, "NVDL": 15.93, "BITX": 10.95, "UVXY": 22.07, "YINN": 22.02}
    rows = []
    for tk, px in prices.items():
        row = {"ticker": tk, "price": px}
        for b in (44.8, 59.73, 85.33, 120.0, 150.0):
            row[f"qty_{b}"] = int(b // px)
        rows.append(row)
    return rows


def daily_cost_math() -> Dict:
    rt = RT_COST * 100
    return {
        "round_trip_pct": round(rt, 3),
        "one_round_trip_per_day_pct_per_year": round(rt * 252, 1),
        "one_round_trip_per_week_pct_per_year": round(rt * 52, 1),
        "breakeven_gross_edge_needed_pct_per_trade": round(rt, 3),
        "note": "notional 기준. 1주/$90 거래도 동일 비율로 적용된다.",
    }


if __name__ == "__main__":
    res = {"bracket_ab": {}, "atr": atr_table(), "deadlock": deadlock_table(),
           "cost": daily_cost_math()}
    for period in ("2y", "1y"):
        for sym in ("SOXL", "TQQQ"):
            cur = chandelier_sim(sym, 90.0, period=period, vix_max=None)          # live engine
            cur_v = chandelier_sim(sym, 90.0, period=period, vix_max=28.0)
            val = chandelier_sim(sym, 90.0, period=period, vix_max=None, tp_atr=1.5)   # validated
            val_v = chandelier_sim(sym, 90.0, period=period, vix_max=28.0, tp_atr=1.5)
            res["bracket_ab"][f"{period}|{sym}|engine_chandelier_clamp8"] = stats(cur, 90.0, f"engine {sym} {period}")
            res["bracket_ab"][f"{period}|{sym}|engine_clamp8+vix28"] = stats(cur_v, 90.0, f"engine+vix {sym} {period}")
            res["bracket_ab"][f"{period}|{sym}|validated_1.5ATR"] = stats(val, 90.0, f"validated {sym} {period}")
            res["bracket_ab"][f"{period}|{sym}|validated_1.5ATR+vix28"] = stats(val_v, 90.0, f"validated+vix {sym} {period}")
            for key in (f"engine {sym} {period}", f"engine+vix {sym} {period}",
                        f"validated {sym} {period}", f"validated+vix {sym} {period}"):
                pass
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    for k, v in res["bracket_ab"].items():
        print(f"{k:48s} n={v.get('n'):3} win={v.get('win_pct')} ev={v.get('ev_pct')} pf={v.get('pf')} "
              f"t={v.get('t_stat')} mdd={v.get('mdd_pct')} stopshare={v.get('exit_kinds')}")
    print("--- ATR ---")
    for k, v in res["atr"].items():
        print(k, v)
    print("--- deadlock ---")
    for r in res["deadlock"]:
        print(r)
    print(res["cost"])
