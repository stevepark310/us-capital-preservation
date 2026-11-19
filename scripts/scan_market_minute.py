#!/usr/bin/env python3
"""Scan every configured CUBE equity using one-minute bars during the regular session."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import market_clock, market_snapshot


def scan():
    symbols = market_snapshot.EQUITY_UNIVERSE
    phase = market_clock.resolve().get("phase")
    if phase != "REGULAR":
        return {"phase": phase, "status": "SKIPPED_OFF_HOURS"}
    raw = market_snapshot._intraday_last(symbols, interval="1m")
    now = datetime.now(timezone.utc)
    quotes = {}
    for symbol, quote in raw.items():
        bar_time = pd.to_datetime(quote["bar_time"], utc=True, errors="coerce")
        age = (now - bar_time).total_seconds()
        price = quote["price"]
        if math.isfinite(age) and 0 <= age <= 120 and math.isfinite(price) and price > 0:
            quotes[symbol] = quote
    missing = sorted(set(symbols) - quotes.keys())
    return {
        "phase": phase,
        "status": "INCOMPLETE" if missing else "OK",
        "checked_at_utc": now.isoformat(),
        "expected_symbols": symbols,
        "missing_or_stale": missing,
        "quotes": quotes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    args = parser.parse_args()
    while True:
        minute = int(time.time() // 60)
        result = scan()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        if args.once:
            return int(result["status"] == "INCOMPLETE")
        now = time.time()
        # Skip missed slots and start the next scan on a minute boundary.
        next_minute = max(minute, int(now // 60)) + 1
        time.sleep(max(0, next_minute * 60 - time.time()))


if __name__ == "__main__":
    sys.exit(main())
