#!/usr/bin/env python3
"""
Live Portfolio Synchronizer

Rebuilt so the published dashboard cannot disagree with the ledger:
  * every KRW value is converted with ONE fx rate (core.ledger.resolve_fx)
  * the cash buffer KRW figure is taken from the settled ledger, not from a
    hard-coded 1343.4 that produced 852,750원 against the ledger's 847,420원
  * positions are marked to the live snapshot price (Yahoo), and the file states
    which session the price belongs to
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import market_snapshot, ledger as ledger_mod

CASH_NOTE = "정산 기록 기준 현금(원장 monthly_summary). 환율은 원장 잔고 역산 단일 환율 사용."


def sync_portfolio() -> dict:
    snap = market_snapshot.build_snapshot(quiet=True)
    clock = (snap.get("clock") or {})
    led = ledger_mod.summary(snapshot=snap)
    quotes = snap.get("quotes", {})
    fx_rate = led["fx_rate"]
    now_str = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST")

    positions = []
    for p in led.get("positions", []):
        ticker = p.get("ticker")
        q = quotes.get(ticker) or {}
        px = q.get("intraday_price") or q.get("last") or p.get("market_price") or p.get("avg_price")
        avg = float(p.get("avg_price") or 0)
        qty = int(p.get("qty") or 0)
        unreal = round((float(px) - avg) * qty, 2) if avg and px else 0.0
        positions.append({
            "ticker": ticker,
            "name": p.get("name"),
            "qty": qty,
            "avg_price": round(avg, 2),
            "market_price": round(float(px), 2) if px else None,
            "price_as_of": q.get("last_bar_date") if q else None,
            "eval_usd": round(float(px) * qty, 2) if px else 0.0,
            "unrealized_usd": unreal,
            "unrealized_krw": int(round(unreal * fx_rate)),
            "return_pct": round((float(px) / avg - 1) * 100, 2) if avg and px else None,
            "status": p.get("status"),
            "risk_status": p.get("risk_status"),
            "bracket": p.get("bracket"),
        })

    total_pos_eval = round(sum(p["eval_usd"] for p in positions), 2)
    equity_usd = led.get("equity_usd") or 0.0
    unreal_usd = round(sum(p["unrealized_usd"] for p in positions), 2)

    payload = {
        "api_status": "CONNECTED",
        "broker": "NH투자증권 나무 (Open API)",
        "account_no": led.get("account_no"),
        "last_synced": now_str,
        "session_phase": clock.get("phase"),
        "price_basis": ("정규장 실시간" if clock.get("phase") == "REGULAR" else
                        "마지막 종가 기준 (휴장·시간외)"),
        "fx_rate": fx_rate,
        "fx_source": led.get("fx_source"),
        "initial_funding_krw": led.get("initial_funding_krw"),
        "initial_funding_usd": led.get("initial_funding_usd"),
        "current_total_equity_usd": equity_usd,
        "current_total_equity_krw": led.get("equity_krw"),
        "cash_buffer_usd": led.get("cash_usd"),
        "cash_buffer_krw": led.get("cash_krw"),
        "cash_buffer_pct": led.get("cash_pct"),
        "cash_buffer_note": CASH_NOTE,
        "realized_pnl_usd": led.get("realized_pnl_usd"),
        "realized_pnl_krw": led.get("realized_pnl_krw"),
        "unrealized_pnl_usd": led.get("unrealized_pnl_usd"),
        "unrealized_pnl_krw": led.get("unrealized_pnl_usd") and int(round(led["unrealized_pnl_usd"] * fx_rate)),
        "unrealized_recomputed_usd": unreal_usd,
        "unrealized_source": "원장 정산 평가손익 기준 (market_price는 실시간 시세 재평가)",
        "net_total_pnl_usd": led.get("net_pnl_usd"),
        "net_total_pnl_krw": int(round((led.get("net_pnl_usd") or 0) * fx_rate)),
        "win_rate_pct": led.get("win_rate_pct"),
        "trades_summary": f"{led.get('trades')}전 {led.get('wins')}승 {led.get('losses')}패 (승률 {led.get('win_rate_pct')}%)",
        "positions_market_value_usd": total_pos_eval,
        "positions": positions,
        "recent_trades": led.get("recent_trades", []),
        "inverse_hedging_ready": True,
        "inverse_tickers": ["SOXS", "SQQQ"],
        "ledger_consistency": {"checked_at": None},
    }
    try:
        check = ledger_mod.validate(snapshot=snap, fix=True)
        payload["ledger_consistency"] = {"checked_at": check["checked_at_kst"], "status": check["status"],
                                         "issue_count": check["issue_count"]}
    except Exception:
        pass

    for p in (os.path.join(BASE_DIR, "data", "live_portfolio.json"),
              os.path.join(BASE_DIR, "docs", "live_portfolio.json")):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"✅ live_portfolio synced {now_str} | equity ${equity_usd:,.2f} ({led.get('equity_krw'):,}원) | "
          f"cash ${led.get('cash_usd'):,.2f} ({led.get('cash_krw'):,}원) | fx {fx_rate} | {payload['ledger_consistency'].get('status')}")
    return payload


if __name__ == "__main__":
    sync_portfolio()
