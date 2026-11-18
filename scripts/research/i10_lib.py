"""10분봉 인트라데이 연구 라이브러리 (2026-11-18).

동훈님 지시(2026-11-18 11:14 KST): "지난 주 5거래일 동안 숏인지 롱인지 제대로 작동해서
장중 매수 매도를 지속 진행하여 데일리 수익을 창출했다면 어느정도 수익이 났는지 10분 단위로
백테스트 + 급락·급등 변곡점을 적절히 찾았는지 확인".

데이터
------
- yfinance 5분봉(최대 60일)을 받아 정규장(09:30~16:00 ET)만 남기고 10분봉으로 리샘플.
- 5분봉 라벨은 봉 시작 시각. 10분봉 = 09:30/09:40/.../15:50 시작, 15:50봉이 마지막(15:50~16:00).
- 캐시: data_cache/research/{SYM}_5m_60d.pkl (기존 btlib 캐시 규약과 동일 위치)

비용·체결
----------
- 왕복 0.24%(편도 12bp). 진입/청산 모두 **신호 봉 종가** 체결로 가정하고 비용을 차감한다.
  (룩어헤드 없음 — 결정은 그 봉까지의 정보만 사용)
- 정수주 수량. NH Open API가 정수주만 받는다.
"""
from __future__ import annotations

import os
import pickle
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(BASE_DIR, "data_cache", "research")
os.makedirs(CACHE, exist_ok=True)

RT_COST = 0.0024
SIDE_COST = RT_COST / 2.0

SYMBOLS = ["SOXL", "TQQQ", "SOXS", "SQQQ", "QQQ", "NVDA", "^VIX"]
SESSION_DAYS = ["2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"]
# 인버스(숏 대용) 매핑 — 현금계좌에서 숏은 인버스 ETF 매수로만 구현 가능
INVERSE_OF = {"SOXS": "SOXL", "SQQQ": "TQQQ"}


# ------------------------------------------------------------------ loading
def load5m(sym: str, period: str = "60d", refresh: bool = False) -> pd.DataFrame:
    path = os.path.join(CACHE, f"__10m_{sym}_{period}.pkl")
    if os.path.exists(path) and not refresh:
        try:
            with open(path, "rb") as f:
                df = pickle.load(f)
            if df is not None and len(df):
                return df
        except Exception:
            pass
    import yfinance as yf
    df = yf.download(sym, period=period, interval="5m", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(how="all")
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("America/New_York")
    if len(df):
        with open(path, "wb") as f:
            pickle.dump(df, f)
    return df


def regular_session(df5: pd.DataFrame) -> pd.DataFrame:
    """정규장 09:30~16:00 ET만."""
    if df5 is None or not len(df5):
        return pd.DataFrame()
    d = df5.copy()
    t = d.index.time
    mask = (np.array([str(x) for x in t]) >= "09:30:00") & (np.array([str(x) for x in t]) < "16:00:00")
    d = d[mask]
    d.index = d.index.tz_localize(None)
    return d


def to10m(df5: pd.DataFrame) -> pd.DataFrame:
    """5분봉 → 10분봉(세션별). 09:30, 09:40, … 15:50 시작."""
    d = regular_session(df5)
    if not len(d):
        return pd.DataFrame()
    rows = []
    for day, g in d.groupby(d.index.date):
        r = g.resample("10min", label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        r = r.dropna(subset=["Close"])
        r["session"] = str(day)
        r["minute"] = [ts.hour * 60 + ts.minute for ts in r.index]
        rows.append(r)
    out = pd.concat(rows).sort_index()
    out["hm"] = [f"{ts.hour:02d}:{ts.minute:02d}" for ts in out.index]
    # hm_end = 봉 종료(=결정·체결) 시각. 10분봉 라벨은 시작 시각이므로 혼동을 막기 위해 함께 둔다.
    out["hm_end"] = [f"{(ts + pd.Timedelta(minutes=10)).hour:02d}:{(ts + pd.Timedelta(minutes=10)).minute:02d}"
                     for ts in out.index]
    return out


def panel(symbols: Optional[List[str]] = None, period: str = "60d",
          refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """10분봉 패널. 세션일은 문자열 'YYYY-MM-DD'."""
    out: Dict[str, pd.DataFrame] = {}
    for s in (symbols or SYMBOLS):
        b = to10m(load5m(s, period, refresh=refresh))
        if len(b):
            out[s] = b
    return out


def sessions(bars: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    return {d: g for d, g in bars.groupby("session")}


# ------------------------------------------------------------------ indicators
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 2) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    c, h, l = df["Close"], df["High"], df["Low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std()
    up, lo = mid + k * sd, mid - k * sd
    pctb = (s - lo) / (up - lo).replace(0, np.nan)
    return mid, up, lo, pctb


def session_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    v = df["Volume"].fillna(0).astype(float)
    cum_v = v.cumsum().replace(0, np.nan)
    return (tp * v).cumsum() / cum_v


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff()
    dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).fillna(0)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def enrich(df: pd.DataFrame, daily_atr_pct: Optional[float] = None) -> pd.DataFrame:
    d = df.copy()
    d["ema9"] = ema(d["Close"], 9)
    d["ema21"] = ema(d["Close"], 21)
    d["ema20"] = ema(d["Close"], 20)
    d["rsi2"] = rsi(d["Close"], 2)
    d["rsi14"] = rsi(d["Close"], 14)
    mid, up, lo, pctb = bollinger(d["Close"], 20, 2.0)
    d["bb_mid"], d["bb_up"], d["bb_lo"], d["pct_b"] = mid, up, lo, pctb
    d["atr"] = atr(d, 14)
    d["vwap"] = session_vwap(d)
    d["adx"] = adx(d, 14)
    d["vol_ma20"] = d["Volume"].rolling(20).mean().shift(1)
    d["rvol"] = d["Volume"] / d["vol_ma20"].replace(0, np.nan)
    d["atr_pct"] = d["atr"] / d["Close"] * 100
    return d


# ------------------------------------------------------------------ stats
def trade_stats(trades: List[Dict], budget: float) -> Dict:
    rows = [t for t in trades if t.get("ret_pct") is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "pnl_usd": 0.0, "win_pct": None, "pf": None, "mdd_usd": 0.0,
                "fee_usd": 0.0, "fee_share_pct": None, "avg_pct": None, "t_stat": None,
                "days": 0, "pos_days": 0, "per_day": {}}
    r = np.array([float(t["ret_pct"]) for t in rows])
    pnl = np.array([float(t.get("pnl_usd", 0.0)) for t in rows])
    wins, losses = r[r > 0], r[r <= 0]
    gw, gl = float(wins.sum()), float(abs(losses.sum()))
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    mdd = float((eq - peak).min()) if n else 0.0
    tstat = float(r.mean() / (r.std(ddof=1) / np.sqrt(n))) if n > 2 and r.std(ddof=1) > 0 else None
    per_day: Dict[str, Dict] = {}
    for t in rows:
        d = str(t.get("date"))
        p = per_day.setdefault(d, {"pnl_usd": 0.0, "n": 0, "ret_pct": 0.0})
        p["pnl_usd"] += float(t.get("pnl_usd", 0.0))
        p["ret_pct"] += float(t["ret_pct"])
        p["n"] += 1
    fee = sum(float(t.get("fee_usd", 0.0)) for t in rows)
    gross_abs = sum(abs(float(t.get("gross_pct", t["ret_pct"]))) / 100.0 * float(t.get("notional_usd", budget))
                    for t in rows)
    return {"n": n, "pnl_usd": round(float(pnl.sum()), 2),
            "win_pct": round(100 * len(wins) / n, 1),
            "pf": round(gw / gl, 2) if gl else None,
            "mdd_usd": round(mdd, 2), "fee_usd": round(fee, 2),
            "fee_share_pct": round(100 * fee / gross_abs, 1) if gross_abs else None,
            "avg_pct": round(float(r.mean()), 3),
            "avg_win_pct": round(float(wins.mean()), 2) if len(wins) else 0.0,
            "avg_loss_pct": round(float(losses.mean()), 2) if len(losses) else 0.0,
            "t_stat": round(tstat, 2) if tstat is not None else None,
            "days": len(per_day), "pos_days": sum(1 for v in per_day.values() if v["pnl_usd"] > 0),
            "per_day": {k: {"pnl_usd": round(v["pnl_usd"], 2), "ret_pct": round(v["ret_pct"], 2), "n": v["n"]}
                        for k, v in sorted(per_day.items())}}


def make_trade(date: str, ticker: str, side: str, entry_ts, exit_ts, entry: float, exit_px: float,
               qty: int, reason: str, rt_cost: float = RT_COST) -> Dict:
    gross_pct = (exit_px / entry - 1) * 100 if side == "LONG" else (entry / exit_px - 1) * 100
    notional = entry * qty
    fee = notional * rt_cost
    pnl = (exit_px - entry) * qty if side == "LONG" else (entry - exit_px) * qty
    pnl -= fee
    net_pct = pnl / notional * 100 if notional else 0.0
    return {"date": date, "ticker": ticker, "side": side,
            "entry_ts": str(entry_ts), "exit_ts": str(exit_ts),
            "entry": round(float(entry), 4), "exit": round(float(exit_px), 4),
            "qty": int(qty), "notional_usd": round(notional, 2),
            "gross_pct": round(gross_pct, 3), "ret_pct": round(net_pct, 3),
            "pnl_usd": round(pnl, 2), "fee_usd": round(fee, 2),
            "hold_min": round((pd.Timestamp(exit_ts) - pd.Timestamp(entry_ts)).total_seconds() / 60.0, 0),
            "reason": reason}


def qty_for_budget(price: float, budget: float) -> int:
    if price <= 0:
        return 0
    return max(0, int(budget // price))
