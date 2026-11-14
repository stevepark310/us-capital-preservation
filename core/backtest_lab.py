"""
CUBE Backtest Lab v2 — hourly-path engine for baseline vs upgraded strategies.

Daily bars cannot judge bracket width: a wider stop is always "worse" when the
engine only knows that the day's low touched it. This lab therefore walks the
actual hourly bars (Yahoo Finance 60m, 1 year) so the order of events inside a
session is (mostly) known:

  * signals use only completed daily bars (i-1) and the current session's bars
  * a breakout fills at the trigger, or at the bar open when the session gapped
    through it, plus slippage; a mean-reversion entry fills at the session open
  * inside the entry bar a take-profit only counts if that bar CLOSED beyond the
    level; a stop counts on a low touch. When both are inside one bar the stop
    is assumed first (worst case)
  * after entry, each subsequent bar is walked in order: stop touch → stop,
    otherwise target touch → target
  * round-trip friction 0.24% (NH 0.09% + slippage 0.15%) is charged per trade
  * idle cash earns the SGOV yield (4.52%/252)

Everything is SIMULATION and is written only to the backtest report, never to
the live ledger.
"""

import copy
import json
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(BASE_DIR, "data", "1y_backtest_report.json")
DOCS_REPORT_PATH = os.path.join(BASE_DIR, "docs", "1y_backtest_report.json")
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")
DAILY_CACHE = os.path.join(CACHE_DIR, "backtest_daily.pkl")
HOURLY_CACHE = os.path.join(CACHE_DIR, "backtest_hourly.pkl")

ROUND_TRIP_FEE = 0.0024
SGOV_APR = 0.0452
TRADING_DAYS = 252
UNIVERSE = ["SOXL", "TQQQ", "SOXS", "SQQQ", "SGOV", "QQQ", "SPY", "NVDA", "SMH"]


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ data
def _cached(path: str, max_age_h: float) -> Optional[dict]:
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                obj = pickle.load(f)
            age_h = (datetime.now() - datetime.fromisoformat(obj["fetched_at"])).total_seconds() / 3600
            if age_h < max_age_h:
                return obj
    except Exception:
        return None
    return None


def _save(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    obj["fetched_at"] = datetime.now().isoformat()
    try:
        with open(path, "wb") as f:
            pickle.dump(obj, f)
    except Exception as e:
        print(f"[lab] cache write failed: {e}")


def load_daily(period: str = "1y", force: bool = False) -> Dict[str, pd.DataFrame]:
    c = None if force else _cached(DAILY_CACHE, 12)
    if c and c.get("period") == period:
        return c["frames"]
    import yfinance as yf
    syms = UNIVERSE + ["^VIX"]
    dl = yf.download(syms, period=period, interval="1d", progress=False,
                     auto_adjust=False, group_by="column", threads=True)
    frames = _split(dl, syms)
    _save(DAILY_CACHE, {"frames": frames, "period": period})
    return frames


def load_hourly(period: str = "1y", force: bool = False) -> Dict[str, pd.DataFrame]:
    c = None if force else _cached(HOURLY_CACHE, 12)
    if c and c.get("period") == period:
        return c["frames"]
    import yfinance as yf
    syms = ["SOXL", "TQQQ", "SOXS", "SQQQ"]
    dl = yf.download(syms, period=period, interval="60m", progress=False,
                     auto_adjust=False, group_by="column", threads=True)
    frames = _split(dl, syms)
    _save(HOURLY_CACHE, {"frames": frames, "period": period})
    return frames


def _split(dl: pd.DataFrame, syms: List[str]) -> Dict[str, pd.DataFrame]:
    out = {}
    if dl is None or len(dl) == 0:
        return out
    if isinstance(dl.columns, pd.MultiIndex):
        first = str(dl.columns.get_level_values(0)[0]).lower()
        lvl = 1 if first in ("open", "close", "high", "low", "adj close", "volume") else 0
        for s in syms:
            try:
                sub = dl.xs(s, axis=1, level=lvl)
            except Exception:
                continue
            sub = sub.reindex(columns=["Open", "High", "Low", "Close", "Volume"]).dropna(subset=["Close"])
            if len(sub) > 20:
                out[s] = sub
    return out


# ------------------------------------------------------------------ indicators
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l = d["Close"], d["High"], d["Low"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["ATR_PCT"] = d["ATR"] / c * 100.0
    d["EMA20"] = c.ewm(span=20, adjust=False).mean()
    d["EMA50"] = c.ewm(span=50, adjust=False).mean()
    delta = c.diff()
    def _rsi(n):
        up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        dn = (-delta).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        rs = up / dn.replace(0, np.nan)
        return (100 - 100 / (1 + rs)).fillna(50)
    d["RSI14"] = _rsi(14)
    d["RSI2"] = _rsi(2)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    d["BB_LOW"] = mid - 2 * sd
    d["BB_UP"] = mid + 2 * sd
    d["PCT_B"] = ((c - d["BB_LOW"]) / (d["BB_UP"] - d["BB_LOW"]).replace(0, np.nan)).fillna(0.5)
    d["RVOL"] = d["Volume"] / d["Volume"].rolling(20).mean().shift(1)
    d["VOL20"] = c.pct_change().rolling(20).std() * np.sqrt(252) * 100
    return d


# ------------------------------------------------------------------ strategies
BASELINE = {
    "name": "BASELINE (현행: 고정 TP +2.0%/+3.5%, SL -1.5%, 샹들리에 1.5ATR)",
    "entry_mode": "breakout",
    "k": 0.5,
    "tp1_pct": {"SOXL": 2.0, "TQQQ": 2.0, "SOXS": 3.5, "SQQQ": 2.8},
    "tp2_pct": {"SOXL": 3.5, "TQQQ": 3.5, "SOXS": 6.0, "SQQQ": 5.0},
    "sl_pct": {"SOXL": 1.5, "TQQQ": 1.5, "SOXS": 2.5, "SQQQ": 2.0},
    "tp1_share": 0.33, "tp2_share": 0.33,
    "weights": {"SOXL": 0.45, "TQQQ": 0.35, "SOXS": 0.40, "SQQQ": 0.30},
    "trend_filter": True,
    "breakeven_after_tp1": True,
    "be_buffer_pct": 0.0,
    "fill_anchor": False,
    "runner_trail": True,
    "trail_atr_mult": 1.5,
    "runner_dual_trail": False,
    "runner_lock_pct": 0.0,
    "mean_reversion": False,
    "vol_sizing": False,
    "bear_trigger": {"vix": 22.0, "use_ema_break": True, "confirmations_required": 1},
    "time_stop_bars": 0,
}

UPGRADED = {
    "name": "UPGRADED (ATR 동적 브래킷 + 체결가 재산정 + 이중 트레일 러너)",
    "entry_mode": "breakout+mr",
    "k": 0.5,
    "tp1_atr_mult": 0.45, "tp2_atr_mult": 0.95, "sl_atr_mult": 0.55,
    "tp1_min": 0.9, "tp1_max": 5.5, "tp2_min": 2.0, "tp2_max": 9.5, "sl_min": 0.9, "sl_max": 4.0,
    "tp1_share": 0.34, "tp2_share": 0.33,
    "weights": {"SOXL": 0.45, "TQQQ": 0.35, "SOXS": 0.40, "SQQQ": 0.30},
    "trend_filter": True,
    "breakeven_after_tp1": True,
    "be_buffer_pct": 0.34,
    "fill_anchor": True,
    "runner_trail": True,
    "trail_atr_mult": 0.9,
    "runner_dual_trail": True,
    "runner_lock_pct": 1.5,
    "mean_reversion": True,
    "mr_rsi2": 10.0, "mr_pct_b": 0.15, "mr_rvol": 1.3, "mr_size_mult": 0.7,
    "vol_sizing": True,
    "target_vol": 18.0,
    "bear_trigger": {"vix": 20.0, "use_ema_break": True, "confirmations_required": 1},
    "time_stop_bars": 0,
}


def _bracket(params: Dict[str, Any], sym: str, entry: float, atr_pct: float) -> Tuple[float, float, float]:
    if params.get("tp1_atr_mult"):
        tp1p = min(max(params["tp1_atr_mult"] * atr_pct, params["tp1_min"]), params["tp1_max"])
        tp2p = min(max(params["tp2_atr_mult"] * atr_pct, params["tp2_min"]), params["tp2_max"])
        slp = min(max(params["sl_atr_mult"] * atr_pct, params["sl_min"]), params["sl_max"])
    else:
        tp1p, tp2p, slp = params["tp1_pct"][sym], params["tp2_pct"][sym], params["sl_pct"][sym]
    return (round(tp1p, 2), round(tp2p, 2), round(slp, 2))


def _simulate_short_day(bars: List[Dict[str, float]], entry_idx: int, entry: float, plan: Dict[str, Any],
                        params: Dict[str, Any], ema20: float) -> Tuple[float, str, float, int]:
    """Walk the session bars after entry. Returns (weighted_ret, reason, last_px, exit_bar_idx)."""
    w1, w2 = params["tp1_share"], params["tp2_share"]
    wr = max(0.0, 1.0 - w1 - w2)
    tp1, tp2, sl = plan["tp1"], plan["tp2"], plan["sl"]
    stop = sl
    tp1_done = tp2_done = False
    realized = 0.0
    running_high = entry
    reason = "EOD_CLOSE"
    last_px = entry

    for j in range(entry_idx, len(bars)):
        b = bars[j]
        running_high = max(running_high, b["h"])

        # 1. stop first (worst case)
        if b["l"] <= stop:
            fill = stop
            if not tp1_done:
                realized = (fill / entry - 1.0)
                return realized, "STOP", fill, j
            # remainder stopped
            remaining = 1.0 - w1 - (w2 if tp2_done else 0.0)
            realized += remaining * (fill / entry - 1.0)
            return realized, "TRAIL_STOP" if (tp1_done and fill > entry) else "STOP", fill, j

        # 2. targets (entry bar needs a close beyond the level)
        def reached(level):
            if b["h"] < level:
                return False
            if j == entry_idx:
                return b["c"] >= level
            return True

        if not tp1_done and reached(tp1):
            realized += w1 * (tp1 / entry - 1.0)
            tp1_done = True
            if params.get("breakeven_after_tp1"):
                stop = max(stop, entry * (1 + params.get("be_buffer_pct", 0.0) / 100.0))
            reason = "TP1"
        if tp1_done and not tp2_done and reached(tp2):
            realized += w2 * (tp2 / entry - 1.0)
            tp2_done = True
            reason = "TP2"
            if params.get("runner_lock_pct"):
                stop = max(stop, entry * (1 + params["runner_lock_pct"] / 100.0))

        # 3. runner trail (updated on bar close)
        if tp2_done and wr > 0 and params.get("runner_trail"):
            atr = plan["atr_value"]
            chand = running_high - params["trail_atr_mult"] * atr
            cand = [chand]
            if params.get("runner_dual_trail") and ema20:
                cand.append(ema20)
            if params.get("runner_lock_pct"):
                cand.append(entry * (1 + params["runner_lock_pct"] / 100.0))
            stop = max(stop, min(max(cand), b["c"]))

        last_px = b["c"]
        if j == len(bars) - 1:
            if wr > 0 and tp2_done:
                realized += wr * (b["c"] / entry - 1.0)
                reason = "RUNNER_EOD"
            elif tp1_done and not tp2_done:
                realized += (1.0 - w1) * (b["c"] / entry - 1.0)
                reason = "TP1_EOD"
            else:
                realized = (b["c"] / entry - 1.0)
                reason = "EOD_CLOSE"
    return realized, reason, last_px, len(bars) - 1


def run_strategy(daily: Dict[str, pd.DataFrame], hourly: Dict[str, pd.DataFrame],
                 params: Dict[str, Any], start: str = None, end: str = None) -> Dict[str, Any]:
    d = {s: add_indicators(df) for s, df in daily.items()}
    need = ["SOXL", "TQQQ", "SOXS", "SQQQ", "^VIX", "QQQ"]
    missing = [s for s in need if s not in d]
    if missing:
        raise RuntimeError(f"missing daily data: {missing}")

    def session_days(hf: pd.DataFrame) -> pd.Index:
        idx = hf.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("America/New_York")
        return pd.Index(sorted(set(pd.DatetimeIndex(idx).normalize().tz_localize(None))))

    h_days = session_days(hourly["SOXL"])
    dates = sorted(set(d["SOXL"].index) & set(h_days))
    dates = [x for x in dates if start is None or x >= pd.Timestamp(start)]
    dates = [x for x in dates if end is None or x <= pd.Timestamp(end)]

    # bars per day per symbol
    bars_by_day: Dict[str, Dict[pd.Timestamp, List[Dict[str, float]]]] = {}
    for sym, hf in hourly.items():
        tmp: Dict[pd.Timestamp, List[Dict[str, float]]] = {}
        idx = hf.index.tz_convert("America/New_York") if getattr(hf.index, "tz", None) is not None else hf.index
        for ts_i, (ts, row) in enumerate(zip(idx, hf.itertuples())):
            day = ts.normalize().tz_localize(None)
            tmp.setdefault(day, []).append({"t": ts, "o": float(row.Open), "h": float(row.High),
                                            "l": float(row.Low), "c": float(row.Close)})
        bars_by_day[sym] = tmp

    def dv(sym: str, day: pd.Timestamp, col: str, offset: int = 0):
        try:
            i = d[sym].index.get_loc(day)
            return float(d[sym][col].iloc[i + offset])
        except Exception:
            return np.nan

    equity = 1_000_000.0
    curve: List[float] = []
    rets: List[float] = []
    trades: List[Dict[str, Any]] = []
    wins = losses = 0
    gain = loss = 0.0

    for i, day in enumerate(dates):
        prev_day = dates[i - 1] if i > 0 else None
        if prev_day is None:
            curve.append(equity); rets.append(0.0); continue

        vix = dv("^VIX", prev_day, "Close")
        tqqq_c = dv("TQQQ", prev_day, "Close")
        tqqq_e = dv("TQQQ", prev_day, "EMA20")
        conf = 0
        bt = params["bear_trigger"]
        if not np.isnan(vix) and vix >= bt["vix"]:
            conf += 1
        if bt.get("use_ema_break") and not np.isnan(tqqq_c) and not np.isnan(tqqq_e) and tqqq_c < tqqq_e:
            conf += 1
        bear = conf >= bt.get("confirmations_required", 1)
        if not bt.get("use_ema_break"):
            bear = (not np.isnan(vix)) and vix >= bt["vix"]

        symbols = ["SOXS", "SQQQ"] if bear else ["SOXL", "TQQQ"]
        day_ret = 0.0
        deployed = 0.0

        for sym in symbols:
            bars = bars_by_day.get(sym, {}).get(day)
            if not bars or len(bars) < 3:
                continue
            atr_pct = dv(sym, prev_day, "ATR_PCT")
            if np.isnan(atr_pct) or atr_pct <= 0:
                continue
            prev_c = dv(sym, prev_day, "Close")
            prev_o = dv(sym, prev_day, "Open")
            prev_h = dv(sym, prev_day, "High")
            prev_l = dv(sym, prev_day, "Low")
            ema = dv(sym, prev_day, "EMA20")
            rsi2 = dv(sym, prev_day, "RSI2")
            pctb = dv(sym, prev_day, "PCT_B")
            rvol = dv(sym, prev_day, "RVOL")
            vol20 = dv(sym, prev_day, "VOL20")

            if not bear and params.get("trend_filter") and sym in ("SOXL", "TQQQ"):
                mode = params.get("trend_mode", "ema20")
                ref = ema
                if mode == "ema50":
                    ref = dv(sym, prev_day, "EMA50")
                if np.isnan(prev_c) or np.isnan(ref) or prev_c < ref:
                    continue

            size = params["weights"][sym]
            if params.get("vol_sizing") and not np.isnan(vol20) and vol20 > 0:
                size *= float(np.clip(params["target_vol"] / vol20, 0.5, 1.3))

            trigger = prev_o + params["k"] * (prev_h - prev_l)
            entry_idx = entry_px = None
            entry_type = None
            for j, b in enumerate([] if params.get("breakout_enabled") is False else bars):
                if b["h"] >= trigger > 0:
                    entry_idx = j
                    # gap-through fills at the open, otherwise at the trigger
                    entry_px = b["o"] if b["o"] > trigger else trigger
                    entry_type = "BREAKOUT"
                    break

            # intraday dip entry: buy a volume-armed flush inside the session, exit same day
            dip = params.get("dip_entry")
            if dip and dip.get("enabled") and entry_idx is None and not bear:
                for j, b in enumerate(bars):
                    floor = prev_c * (1 - dip["atr_trigger"] * atr_pct / 100.0)
                    rng = max(1e-9, b["h"] - b["l"])
                    if b["l"] <= floor and (b["c"] - b["l"]) / rng >= dip.get("close_confirm", 0.25):
                        entry_idx, entry_px, entry_type = j, b["c"], "INTRADAY_DIP"
                        break

            if params.get("mean_reversion") and sym in ("SOXL", "TQQQ") and not bear:
                if (not np.isnan(rsi2) and rsi2 <= params["mr_rsi2"]
                        and not np.isnan(pctb) and pctb <= params["mr_pct_b"]
                        and not np.isnan(rvol) and rvol >= params["mr_rvol"]):
                    if entry_idx is None:
                        entry_idx, entry_px, entry_type = 0, bars[0]["o"], "MR_DIP"
                        size *= params["mr_size_mult"]
                    else:
                        entry_type = "BREAKOUT+MR"

            if entry_idx is None:
                continue

            planned = entry_px
            fill = entry_px * (1 + ROUND_TRIP_FEE / 2)
            anchor = fill if params.get("fill_anchor") else planned

            tp1p, tp2p, slp = _bracket(params, sym, anchor, atr_pct)
            plan = {
                "tp1": anchor * (1 + tp1p / 100), "tp2": anchor * (1 + tp2p / 100),
                "sl": anchor * (1 - slp / 100), "atr_value": anchor * atr_pct / 100,
                "tp1_pct": tp1p, "tp2_pct": tp2p, "sl_pct": slp,
            }
            ret_gross, reason, last_px, exit_idx = _simulate_short_day(bars, entry_idx, fill, plan, params, ema)
            ret_net = ret_gross - ROUND_TRIP_FEE / 2
            size = float(np.clip(size, 0.1, 0.6))
            contrib = ret_net * size
            day_ret += contrib
            deployed += size
            if contrib > 0:
                wins += 1; gain += contrib
            else:
                losses += 1; loss += abs(contrib)
            trades.append({
                "date": str(day.date()), "symbol": sym, "type": entry_type,
                "regime": "BEAR" if bear else "BULL",
                "entry": round(fill, 3), "planned": round(planned, 3),
                "tp1_pct": tp1p, "tp2_pct": tp2p, "sl_pct": slp,
                "exit_reason": reason, "exit": round(last_px, 3),
                "ret_pct": round(ret_net * 100, 3), "weight": round(size, 3),
                "contrib_pct": round(contrib * 100, 4),
                "vix_prev": round(float(vix), 2) if not np.isnan(vix) else None,
                "atr_pct": round(float(atr_pct), 2),
                "entry_bar": str(bars[entry_idx]["t"]), "exit_bar": str(bars[min(exit_idx, len(bars) - 1)]["t"]),
            })

        day_ret += max(0.0, 1.0 - deployed) * (SGOV_APR / TRADING_DAYS)
        equity *= (1 + day_ret)
        curve.append(equity)
        rets.append(day_ret)

    eq = pd.Series(curve, index=dates)
    r = pd.Series(rets, index=dates)
    total = (eq.iloc[-1] / 1_000_000 - 1) * 100
    n = len(r)
    cagr = ((eq.iloc[-1] / 1_000_000) ** (TRADING_DAYS / max(1, n)) - 1) * 100
    dd = (eq / eq.cummax() - 1) * 100
    mdd = float(dd.min())
    mean, std = r.mean(), r.std()
    sharpe = float((mean - SGOV_APR / TRADING_DAYS) / std * np.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    down = r[r < 0].std()
    sortino = float((mean - SGOV_APR / TRADING_DAYS) / down * np.sqrt(TRADING_DAYS)) if down and down > 0 else 0.0
    tt = wins + losses
    wr = wins / tt * 100 if tt else 0.0
    pf = (gain / loss) if loss > 0 else None
    by_sym: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        s = by_sym.setdefault(t["symbol"], {"trades": 0, "wins": 0, "pnl_pct": 0.0,
                                            "bear_trades": 0, "bear_wins": 0, "bear_pnl": 0.0,
                                            "avg_ret_pct": 0.0})
        s["trades"] += 1
        s["wins"] += 1 if t["ret_pct"] > 0 else 0
        s["pnl_pct"] += t["contrib_pct"]
        s["avg_ret_pct"] += t["ret_pct"]
        if t["regime"] == "BEAR":
            s["bear_trades"] += 1
            s["bear_wins"] += 1 if t["ret_pct"] > 0 else 0
            s["bear_pnl"] += t["contrib_pct"]
    for s in by_sym.values():
        s["win_rate_pct"] = round(s["wins"] / max(1, s["trades"]) * 100, 1)
        s["pnl_pct"] = round(s["pnl_pct"], 2)
        s["avg_ret_pct"] = round(s["avg_ret_pct"] / max(1, s["trades"]), 3)
        s["bear_win_rate_pct"] = round(s["bear_wins"] / s["bear_trades"] * 100, 1) if s["bear_trades"] else None
        s["bear_pnl_pct"] = round(s["bear_pnl"], 2)

    monthly = {}
    if len(r):
        for m, g in r.groupby(r.index.to_period("M")):
            monthly[str(m)] = round(((1 + g).prod() - 1) * 100, 2)

    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return {
        "strategy": params["name"],
        "period": f"{dates[0].date()} ~ {dates[-1].date()} ({n}거래일)",
        "initial_capital_krw": 1_000_000,
        "final_capital_krw": int(round(eq.iloc[-1])),
        "total_return_pct": round(float(total), 2),
        "cagr_pct": round(float(cagr), 2),
        "mdd_pct": round(mdd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "calmar_ratio": round(float(cagr / abs(mdd)), 2) if mdd else None,
        "annualized_volatility_pct": round(float(std * np.sqrt(TRADING_DAYS) * 100), 2),
        "win_rate_pct": round(float(wr), 1),
        "profit_factor": round(float(pf), 2) if pf else None,
        "total_trades": tt, "wins": wins, "losses": losses,
        "avg_win_pct": round(float(np.mean([t["ret_pct"] for t in trades if t["ret_pct"] > 0])) if wins else 0.0, 2),
        "avg_loss_pct": round(float(np.mean([t["ret_pct"] for t in trades if t["ret_pct"] <= 0])) if losses else 0.0, 2),
        "exit_reasons": reasons,
        "monthly_breakdown": monthly,
        "by_symbol": by_sym,
        "equity_curve": [round(float(x), 0) for x in eq.iloc[::5]],
        "params_effective": {k: v for k, v in params.items() if k not in ("weights",)},
        "trades_sample": trades[-10:],
    }


def buy_and_hold(daily: Dict[str, pd.DataFrame], symbol: str, dates) -> Dict[str, float]:
    f = daily.get(symbol)
    if f is None:
        return {}
    c = f["Close"].reindex(dates).dropna()
    if len(c) < 2:
        return {}
    eq = c / c.iloc[0]
    return {"total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
            "mdd_pct": round(float((eq / eq.cummax() - 1).min() * 100), 2)}


def bear_sweep(daily, hourly, base: Dict[str, Any], start=None, end=None) -> List[Dict[str, Any]]:
    rows = []
    for vix in [18.0, 19.0, 20.0, 21.0, 22.0, 24.0]:
        for conf in [1, 2]:
            p = copy.deepcopy(base)
            p["bear_trigger"] = {"vix": vix, "use_ema_break": True, "confirmations_required": conf}
            r = run_strategy(daily, hourly, p, start, end)
            bear = {k: v for k, v in r["by_symbol"].items() if k in ("SOXS", "SQQQ")}
            bt = sum(b["bear_trades"] for b in bear.values())
            rows.append({
                "vix_trigger": vix, "confirmations_required": conf,
                "total_return_pct": r["total_return_pct"], "mdd_pct": r["mdd_pct"],
                "sharpe": r["sharpe_ratio"], "profit_factor": r["profit_factor"],
                "win_rate_pct": r["win_rate_pct"], "trades": r["total_trades"],
                "bear_trades": bt,
                "bear_win_rate_pct": round(sum(b["bear_wins"] for b in bear.values()) / bt * 100, 1) if bt else None,
                "bear_pnl_contrib_pct": round(sum(b["bear_pnl"] for b in bear.values()), 2),
                "bear_share_of_trades_pct": round(bt / max(1, r["total_trades"]) * 100, 1),
            })
    return rows


def ab_compare(period: str = "1y", sweep: bool = True, start: str = None, end: str = None) -> Dict[str, Any]:
    daily = load_daily(period)
    hourly = load_hourly(period)
    base = run_strategy(daily, hourly, BASELINE, start, end)
    up = run_strategy(daily, hourly, UPGRADED, start, end)

    # isolated ablation: upgraded rules but brackets anchored on the planned price (the 11-14 bug)
    nofill = copy.deepcopy(UPGRADED)
    nofill["fill_anchor"] = False
    nofill["name"] = "UPGRADED minus fill-anchoring"
    ab_fill = run_strategy(daily, hourly, nofill, start, end)

    # isolated ablation: upgraded but fixed percentage brackets (no ATR scaling)
    fixed = copy.deepcopy(UPGRADED)
    fixed.pop("tp1_atr_mult"); fixed.pop("tp2_atr_mult"); fixed.pop("sl_atr_mult")
    fixed.update({"tp1_pct": {"SOXL": 2.0, "TQQQ": 2.0, "SOXS": 3.5, "SQQQ": 2.8},
                  "tp2_pct": {"SOXL": 3.5, "TQQQ": 3.5, "SOXS": 6.0, "SQQQ": 5.0},
                  "sl_pct": {"SOXL": 1.5, "TQQQ": 1.5, "SOXS": 2.5, "SQQQ": 2.0}})
    fixed["name"] = "UPGRADED minus ATR scaling"
    ab_atr = run_strategy(daily, hourly, fixed, start, end)

    h_idx = hourly["SOXL"].index
    if getattr(h_idx, "tz", None) is not None:
        h_idx = h_idx.tz_convert("America/New_York")
    d_dates = sorted(set(daily["SOXL"].index) & set(pd.DatetimeIndex(h_idx).normalize().tz_localize(None)))
    report = {
        "title": "CUBE 매수·매도 알고리즘 개선 전/후 백테스트 (시간봉 경로 기준)",
        "is_simulation": True,
        "ledger_warning": "백테스트(시뮬레이션)입니다. 실전 원장(daily_trading_calendar.json / live_portfolio.json)과 완전히 분리되어 있으며 실전 수익으로 표기하지 않습니다.",
        "generated_at_kst": _now_kst(),
        "data": {
            "source": "Yahoo Finance — daily OHLCV + 60분봉 (최근 1년)",
            "universe": UNIVERSE + ["^VIX"],
            "friction": "왕복 0.24% (NH 수수료 0.09% + 슬리피지 0.15%)",
            "fill_model": ("시가/트리거 체결 + 슬리피지. 진입 봉에서는 봉 종가가 목표가를 넘긴 경우에만 익절 인정, "
                           "손절은 저가 터치 인정. 같은 봉에서 손절·목표가가 동시에 닿으면 손절 우선(보수적). "
                           "러너는 당일 종가 강제 청산(당일 회전 원칙)."),
            "limitation": "60분봉 기준이므로 봉 내부 실제 체결 순서는 근사값입니다. 실전 결과와 다를 수 있습니다.",
        },
        "baseline": base,
        "upgraded": up,
        "ablations": {
            "fill_anchor_removed": {"total_return_pct": ab_fill["total_return_pct"], "win_rate_pct": ab_fill["win_rate_pct"],
                                    "profit_factor": ab_fill["profit_factor"], "mdd_pct": ab_fill["mdd_pct"],
                                    "sharpe_ratio": ab_fill["sharpe_ratio"], "total_trades": ab_fill["total_trades"]},
            "atr_scaling_removed": {"total_return_pct": ab_atr["total_return_pct"], "win_rate_pct": ab_atr["win_rate_pct"],
                                    "profit_factor": ab_atr["profit_factor"], "mdd_pct": ab_atr["mdd_pct"],
                                    "sharpe_ratio": ab_atr["sharpe_ratio"], "total_trades": ab_atr["total_trades"]},
        },
    }
    report["delta"] = {
        "total_return_pct": round(up["total_return_pct"] - base["total_return_pct"], 2),
        "cagr_pct": round(up["cagr_pct"] - base["cagr_pct"], 2),
        "win_rate_pct": round(up["win_rate_pct"] - base["win_rate_pct"], 1),
        "profit_factor": (round(up["profit_factor"] - base["profit_factor"], 2)
                          if up["profit_factor"] is not None and base["profit_factor"] is not None else None),
        "sharpe_ratio": round(up["sharpe_ratio"] - base["sharpe_ratio"], 2),
        "mdd_pct": round(up["mdd_pct"] - base["mdd_pct"], 2),
        "total_trades": up["total_trades"] - base["total_trades"],
    }
    report["benchmarks"] = {f"{s}_buy_and_hold": buy_and_hold(daily, s, d_dates) for s in ("SOXL", "TQQQ", "QQQ")}
    if sweep:
        rows = bear_sweep(daily, hourly, UPGRADED, start, end)
        report["bear_engine_sweep"] = rows
        elig = [r for r in rows if r["bear_trades"] >= 5]
        if elig:
            best = max(elig, key=lambda r: r["total_return_pct"] / max(1.0, abs(r["mdd_pct"])))
            report["bear_engine_choice"] = {
                "vix_trigger": best["vix_trigger"], "confirmations_required": best["confirmations_required"],
                "objective": "총수익률 / |MDD| (Calmar형) 최대. 숏 트레이드 최소 5회 이상 조건",
                "detail": best,
            }
    return report


def write_report(report: Dict[str, Any]) -> str:
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    for p in (REPORT_PATH, DOCS_REPORT_PATH):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
    return REPORT_PATH


if __name__ == "__main__":
    rep = ab_compare(period="1y")
    write_report(rep)
    for k in ("baseline", "upgraded"):
        r = rep[k]
        print(f"{k:9s} ret {r['total_return_pct']:+.2f}% wr {r['win_rate_pct']:.1f}% pf {r['profit_factor']} "
              f"sharpe {r['sharpe_ratio']} mdd {r['mdd_pct']:.2f}% trades {r['total_trades']} "
              f"avgW {r['avg_win_pct']} avgL {r['avg_loss_pct']}")
    print("delta", rep["delta"])
    print("ablations", rep["ablations"])
    print("bear choice", rep.get("bear_engine_choice"))
