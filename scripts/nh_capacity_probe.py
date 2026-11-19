#!/usr/bin/env python3
"""NH live *read-only* capacity probe; never invokes /order/ endpoints."""
from __future__ import annotations

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from execution.nh_trader import NHBroker


def main() -> int:
    required = ("NH_APP_KEY", "NH_APP_SECRET", "NH_ACT_NO")
    if any(not os.getenv(k) for k in required):
        print("PROBE FAIL: NH secret(s) missing")
        return 1
    broker = NHBroker(dry_run=False)
    acct = broker.get_portfolio_state(include_raw=False)
    if str(acct.get("rsp_cd")) != "00166" or not isinstance(acct.get("positions"), dict):
        print("PROBE FAIL: live USD account query unsuccessful")
        return 1
    # Sample prices here are for a quote-independent API schema probe only.
    # No order is constructed or transmitted.
    cases = (("SOXS", "BUY", 55.0), ("SQQQ", "BUY", 45.0),
             ("NVDA", "SELL", 150.0))
    success = True
    for ticker, side, price in cases:
        try:
            result = broker.get_order_capacity(ticker, side, price)
            ok = result.get("rsp_cd") == "00166"
            if side == "BUY":
                ok = ok and result.get("currency") == "USD" and result.get("orderable_usd") is not None
                status = {"symbol": ticker, "side": side, "ok": bool(ok),
                          "currency": result.get("currency"), "has_usd_buyable": result.get("orderable_usd") is not None}
            else:
                ok = ok and result.get("sellable_qty") is not None
                status = {"symbol": ticker, "side": side, "ok": bool(ok),
                          "has_sellable_qty": result.get("sellable_qty") is not None}
        except Exception as exc:
            status = {"symbol": ticker, "side": side, "ok": False,
                      "exception": type(exc).__name__}
            ok = False
        success &= bool(ok)
        print("NH_CAPACITY_PROBE " + json.dumps(status, ensure_ascii=False))
    print("NH_CAPACITY_PROBE " + ("PASS" if success else "FAIL") + " (READ-ONLY; NO ORDERS)")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
