"""
Strategy approval register.

DIP_SWING holds a position overnight (up to 3 sessions). That is a risk-profile
change only the user can accept, so the desk keeps the strategy blocked until an
explicit approval exists in config/swing_approval.json.

Agent 2 (signal scan), Agent 10 (execution plan) and the feed all read this one
file, so the dashboard cannot say "approved" while execution still treats the
strategy as pending, and turning the flag on/off is a one-file edit rather than a
code change scattered across agents.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVAL_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")

PENDING = "PENDING_APPROVAL"
APPROVED = "APPROVED"
ACTIVE_STATES = {APPROVED, "ACTIVE"}

_DEFAULT: Dict[str, Any] = {
    "strategy": "DIP_SWING",
    "overnight_status": PENDING,
    "execution_mode": "BLOCKED_PENDING_USER_APPROVAL",
    "approved_at_kst": None,
    "universe": ["SOXL", "TQQQ"],
    "entry_conditions": {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0},
    "exit_rules": {"atr_bracket_mult": 1.5, "max_hold_sessions": 3},
    "risk_limits": {"risk_per_trade_pct": 2.0, "max_positions": 2, "position_cap_pct": 25.0},
}


def load() -> Dict[str, Any]:
    """Approval record; falls back to a blocked default if the file is absent."""
    data = json.loads(json.dumps(_DEFAULT))
    try:
        with open(APPROVAL_PATH, "r", encoding="utf-8") as f:
            record = json.load(f) or {}
        data.update(record)
    except Exception:
        return data
    return data


def status(record: Dict[str, Any] = None) -> str:
    rec = record if record is not None else load()
    return str(rec.get("overnight_status") or PENDING).upper()


def is_approved(record: Dict[str, Any] = None) -> bool:
    return status(record) in ACTIVE_STATES


def status_ko(record: Dict[str, Any] = None) -> str:
    rec = record if record is not None else load()
    if is_approved(rec):
        return f"승인 완료 ({rec.get('approved_at_kst') or '—'} KST·사용자 승인)"
    return f"승인 대기 ({PENDING})"


def plan_block(record: Dict[str, Any] = None) -> Dict[str, Any]:
    """Compact block embedded in agent reports."""
    rec = record if record is not None else load()
    cond = rec.get("entry_conditions") or {}
    exitr = rec.get("exit_rules") or {}
    risk = rec.get("risk_limits") or {}
    return {
        "strategy": rec.get("strategy", "DIP_SWING"),
        "status": status(rec),
        "status_ko": status_ko(rec),
        "approved": is_approved(rec),
        "execution_mode": rec.get("execution_mode"),
        "approved_by": rec.get("approved_by"),
        "approved_at_kst": rec.get("approved_at_kst"),
        "effective_from_open_kst": rec.get("effective_from_open_kst"),
        "entry_rule": f"RSI2 ≤ {cond.get('rsi2_max')} · 볼린저 %B ≤ {cond.get('pct_b_max')} · RVOL ≥ {cond.get('rvol_min')}",
        "rsi2_max": cond.get("rsi2_max"), "pct_b_max": cond.get("pct_b_max"), "rvol_min": cond.get("rvol_min"),
        "max_hold_sessions": exitr.get("max_hold_sessions"),
        "atr_bracket_mult": exitr.get("atr_bracket_mult"),
        "risk_per_trade_pct": risk.get("risk_per_trade_pct"),
        "max_positions": risk.get("max_positions"),
        "position_cap_pct": risk.get("position_cap_pct"),
        "as_of_kst": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    print(json.dumps(plan_block(), ensure_ascii=False, indent=2))
