"""
CUBE Real Market Data Layer (single fetch point for all 10 agents)

Every agent in the desk reads one snapshot instead of inventing numbers:
  - daily OHLCV history (1y) for the tradable universe & benchmarks
  - latest quotes for equities, index futures, volatility, rates, FX, crypto
  - a freshness/staleness contract so downstream text never claims "실시간"
    for a stale cache.

Data source: Yahoo Finance via yfinance (no API key). Cache: data_cache/.
Snapshot: data/market_snapshot.json (also mirrored to docs/).
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")
SNAPSHOT_PATH = os.path.join(BASE_DIR, "data", "market_snapshot.json")
DOCS_SNAPSHOT_PATH = os.path.join(BASE_DIR, "docs", "market_snapshot.json")
HISTORY_CACHE = os.path.join(CACHE_DIR, "history_1y.pkl")

HISTORY_TTL_HOURS = float(os.getenv("CUBE_HISTORY_TTL_H", "4"))
QUOTE_TTL_MINUTES = float(os.getenv("CUBE_QUOTE_TTL_MIN", "8"))

EQUITY_UNIVERSE = ["SOXL", "TQQQ", "NVDA", "TSLA", "SGOV", "SOXS", "SQQQ", "QQQ", "SPY", "AMD", "AVGO", "TSM", "MU", "SMH"]
MACRO_UNIVERSE = ["^VIX", "^TNX", "^IRX", "DX-Y.NYB", "KRW=X", "JPY=X", "EURUSD=X", "^HSI", "^N225", "^GDAXI", "NQ=F", "ES=F", "CL=F", "BZ=F", "GC=F", "BTC-USD", "ETH-USD"]
ALL_SYMBOLS = EQUITY_UNIVERSE + MACRO_UNIVERSE

PARQUET_CACHE = os.path.join(CACHE_DIR, "quotes_5m.pkl")


def _read_pickle(path: str) -> Optional[dict]:
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                import pickle
                return pickle.load(f)
    except Exception:
        return None
    return None


def _write_pickle(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "wb") as f:
            import pickle
            pickle.dump(obj, f)
    except Exception as e:
        print(f"[snapshot] cache write failed: {e}")


def _normalize_download(df: pd.DataFrame, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """Return {symbol: DataFrame(OHLCV)} from a yfinance download in either shape."""
    out: Dict[str, pd.DataFrame] = {}
    if df is None or len(df) == 0:
        return out
    if isinstance(df.columns, pd.MultiIndex):
        levels = [list(df.columns.get_level_values(i)) for i in range(df.columns.nlevels)]
        # yfinance default: level0 = field, level1 = ticker
        if set(s.lower() for s in map(str, levels[0])) & {"open", "close", "high", "low", "adj close"}:
            by_ticker = 1
        else:
            by_ticker = 0
        for sym in symbols:
            try:
                sub = df.xs(sym, axis=1, level=by_ticker, drop_level=True)
            except Exception:
                continue
            sub = sub.reindex(columns=[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in sub.columns])
            if "Close" in sub.columns:
                out[sym] = sub.dropna(subset=["Close"])
    else:
        # single ticker
        if symbol_present := ("Close" in df.columns):
            sub = df.reindex(columns=[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns])
            out[symbols[0]] = sub.dropna(subset=["Close"]) if "Close" in sub.columns else sub
    return out


def fetch_history(symbols: List[str] = None, period: str = "1y", force: bool = False) -> Dict[str, pd.DataFrame]:
    """Daily OHLCV history per symbol, cached for HISTORY_TTL_HOURS."""
    symbols = symbols or ALL_SYMBOLS
    cache = _read_pickle(HISTORY_CACHE) or {}
    frames: Dict[str, pd.DataFrame] = cache.get("frames", {}) or {}
    fetched_at = cache.get("fetched_at")
    fresh = False
    if fetched_at:
        try:
            age_h = (datetime.utcnow() - datetime.fromisoformat(fetched_at)).total_seconds() / 3600.0
            fresh = age_h < HISTORY_TTL_HOURS
        except Exception:
            fresh = False

    missing = [s for s in symbols if s not in frames or frames[s] is None or len(frames[s]) < 30]
    if fresh and not missing and not force:
        return {s: frames[s] for s in symbols if s in frames}

    try:
        import yfinance as yf
        dl = yf.download(symbols, period=period, interval="1d", progress=False,
                         auto_adjust=False, group_by="column", threads=True)
        got = _normalize_download(dl, symbols)
        if got:
            frames.update(got)
            _write_pickle(HISTORY_CACHE, {"fetched_at": datetime.utcnow().isoformat(), "frames": frames})
    except Exception as e:
        print(f"[snapshot] history fetch failed: {e}")

    return {s: frames[s] for s in symbols if s in frames}


def _last_two(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or len(frame) == 0 or "Close" not in frame:
        return {}
    c = frame["Close"].astype(float).dropna()
    if len(c) < 2:
        return {}
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    vol = float(frame["Volume"].iloc[-1]) if "Volume" in frame and pd.notna(frame["Volume"].iloc[-1]) else None
    avg_vol = None
    if "Volume" in frame and len(frame) > 21:
        avg_vol = float(frame["Volume"].astype(float).tail(21).head(20).mean())
    return {
        "last": round(last, 4),
        "prev_close": round(prev, 4),
        "change": round(last - prev, 4),
        "change_pct": round((last / prev - 1.0) * 100, 2) if prev else 0.0,
        "day_high": round(float(frame["High"].iloc[-1]), 4) if "High" in frame and pd.notna(frame["High"].iloc[-1]) else None,
        "day_low": round(float(frame["Low"].iloc[-1]), 4) if "Low" in frame and pd.notna(frame["Low"].iloc[-1]) else None,
        "volume": int(vol) if vol else None,
        "avg_volume_20d": int(avg_vol) if avg_vol else None,
        "rvol": round(vol / avg_vol, 2) if vol and avg_vol else None,
        "last_bar_date": str(frame.index[-1].date()) if hasattr(frame.index[-1], "date") else str(frame.index[-1]),
    }


def _intraday_last(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest intraday price for live sessions (5m bars, 1 day)."""
    if not symbols:
        return {}
    try:
        import yfinance as yf
        dl = yf.download(symbols, period="1d", interval="5m", progress=False,
                         auto_adjust=False, group_by="column", threads=True)
        frames = _normalize_download(dl, symbols)
        out = {}
        for sym, f in frames.items():
            if f is None or len(f) == 0:
                continue
            c = f["Close"].astype(float).dropna()
            if len(c):
                out[sym] = {
                    "price": round(float(c.iloc[-1]), 4),
                    "bar_time": str(f.index[-1]),
                    "bars": int(len(c)),
                }
        return out
    except Exception as e:
        print(f"[snapshot] intraday fetch failed: {e}")
        return {}


def build_snapshot(force_history: bool = False, quiet: bool = False) -> Dict[str, Any]:
    """Build (and persist) the desk-wide market snapshot."""
    from core import market_clock

    clock = market_clock.resolve()
    now_utc = datetime.utcnow()

    # Serve a very recent snapshot instead of hammering the API on repeated calls.
    prev = None
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = None
    if prev and not force_history:
        try:
            age_min = (now_utc - datetime.fromisoformat(prev["generated_at_utc"])).total_seconds() / 60.0
            if age_min < QUOTE_TTL_MINUTES and prev.get("phase") == clock["phase"]:
                prev["clock"] = clock
                return prev
        except Exception:
            pass

    hist = fetch_history(force=force_history)
    quotes: Dict[str, Any] = {}
    for sym, frame in hist.items():
        q = _last_two(frame)
        if q:
            quotes[sym] = q

    intraday = {}
    if clock["phase"] in {"REGULAR", "PRE_MARKET", "POST_MARKET"}:
        intraday = _intraday_last(EQUITY_UNIVERSE)

    for sym, iv in intraday.items():
        if sym in quotes:
            base = quotes[sym]
            # intraday price replaces the daily close only when it is from the same session
            quotes[sym]["intraday_price"] = iv["price"]
            quotes[sym]["intraday_bar_time"] = iv["bar_time"]
            quotes[sym]["change_pct_intraday"] = round((iv["price"] / base["prev_close"] - 1.0) * 100, 2) if base.get("prev_close") else None

    def q(sym: str) -> Dict[str, Any]:
        return quotes.get(sym, {})

    fx_usd_krw = q("KRW=X").get("last") or 1335.0
    snapshot = {
        "generated_at_utc": now_utc.isoformat(),
        "generated_at_kst": clock["now_kst"],
        "phase": clock["phase"],
        "clock": clock,
        "data_source": "Yahoo Finance (yfinance) — daily OHLCV + 5m intraday",
        "history_fetched_at": (_read_pickle(HISTORY_CACHE) or {}).get("fetched_at"),
        "quotes": quotes,
        "fx": {
            "usd_krw": round(float(fx_usd_krw), 2),
            "usd_jpy": q("JPY=X").get("last"),
            "eur_usd": q("EURUSD=X").get("last"),
        },
        "macro": {
            "vix": q("^VIX"),
            "us_10y_yield": q("^TNX").get("last"),
            "us_3m_yield": q("^IRX").get("last"),
            "dxy": q("DX-Y.NYB"),
            "hang_seng": q("^HSI"),
            "nikkei": q("^N225"),
            "dax": q("^GDAXI"),
        },
        "futures": {"nq": q("NQ=F"), "es": q("ES=F"), "wti": q("CL=F"), "brent": q("BZ=F"), "gold": q("GC=F")},
        "crypto": {"btc": q("BTC-USD"), "eth": q("ETH-USD")},
        "intraday": intraday,
        "universe": EQUITY_UNIVERSE,
    }

    # Staleness contract: last bar must match the last completed US session when closed.
    last_bar = q("SOXL").get("last_bar_date")
    snapshot["freshness"] = {
        "last_equity_bar": last_bar,
        "last_completed_session": clock["last_completed_session"],
        "quotes_are_last_close": clock,  # explicit: outside REGULAR these are closing prices
    }

    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(DOCS_SNAPSHOT_PATH), exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    for p in (SNAPSHOT_PATH, DOCS_SNAPSHOT_PATH):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception as e:
            print(f"[snapshot] write failed {p}: {e}")

    if not quiet:
        print(f"✅ market snapshot {clock['now_kst']} [{clock['phase']}] "
              f"SOXL {q('SOXL').get('last')} VIX {q('^VIX').get('last')} BTC {q('BTC-USD').get('last')}")
    return snapshot


def load_snapshot() -> Dict[str, Any]:
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return build_snapshot()


if __name__ == "__main__":
    build_snapshot(force_history=True)
