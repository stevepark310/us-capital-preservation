#!/usr/bin/env python3
"""Reconcile pending real NH orders to confirmed fills; this script NEVER sends orders."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DATA = os.path.join(BASE, "data")
LIVE = os.path.join(DATA, "live_portfolio.json")

from core import market_clock
from core.autotrader import AutonomousTrader


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def _save(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def now_kst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST")


def refresh_live_holdings(account: dict, confirmed_events: list) -> dict:
    """Replace only holdings/equity with verified NH values, preserving old cash accounting.

    Existing cash_buffer and pending settlement are *not* equivalent to the
    broker's orderable amount. Keep them separate to prevent double counting.
    Every new order continues to call NH's live buyableAmount API.
    """
    live = _read(LIVE)
    if not isinstance(account.get("positions"), dict):
        raise ValueError("NH positions absent")
    prev = {p.get("ticker"): p for p in live.get("positions", [])
            if isinstance(p, dict) and p.get("ticker")}
    positions = []
    for symbol, p in sorted(account["positions"].items()):
        if not isinstance(p, dict) or int(p.get("qty") or 0) <= 0:
            continue
        qty = int(p["qty"])
        item = dict(prev.get(symbol) or {})
        item["ticker"] = symbol
        item["qty"] = qty
        if p.get("avg_price") is not None:
            item["avg_price"] = round(float(p["avg_price"]), 2)
        if p.get("price") is not None:
            item["market_price"] = round(float(p["price"]), 2)
            item["broker_mark_price"] = round(float(p["price"]), 2)
        item["status"] = "NH verified holding after fill reconciliation"
        positions.append(item)
    live["positions"] = positions
    if account.get("equity_usd") is not None:
        live["current_total_equity_usd"] = round(float(account["equity_usd"]), 2)
    live["nh_balance_cash_usd"] = account.get("cash_usd")
    live["last_synced"] = now_kst()
    live["api_status"] = "CONNECTED"
    live["account_sync"] = {
        "source": "NH Open API live balance + 3-day order execution history",
        "rsp_cd": account.get("rsp_cd"),
        "synced_at_kst": now_kst(),
        "channel": "direct_nh",
    }
    prior = live.get("fills_confirmed") or []
    # Persist only new, confirmed fills, not order submissions.
    live["fills_confirmed"] = (prior + [
        {"at_kst": now_kst(), "ticker": e.get("ticker"),
         "side": e.get("side"), "qty": e.get("qty"),
         "price": e.get("price"), "pnl_usd": e.get("pnl_usd")}
        for e in confirmed_events
    ])[-50:]
    _save(LIVE, live)
    return {"tickers": [p["ticker"] for p in positions], "equity_usd": live.get("current_total_equity_usd")}


def reconcile_pending(trader: Optional[AutonomousTrader] = None,
                      account: Optional[dict] = None) -> dict:
    t = trader or AutonomousTrader(dry_run=False)
    if not t.acquire_lease("autotrader"):
        raise RuntimeError("Another trading writer holds the lease")
    try:
        clock = market_clock.resolve()
        st = t.load_state(str(clock.get("session_date") or ""))
        brackets = t._open_brackets()
        pending = st.get("pending") or []
        if not pending:
            return {"checked": 0, "confirmed": 0, "note": "nothing pending, no changes"}
        acct = account if account is not None else t.router.account_state(refresh_min=0)
        if not acct or not acct.get("found") or str(acct.get("rsp_cd")) != "00166":
            raise RuntimeError("Fresh NH account inquiry failed; no reconciliation performed")
        if not isinstance(acct.get("positions"), dict):
            raise RuntimeError("Fresh NH positions missing")
        result = t.reconcile(st, brackets, acct)
        if result.get("error"):
            raise RuntimeError(result["error"])
        # Keep unconfirmed/partial orders pending; apply only confirmed fills.
        if result.get("confirmed"):
            t.save_state(st)
            t._save_brackets(brackets)
            confirmed = [e for e in t.events if e.get("kind") == "FILL_CONFIRMED"]
            result["holdings"] = refresh_live_holdings(acct, confirmed)
        return result
    finally:
        t.release_lease("autotrader")


def main() -> int:
    try:
        result = reconcile_pending()
        print("NH_FILL_RECONCILE " + json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"NH_FILL_RECONCILE FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
