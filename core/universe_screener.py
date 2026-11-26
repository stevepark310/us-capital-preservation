"""
동적 유니버스 스크리너 — 코어 4종목 고정에서 '코어 + 실측 스크리닝'으로 확장.

동훈님 지시(2026-11-26 20:58): "ETF·나스닥100 대형주까지 발굴 + 게이트 통과 전략 전부 자동 반영".

왜 필요한가
----------
지금까지 무인 집행의 진입 탐색 대상은 `SOXL·TQQQ`(롱) / `SOXS·SQQQ`(헤지) 네 종목이었다.
시장 국면이 한쪽으로 쏠리면(예: 반도체 단독 조정) 네 종목만으로는 진입 자리가 없고, 반대로
다른 섹터에 같은 과매도 신호가 있어도 잡지 못한다. 이 모듈은 **후보 풀 전체를 일봉으로 스캔해
코어와 함께 볼 종목을 매시간 다시 고른다**.

지키는 규칙
-----------
1. **코어 4종목은 언제나 포함된다** — 스크리너는 '추가'만 한다(교체·제외 없음).
2. **선정은 실측 지표로만** — 거래대금(20일 달러 거래대금)·가격·ATR%·RSI2·%B·RVOL·모멘텀을
   계산해 점수화한다. 숫자가 없으면(캐시 없음) 아무것도 선정하지 않고 코어만 남긴다(fail-closed).
3. **계좌 규모를 존중한다** — 1회 예산 상한(~$220)보다 비싼 1주 가격은 `affordable=false`로
   표시하고 선정에서 뺀다(정수주 0주가 되는 신호를 만들지 않는다).
4. **금지 종목은 절대 넣지 않는다** — `execution_policy.BANNED_TICKERS`(ENVX·IBRX·SOUN).

출력
----
- `data/dynamic_universe.json` — 선정 결과(점수·사유·원지표)·허용 목록의 정본.
- `data_cache/universe_daily.pkl` — 후보 풀 일봉 캐시(네트워크 실패 시 폴백).
- `config/swing_approval.json → autonomous_execution.universe` — 실제 주문 허용 목록
  (`sync_policy_universe`, 코어 + 선정 종목). 하드락(`core/buildup`)이 이 파일을 검사한다.

실행: python3 -m core.universe_screener [--force] [--json]
"""

from __future__ import annotations

import json
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")
CACHE_PATH = os.path.join(CACHE_DIR, "universe_daily.pkl")
OUT_PATH = os.path.join(DATA, "dynamic_universe.json")
POLICY_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")

# ---------------------------------------------------------------- universes
CORE_LONG = ["SOXL", "TQQQ"]
CORE_HEDGE = ["SOXS", "SQQQ"]
CORE_UNIVERSE = CORE_LONG + CORE_HEDGE
BANNED = {"ENVX", "IBRX", "SOUN"}

# 주요 미국 섹터·지수·레버리지·인버스·채권 ETF
ETF_POOL = [
    # 지수·섹터
    "SPY", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLK", "XLF", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # 채권·원자재
    "TLT", "IEF", "SHY", "HYG", "LQD", "GLD", "SLV",
    # 레버리지 롱
    "UPRO", "SPXL", "TNA", "QLD", "SSO", "UDOW", "TQQQ", "SOXL",
    # 인버스·헤지
    "SOXS", "SQQQ", "SPXU", "SPXS", "TZA", "PSQ", "SH", "NVDS", "FNGD",
]

# 나스닥100 주요 유동성 대형주(1주 단가가 예산보다 비싼 종목은 affordable=false로 표시)
NASDAQ100_POOL = [
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "AMD",
    "NFLX", "COST", "PLTR", "MU", "QCOM", "ARM", "ASML", "LRCX", "AMAT", "PANW",
    "CRWD", "ADBE", "CSCO", "INTC", "INTU", "ISRG", "BKNG", "TXN", "AMGN", "HON",
    "PDD", "MELI", "MAR", "ORLY", "CEG", "APP", "DASH", "ABNB",
]


def _dedupe(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        s = str(s).upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


CANDIDATE_POOL = _dedupe(ETF_POOL + NASDAQ100_POOL + CORE_UNIVERSE)
INVERSE = {"SOXS", "SQQQ", "SPXU", "SPXS", "TZA", "PSQ", "SH", "NVDS", "FNGD"}
LEVERAGED = {"UPRO", "SPXL", "TNA", "QLD", "SSO", "UDOW", "TQQQ", "SOXL",
             "SOXS", "SQQQ", "SPXU", "SPXS", "TZA", "NVDS", "FNGD"}

SCREEN: Dict[str, Any] = {
    "min_dollar_volume_usd": 20_000_000.0,   # 20일 평균 달러 거래대금 하한(유동성)
    "min_price_usd": 3.0,
    "max_atr_pct": 25.0,                     # 3배 레버리지 ETF까지 허용(그 이상은 노이즈)
    "min_atr_pct": 0.8,
    "affordable_max_usd": 220.0,             # 1회 예산 상한(guardrails entry_budget_cap_usd)
    "top_long": 6,
    "top_hedge": 2,
    "intraday_dynamic_max": 3,               # 장중 5분봉 스캔에 추가로 넣는 동적 종목 수(지연 억제)
    "cache_max_age_h": 6.0,
    "rescan_after_h": 1.0,
}


def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _now_kst_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- data layer
def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_PATH, "rb") as f:
            obj = pickle.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _write_cache(frames: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(CACHE_PATH, "wb") as f:
            pickle.dump({"frames": frames,
                     "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}, f)
    except Exception:
        pass


def _cache_age_h(cache: Dict[str, Any]) -> Optional[float]:
    ts = cache.get("fetched_at")
    if not ts:
        return None
    try:
        return round((datetime.now(timezone.utc).replace(tzinfo=None)
                      - datetime.fromisoformat(str(ts))).total_seconds() / 3600.0, 2)
    except Exception:
        return None


def load_frames(pool: Optional[List[str]] = None, force: bool = False,
                max_age_h: Optional[float] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """후보 풀 일봉. {ticker: DataFrame} 과 출처 메타를 돌려준다.

    순서: ① 디스크 캐시(신선하면 즉시) ② yfinance ③ 실패 시 기존 캐시 → 시장 스냅샷
    캐시 → 백테스트 일봉 캐시(코어·기존 유니버스만). 네트워크가 막혀도 코어 신호는 계속
    계산되도록 하는 폴백이다.
    """
    pool = _dedupe(pool or CANDIDATE_POOL)
    cache = _load_cache()
    frames: Dict[str, Any] = dict(cache.get("frames") or {})
    age = _cache_age_h(cache)
    max_age = float(max_age_h if max_age_h is not None else SCREEN["cache_max_age_h"])
    have = {s for s in pool if frames.get(s) is not None and len(frames[s]) >= 40}
    if not force and have == set(pool) and age is not None and age <= max_age:
        return frames, {"source": "cache", "cache_age_h": age, "network": False}

    fetched, err = {}, None
    try:
        from core import market_snapshot
        import yfinance as yf  # noqa: F401  (없으면 아래에서 예외로 떨어진다)
        dl = yf.download(pool, period="1y", interval="1d", progress=False,
                         auto_adjust=False, group_by="column", threads=True)
        fetched = market_snapshot._normalize_download(dl, pool)
    except Exception as e:                     # 네트워크·패키지 문제 — 캐시로 계속
        err = f"{type(e).__name__}: {e}"

    if fetched:
        frames.update({k: v for k, v in fetched.items() if v is not None and len(v) >= 40})
        _write_cache(frames)
        return frames, {"source": "yfinance", "cache_age_h": 0.0, "network": True,
                        "fetched": len(fetched), "error": err}

    # 폴백 — 기존 캐시·다른 캐시
    fallbacks = []
    try:
        from core import market_snapshot
        snap_cache = market_snapshot._read_pickle(market_snapshot.HISTORY_CACHE) or {}
        for s, df in (snap_cache.get("frames") or {}).items():
            if s in pool and frames.get(s) is None:
                frames[s] = df
        fallbacks.append("market_snapshot.history_1y")
    except Exception:
        pass
    try:
        from core import desk_lab
        for s, df in (desk_lab.load_daily() or {}).items():
            if s in pool and frames.get(s) is None:
                frames[s] = df
        fallbacks.append("backtest_daily")
    except Exception:
        pass
    return frames, {"source": "cache_fallback", "cache_age_h": age, "network": False,
                    "fallbacks": fallbacks, "error": err}


# ------------------------------------------------------------------ metrics
def metrics_of(df: Any) -> Optional[Dict[str, Any]]:
    """일봉 DataFrame → 스크리닝 지표(마지막 완료 세션 기준)."""
    try:
        import pandas as pd
        from core import desk_lab
        if df is None or len(df) < 40:
            return None
        d = desk_lab.with_indicators(df)
        row = d.iloc[-1]
        close = float(row["Close"])
        if not (close > 0):
            return None
        vol = d["Volume"].astype(float)
        px = d["Close"].astype(float)
        dv20 = float((px * vol).tail(20).mean())
        atr_pct = float(row.get("ATR_PCT") or 0.0)
        ema20 = float(row.get("EMA20") or close)
        mom20 = (close / float(px.iloc[-21]) - 1.0) * 100.0 if len(px) > 21 and float(px.iloc[-21]) > 0 else 0.0
        mom5 = (close / float(px.iloc[-6]) - 1.0) * 100.0 if len(px) > 6 and float(px.iloc[-6]) > 0 else 0.0
        def _num(key: str, default: float) -> float:
            v = row.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return float(default)
            return float(default) if v != v else v          # NaN → 기본값 (0도 유효한 값이다)

        return {
            "close": round(close, 2),
            "dollar_volume_20d": int(dv20),
            "atr_pct": round(atr_pct, 2),
            "rsi2": round(_num("RSI2", 50.0), 1),
            "pct_b": round(_num("PCT_B", 0.5), 3),
            "rvol": round(_num("RVOL", 1.0), 2),
            "mom_20d_pct": round(mom20, 2),
            "mom_5d_pct": round(mom5, 2),
            "above_ema20": bool(close > ema20),
            "as_of": str(d.index[-1].date()) if hasattr(d.index[-1], "date") else str(d.index[-1]),
        }
    except Exception:
        return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def score_long(m: Dict[str, Any]) -> float:
    """눌림(과매도) 진입 후보 점수 0~100 — 깊은 과매도 + 거래량 + 유동성.

    DIP_SWING 규격(RSI2 ≤ 20 · %B ≤ 0.15 · RVOL ≥ 1.0)에 가까울수록 높다.
    유동성은 '체결 가능성'이라 점수에 가산하되, 과열(모멘텀 +)은 가산하지 않는다.
    """
    rsi2 = float(m.get("rsi2") or 50.0)
    pct_b = float(m.get("pct_b") or 0.5)
    rvol = float(m.get("rvol") or 1.0)
    dv = float(m.get("dollar_volume_20d") or 0.0)
    atr = float(m.get("atr_pct") or 0.0)
    depth = _clamp01((20.0 - rsi2) / 20.0) * 45.0          # 과매도 깊이
    band = _clamp01((0.15 - pct_b) / 0.15) * 30.0          # 밴드 하단 이탈
    vol = _clamp01((rvol - 1.0) / 1.5) * 15.0              # 거래량 확인
    liq = _clamp01(dv / 500_000_000.0) * 10.0              # 유동성
    atr_pen = max(0.0, (atr - 15.0)) * 1.0                 # 15% 초과 변동성은 감점
    return round(max(0.0, depth + band + vol + liq - atr_pen), 1)


def score_hedge(m: Dict[str, Any]) -> float:
    """헤지(인버스) 후보 점수 — 하락 추세가 뚜렷하고 유동적인 인버스 ETF."""
    rvol = float(m.get("rvol") or 1.0)
    dv = float(m.get("dollar_volume_20d") or 0.0)
    mom20 = float(m.get("mom_20d_pct") or 0.0)
    trend = _clamp01(mom20 / 10.0) * 50.0                  # 20일 상승(인버스=시장 하락)
    vol = _clamp01((rvol - 1.0) / 1.5) * 20.0
    liq = _clamp01(dv / 300_000_000.0) * 30.0
    return round(max(0.0, trend + vol + liq), 1)


# ------------------------------------------------------------------- screen
def screen(frames: Optional[Dict[str, Any]] = None, pool: Optional[List[str]] = None,
           save_doc: bool = True) -> Dict[str, Any]:
    """후보 풀을 스캔해 롱·헤지 후보를 고른다(코어는 항상 포함)."""
    pool = _dedupe(pool or CANDIDATE_POOL)
    meta: Dict[str, Any] = {"source": "injected", "network": False}
    if frames is None:
        frames, meta = load_frames(pool)
    scored: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for sym in pool:
        if sym in BANNED:
            skipped.append({"ticker": sym, "reason": "금지 종목"})
            continue
        df = (frames or {}).get(sym)
        m = metrics_of(df) if df is not None else None
        if not m:
            skipped.append({"ticker": sym, "reason": "일봉 부족/없음"})
            continue
        if m["close"] < float(SCREEN["min_price_usd"]):
            skipped.append({"ticker": sym, "reason": f"가격 ${m['close']} < ${SCREEN['min_price_usd']}"})
            continue
        if m["dollar_volume_20d"] < float(SCREEN["min_dollar_volume_usd"]):
            skipped.append({"ticker": sym, "reason": f"거래대금 ${m['dollar_volume_20d']:,} < 하한"})
            continue
        if not (float(SCREEN["min_atr_pct"]) <= m["atr_pct"] <= float(SCREEN["max_atr_pct"])):
            skipped.append({"ticker": sym, "reason": f"ATR {m['atr_pct']}% 범위 밖"})
            continue
        side = "HEDGE" if sym in INVERSE else "LONG"
        rec = {"ticker": sym, "side": side, **m}
        rec["affordable"] = bool(rec["close"] <= float(SCREEN["affordable_max_usd"]))
        rec["leveraged"] = sym in LEVERAGED
        rec["score"] = score_hedge(rec) if side == "HEDGE" else score_long(rec)
        if not rec["affordable"]:
            # 1주 단가가 1회 예산을 넘으면 정수주 0주가 되어 신호가 무의미하다 — 선정에서 빼고 이유를 남긴다.
            skipped.append({"ticker": sym,
                            "reason": f"1주 ${rec['close']:,.0f} > 예산 ${SCREEN['affordable_max_usd']:.0f}"})
            continue
        scored.append(rec)

    longs = sorted([r for r in scored if r["side"] == "LONG" and r["affordable"]],
                   key=lambda r: r["score"], reverse=True)
    hedges = sorted([r for r in scored if r["side"] == "HEDGE" and r["affordable"]],
                    key=lambda r: r["score"], reverse=True)
    selected = longs[: int(SCREEN["top_long"])] + hedges[: int(SCREEN["top_hedge"])]
    core = [r for r in scored if r["ticker"] in CORE_UNIVERSE]
    as_of = max([r.get("as_of") or "" for r in scored] or [""]) or None
    doc = {
        "version": "1.0",
        "generated_at_kst": _now_kst(),
        "as_of": as_of,
        "source": meta.get("source"),
        "network": bool(meta.get("network")),
        "cache_age_h": meta.get("cache_age_h"),
        "error": meta.get("error"),
        "core": CORE_UNIVERSE,
        "counts": {"pool": len(pool), "scored": len(scored), "skipped": len(skipped),
                   "selected": len(selected)},
        "rule": ("코어 4종목 고정 포함 + 아래 `selected`(거래대금·ATR·과매도·거래량 점수 상위)만 추가 허용. "
                 f"1회 예산 ${SCREEN['affordable_max_usd']:.0f}보다 비싼 1주 가격은 제외(affordable=false)."),
        "thresholds": {k: SCREEN[k] for k in ("min_dollar_volume_usd", "min_price_usd", "max_atr_pct",
                                              "min_atr_pct", "affordable_max_usd", "top_long", "top_hedge")},
        "selected": [{k: r[k] for k in ("ticker", "side", "score", "close", "atr_pct", "rsi2",
                                        "pct_b", "rvol", "mom_20d_pct", "dollar_volume_20d",
                                        "affordable", "leveraged", "as_of")} for r in selected],
        "core_detail": [{k: r[k] for k in ("ticker", "side", "score", "close", "atr_pct", "rsi2",
                                           "pct_b", "rvol", "affordable")} for r in core],
        "selected_long": [r["ticker"] for r in selected if r["side"] == "LONG"],
        "selected_hedge": [r["ticker"] for r in selected if r["side"] == "HEDGE"],
        "skipped": skipped[:40],
    }
    doc["allowed"] = _dedupe(CORE_UNIVERSE + [r["ticker"] for r in selected])
    if save_doc:
        _save(OUT_PATH, doc)
    return doc


def refresh_if_stale(max_age_h: Optional[float] = None, force: bool = False) -> Tuple[Dict[str, Any], bool]:
    """스캔 결과가 `max_age_h`보다 오래됐으면 다시 스캔한다. (문서, 재스캔여부)"""
    limit = float(max_age_h if max_age_h is not None else SCREEN["rescan_after_h"])
    doc = load()
    if not force and doc.get("generated_at_kst"):
        try:
            t = datetime.strptime(str(doc["generated_at_kst"])[:19], "%Y-%m-%d %H:%M:%S")
            if (_now_kst_dt() - t) < timedelta(hours=limit) and doc.get("allowed"):
                return doc, False
        except Exception:
            pass
    fresh = screen()
    return fresh, True


# ------------------------------------------------------------------ readers
def load() -> Dict[str, Any]:
    return _load(OUT_PATH, {}) or {}


def allowed_symbols() -> List[str]:
    """하드락이 인정하는 추가 허용 목록(코어 포함). 파일이 없으면 코어만."""
    doc = load()
    return _dedupe(CORE_UNIVERSE + list(doc.get("allowed") or []))


def selected_symbols(max_age_h: float = 6.0) -> List[str]:
    """선정 종목만(코어 제외). 오래된 결과는 쓰지 않는다."""
    doc = load()
    ts = doc.get("generated_at_kst")
    if not ts:
        return []
    try:
        t = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        if (_now_kst_dt() - t) > timedelta(hours=float(max_age_h)):
            return []
    except Exception:
        return []
    return _dedupe([s for s in (doc.get("allowed") or []) if s not in CORE_UNIVERSE])


def long_universe(max_age_h: float = 6.0) -> List[str]:
    doc = load()
    fresh = selected_symbols(max_age_h)
    picks = [s for s in (doc.get("selected_long") or []) if s in fresh]
    return _dedupe(CORE_LONG + picks)


def hedge_universe(max_age_h: float = 6.0) -> List[str]:
    doc = load()
    fresh = selected_symbols(max_age_h)
    picks = [s for s in (doc.get("selected_hedge") or []) if s in fresh]
    return _dedupe(CORE_HEDGE + picks)


def effective_universe(core: Optional[List[str]] = None, max_age_h: float = 6.0) -> List[str]:
    """코어 + 선정 종목(진입 신호 탐색 대상)."""
    base = _dedupe(core or CORE_UNIVERSE)
    return _dedupe(base + [s for s in selected_symbols(max_age_h) if s not in base])


def cached_frames(tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    """캐시에 있는 후보 일봉(무인 집행이 일봉을 아직 못 받은 종목 보충용)."""
    frames = (_load_cache().get("frames") or {})
    if not tickers:
        return frames
    want = {str(t).upper() for t in tickers}
    return {k: v for k, v in frames.items() if str(k).upper() in want}


def summary(max_age_h: float = 6.0) -> Dict[str, Any]:
    doc = load()
    age_h = None
    if doc.get("generated_at_kst"):
        try:
            t = datetime.strptime(str(doc["generated_at_kst"])[:19], "%Y-%m-%d %H:%M:%S")
            age_h = round((_now_kst_dt() - t).total_seconds() / 3600.0, 2)
        except Exception:
            age_h = None
    return {"generated_at_kst": doc.get("generated_at_kst"), "age_h": age_h,
            "as_of": doc.get("as_of"), "source": doc.get("source"),
            "core": CORE_UNIVERSE,
            "selected_long": doc.get("selected_long") or [],
            "selected_hedge": doc.get("selected_hedge") or [],
            "selected": doc.get("selected") or [],
            "counts": doc.get("counts") or {},
            "stale": bool(age_h is None or age_h > float(max_age_h)),
            "path": OUT_PATH}


# --------------------------------------------------------------- policy sync
def sync_policy_universe(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`config/swing_approval.json`의 주문 허용 유니버스 = 코어 + 선정 종목.

    무인 집행(`execution_policy.allowed_ticker`)은 이 파일의 유니버스만 본다. 스크리너가
    선정만 하고 파일을 갱신하지 않으면 신호가 나와도 '유니버스 밖'으로 버려지므로,
    선정 결과를 그대로 집행 허용 목록에 반영한다(하드락이 사후 검사).
    """
    doc = doc or load()
    allowed = _dedupe(CORE_UNIVERSE + [s for s in (doc.get("allowed") or [])])
    cfg = _load(POLICY_PATH, {}) or {}
    ae = cfg.setdefault("autonomous_execution", {})
    before = list(ae.get("universe") or [])
    ae["universe"] = allowed
    ae["dynamic_universe"] = {
        "source": "data/dynamic_universe.json",
        "synced_at_kst": _now_kst(),
        "rule": doc.get("rule") or "코어 4종목 + 스크리닝 선정 종목",
        "core": CORE_UNIVERSE,
        "selected_long": doc.get("selected_long") or [],
        "selected_hedge": doc.get("selected_hedge") or [],
        "scanned_at_kst": doc.get("generated_at_kst"),
        "as_of": doc.get("as_of"),
    }
    _save(POLICY_PATH, cfg)
    return {"universe": allowed, "before": before, "changed": before != allowed,
            "added": [s for s in allowed if s not in before],
            "removed": [s for s in before if s not in allowed]}


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else [])
    force = "--force" in args
    doc, refreshed = refresh_if_stale(force=force)
    if refreshed:
        sync = sync_policy_universe(doc)
    else:
        sync = {"universe": _load(POLICY_PATH, {}).get("autonomous_execution", {}).get("universe"),
                "changed": False}
    print(f"[universe] {doc.get('generated_at_kst')} as_of {doc.get('as_of')} "
          f"source {doc.get('source')} · 스캔 {doc.get('counts', {}).get('scored')}종목 "
          f"· 롱 {doc.get('selected_long')} · 헤지 {doc.get('selected_hedge')} "
          f"· 재스캔 {'O' if refreshed else 'X'}")
    if sync.get("changed"):
        print(f"  정책 유니버스 갱신: {sync['before']} → {sync['universe']}")
    if "--json" in args:
        print(json.dumps({"doc": doc, "sync": sync}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
