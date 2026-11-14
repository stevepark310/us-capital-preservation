"""
CUBE Real Market Data Layer (single fetch point for all 10 agents)

Every agent in the desk reads one snapshot instead of inventing numbers:
  - daily OHLCV history (1y) for the tradable universe & benchmarks
  - latest quotes for equities, index futures, volatility, rates, FX, crypto,
    US Treasuries / credit ETFs and the business-cycle proxies
  - official macro series from FRED's public CSV endpoint (no API key), cached
    on disk with a TTL and a stale-cache fallback
  - a freshness/staleness contract so downstream text never claims "실시간"
    for a stale cache.

Data source: Yahoo Finance via yfinance (no API key) + FRED fredgraph.csv.
Cache: data_cache/.  Snapshot: data/market_snapshot.json (mirrored to docs/).
"""

import csv
import io
import urllib.request

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

# --- 2026-11-14 확장: 미국 국채/채권 · 외환 · 코인 · 경기순환 프록시 -----------------
# ^TNX=10Y, ^IRX=13주 할인율(3M 근사), ^FVX=5Y, ^TYX=30Y 지수형 금리
BOND_UNIVERSE = ["^TNX", "^IRX", "^FVX", "^TYX", "TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD"]
FX_UNIVERSE = ["DX-Y.NYB", "KRW=X", "JPY=X", "EURUSD=X", "CNY=X"]
CRYPTO_UNIVERSE = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "IBIT", "COIN"]
CYCLE_UNIVERSE = ["HG=F", "GC=F", "XLY", "XLP", "IWM"]
MACRO_UNIVERSE = ["^VIX", "^HSI", "^N225", "^GDAXI", "NQ=F", "ES=F", "CL=F", "BZ=F"]


def _dedupe(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


ALL_SYMBOLS = _dedupe(EQUITY_UNIVERSE + MACRO_UNIVERSE + BOND_UNIVERSE
                      + FX_UNIVERSE + CRYPTO_UNIVERSE + CYCLE_UNIVERSE)

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


# --------------------------------------------------------------- FRED loader
# Public CSV endpoint (no API key): https://fred.stlouisfed.org/graph/fredgraph.csv?id=...
# Cache: data_cache/fred/{SERIES}.csv, TTL default 6h, stale cache returned on failure.
FRED_DIR = os.path.join(CACHE_DIR, "fred")
FRED_TTL_HOURS = float(os.getenv("CUBE_FRED_TTL_H", "6"))
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_SERIES: Dict[str, Dict[str, str]] = {
    "T10Y3M": {"label": "10Y-3M 장단기 금리차", "units": "%p", "scale": "bp", "freq": "daily"},
    "T10Y2Y": {"label": "10Y-2Y 장단기 금리차", "units": "%p", "scale": "bp", "freq": "daily"},
    "DGS2": {"label": "미 국채 2년 금리", "units": "%", "scale": "yield", "freq": "daily"},
    "DGS10": {"label": "미 국채 10년 금리", "units": "%", "scale": "yield", "freq": "daily"},
    "BAMLH0A0HYM2": {"label": "ICE BofA US High Yield OAS", "units": "%", "scale": "oas", "freq": "daily"},
    "NFCI": {"label": "시카고 연준 금융여건지수(NFCI)", "units": "index", "scale": "z", "freq": "weekly"},
    "ICSA": {"label": "신규 실업수당 청구건수", "units": "건", "scale": "count", "freq": "weekly"},
    "UMCSENT": {"label": "미시간대 소비자심리지수", "units": "index", "scale": "index", "freq": "monthly"},
}


def _http_get(url: str, timeout: float = 15.0) -> str:
    """Plain HTTPS GET. requests/curl_cffi first (this sandbox's raw urllib TLS
    path times out on some hosts), urllib only as a last resort."""
    try:
        import requests  # type: ignore
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    try:
        from curl_cffi import requests as creq  # type: ignore
        r = creq.get(url, timeout=timeout, impersonate="chrome")
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _fred_path(sid: str) -> str:
    return os.path.join(FRED_DIR, f"{sid}.csv")


def _parse_fred_csv(text: str) -> List[tuple]:
    """FRED CSV -> [(date_str, float)] skipping '.' / blank observations."""
    rows: List[tuple] = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if not v or v == ".":
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def fetch_fred_series(sid: str, ttl_hours: float = None, force: bool = False,
                      include_history: bool = False) -> Dict[str, Any]:
    """One FRED series: latest observation, change, 5y stats, optional history."""
    ttl = FRED_TTL_HOURS if ttl_hours is None else ttl_hours
    path = _fred_path(sid)
    meta = FRED_SERIES.get(sid, {})
    rows: List[tuple] = []
    source = "UNKNOWN"
    cache_age_h = None

    if os.path.exists(path) and not force:
        try:
            age_h = (datetime.utcnow() - datetime.utcfromtimestamp(os.path.getmtime(path))).total_seconds() / 3600.0
            cache_age_h = round(age_h, 2)
            with open(path, "r", encoding="utf-8") as f:
                rows = _parse_fred_csv(f.read())
            if rows and age_h < ttl:
                source = "FRED cache (fresh)"
        except Exception:
            rows = []

    if not rows or source == "UNKNOWN":
        text = _http_get(FRED_URL.format(sid=sid))
        if text:
            parsed = _parse_fred_csv(text)
            if parsed:
                os.makedirs(FRED_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                rows = parsed
                source = "FRED live"
                cache_age_h = 0.0
        if source == "UNKNOWN":
            if rows:
                source = "FRED cache (stale, fetch failed)"
            else:
                return {"id": sid, "label": meta.get("label", sid), "available": False,
                        "error": "fetch failed (requests/curl_cffi/urllib 모두 실패)", "source": "UNAVAILABLE"}

    if not rows:
        return {"id": sid, "label": meta.get("label", sid), "available": False, "source": "UNAVAILABLE"}

    ser = pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
    ser = ser[~ser.index.duplicated(keep="last")]
    latest = float(ser.iloc[-1])
    prev = float(ser.iloc[-2]) if len(ser) > 1 else latest
    win5y = ser[ser.index >= (ser.index[-1] - pd.Timedelta(days=365 * 5))]
    mean5y = float(win5y.mean()) if len(win5y) else float(ser.mean())
    std5y = float(win5y.std()) if len(win5y) > 2 else float(ser.std())
    z5y = round((latest - mean5y) / std5y, 2) if std5y and std5y > 0 else None
    pct_rank = round(float((ser.tail(2520) <= latest).mean()) * 100, 1) if len(ser) else None

    out: Dict[str, Any] = {
        "id": sid, "label": meta.get("label", sid), "available": True,
        "latest": round(latest, 4), "prev": round(prev, 4),
        "change": round(latest - prev, 4),
        "unit": meta.get("units", ""), "scale": meta.get("scale", ""),
        "date": ser.index[-1].strftime("%Y-%m-%d"),
        "mean_5y": round(mean5y, 4), "std_5y": round(std5y, 4), "z_5y": z5y,
        "pct_rank_10y": pct_rank,
        "source": source, "cache_age_h": cache_age_h,
        "observations": int(len(ser)),
    }
    # monthly tail for charting (keeps the snapshot small)
    try:
        monthly = ser.resample("ME").last().dropna().tail(36)
        out["history_monthly"] = [{"date": ix.strftime("%Y-%m"), "value": round(float(v), 4)} for ix, v in monthly.items()]
    except Exception:
        out["history_monthly"] = []
    if include_history:
        out["_series"] = ser
    return out


def load_fred(series: List[str] = None, force: bool = False, include_history: bool = False) -> Dict[str, Any]:
    """All configured FRED series in one dict (used by snapshot/LEI engine).

    Fetched concurrently: a cold cache costs ~15s per series here, so eight
    series in a thread pool take about one round-trip instead of two minutes.
    """
    sids = series or list(FRED_SERIES.keys())
    out: Dict[str, Any] = {}

    def one(sid: str):
        try:
            return sid, fetch_fred_series(sid, force=force, include_history=include_history)
        except Exception as e:
            return sid, {"id": sid, "available": False, "error": str(e), "source": "UNAVAILABLE"}

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(sids))) as ex:
            for sid, res in ex.map(one, sids):
                out[sid] = res
    except Exception:
        for sid in sids:
            k, v = one(sid)
            out[k] = v
    return out


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
            # a snapshot written before the 2026-11-14 expansion has no bonds/fx/crypto
            # blocks — rebuild instead of serving a partial schema to the agents.
            if (age_min < QUOTE_TTL_MINUTES and prev.get("phase") == clock["phase"]
                    and "bonds" in prev and "cycle" in prev):
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

    def lv(sym: str) -> Any:
        return q(sym).get("last")

    def pct(sym: str) -> Any:
        return q(sym).get("change_pct")

    def ret(sym: str, bars: int) -> Any:
        f = hist.get(sym)
        if f is None or "Close" not in f or len(f) <= bars:
            return None
        c = f["Close"].astype(float).dropna()
        if len(c) <= bars:
            return None
        return round((float(c.iloc[-1]) / float(c.iloc[-1 - bars]) - 1.0) * 100, 2)

    fx_usd_krw = q("KRW=X").get("last") or 1335.0

    # ---- FRED (official macro/credit series) ---------------------------------
    try:
        fred = load_fred(include_history=False)
    except Exception as e:
        fred = {}
        print(f"[snapshot] FRED unavailable: {e}")

    def fv(sid: str) -> Any:
        d = fred.get(sid) or {}
        return d.get("latest") if d.get("available") else None

    def fz(sid: str) -> Any:
        d = fred.get(sid) or {}
        return d.get("z_5y") if d.get("available") else None

    # 10Y-3M: FRED is the official spread (^TNX-^IRX is a live approximation:
    # ^IRX is a 13-week bank-discount rate, ~15-25bp below DGS3MO).
    spread_10y3m = fv("T10Y3M")
    spread_10y2y = fv("T10Y2Y")
    y10 = fv("DGS10") if fv("DGS10") is not None else lv("^TNX")
    y2 = fv("DGS2")
    y5 = lv("^FVX")
    y30 = lv("^TYX")
    y3m_live = lv("^IRX")
    if spread_10y3m is None and y10 is not None and y3m_live is not None:
        spread_10y3m = round(float(y10) - float(y3m_live), 4)
        spread_source = "^TNX - ^IRX (FRED 실패 폴백)"
    else:
        spread_source = "FRED T10Y3M"
    if spread_10y2y is None and y10 is not None and y2 is not None:
        spread_10y2y = round(float(y10) - float(y2), 4)

    def dur_pnl(ticker: str, duration: float, bp: float) -> Any:
        """Price % change implied by a parallel yield move (duration approximation)."""
        return round(-duration * (bp / 100.0), 2)

    bonds = {
        "yields_pct": {"3m": y3m_live, "2y": y2, "5y": y5, "10y": y10, "30y": y30},
        "curve": {
            "10y3m_bp": round(float(spread_10y3m) * 100, 1) if spread_10y3m is not None else None,
            "10y2y_bp": round(float(spread_10y2y) * 100, 1) if spread_10y2y is not None else None,
            "30y10y_bp": round((float(y30) - float(y10)) * 100, 1) if (y30 and y10) else None,
            "5y10y_bp": round((float(y10) - float(y5)) * 100, 1) if (y5 and y10) else None,
            "source": spread_source,
            "shape": ("INVERTED (역전)" if (spread_10y3m is not None and spread_10y3m < 0) else
                      "FLAT (평탄·디커플링)" if (spread_10y3m is not None and spread_10y3m < 0.5) else
                      "STEEP (정상 우상향)" if spread_10y3m is not None else "UNKNOWN"),
        },
        "etfs": {
            t: {"last": lv(t), "chg_pct": pct(t), "ret_5d": ret(t, 5), "ret_20d": ret(t, 20), "ret_63d": ret(t, 63),
                "atr_pct": None, "volume": q(t).get("volume"), "bar": q(t).get("last_bar_date")}
            for t in ("TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD")
        },
        "duration_years": {"TLT": 16.8, "IEF": 7.2, "SHY": 1.8, "SGOV": 0.1, "TIP": 4.6, "LQD": 8.4, "HYG": 3.1},
        "duration_pnl_per_10bp_pct": {t: dur_pnl(t, d, 10) for t, d in
                                      {"TLT": 16.8, "IEF": 7.2, "SHY": 1.8, "TIP": 4.6, "LQD": 8.4, "HYG": 3.1}.items()},
        "hy_oas_pct": fv("BAMLH0A0HYM2"),
        "hy_oas_bp": round(float(fv("BAMLH0A0HYM2")) * 100, 1) if fv("BAMLH0A0HYM2") is not None else None,
        "hy_oas_z_5y": fz("BAMLH0A0HYM2"),
        "hyg_ief_rel_63d_pct": (round((ret("HYG", 63) or 0.0) - (ret("IEF", 63) or 0.0), 2)
                                if ret("HYG", 63) is not None and ret("IEF", 63) is not None else None),
    }

    fx_market = {
        "dxy": {"last": lv("DX-Y.NYB"), "chg_pct": pct("DX-Y.NYB"), "ret_20d": ret("DX-Y.NYB", 20)},
        "usd_krw": {"last": lv("KRW=X"), "chg_pct": pct("KRW=X"), "ret_5d": ret("KRW=X", 5), "ret_20d": ret("KRW=X", 20)},
        "usd_jpy": {"last": lv("JPY=X"), "chg_pct": pct("JPY=X"), "ret_20d": ret("JPY=X", 20)},
        "eur_usd": {"last": lv("EURUSD=X"), "chg_pct": pct("EURUSD=X"), "ret_20d": ret("EURUSD=X", 20)},
        "usd_cny": {"last": lv("CNY=X"), "chg_pct": pct("CNY=X"), "ret_20d": ret("CNY=X", 20)},
    }

    crypto_market = {
        "btc": {"last": lv("BTC-USD"), "chg_pct": pct("BTC-USD"), "ret_7d": ret("BTC-USD", 7), "ret_30d": ret("BTC-USD", 30)},
        "eth": {"last": lv("ETH-USD"), "chg_pct": pct("ETH-USD"), "ret_7d": ret("ETH-USD", 7), "ret_30d": ret("ETH-USD", 30)},
        "sol": {"last": lv("SOL-USD"), "chg_pct": pct("SOL-USD"), "ret_7d": ret("SOL-USD", 7), "ret_30d": ret("SOL-USD", 30)},
        "xrp": {"last": lv("XRP-USD"), "chg_pct": pct("XRP-USD"), "ret_7d": ret("XRP-USD", 7), "ret_30d": ret("XRP-USD", 30)},
        "ibit": {"last": lv("IBIT"), "chg_pct": pct("IBIT"), "ret_20d": ret("IBIT", 20)},
        "coin": {"last": lv("COIN"), "chg_pct": pct("COIN"), "ret_20d": ret("COIN", 20)},
    }

    hg, gc = lv("HG=F"), lv("GC=F")
    xly, xlp = lv("XLY"), lv("XLP")
    cycle = {
        "copper": {"last": hg, "chg_pct": pct("HG=F"), "ret_63d": ret("HG=F", 63)},
        "gold": {"last": gc, "chg_pct": pct("GC=F"), "ret_63d": ret("GC=F", 63)},
        "copper_gold_ratio": round(float(hg) / float(gc), 5) if (hg and gc) else None,
        "consumer_discretionary": {"last": xly, "chg_pct": pct("XLY"), "ret_63d": ret("XLY", 63)},
        "consumer_staples": {"last": xlp, "chg_pct": pct("XLP"), "ret_63d": ret("XLP", 63)},
        "xly_xlp_ratio": round(float(xly) / float(xlp), 5) if (xly and xlp) else None,
        "russell_2000": {"last": lv("IWM"), "chg_pct": pct("IWM"), "ret_63d": ret("IWM", 63)},
        "iwm_spy_rel_63d_pct": (round((ret("IWM", 63) or 0.0) - (ret("SPY", 63) or 0.0), 2)
                                if ret("IWM", 63) is not None and ret("SPY", 63) is not None else None),
    }

    snapshot = {
        "generated_at_utc": now_utc.isoformat(),
        "generated_at_kst": clock["now_kst"],
        "phase": clock["phase"],
        "clock": clock,
        "data_source": "Yahoo Finance (yfinance) — daily OHLCV + 5m intraday; FRED fredgraph.csv (공식 거시·신용 시계열)",
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
        "bonds": bonds,
        "fx_market": fx_market,
        "crypto_market": crypto_market,
        "cycle": cycle,
        "fred": {sid: {k: v for k, v in d.items() if k not in ("_series", "history_monthly")}
                 for sid, d in fred.items()},
        "intraday": intraday,
        "universe": EQUITY_UNIVERSE,
        "universe_extended": {
            "bonds": BOND_UNIVERSE, "fx": FX_UNIVERSE, "crypto": CRYPTO_UNIVERSE, "cycle": CYCLE_UNIVERSE,
        },
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
              f"SOXL {q('SOXL').get('last')} VIX {q('^VIX').get('last')} BTC {q('BTC-USD').get('last')} "
              f"10Y {y10} 10Y3M {bonds['curve']['10y3m_bp']}bp HY OAS {bonds['hy_oas_bp']}bp")
    return snapshot


def load_snapshot() -> Dict[str, Any]:
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return build_snapshot()


if __name__ == "__main__":
    build_snapshot(force_history=True)
