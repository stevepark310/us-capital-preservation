#!/usr/bin/env python3
"""
CUBE Backtest Sweep — coordinate search over the upgraded strategy space.

Goal: find a configuration whose edge survives realistic hourly-path fills, and
verify it out-of-sample (first half in-sample, second half validation).
Prints a plain table; the winning configuration is copied into
core/backtest_lab.py UPGRADED and core/exit_planner defaults by hand.
"""
import copy
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import backtest_lab as lab


def score(r):
    """Calmar-like objective with a trade-count floor."""
    if r["total_trades"] < 20 or r["mdd_pct"] >= 0:
        return -99
    return r["total_return_pct"] / max(1.0, abs(r["mdd_pct"]))


def evaluate(daily, hourly, params, start=None, end=None):
    r = lab.run_strategy(daily, hourly, params, start, end)
    return r


def fmt(base_params, r):
    return (f"ret {r['total_return_pct']:+7.2f}%  mdd {r['mdd_pct']:7.2f}%  "
            f"wr {r['win_rate_pct']:5.1f}%  pf {str(r['profit_factor']):>5}  "
            f"sharpe {r['sharpe_ratio']:+5.2f}  n {r['total_trades']:3d}")


def main():
    daily = lab.load_daily("1y")
    hourly = lab.load_hourly("1y")

    # ---- coordinate search dimensions
    dims = {
        "entry_mode": ["breakout", "mr", "breakout+mr"],
        "k": [0.35, 0.5, 0.7],
        "trend": ["none", "ema20", "ema50"],
        "tp1_atr_mult": [0.3, 0.45, 0.6],
        "tp2_atr_mult": [0.6, 0.95, 1.3],
        "sl_atr_mult": [0.3, 0.45, 0.6],
        "runner_trail_min": [True, False],
        "bear": ["off", "v20c1", "v24c2"],
        "size": ["0.45/0.35", "0.30/0.25"],
    }

    def build(cfg):
        p = copy.deepcopy(lab.UPGRADED)
        p["entry_mode"] = cfg["entry_mode"]
        p["mean_reversion"] = cfg["entry_mode"] in ("mr", "breakout+mr")
        p["breakout_enabled"] = cfg["entry_mode"] in ("breakout", "breakout+mr")
        p["k"] = cfg["k"]
        p["trend_filter"] = cfg["trend"] != "none"
        p["trend_mode"] = cfg["trend"]
        p["tp1_atr_mult"] = cfg["tp1_atr_mult"]
        p["tp2_atr_mult"] = max(cfg["tp2_atr_mult"], cfg["tp1_atr_mult"] + 0.2)
        p["sl_atr_mult"] = cfg["sl_atr_mult"]
        p["runner_trail"] = cfg["runner_trail_min"]
        p["name"] = json.dumps(cfg)
        if cfg["bear"] == "off":
            p["bear_trigger"] = {"vix": 999, "use_ema_break": False, "confirmations_required": 1}
        elif cfg["bear"] == "v24c2":
            p["bear_trigger"] = {"vix": 24.0, "use_ema_break": True, "confirmations_required": 2}
        else:
            p["bear_trigger"] = {"vix": 20.0, "use_ema_break": True, "confirmations_required": 1}
        w = cfg["size"].split("/")
        p["weights"] = {"SOXL": float(w[0]), "TQQQ": float(w[1]), "SOXS": float(w[0]), "SQQQ": float(w[1])}
        return p

    base = {
        "entry_mode": "breakout+mr", "k": 0.5, "trend": "ema20",
        "tp1_atr_mult": 0.45, "tp2_atr_mult": 0.95, "sl_atr_mult": 0.45,
        "runner_trail_min": True, "bear": "v20c1", "size": "0.45/0.35",
    }

    results = []
    for dim, values in dims.items():
        best = None
        for v in values:
            cfg = dict(base)
            cfg[dim] = v
            p = build(cfg)
            r = evaluate(daily, hourly, p)
            s = score(r)
            results.append((dim, v, s, r))
            print(f"{dim:16s} = {str(v):12s} {fmt(p, r)}")
            if best is None or s > best[0]:
                best = (s, v)
        base[dim] = best[1]
        print(f"  → best {dim}: {best[1]} (score {best[0]:.2f})\n")

    print("FINAL CONFIG:", json.dumps(base, ensure_ascii=False))
    p = build(base)
    r = evaluate(daily, hourly, p)
    print("FULL 1Y :", fmt(p, r))

    # out-of-sample split
    dates = sorted(set(daily["SOXL"].index))
    mid = dates[len(dates) // 2]
    r_is = evaluate(daily, hourly, p, start=str(dates[20].date()), end=str(mid.date()))
    r_oos = evaluate(daily, hourly, p, start=str(mid.date()))
    print("H1 (in-sample) :", fmt(p, r_is))
    print("H2 (validation):", fmt(p, r_oos))

    out = {
        "final_config": base,
        "full_1y": {k: r[k] for k in ("total_return_pct", "mdd_pct", "win_rate_pct", "profit_factor",
                                      "sharpe_ratio", "total_trades", "by_symbol")},
        "in_sample_h1": {k: r_is[k] for k in ("total_return_pct", "mdd_pct", "win_rate_pct", "profit_factor",
                                              "sharpe_ratio", "total_trades")},
        "validation_h2": {k: r_oos[k] for k in ("total_return_pct", "mdd_pct", "win_rate_pct", "profit_factor",
                                                "sharpe_ratio", "total_trades")},
        "grid": [{"dim": d, "value": str(v), "score": round(s, 3),
                  "return_pct": rr["total_return_pct"], "mdd_pct": rr["mdd_pct"],
                  "trades": rr["total_trades"], "win_rate_pct": rr["win_rate_pct"],
                  "profit_factor": rr["profit_factor"]} for d, v, s, rr in results],
    }
    path = os.path.join(lab.BASE_DIR, "data", "backtest_sweep.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", path)


if __name__ == "__main__":
    main()
