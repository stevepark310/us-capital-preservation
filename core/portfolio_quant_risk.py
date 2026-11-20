"""Conservative, deterministic portfolio-level quant risk checks for new BUYs.

Applied AFTER existing entry signals/position sizing but BEFORE router.place().
Only decreases proposed quantities (or vetoes entries); never adds symbols,
raises a stop, sends an order or changes an exit. The shock estimates are
stress assumptions, NOT VaR guarantees. All gross risk ignores hedge netting.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Underlying exposure approximations: 3x ETF is daily leverage, not a stable
# multi-day delta. These factors are used only as conservative gross limits.
FACTORS = {
    "SOXL": ("SEMICONDUCTOR", 3), "SOXS": ("SEMICONDUCTOR", -3),
    "TQQQ": ("NASDAQ", 3), "SQQQ": ("NASDAQ", -3),
    "QQQ": ("NASDAQ", 1), "NVDA": ("SEMICONDUCTOR", 1),
    "SMH": ("SEMICONDUCTOR", 1), "SGOV": ("CASH", 0),
}
HARD_CAPS = {
    "max_leveraged_sleeve_pct": 15.0,     # total 3x ETF notional / equity
    "max_gross_factor_pct": 75.0,         # gross 3x-equivalent (no net hedge credit)
    "max_portfolio_shock_pct": 5.0,       # additive downside stress / equity
    "max_trade_stop_risk_pct": 1.0,       # planned stop loss / equity (slippage extra)
    "target_annual_vol_pct": 18.0,       # volatility-target gross risk for 3x sleeve
    "stress_3x_pct": 15.0,               # one-session severe ETF shock assumption
    "stress_stock_pct": 12.0,
    "stress_other_pct": 8.0,
}
PAIR_CONFLICTS = (frozenset(("SOXL", "SOXS")),
                  frozenset(("TQQQ", "SQQQ")))


def _positive(v: Any) -> Optional[float]:
    try:
        n = float(v)
        return n if math.isfinite(n) and n > 0.0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def limits(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """External config may tighten, never loosen, hard safety caps."""
    overrides = (config or {}).get("limits") or {}
    caps = dict(HARD_CAPS)
    for name, hard in HARD_CAPS.items():
        n = _positive(overrides.get(name))
        if n is not None:
            caps[name] = min(hard, n)
    return caps


def annualized_vol_pct(closes: Any) -> Optional[float]:
    """20d vs 60d sample log-return volatility. Need 21 clean completed bars."""
    try:
        if hasattr(closes, "columns"):
            closes = closes["Close"]
        values = [float(x) for x in closes]
        if len(values) < 22 or any(not math.isfinite(x) or x <= 0 for x in values[-61:]):
            return None
        vals = values[-61:]
        returns = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals))]
        estimates = []
        for n in (20, 60):
            tail = returns[-n:]
            if len(tail) < n:
                continue
            avg = sum(tail) / len(tail)
            var = sum((x - avg) ** 2 for x in tail) / (len(tail) - 1)
            estimates.append(math.sqrt(max(0.0, var) * 252.0) * 100.0)
        return round(max(estimates), 3) if estimates else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def exposure_row(symbol: str, qty: int, price: float) -> dict:
    ticker = str(symbol or "").upper()
    group, factor = FACTORS.get(ticker, ("OTHER", 1))
    notional = max(0, int(qty)) * float(price)
    if factor == 0:
        shock = 0.0
    elif abs(factor) >= 3:
        shock = HARD_CAPS["stress_3x_pct"]
    elif ticker in ("NVDA",):
        shock = HARD_CAPS["stress_stock_pct"]
    else:
        shock = HARD_CAPS["stress_other_pct"]
    return {"ticker": ticker, "group": group, "factor": factor,
            "qty": int(qty), "notional_usd": notional,
            "gross_factor_usd": notional * abs(factor),
            "shock_loss_usd": notional * shock / 100.0}


def positions_from_ledger(ledger: dict, brackets: dict,
                          quotes: dict, pending: Optional[List[dict]] = None,
                          actions: Optional[List[dict]] = None) -> dict:
    """Merge broker ledger + open brackets + unconfirmed buys, never double count."""
    rows = {}
    missing = []
    candidates = {}
    for p in ledger.get("positions") or []:
        if isinstance(p, dict) and p.get("ticker"):
            ticker = str(p["ticker"]).upper()
            if int(p.get("qty") or 0) > 0:
                candidates[ticker] = {"qty": int(p["qty"]),
                                      "fallback": p.get("broker_mark_price") or
                                      p.get("market_price") or p.get("avg_price")}
    for ticker, b in (brackets or {}).items():
        if not isinstance(b, dict) or b.get("closed") or b.get("carry_parking") and ticker == "SGOV":
            continue
        qty = int(b.get("bought_qty") or 0)
        if qty > 0 and str(ticker).upper() not in candidates:
            candidates[str(ticker).upper()] = {
                "qty": qty, "fallback": b.get("entry_price")}
    for p in (pending or []) + (actions or []):
        if p.get("side") != "BUY" or p.get("status") not in (
            "SUBMITTED", "PENDING", "DISPATCHED", "DRY_RUN"):
            continue
        ticker = str(p.get("ticker") or "").upper()
        if not ticker:
            continue
        current = candidates.get(ticker) or {"qty": 0, "fallback": p.get("price") or p.get("limit_price")}
        current["qty"] += int(p.get("qty") or 0)
        candidates[ticker] = current
    for ticker, p in candidates.items():
        q = (quotes or {}).get(ticker) or {}
        quote_px = (q.get("intraday_price") or q.get("price") or q.get("last")
                    if isinstance(q, dict) else None)
        px = _positive(quote_px) or _positive(p.get("fallback"))
        if px is None:
            missing.append(ticker)
            continue
        rows[ticker] = exposure_row(ticker, p["qty"], px)
    return {"positions": rows, "unpriced": missing}


def assess_entry(symbol: str, qty: int, price: float, equity: float,
                 sl_pct: float, current: dict, history: Any = None,
                 config: Optional[dict] = None) -> dict:
    """Return max allowed quantity and machine-readable veto reasons.

    No assumed netting benefit from opposite 3x ETFs; correlation can break in
    stressed markets. Position shock estimate is gross and intentionally crude.
    """
    caps = limits(config)
    symbol = str(symbol or "").upper()
    equity = _positive(equity)
    price = _positive(price)
    sl_pct = _positive(sl_pct)
    if not equity or not price or not sl_pct or qty <= 0 or symbol not in FACTORS:
        return {"ok": False, "approved_qty": 0, "reasons": ["invalid risk inputs"],
                "model": "QUANT_GROSS_V1"}
    missing = current.get("unpriced") or []
    if missing:
        return {"ok": False, "approved_qty": 0,
                "reasons": ["held positions have unknown prices: " + ",".join(missing)],
                "model": "QUANT_GROSS_V1"}
    existing = current.get("positions") or {}
    for pair in PAIR_CONFLICTS:
        if symbol in pair and any(t != symbol and t in existing for t in pair):
            return {"ok": False, "approved_qty": 0,
                    "reasons": ["conflicting daily leveraged pair, reduce exposure before hedging"],
                    "model": "QUANT_GROSS_V1"}
    prior = [v for v in existing.values() if v.get("ticker") != symbol]
    prior += [v for v in existing.values() if v.get("ticker") == symbol]
    used_shock = sum(float(v.get("shock_loss_usd") or 0) for v in prior)
    used_factor = sum(float(v.get("gross_factor_usd") or 0) for v in prior)
    used_3x = sum(float(v.get("notional_usd") or 0) for v in prior
                  if abs(float(v.get("factor") or 0)) >= 3)
    _, leverage = FACTORS[symbol]
    shock_pct = caps["stress_3x_pct"] if abs(leverage) >= 3 else (
        caps["stress_stock_pct"] if symbol == "NVDA" else caps["stress_other_pct"])
    per_share_stop = price * sl_pct / 100.0
    if per_share_stop <= 0:
        return {"ok": False, "approved_qty": 0, "reasons": ["invalid stop risk"],
                "model": "QUANT_GROSS_V1"}
    shock_allow = max(0.0, equity * caps["max_portfolio_shock_pct"] / 100.0 - used_shock)
    factor_allow = max(0.0, equity * caps["max_gross_factor_pct"] / 100.0 - used_factor)
    max_qty = min(int(qty),
                  int((equity * caps["max_trade_stop_risk_pct"] / 100.0 + 1e-9) // per_share_stop),
                  int((shock_allow + 1e-9) // (price * shock_pct / 100.0)),
                  int((factor_allow + 1e-9) // (price * abs(leverage) if leverage else price)))
    if abs(leverage) >= 3:
        sleeve_allow = max(0.0, equity * caps["max_leveraged_sleeve_pct"] / 100.0 - used_3x)
        max_qty = min(max_qty, int((sleeve_allow + 1e-9) // price))
    # Volatility-target sizing: reduce risky ETF quantity as historical 20d/60d
    # volatility increases. Historical bars must be from completed sessions.
    vol = annualized_vol_pct(history) if history is not None else None
    if abs(leverage) >= 3 and vol is not None and vol > 0:
        vol_target_notional = equity * caps["target_annual_vol_pct"] / vol
        max_qty = min(max_qty, int((vol_target_notional + 1e-9) // price))
    reasons = []
    if max_qty < qty:
        reasons.append("gross factor / adverse shock / planned stop / volatility risk cap")
    if max_qty == 0:
        reasons.append("one-share minimum is unsafe; hold cash")
    return {"ok": max_qty > 0, "approved_qty": max_qty, "proposed_qty": qty,
            "reasons": reasons, "model": "QUANT_GROSS_V1",
            "realized_annual_vol_pct": vol,
            "gross_factor_pct": round(used_factor / equity * 100, 2),
            "shock_loss_pct_before": round(used_shock / equity * 100, 2),
            "one_share_stress_usd": round(price * shock_pct / 100.0, 2),
            "note": "stress assumptions, not guaranteed risk; does not model gap/slippage"}


def overdue_managed_exits(brackets: Dict[str, Any], clock: Dict[str, Any]) -> List[str]:
    """Prevent new risk while a prior managed intraday force-exit remains open.

    force_exit_kst is anchored to the bracket's first Korean opening date.
    Openings after midday with morning exit clock imply next calendar day.
    This does not create permission to sell outside the user's live gate.
    """
    stamp = str((clock or {}).get("now_kst") or "")[:19]
    try:
        now = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return []
    overdue = []
    for symbol, b in (brackets or {}).items():
        if not isinstance(b, dict) or b.get("closed") or not b.get("force_exit_kst"):
            continue
        qty = int(b.get("bought_qty") or 0)
        if qty <= 0 or b.get("pending_entry"):
            continue
        opened = str(b.get("opened_at") or b.get("filled_at_kst") or "")[:19]
        force_clock = str(b["force_exit_kst"])
        try:
            opened_at = datetime.strptime(opened, "%Y-%m-%d %H:%M:%S")
            hh, mm = [int(x) for x in force_clock.split(":")[:2]]
            due = opened_at.replace(hour=hh, minute=mm, second=0)
            if due < opened_at:
                due += timedelta(days=1)
        except (ValueError, TypeError):
            if b.get("force_exit_due"):
                overdue.append(str(symbol).upper())
            continue
        if now >= due and now.date() > due.date() or b.get("force_exit_due"):
            overdue.append(str(symbol).upper())
    return overdue
