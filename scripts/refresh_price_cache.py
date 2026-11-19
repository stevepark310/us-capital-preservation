#!/usr/bin/env python3
"""
가격 캐시 갱신 — 검증 데스크(core.desk_lab)가 쓰는 2년 일봉 캐시를 새로 받는다.

검증 데스크는 네트워크 없이 수 초 안에 돌아야 하므로 `data_cache/backtest_daily.pkl`을
쓴다. 그 캐시가 오래되면 '같은 데이터를 다시 재는' 일만 하게 되므로, 클라우드 펄스가
하루 한 번(캐시가 20시간보다 오래됐으면) 이 스크립트로 갱신한다.

실패하면 기존 캐시를 그대로 둔다(검증은 계속 가능, 데이터 기준일만 그대로 보고).
    python3 scripts/refresh_price_cache.py [--force] [--period 2y]
"""

from __future__ import annotations

import json
import os
import pickle
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import desk_lab  # noqa: E402

CACHE = os.path.join(BASE_DIR, "data_cache", "backtest_daily.pkl")
MAX_AGE_H = 20.0


def cached_age_h() -> float:
    try:
        with open(CACHE, "rb") as f:
            obj = pickle.load(f)
        ts = datetime.fromisoformat(obj["fetched_at"])
        return (datetime.now() - ts).total_seconds() / 3600.0
    except Exception:
        return 1e9


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    force = "--force" in args
    period = "2y"
    if "--period" in args:
        try:
            period = args[args.index("--period") + 1]
        except Exception:
            period = "2y"
    age = cached_age_h()
    if not force and age < MAX_AGE_H:
        frames = desk_lab.load_daily()
        print(f"[price-cache] 최신 (age {age:.1f}h < {MAX_AGE_H}h) · 기준일 {desk_lab.data_as_of(frames)} "
              f"· 지문 {desk_lab.data_fingerprint(frames)}")
        return 0
    t0 = time.time()
    frames = desk_lab.load_daily(force=True, period=period)
    if not frames:
        print(f"[price-cache] 갱신 실패 — 기존 캐시 유지(age {age:.1f}h)")
        return 1
    print(f"[price-cache] 갱신 {time.time() - t0:.1f}s · 종목 {len(frames)} · "
          f"기준일 {desk_lab.data_as_of(frames)} · 지문 {desk_lab.data_fingerprint(frames)}")
    if "--json" in args:
        print(json.dumps({k: str(max(v.index)) for k, v in frames.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
