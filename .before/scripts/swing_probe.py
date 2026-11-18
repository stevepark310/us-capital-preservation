#!/usr/bin/env python3
"""
Swing probe — does an overnight (multi-day) variant of the same dip signal carry
a real edge where the intraday rotation does not?

Research artifact only: it writes data/swing_probe.json and is NOT wired into
execution. Entry is the next session's open (no look-ahead), friction 0.24%.
"""
import json
import os
import sys
from itertools import product

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import backtest_lab as lab

FEE = lab.ROUND_TRIP_FEE


def probe(daily, symbols=("SOXL", "TQQQ"), hold_days=(1, 2, 3), tp_mult=1.0, sl_mult=1.0,
          rsi2_max=10.0, pctb_max=0.15, rvol_min=1.3, trend="ema50"):
    rows = []
    for sym in symbols:
        d = lab.add_indicators(daily[sym])
        idx = d.index
        for i in range(25, len(idx) - max(hold_days) - 1):
            row = d.iloc[i]
            if not np.isfinite(row["ATR_PCT"]) or row["ATR_PCT"] <= 0:
                continue
            if row["RSI2"] > rsi2_max or row["PCT_B"] > pctb_max or row["RVOL"] < rvol_min:
                continue
            if trend == "ema50" and row["Close"] < row["EMA50"]:
                continue
            entry = float(d["Open"].iloc[i + 1]) * (1 + FEE / 2)
            atr = float(row["ATR"])
            tp = entry + tp_mult * atr
            sl = entry - sl_mult * atr
            for h in hold_days:
                if i + 1 + h >= len(idx):
                    break
                best = None
                reason = "TIME"
                exit_px = float(d["Close"].iloc[i + 1 + h])
                for k in range(i + 1, i + 1 + h + 1):
                    hi = float(d["High"].iloc[k]); lo = float(d["Low"].iloc[k])
                    if lo <= sl:
                        best, reason = sl, "SL"; break
                    if hi >= tp:
                        best, reason = tp, "TP"; break
                if best is not None:
                    exit_px = best
                ret = (exit_px / entry - 1) - FEE / 2
                rows.append({"sym": sym, "date": str(idx[i + 1].date()), "hold": h,
                             "ret_pct": ret * 100, "reason": reason,
                             "rsi2": round(float(row["RSI2"]), 1), "pctb": round(float(row["PCT_B"]), 2)})
                break
    df = pd.DataFrame(rows)
    if df.empty:
        return {"trades": 0}
    wins = df[df["ret_pct"] > 0]
    losses = df[df["ret_pct"] <= 0]
    pf = wins["ret_pct"].sum() / abs(losses["ret_pct"].sum()) if len(losses) and losses["ret_pct"].sum() != 0 else None
    return {
        "trades": int(len(df)),
        "win_rate_pct": round(len(wins) / len(df) * 100, 1),
        "avg_ret_pct": round(float(df["ret_pct"].mean()), 3),
        "median_ret_pct": round(float(df["ret_pct"].median()), 3),
        "profit_factor": round(float(pf), 2) if pf else None,
        "avg_win_pct": round(float(wins["ret_pct"].mean()), 2) if len(wins) else 0,
        "avg_loss_pct": round(float(losses["ret_pct"].mean()), 2) if len(losses) else 0,
        "by_symbol": {s: {"trades": int(len(g)), "avg_ret_pct": round(float(g["ret_pct"].mean()), 3),
                          "win_rate_pct": round(float((g["ret_pct"] > 0).mean() * 100), 1)}
                      for s, g in df.groupby("sym")},
        "best": df.sort_values("ret_pct", ascending=False).head(3)[["date", "sym", "ret_pct"]].to_dict("records"),
        "worst": df.sort_values("ret_pct").head(3)[["date", "sym", "ret_pct"]].to_dict("records"),
    }


def main():
    daily = lab.load_daily("1y")
    out = {"note": "리서치용 스윙 탐침(시뮬레이션). 실전 원장과 무관.", "grid": []}
    best = None
    for hold, tp, sl, trend, rsi2m, rvolm in product([1, 2, 3], [1.0, 2.0], [1.0, 2.0], ["ema50", "none"], [10.0, 20.0], [1.0]):
        r = probe(daily, hold_days=(hold,), tp_mult=tp, sl_mult=sl, trend=trend, rsi2_max=rsi2m, rvol_min=rvolm)
        r.update({"hold_days": hold, "tp_atr": tp, "sl_atr": sl, "trend": trend, "rsi2_max": rsi2m})
        out["grid"].append(r)
        if r.get("trades", 0) >= 5:
            score = r["avg_ret_pct"] * (r["win_rate_pct"] / 100)
            if best is None or score > best[0]:
                best = (score, r)
    out["best"] = best[1] if best else None
    path = os.path.join(lab.BASE_DIR, "data", "swing_probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    for r in sorted([g for g in out["grid"] if g.get("trades")], key=lambda x: -x["avg_ret_pct"])[:10]:
        print(f"hold {r['hold_days']} tp {r['tp_atr']} sl {r['sl_atr']} trend {r['trend']:5s} "
              f"n {r['trades']:3d} wr {r['win_rate_pct']:5.1f}% avg {r['avg_ret_pct']:+.3f}% pf {r['profit_factor']}")
    print("saved", path)


if __name__ == "__main__":
    main()
