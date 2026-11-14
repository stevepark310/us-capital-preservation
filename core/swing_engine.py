"""
CUBE Swing Dip Engine — the validated edge the intraday rotation lacks.

Evidence (see data/1y_backtest_report.json + data/swing_probe.json):
  * intraday breakout/dip rotation on 3x ETFs nets ≈0 to −40% over the last year
    under hourly-path fills and 0.24% friction;
  * buying the panic close (RSI2 ≤ 20, Bollinger %B ≤ 0.15, RVOL ≥ 1.0) and
    holding 2-3 sessions with a 2×ATR bracket earned +2.66% per trade over 2
    years (90 trades, 58.9% win, PF 1.86) and in both half-year splits.

This module is the production logic for that signal:
  signal → next-open entry plan → ATR bracket → max-hold exit, with portfolio
  concurrency limits so the backtest is capital-honest.

It is deliberately NOT enabled in execution until the user approves overnight
holding (Agent 10 reports it as PENDING_APPROVAL).
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from core.exit_planner import AdaptiveExitPlanner, ROUND_TRIP_FEE_PCT as ROUND_TRIP_FEE

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SGOV_APR = 0.0452
TRADING_DAYS = 252

DIP_PARAMS = {
    "universe": ["SOXL", "TQQQ"],
    "rsi2_max": 20.0,
    "pct_b_max": 0.15,
    "rvol_min": 1.0,
    "tp_atr_mult": 1.5,      # validated: 2y +38.2% / MDD -6.6% (risk 2%/trade)
    "sl_atr_mult": 1.5,
    "max_hold_days": 3,
    "size_pct": 0.25,          # of equity per position (fallback / cap)
    "risk_per_trade_pct": 0.02,  # equity risked to the stop on one position
    "max_positions": 2,
    "min_gap_days": 0,
}


def _rsi(series: pd.Series, n: int) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l = d["Close"], d["High"], d["Low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["ATR_PCT"] = d["ATR"] / c * 100
    d["RSI2"] = _rsi(c, 2)
    d["RSI14"] = _rsi(c, 14)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    d["BB_LOW"] = mid - 2 * sd
    d["BB_UP"] = mid + 2 * sd
    d["PCT_B"] = ((c - d["BB_LOW"]) / (d["BB_UP"] - d["BB_LOW"]).replace(0, np.nan)).fillna(0.5)
    d["RVOL"] = d["Volume"] / d["Volume"].rolling(20).mean().shift(1)
    d["EMA20"] = c.ewm(span=20, adjust=False).mean()
    d["EMA50"] = c.ewm(span=50, adjust=False).mean()
    return d


def signal_row(row: pd.Series, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
    p = params or DIP_PARAMS
    try:
        if not np.isfinite(row["ATR"]) or row["ATR"] <= 0:
            return None
        rsi2, pctb, rvol = float(row["RSI2"]), float(row["PCT_B"]), float(row["RVOL"])
    except Exception:
        return None
    if rsi2 > p["rsi2_max"] or pctb > p["pct_b_max"] or rvol < p["rvol_min"]:
        return None
    score = 0.0
    score += min(40.0, (p["rsi2_max"] - rsi2) * 2.0)          # deeper oversold
    score += min(30.0, (p["pct_b_max"] - pctb) * 100.0)       # below the band
    score += min(30.0, (rvol - p["rvol_min"]) * 15.0)         # volume confirmation
    return {
        "rsi2": round(rsi2, 1), "rsi14": round(float(row["RSI14"]), 1),
        "pct_b": round(pctb, 3), "rvol": round(rvol, 2),
        "atr_pct": round(float(row["ATR_PCT"]), 2),
        "close": round(float(row["Close"]), 2),
        "score": round(min(100.0, score + 20.0), 1),
    }


def scan_latest(daily: Dict[str, pd.DataFrame], params: Dict[str, Any] = None) -> Dict[str, Any]:
    """Signals on the most recent completed session (used by agents on weekends)."""
    p = params or DIP_PARAMS
    planner = AdaptiveExitPlanner()
    out = {"as_of": None, "signals": [], "watch": []}
    for sym in p["universe"]:
        df = daily.get(sym)
        if df is None or len(df) < 30:
            continue
        d = prepare(df)
        row = d.iloc[-1]
        out["as_of"] = str(d.index[-1].date())
        sig = signal_row(row, p)
        entry_ref = float(row["Close"])
        plan = planner.plan(
            symbol=sym, planned_price=entry_ref, fill_price=entry_ref, qty=2,
            atr_pct=float(row["ATR_PCT"]),
            sentiment_score=50.0, allow_runner=True,
        )
        base = {
            "symbol": sym, "date": str(d.index[-1].date()),
            "close": round(entry_ref, 2), "atr_pct": round(float(row["ATR_PCT"]), 2),
            "validated_tp": round(entry_ref + p["tp_atr_mult"] * float(row["ATR"]), 2),
            "validated_sl": round(entry_ref - p["sl_atr_mult"] * float(row["ATR"]), 2),
            "validated_tp_pct": round(p["tp_atr_mult"] * float(row["ATR_PCT"]), 2),
            "validated_sl_pct": round(p["sl_atr_mult"] * float(row["ATR_PCT"]), 2),
            "max_hold_sessions": p["max_hold_days"],
            "reference_intraday_plan": {"tp1": plan.tp1_price, "tp2": plan.tp2_price, "sl": plan.hard_sl_price},
        }
        if sig:
            sig.update(base)
            sig["entry_rule"] = "다음 세션 시가 진입 (지정가/시장가), 최대 3세션 보유"
            out["signals"].append(sig)
        else:
            base.update({"rsi2": round(float(row["RSI2"]), 1), "pct_b": round(float(row["PCT_B"]), 3),
                         "rvol": round(float(row["RVOL"]), 2) if np.isfinite(row["RVOL"]) else None,
                         "why_not": f"RSI2 {row['RSI2']:.0f}>{p['rsi2_max']:.0f} or %B {row['PCT_B']:.2f}>{p['pct_b_max']:.2f} or RVOL {row['RVOL']:.1f}<{p['rvol_min']}"})
            out["watch"].append(base)
    out["params"] = p
    return out


def portfolio_backtest(daily: Dict[str, pd.DataFrame], params: Dict[str, Any] = None,
                       start: str = None, end: str = None) -> Dict[str, Any]:
    """Capital-honest portfolio simulation of the dip signal (daily bars)."""
    p = params or DIP_PARAMS
    prepared = {s: prepare(daily[s]) for s in p["universe"] if s in daily and len(daily[s]) > 40}
    if not prepared:
        return {"total_return_pct": 0.0, "total_trades": 0}
    idx = None
    for df in prepared.values():
        idx = df.index if idx is None else idx.intersection(df.index)
    idx = sorted(idx)
    if start:
        idx = [x for x in idx if x >= pd.Timestamp(start)]
    if end:
        idx = [x for x in idx if x <= pd.Timestamp(end)]

    equity = 1_000_000.0
    cash = equity
    positions: List[Dict[str, Any]] = []
    curve, rets, trades = [], [], []

    for k, day in enumerate(idx):
        # ---- manage open positions on this bar
        for pos in list(positions):
            df = prepared[pos["symbol"]]
            if day not in df.index:
                continue
            row = df.loc[day]
            hi, lo, cl = float(row["High"]), float(row["Low"]), float(row["Close"])
            hit = None
            if lo <= pos["sl"]:
                hit = pos["sl"]
                pos["reason"] = "STOP"
            elif hi >= pos["tp"]:
                hit = pos["tp"]
                pos["reason"] = "TARGET"
            pos["held"] = pos.get("held", 0) + 1
            if hit is None and pos["held"] >= p["max_hold_days"]:
                hit = cl
                pos["reason"] = "TIME_EXIT"
            if hit is not None:
                gross = pos["qty"] * (hit - pos["entry"])
                fees = pos["qty"] * pos["entry"] * ROUND_TRIP_FEE
                pnl = gross - fees
                cash += pos["qty"] * hit - pos["qty"] * pos["entry"] * (ROUND_TRIP_FEE / 2)
                trades.append({
                    "symbol": pos["symbol"], "entry_date": pos["entry_date"], "exit_date": str(day.date()),
                    "entry": round(pos["entry"], 2), "exit": round(hit, 2),
                    "ret_pct": round((hit / pos["entry"] - 1) * 100 - ROUND_TRIP_FEE * 100, 2),
                    "reason": pos["reason"], "held": pos["held"],
                    "score": pos.get("score"),
                })
                positions.remove(pos)

        # ---- new signals from the previous bar (entry at today's open)
        if k > 0:
            prev = idx[k - 1]
            if len(positions) < p["max_positions"]:
                need_cash = 0
                cands = []
                for sym, df in prepared.items():
                    if prev not in df.index or day not in df.index:
                        continue
                    if any(pos["symbol"] == sym for pos in positions):
                        continue
                    sig = signal_row(df.loc[prev], p)
                    if sig:
                        cands.append((sig["score"], sym, sig))
                cands.sort(reverse=True)
                for sc, sym, sig in cands:
                    if len(positions) >= p["max_positions"]:
                        break
                    df = prepared[sym]
                    entry = float(df.loc[day, "Open"]) * (1 + ROUND_TRIP_FEE / 2)
                    budget = equity * p["size_pct"]
                    if budget > cash:
                        continue
                    qty = max(1, int(budget // entry))
                    cost = qty * entry
                    if cost > cash:
                        continue
                    atr = float(df.loc[prev, "ATR"])
                    stop = round(entry - p["sl_atr_mult"] * atr, 2)
                    # risk-based sizing: never risk more than risk_per_trade_pct of equity
                    stop_dist_pct = max(0.02, (entry - stop) / entry)
                    risk_budget = equity * p.get("risk_per_trade_pct", 0.02)
                    qty_risk = max(1, int(risk_budget // (stop_dist_pct * entry)))
                    qty_cap = max(1, int((equity * p["size_pct"]) // entry))
                    qty = min(qty, qty_risk, qty_cap)
                    cost = qty * entry
                    if cost > cash:
                        continue
                    positions.append({
                        "symbol": sym, "entry": entry, "qty": qty,
                        "entry_date": str(day.date()), "held": 0,
                        "tp": round(entry + p["tp_atr_mult"] * atr, 2),
                        "sl": stop,
                        "stop_dist_pct": round(stop_dist_pct * 100, 2),
                        "risk_pct_of_equity": round(qty * stop_dist_pct * entry / equity * 100, 2),
                        "score": sc,
                    })
                    cash -= cost

        # ---- mark to market
        mv = 0.0
        for pos in positions:
            df = prepared[pos["symbol"]]
            px = float(df.loc[day, "Close"]) if day in df.index else pos["entry"]
            mv += pos["qty"] * px
        equity = cash + mv
        curve.append(equity)
        rets.append(curve[-1] / curve[-2] - 1 if len(curve) > 1 else 0.0)
        cash *= (1 + SGOV_APR / TRADING_DAYS) if cash > 0 else 1.0

    eq = pd.Series(curve, index=pd.DatetimeIndex(idx))
    r = pd.Series(rets, index=eq.index)
    total = (eq.iloc[-1] / 1_000_000 - 1) * 100
    dd = (eq / eq.cummax() - 1) * 100
    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    gain = sum(t["ret_pct"] for t in wins)
    loss = abs(sum(t["ret_pct"] for t in losses))
    monthly = {}
    for m, g in r.groupby(r.index.to_period("M")):
        monthly[str(m)] = round(((1 + g).prod() - 1) * 100, 2)
    return {
        "strategy": "DIP_SWING (과매도 패닉 매수 · 2~3세션 보유)",
        "period": f"{eq.index[0].date()} ~ {eq.index[-1].date()} ({len(eq)}거래일)",
        "total_return_pct": round(float(total), 2),
        "mdd_pct": round(float(dd.min()), 2),
        "win_rate_pct": round(len(wins) / max(1, len(trades)) * 100, 1),
        "total_trades": len(trades),
        "profit_factor": round(gain / loss, 2) if loss else None,
        "avg_trade_pct": round(float(np.mean([t["ret_pct"] for t in trades])) if trades else 0.0, 2),
        "sharpe_ratio": round(float(r.mean() / r.std() * np.sqrt(TRADING_DAYS)), 2) if r.std() else None,
        "monthly_breakdown": monthly,
        "equity_curve": [round(float(x), 0) for x in eq.iloc[::5]],
        "trades": trades,
        "params": p,
        "note": "일봉 기준 시뮬레이션(체결=다음 세션 시가, 손절·목표 동시 터치 시 손절 우선).",
    }


if __name__ == "__main__":
    from core import market_snapshot, backtest_lab
    daily = backtest_lab.load_daily("2y")
    for label, (s, e) in {"2y": (None, None)}.items():
        r = portfolio_backtest(daily, start=s, end=e)
        print(f"{label}: ret {r['total_return_pct']:+.2f}% mdd {r['mdd_pct']:.2f}% wr {r['win_rate_pct']}% "
              f"n {r['total_trades']} pf {r['profit_factor']} avg {r['avg_trade_pct']}% sharpe {r['sharpe_ratio']}")
    daily1 = backtest_lab.load_daily("1y")
    print("1y:", json.dumps({k: v for k, v in portfolio_backtest(daily1).items() if k not in ("trades", "monthly_breakdown", "params")}, ensure_ascii=False))
    snap = market_snapshot.load_snapshot()
    hist = market_snapshot.fetch_history(["SOXL", "TQQQ"])
    print(json.dumps(scan_latest(hist), ensure_ascii=False, indent=1)[:1200])
