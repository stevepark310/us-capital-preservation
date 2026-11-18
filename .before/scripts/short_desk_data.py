#!/usr/bin/env python3
"""숏/인버스 데스크 데이터 수집·캐시 (yfinance).

60일 5분봉, 730일 60분봉, 2년 일봉을 한 번 받아 data_cache/ 에 pickle로 남긴다.
다른 백테스트 스크립트는 이 캐시만 읽는다(재다운로드 금지).
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime
from typing import Dict, List

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data_cache")
DAILY = os.path.join(CACHE, "short_desk_daily.pkl")
HOUR = os.path.join(CACHE, "short_desk_60m.pkl")
MIN5 = os.path.join(CACHE, "short_desk_5m.pkl")

INVERSE = ["SOXS", "SQQQ", "SPXS", "SPXU", "PSQ", "SH", "NVDS", "TZA", "FNGD", "TECS", "WEBS", "YANG", "LABD"]
LONG = ["SOXL", "TQQQ", "NVDA", "QQQ", "SPY", "SOXX", "SGOV"]
IDX = ["^VIX", "^VXN"]

DAILY_UNIVERSE = INVERSE + LONG + IDX
HOUR_UNIVERSE = ["SOXS", "SQQQ", "SPXS", "PSQ", "SH", "NVDS", "SOXL", "TQQQ", "QQQ", "SPY", "NVDA", "^VIX"]
MIN5_UNIVERSE = ["SOXS", "SQQQ", "SPXS", "PSQ", "SH", "NVDS", "SOXL", "TQQQ", "QQQ", "SPY", "NVDA"]


def _save(path: str, frames: Dict[str, pd.DataFrame]) -> None:
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"fetched_at": datetime.now().isoformat(), "frames": frames}, f)


def _load(path: str) -> Dict[str, pd.DataFrame]:
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return pickle.load(f)["frames"]


def fetch_all(force: bool = False) -> None:
    import yfinance as yf

    jobs = [(DAILY, DAILY_UNIVERSE, "2y", "1d"), (HOUR, HOUR_UNIVERSE, "730d", "60m"),
            (MIN5, MIN5_UNIVERSE, "60d", "5m")]
    for path, syms, period, interval in jobs:
        if os.path.exists(path) and not force:
            print(f"skip {os.path.basename(path)} (cached)")
            continue
        frames: Dict[str, pd.DataFrame] = {}
        df = yf.download(syms, period=period, interval=interval, group_by="ticker",
                         progress=False, auto_adjust=False, threads=True)
        for s in syms:
            try:
                sub = df[s].dropna(how="all")
                if len(sub):
                    frames[s] = sub
            except Exception as e:  # noqa: BLE001
                print(f"  {s} 실패: {str(e)[:60]}")
        _save(path, frames)
        print(f"saved {os.path.basename(path)}: {sorted(frames)}")


def load(interval: str) -> Dict[str, pd.DataFrame]:
    return {"1d": _load(DAILY), "60m": _load(HOUR), "5m": _load(MIN5)}[interval]


if __name__ == "__main__":
    import sys
    fetch_all(force="--force" in sys.argv)
