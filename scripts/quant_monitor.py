#!/usr/bin/env python3
"""Read-only portfolio risk monitor; publishes a shared 10-desk status file.

Never places/cancels orders. Does not assert guaranteed VaR. Uses last known
ledger marks as fallbacks and explicitly says when prices could be stale.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data")

from core import market_clock, portfolio_quant_risk as qr


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


def report(ledger: dict, brackets: dict, quotes: dict, clock: dict) -> dict:
    equity = qr._positive(ledger.get("current_total_equity_usd") or ledger.get("equity_usd"))
    snapshot = qr.positions_from_ledger(ledger, brackets, quotes)
    positions = snapshot.get("positions") or {}
    gross = sum(float(v.get("gross_factor_usd") or 0) for v in positions.values())
    tail = sum(float(v.get("shock_loss_usd") or 0) for v in positions.values())
    lev = sum(float(v.get("notional_usd") or 0) for v in positions.values()
              if abs(v.get("factor") or 0) >= 3)
    overdue = qr.overdue_managed_exits(brackets, clock)
    limits = qr.limits()
    flagged = []
    if not equity:
        flagged.append("equity unavailable")
    if snapshot["unpriced"]:
        flagged.append("unpriced holdings: " + ",".join(snapshot["unpriced"]))
    if overdue:
        flagged.append("managed exits overdue: " + ",".join(overdue))
    if equity and 100.0 * gross / equity > limits["max_gross_factor_pct"]:
        flagged.append("gross factor exposure above new-buy cap")
    if equity and 100.0 * tail / equity > limits["max_portfolio_shock_pct"]:
        flagged.append("portfolio shock exposure above new-buy cap")
    generated = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
    return {"generated_at_kst": generated, "market_phase": clock.get("phase"),
            "session_date": clock.get("session_date"),
            "model": "QUANT_GROSS_V1", "status": "FREEZE_NEW_RISK" if flagged else "MONITOR",
            "flags": flagged, "overdue_managed_exits": overdue,
            "account_equity_usd": round(equity, 2) if equity else None,
            "gross_factor_pct": round(100.0 * gross / equity, 2) if equity else None,
            "three_x_notional_pct": round(100.0 * lev / equity, 2) if equity else None,
            "additive_shock_loss_usd": round(tail, 2),
            "additive_shock_loss_pct": round(100.0 * tail / equity, 2) if equity else None,
            "stress_assumptions": "3x ETF adverse -15%; single stock -12%; no offset from inverse hedge",
            "prices_may_be_stale": True,
            "quotes_last_verified_by": "existing live_integrity when market runner executes",
            "limits": limits,
            "portfolio": [
                {"ticker": v["ticker"], "qty": v["qty"],
                 "notional_usd": round(v["notional_usd"], 2),
                 "gross_factor_usd": round(v["gross_factor_usd"], 2),
                 "shock_loss_usd": round(v["shock_loss_usd"], 2)}
                for v in positions.values()],
            "note": "Read-only; risk flags block NEW buys in autotrader, not broker-native stop orders"}


def main() -> int:
    ledger = _load(os.path.join(DATA, "live_portfolio.json"))
    brackets = _load(os.path.join(DATA, "active_brackets.json"))
    snapshot = _load(os.path.join(DATA, "market_snapshot.json"))
    output = report(ledger, brackets, snapshot.get("quotes") or {}, market_clock.resolve())
    with open(os.path.join(DATA, "quant_risk_status.json"), "w", encoding="utf-8") as fp:
        json.dump(output, fp, ensure_ascii=False, indent=2)
    print("[quant-monitor] " + output["status"] + " | "
          + ", ".join(output["flags"] or ["risk exposure within stated new-buy limits"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
