"""Fail-closed, one-US-session authorization for unattended real orders.

The config cannot silently authorize later sessions: a US Eastern session date
and a KST expiry must both be present. A disabled gate always permits dry-runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

KST = timezone(timedelta(hours=9))


def check_live_gate(gate: Any, clock: Dict[str, Any],
                    now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    if not isinstance(gate, dict) or gate.get("enabled") is not True:
        return {"ok": False, "reason": "live gate disabled"}
    if clock.get("phase") != "REGULAR":
        return {"ok": False, "reason": "US regular session required"}
    session_date = str(clock.get("session_date") or "")
    if not session_date or not gate.get("session_date") or gate.get("session_date") != session_date:
        return {"ok": False, "reason": "live gate does not authorize this US session"}
    expiry = gate.get("expires_at_kst")
    if not isinstance(expiry, str):
        return {"ok": False, "reason": "live gate expiration missing"}
    try:
        end = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return {"ok": False, "reason": "live gate expiration invalid"}
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return {"ok": False, "reason": "naive UTC clock not allowed"}
    if current.astimezone(KST) >= end:
        return {"ok": False, "reason": "live gate expired"}
    try:
        cap = float(gate["max_buy_order_usd"])
        session_cap = float(gate["max_session_buy_usd"])
        count = int(gate["max_session_buy_orders"])
        if not (0 < cap <= 100 and cap <= session_cap <= 200 and 1 <= count <= 3):
            return {"ok": False, "reason": "live gate risk caps invalid"}
    except (TypeError, KeyError, ValueError, OverflowError):
        return {"ok": False, "reason": "live gate risk caps missing"}
    return {"ok": True, "session_date": session_date,
            "expires_at_kst": expiry, "max_buy_order_usd": cap,
            "max_session_buy_usd": session_cap,
            "max_session_buy_orders": count}
