"""
CUBE Live Ledger — one source of truth for the real account.

Problems this fixes
-------------------
* `live_portfolio.json` converted USD→KRW with a hard-coded 1343.4 while
  `daily_trading_calendar.json` used 1335.0, so the same 634.77 USD cash buffer
  showed up as 852,750원 in one file and 847,420원 in the other.
* `cube_report.json` still carried 11-11 balances inside a 11-14 report because
  the orchestrator never re-read the ledger.
* The calendar marked 2026-11-16 (Monday) as a non-trading day.

Everything financial is read here and nowhere else; anything derived (KRW
conversions) is computed from the single `fx_rate` this module resolves.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
CALENDAR = os.path.join(DATA, "daily_trading_calendar.json")
LIVE = os.path.join(DATA, "live_portfolio.json")
BRACKETS = os.path.join(DATA, "active_brackets.json")
CONSISTENCY = os.path.join(DATA, "ledger_consistency.json")

ACCOUNT_NO = "202-05-134849"


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_calendar(cal: Dict[str, Any]) -> None:
    with open(CALENDAR, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)


def load_calendar() -> Dict[str, Any]:
    return _load(CALENDAR)


def load_live() -> Dict[str, Any]:
    return _load(LIVE)


def load_brackets() -> Dict[str, Any]:
    return _load(BRACKETS)


def resolve_fx(cal: Optional[Dict[str, Any]] = None, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One FX rate for every KRW conversion, in priority order.

    1. the ledger's own implied rate (current_balance_krw / current_balance_usd)
    2. the ledger's recorded fx_rate field
    3. the live Yahoo KRW=X quote
    """
    cal = cal if cal is not None else load_calendar()
    ms = cal.get("monthly_summary", {})
    krw, usd = ms.get("current_balance_krw"), ms.get("current_balance_usd")
    if krw and usd:
        return {"rate": round(krw / usd, 2), "source": "실전 원장 잔고 역산 (calendar.current_balance)"}
    if ms.get("fx_rate"):
        return {"rate": float(ms["fx_rate"]), "source": "calendar.monthly_summary.fx_rate"}
    if snapshot:
        r = (snapshot.get("fx") or {}).get("usd_krw")
        if r:
            return {"rate": float(r), "source": "Yahoo KRW=X 실시간"}
    return {"rate": 1335.0, "source": "기본값 (근거 없음)"}


def summary(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cal = load_calendar()
    live = load_live()
    bracks = load_brackets()
    ms = cal.get("monthly_summary", {})
    fx = resolve_fx(cal, snapshot)

    cash_usd = live.get("cash_buffer_usd", ms.get("cash_buffer_usd", 634.77))
    cash_krw = ms.get("cash_buffer_krw")
    if cash_krw is None:
        cash_krw = int(round(cash_usd * fx["rate"]))

    positions = live.get("positions", [])
    equity_usd = live.get("current_total_equity_usd", ms.get("current_balance_usd", 0.0))
    equity_krw = live.get("current_total_equity_krw", ms.get("current_balance_krw", 0))

    return {
        "account_no": live.get("account_no", ACCOUNT_NO),
        "ledger_type": cal.get("ledger_type", "REAL_TRADING_LIVE_LEDGER"),
        "month": cal.get("month"),
        "fx_rate": fx["rate"],
        "fx_source": fx["source"],
        "realized_pnl_usd": ms.get("total_profit_usd", live.get("realized_pnl_usd", 0.0)),
        "realized_pnl_krw": ms.get("total_profit_krw", live.get("realized_pnl_krw", 0)),
        "unrealized_pnl_usd": ms.get("total_unrealized_usd", live.get("unrealized_pnl_usd", 0.0)),
        "net_pnl_usd": ms.get("net_total_pnl_usd", live.get("net_daily_pnl_usd", 0.0)),
        "equity_usd": equity_usd,
        "equity_krw": equity_krw,
        "cash_usd": round(float(cash_usd), 2),
        "cash_krw": int(cash_krw),
        "cash_pct": round(float(cash_usd) / equity_usd * 100, 1) if equity_usd else None,
        "initial_funding_krw": live.get("initial_funding_krw", 1209215),
        "initial_funding_usd": live.get("initial_funding_usd", 900.18),
        "win_rate_pct": ms.get("win_rate_pct", live.get("win_rate_pct")),
        "trades": ms.get("total_trades"),
        "wins": ms.get("wins"), "losses": ms.get("losses"),
        "positions": positions,
        "active_brackets": bracks,
        "recent_trades": live.get("recent_trades", [])[:8],
        "last_synced": live.get("last_synced"),
        "last_trade_date": _last_trade_date(cal),
        "as_of": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST"),
    }


def _last_trade_date(cal: Dict[str, Any]) -> Optional[str]:
    recs = cal.get("daily_records", {})
    for d in sorted(recs.keys(), reverse=True):
        r = recs[d]
        if r.get("trades"):
            return d
    return None


def validate(snapshot: Optional[Dict[str, Any]] = None, fix: bool = False) -> Dict[str, Any]:
    """Cross-file consistency checks. With fix=True only structural flags are repaired."""
    from core import market_clock

    cal = load_calendar()
    live = load_live()
    ms = cal.get("monthly_summary", {})
    fx = resolve_fx(cal, snapshot)
    issues: List[Dict[str, Any]] = []

    # 1. KRW cash buffer mismatch between the two published files
    live_krw = live.get("cash_buffer_krw")
    cash_usd = live.get("cash_buffer_usd", ms.get("cash_buffer_usd"))
    canonical_krw = ms.get("cash_buffer_krw") or int(round(float(cash_usd) * fx["rate"]))
    if live_krw is not None and abs(int(live_krw) - int(canonical_krw)) > 2:
        issues.append({
            "code": "CASH_KRW_MISMATCH", "severity": "HIGH",
            "detail": f"live_portfolio cash_buffer_krw {live_krw:,}원 vs 원장 기준 {canonical_krw:,}원 "
                      f"(${cash_usd} × {fx['rate']}). live_portfolio 쪽 환율 {round(live_krw/float(cash_usd),2)} 사용 흔적.",
            "fix": "sync_live_portfolio가 원장 환율로 재계산",
        })

    # 2. realized P&L vs sum of daily records
    recs = cal.get("daily_records", {})
    realized_sum = 0.0
    counted = 0
    for d, r in recs.items():
        if r.get("is_trading_day") and not r.get("future") and r.get("profit_usd") is not None:
            realized_sum += float(r["profit_usd"])
            counted += 1
    if counted and abs(realized_sum - float(ms.get("total_profit_usd", 0))) > 0.02:
        issues.append({
            "code": "REALIZED_SUM_DRIFT", "severity": "MEDIUM",
            "detail": f"일별 profit_usd 합계 {realized_sum:+.2f} vs monthly_summary {ms.get('total_profit_usd')}",
            "fix": "일별 기록 검토 필요(자동 수정하지 않음)",
        })

    # 3. trading-day flags
    fixed_days = []
    for d, r in recs.items():
        try:
            y, m, dd = map(int, d.split("-"))
            should = market_clock.is_trading_day(datetime(y, m, dd).date())
        except Exception:
            continue
        if bool(r.get("is_trading_day")) != should:
            issues.append({
                "code": "TRADING_DAY_FLAG", "severity": "LOW",
                "detail": f"{d}: is_trading_day={r.get('is_trading_day')} (실제 {should})",
                "fix": "market_clock 기준으로 플래그만 수정 (금액·체결 기록은 불변)",
            })
            if fix:
                r["is_trading_day"] = should
                fixed_days.append(d)

    # 4. positions vs brackets
    bracks = load_brackets()
    pos_tickers = {p["ticker"]: p for p in live.get("positions", [])}
    for t, p in pos_tickers.items():
        b = bracks.get(t)
        if b and abs(float(b.get("entry_price", 0)) - float(p.get("avg_price", 0))) > 0.02:
            issues.append({
                "code": "BRACKET_ENTRY_MISMATCH", "severity": "MEDIUM",
                "detail": f"{t}: 활성 브래킷 평단 ${b.get('entry_price')} vs 원장 평단 ${p.get('avg_price')}",
                "fix": "체결 원장 기준으로 브래킷 재산정 필요",
            })

    # 5. stale ledger vs last completed session
    clock = market_clock.resolve()
    if ms.get("current_balance_usd") is None:
        issues.append({"code": "LEDGER_INCOMPLETE", "severity": "HIGH",
                       "detail": "monthly_summary.current_balance_usd 없음", "fix": "정산 필요"})

    report = {
        "checked_at_kst": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
        "phase": clock["phase"],
        "fx_rate": fx["rate"], "fx_source": fx["source"],
        "canonical_cash": {"usd": cash_usd, "krw": int(canonical_krw)},
        "issue_count": len(issues),
        "issues": issues,
        "fixed_days": fixed_days,
        "status": "CONSISTENT" if not issues else ("REPAIRED" if fix and not [i for i in issues if i["code"] != "TRADING_DAY_FLAG"] else "ISSUES_FOUND"),
    }
    if fix and fixed_days:
        save_calendar(cal)
    with open(CONSISTENCY, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def ensure_cash_fields() -> Dict[str, Any]:
    """Write explicit cash_buffer fields + fx_rate into the ledger summary (additive only)."""
    cal = load_calendar()
    ms = cal.setdefault("monthly_summary", {})
    fx = resolve_fx(cal)
    live = load_live()
    cash_usd = live.get("cash_buffer_usd", ms.get("cash_buffer_usd"))
    if cash_usd is None:
        return {"changed": False}
    before = (ms.get("cash_buffer_usd"), ms.get("cash_buffer_krw"), ms.get("fx_rate"))
    computed = int(round(float(cash_usd) * fx["rate"]))
    existing = ms.get("cash_buffer_krw")
    # A settled ledger figure wins over a 2원 rounding difference; anything larger is recomputed.
    if existing is None or abs(int(existing) - computed) > max(5, computed * 0.001):
        ms["cash_buffer_krw"] = computed
    ms.setdefault("cash_buffer_usd", round(float(cash_usd), 2))
    ms.setdefault("fx_rate", fx["rate"])
    ms.setdefault("fx_rate_source", fx["source"])
    after = (ms.get("cash_buffer_usd"), ms.get("cash_buffer_krw"), ms.get("fx_rate"))
    if before != after:
        save_calendar(cal)
    return {"changed": before != after, "cash_buffer_usd": ms["cash_buffer_usd"],
            "cash_buffer_krw": ms["cash_buffer_krw"], "fx_rate": ms["fx_rate"]}


if __name__ == "__main__":
    import pprint
    print("== ensure cash fields")
    pprint.pprint(ensure_cash_fields())
    print("== summary")
    pprint.pprint({k: v for k, v in summary().items() if k not in ("positions", "active_brackets", "recent_trades")})
    print("== validate(fix=True)")
    pprint.pprint(validate(fix=True))
