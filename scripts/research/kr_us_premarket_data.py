"""한국 증시 ↔ 미국 프리마켓/정규장 연구용 데이터 수집 (2026-11-18 study).

무엇을 만드나
-------------
* 미국 5종(NVDA·SOXL·TQQQ·SOXS·SQQQ) 60분봉(prepost=True, 730일) → 세션별 패널
  : 04:00 ET 프리장 시가, 06:00/08:00 ET 시가, 09:30 ET 정규장 시가, 15:30 ET 정규장 종가
* 한국 4종(^KS11·^KQ11·005930.KS·000660.KS) 일봉 5년 + 60분봉 730일(마지막 1시간 수익률)
* 원/달러(KRW=X) 일봉 5년
* 미국 5종 5분봉(prepost=True, 60일) → 프리장 미시구조(슬리피지 가정 근거)

정렬 규칙(미래정보 누수 없음)
------------------------------
미국 세션 날짜 D(ET). 한국 세션 D는 15:30 KST(=01:30 ET)에 종료되므로
미국 프리장 04:00 ET(=18:00 KST, EST) 시작 전에 완전히 확정된다. 신호는 D의 한국 세션,
대상은 D의 미국 세션 — 같은 달력 날짜로 조인한다(EDT에서도 09:30 ET = 22:30 KST로 날짜 불변).

산출물
------
data_cache/research/kr_us/panel_60m.csv      세션 단위 패널(신호 + 미국 가격 경로)
data_cache/research/kr_us/panel_daily_5y.csv 5년 일봉 교차확인용 패널
data_cache/research/kr_us/micro_5m.json       5분봉 미시구조 요약
data_cache/ 는 .gitignore 대상(재생성 가능 캐시)이다.
"""
from __future__ import annotations

import json
import os
import pickle
from typing import Dict, Optional

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(BASE_DIR, "data_cache", "research")
OUT_DIR = os.path.join(CACHE, "kr_us")
os.makedirs(OUT_DIR, exist_ok=True)

US_TICKERS = ["NVDA", "SOXL", "TQQQ", "SOXS", "SQQQ"]
UNLEV = ["SOXX", "SMH", "QQQ"]  # 레버리지 효과 검증용 1배 지수(실행성 비교)
KR_DAILY = ["^KS11", "^KQ11", "005930.KS", "000660.KS"]
KR_INTRADAY = ["^KS11", "005930.KS"]
FX = "KRW=X"


# --------------------------------------------------------------------- fetch
def fetch(sym: str, interval: str, period: str, prepost: bool = False) -> pd.DataFrame:
    path = os.path.join(CACHE, f"{sym.replace('^', 'IDX_')}_{interval}_{period}_pre{int(prepost)}.pkl")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                df = pickle.load(f)
            if df is not None and len(df):
                return df
        except Exception:
            pass
    import yfinance as yf
    df = yf.download(sym, period=period, interval=interval, prepost=prepost,
                     progress=False, auto_adjust=False, threads=False)
    if df is None or not len(df):
        return pd.DataFrame()
    if getattr(df.columns, "nlevels", 1) > 1:
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(how="all")
    with open(path, "wb") as f:
        pickle.dump(df, f)
    return df


def to_tz(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    d = df.copy()
    if getattr(d.index, "tz", None) is None:
        d.index = d.index.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
    else:
        d.index = d.index.tz_convert(tz)
    return d


# --------------------------------------------------------------- US sessions
def us_session_panel(ticker: str, period: str = "730d", interval: str = "60m") -> pd.DataFrame:
    """60분봉(프리장 포함) → 세션별 진입/청산 가격."""
    raw = fetch(ticker, interval, period, prepost=True)
    if not len(raw):
        return pd.DataFrame()
    df = to_tz(raw, "America/New_York")
    df = df[~df.index.duplicated(keep="first")].sort_index()
    rows = []
    for day, g in df.groupby(df.index.date):
        g = g.sort_index()
        mins = np.array([t.hour * 60 + t.minute for t in g.index.time])
        pre = g[mins < 9 * 60 + 30]
        reg = g[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]
        ah = g[(mins >= 16 * 60) & (mins < 20 * 60)]

        def _open_at(target_min: int) -> Optional[float]:
            """target 시각 봉의 시가. 없으면 target 이전 가장 늦은 봉의 종가."""
            exact = g[[m == target_min for m in mins]]
            if len(exact):
                return float(exact["Open"].iloc[0])
            before = g[mins < target_min]
            if len(before):
                return float(before["Close"].iloc[-1])
            return None

        if not len(reg):
            continue
        rows.append({
            "date": pd.Timestamp(day),
            "pm_open_0400": _open_at(4 * 60),
            "p_0600": _open_at(6 * 60),
            "p_0800": _open_at(8 * 60),
            "p_0900": _open_at(9 * 60),
            "pre_first": float(pre["Open"].iloc[0]) if len(pre) else None,
            "rth_open": float(reg["Open"].iloc[0]),
            "rth_close": float(reg["Close"].iloc[-1]),
            "rth_high": float(reg["High"].max()),
            "rth_low": float(reg["Low"].min()),
            "ah_close": float(ah["Close"].iloc[-1]) if len(ah) else None,
            "pre_volume": float(pre["Volume"].sum()) if len(pre) and "Volume" in pre else np.nan,
            "rth_volume": float(reg["Volume"].sum()) if len(reg) and "Volume" in reg else np.nan,
            "pre_bars": int(len(pre)),
        })
    out = pd.DataFrame(rows).set_index("date").sort_index()
    out["prev_rth_close"] = out["rth_close"].shift(1)
    out["gap"] = out["rth_open"] / out["prev_rth_close"] - 1.0
    out["rth_ret"] = out["rth_close"] / out["rth_open"] - 1.0
    # 프리장 내 이동: 04:00 ET 진입 → 09:30 ET 정규장 시가 청산
    out["pm_0400_0930"] = out["rth_open"] / out["pm_open_0400"] - 1.0
    out["pm_0600_0930"] = out["rth_open"] / out["p_0600"] - 1.0
    out["pm_0800_0930"] = out["rth_open"] / out["p_0800"] - 1.0
    # 프리장 내 이동만(04:00 → 09:00 봉 종가 = 09:30 직전)
    out["pm_0400_0900"] = out["p_0900"] / out["pm_open_0400"] - 1.0
    # 종일(프리장 시가 → 정규장 종가)
    out["day_0400_1600"] = out["rth_close"] / out["pm_open_0400"] - 1.0
    out["day_0400_0930_pm"] = out["rth_open"] / out["pm_open_0400"] - 1.0
    # 갭 구간 분해: 전일 종가 → 04:00 시가(한국장 직후 첫 프리장 가격)
    out["gap_prevclose_0400"] = out["pm_open_0400"] / out["prev_rth_close"] - 1.0
    out["ah_ret"] = out["ah_close"] / out["rth_close"] - 1.0
    out["ticker"] = ticker
    return out


def kr_session_panel(ticker: str, period: str = "730d") -> pd.DataFrame:
    """한국 60분봉 → 마지막 1시간(14:00~15:30) 수익률 + 마지막 30분 프록시."""
    raw = fetch(ticker, "60m", period, prepost=False)
    if not len(raw):
        return pd.DataFrame()
    df = to_tz(raw, "Asia/Seoul")
    df = df[~df.index.duplicated(keep="first")].sort_index()
    rows = []
    for day, g in df.groupby(df.index.date):
        g = g.sort_index()
        mins = np.array([t.hour * 60 + t.minute for t in g.index.time])
        last = g.iloc[-1]
        first = g.iloc[0]
        if last.name.hour * 60 + last.name.minute < 14 * 60:
            continue
        rows.append({
            "date": pd.Timestamp(day),
            "kr_open": float(first["Open"]),
            "kr_close": float(last["Close"]),
            "last_hour_ret": float(last["Close"] / last["Open"] - 1.0)
            if last.name.hour * 60 + last.name.minute == 14 * 60 else np.nan,
            "bars": int(len(g)),
        })
    out = pd.DataFrame(rows).set_index("date").sort_index()
    out["kr_intraday_ret"] = out["kr_close"] / out["kr_open"] - 1.0
    return out


# ------------------------------------------------------------------- build
def build() -> Dict:
    us: Dict[str, pd.DataFrame] = {}
    for t in US_TICKERS + UNLEV:
        us[t] = us_session_panel(t)
        print(f"[US 60m] {t}: {len(us[t])} sessions {us[t].index.min()} ~ {us[t].index.max()}")

    kr_daily: Dict[str, pd.DataFrame] = {}
    for t in KR_DAILY + [FX]:
        d = fetch(t, "1d", "5y")
        if not len(d):
            continue
        d = d[~d.index.duplicated(keep="first")].sort_index()
        d.index = pd.to_datetime(d.index).tz_localize(None) if getattr(d.index, "tz", None) is None \
            else pd.to_datetime(d.index).tz_convert("Asia/Seoul").tz_localize(None)
        d.index = d.index.normalize()
        kr_daily[t] = d
        print(f"[KR/FX daily] {t}: {len(d)} rows {d.index.min().date()} ~ {d.index.max().date()}")

    kr_intra: Dict[str, pd.DataFrame] = {}
    for t in KR_INTRADAY:
        kr_intra[t] = kr_session_panel(t)
        print(f"[KR 60m] {t}: {len(kr_intra[t])} sessions")

    # ---- 신호 프레임: 한국 세션 D (15:30 KST 확정)
    sig = pd.DataFrame(index=sorted(set().union(*[set(d.index) for d in kr_daily.values()])))
    for t, label in [("^KS11", "kospi"), ("^KQ11", "kosdaq"),
                     ("005930.KS", "samsung"), ("000660.KS", "hynix")]:
        if t in kr_daily:
            c = kr_daily[t]["Close"]
            sig[f"{label}_ret"] = c / c.shift(1) - 1.0
    sig["semis_ret"] = sig[["samsung_ret", "hynix_ret"]].mean(axis=1)
    sig["semis_excess"] = sig["semis_ret"] - sig["kospi_ret"]
    if FX in kr_daily:
        fx = kr_daily[FX]["Close"]
        sig["usdkrw_ret"] = fx / fx.shift(1) - 1.0
    if "^KS11" in kr_intra:
        sig["kospi_last_hour"] = kr_intra["^KS11"]["last_hour_ret"]
    if "005930.KS" in kr_intra:
        sig["samsung_last_hour"] = kr_intra["005930.KS"]["last_hour_ret"]

    # ---- 미국 세션 D 패널 (프리장 경로 + 정규장)
    panels = []
    for t, df in us.items():
        if not len(df):
            continue
        cols = ["pm_open_0400", "p_0600", "p_0800", "rth_open", "rth_close", "ah_close",
                "prev_rth_close", "gap", "rth_ret", "pm_0400_0930", "pm_0600_0930",
                "pm_0800_0930", "pm_0400_0900", "day_0400_1600", "gap_prevclose_0400",
                "ah_ret", "pre_volume", "rth_volume", "pre_bars"]
        p = df[cols].add_prefix(f"{t}_").rename(columns={f"{t}_date": "date"})
        panels.append(p)
    wide = pd.concat(panels, axis=1)

    # ---- 전일 미국 세션 정보(한국장이 반응한 그 밤) — D-1 세션
    prev = pd.DataFrame(index=wide.index)
    for t in US_TICKERS:
        if f"{t}_day_0400_1600" in wide:
            prev[f"us_prev_{t}_day"] = wide[f"{t}_day_0400_1600"].shift(1)
            prev[f"us_prev_{t}_ah"] = wide[f"{t}_ah_ret"].shift(1)
            prev[f"us_prev_{t}_rth"] = wide[f"{t}_rth_ret"].shift(1)

    panel = sig.join(wide, how="inner").join(prev, how="inner")
    panel = panel.dropna(subset=["kospi_ret", "semis_ret", "NVDA_rth_open", "NVDA_rth_close"])
    panel.index.name = "date"

    # ---- 5년 일봉 교차확인용(프리장 없음): 한국 D vs 미국 D 갭/장중
    daily = pd.DataFrame(index=sig.index)
    daily = daily.join(sig[["kospi_ret", "kosdaq_ret", "semis_ret", "semis_excess", "usdkrw_ret"]])
    for t in US_TICKERS:
        d = fetch(t, "1d", "5y")
        if not len(d):
            continue
        d = d[~d.index.duplicated(keep="first")].sort_index()
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        c, o = d["Close"], d["Open"]
        daily[f"{t}_daily_ret"] = c / c.shift(1) - 1.0
        daily[f"{t}_gap"] = o / c.shift(1) - 1.0
        daily[f"{t}_o2c"] = c / o - 1.0
        daily[f"{t}_prev_ret"] = daily[f"{t}_daily_ret"].shift(1)
    daily = daily.dropna(subset=["kospi_ret", "NVDA_gap"])

    panel.to_csv(os.path.join(OUT_DIR, "panel_60m.csv"))
    daily.to_csv(os.path.join(OUT_DIR, "panel_daily_5y.csv"))

    micro = micro_structure()
    with open(os.path.join(OUT_DIR, "micro_5m.json"), "w", encoding="utf-8") as f:
        json.dump(micro, f, ensure_ascii=False, indent=2)

    summary = {
        "as_of": "2026-11-18",
        "panel_60m_sessions": int(len(panel)),
        "panel_60m_range": [str(panel.index.min().date()), str(panel.index.max().date())],
        "panel_daily_sessions": int(len(daily)),
        "panel_daily_range": [str(daily.index.min().date()), str(daily.index.max().date())],
        "per_ticker_sessions": {t: int(us[t].shape[0]) for t in US_TICKERS + UNLEV},
        "micro": micro,
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:1200])
    return {"panel": panel, "daily": daily, "micro": micro, "summary": summary}


def micro_structure() -> Dict:
    """5분봉(프리장 포함, 최근 60일) 미시구조: 프리장 vs 정규장 봉 변동·거래량."""
    out = {}
    for t in US_TICKERS:
        raw = fetch(t, "5m", "60d", prepost=True)
        if not len(raw):
            continue
        df = to_tz(raw, "America/New_York")
        df = df[~df.index.duplicated(keep="first")].sort_index()
        mins = np.array([x.hour * 60 + x.minute for x in df.index.time])
        pre = df[mins < 9 * 60 + 30]
        reg = df[(mins >= 9 * 60 + 30) & (mins < 16 * 60)]
        absr_pre = (pre["Close"] / pre["Open"] - 1).abs()
        absr_reg = (reg["Close"] / reg["Open"] - 1).abs()
        vol_pre = pre["Volume"].replace(0, np.nan)
        vol_reg = reg["Volume"].replace(0, np.nan)
        out[t] = {
            "days": int(df.index.normalize().nunique()),
            "pre_bars_per_day": round(len(pre) / max(df.index.normalize().nunique(), 1), 1),
            "pre_median_abs_5m_pct": round(float(absr_pre.median()) * 100, 4),
            "reg_median_abs_5m_pct": round(float(absr_reg.median()) * 100, 4),
            "pre_median_bar_volume": round(float(vol_pre.median())) if vol_pre.notna().any() else None,
            "reg_median_bar_volume": round(float(vol_reg.median())) if vol_reg.notna().any() else None,
            "pre_close_price_median": round(float(pre["Close"].median()), 2) if len(pre) else None,
        }
        print(f"[micro] {t}: {out[t]}")
    return out


if __name__ == "__main__":
    build()
