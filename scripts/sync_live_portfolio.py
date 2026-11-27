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

LIVE_PATH = os.path.join(BASE_DIR, "data", "live_portfolio.json")
LIVE_MIRROR = os.path.join(BASE_DIR, "docs", "live_portfolio.json")
SNAPSHOT_PATH = os.path.join(BASE_DIR, "data", "nh_account_snapshot.json")


# --------------------------------------------------------------- safety
# 2026-11-28 04:02 KST 사고: 원장 파일 읽기가 한 번 실패하자 이 동기화가 `ledger.summary()`의
# 빈 기본값(positions=[], account_sync=null, cash_deposit_usd=null)을 그대로 정본에 덮어써
# 실측 보유(SGOV 1주, $100.68)가 사라졌다. 동기화는 '파생 화면'을 다시 쓰는 단계이지
# 보유를 지우는 단계가 아니다 — 원본을 못 읽었거나, 체결 근거 없이 보유가 사라지면 쓰지 않는다.
class LedgerReadError(RuntimeError):
    """원본 원장을 읽지 못해 덮어쓰기를 거부했다."""


class PositionLossError(RuntimeError):
    """체결 근거 없이 보유가 사라져 덮어쓰기를 거부했다."""


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return (doc if isinstance(doc, dict) else {}), True
    except Exception:
        return {}, False


def _atomic_write(path: str, payload: dict) -> None:
    """반쯤 쓰인 JSON이 남지 않게 tmp → os.replace(원자적 교체)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _previous_live():
    """직전 원장 원본 — data 정본 → docs 사본 순. (문서, 정본읽기가능, 사용한출처)"""
    doc, ok = _read_json(LIVE_PATH)
    if ok and doc:
        return doc, True, "data"
    mirror, mok = _read_json(LIVE_MIRROR)
    if mok and (mirror.get("positions") or mirror.get("current_total_equity_usd")):
        return mirror, False, "docs"
    return doc, ok, None


def _sell_recorded(ticker: str, prev: dict) -> bool:
    """이 종목의 보유가 사라진 근거 — 확인된 매도 체결/정산/브로커 스냅샷."""
    ticker = str(ticker).upper()
    for f in (prev.get("fills_confirmed") or []):
        if isinstance(f, dict) and str(f.get("ticker", "")).upper() == ticker \
                and str(f.get("side", "")).upper() == "SELL":
            return True
    try:
        cal = ledger_mod.load_calendar()
    except Exception:
        cal = {}
    for rec in (cal.get("daily_records") or {}).values():
        for t in (rec.get("trades") or []):
            if isinstance(t, dict) and str(t.get("ticker", "")).upper() == ticker \
                    and str(t.get("side", "")).upper() == "SELL":
                return True
    snap, ok = _read_json(SNAPSHOT_PATH)
    if ok and snap.get("found"):
        positions = snap.get("positions") or {}
        prev_date = str(prev.get("last_synced") or "")[:10]
        snap_date = str(snap.get("fetched_at_kst") or "")[:10]
        if isinstance(positions, dict) and ticker not in positions \
                and prev_date and snap_date and snap_date >= prev_date:
            return True
    return False


def sync_portfolio() -> dict:
    prev, prev_readable, prev_source = _previous_live()
    data_exists = os.path.exists(LIVE_PATH) and os.path.getsize(LIVE_PATH) > 0
    if prev_source == "docs":
        # 정본이 깨졌다 — 사본(docs)은 마지막으로 정상 발행된 파생값이다. 그것으로 정본을 되살린다.
        _atomic_write(LIVE_PATH, prev)
        print("[sync_live_portfolio] data 원장 읽기 실패 → docs 사본으로 정본 복구")
    elif data_exists and not prev_readable:
        raise LedgerReadError(
            f"원장 원본을 읽을 수 없어 덮어쓰기를 중단했습니다: {LIVE_PATH} "
            "(보유·현금은 그대로 보존. 원본 확인 후 재실행)")

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
        # 브로커 표시가(NH 종가)가 있으면 그것이 계좌 평가 기준이다. Yahoo 일봉이 미확정(NaN)이거나
        # 시간외 호가와 섞여 원장과 어긋나는 경우가 있어 실측 잔고를 우선한다.
        broker_mark = p.get("broker_mark_price")
        px = broker_mark or q.get("intraday_price") or q.get("last") or p.get("market_price") or p.get("avg_price")
        avg = float(p.get("avg_price") or 0)
        qty = int(p.get("qty") or 0)
        unreal = round((float(px) - avg) * qty, 2) if avg and px else 0.0
        positions.append({
            "ticker": ticker,
            "name": p.get("name"),
            "qty": qty,
            "avg_price": round(avg, 2),
            "market_price": round(float(px), 2) if px else None,
            "price_as_of": p.get("price_as_of") or (q.get("last_bar_date") if q else None),
            "broker_mark_price": broker_mark,
            "quote_last": q.get("last"),
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

    # 현금성 자산 = 외화예수금(fc_dca) + 미결제 정산금(fc_ny_stl_xcl_amt).
    # 미결제 정산금을 빼고 총자산과 비교하면 '예수금+평가≠총자산' 차이가 매번 생긴다
    # (11-18 06:01 스냅샷의 -$9.15가 정확히 이 항목이었다).
    pending_usd = float(led.get("cash_pending_settlement_usd") or 0.0)
    cash_total_usd = round(float(led.get("cash_usd") or 0.0) + pending_usd, 2)

    payload = {
        "api_status": "CONNECTED",
        "broker": "NH투자증권 나무 (Open API)",
        "account_no": led.get("account_no"),
        "last_synced": now_str,
        "session_phase": clock.get("phase"),
        "price_basis": ("정규장 실시간" if clock.get("phase") == "REGULAR" else
                        "마지막 종가 기준 (휴장·시간외)"),
        "price_source": "NH Open API 종가(fc_sec_end_pr) 우선, 없으면 Yahoo 마지막 시세",
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
        # 파생값이 비었다고 이미 확인된 값을 null로 되돌리지 않는다(2026-11-28 재발 방지).
        "cash_deposit_usd": (led.get("cash_deposit_usd") if led.get("cash_deposit_usd") is not None
                             else prev.get("cash_deposit_usd")),
        "cash_pending_settlement_usd": pending_usd,
        "cash_pending_settlement_krw": int(round(pending_usd * fx_rate)),
        "cash_total_usd": cash_total_usd,
        "cash_total_krw": int(round(cash_total_usd * fx_rate)),
        "cash_total_pct": round(cash_total_usd / equity_usd * 100, 1) if equity_usd else None,
        "account_sync": led.get("account_sync") or prev.get("account_sync"),
        "fills_confirmed": (led.get("fills_confirmed") if led.get("fills_confirmed") is not None
                            else (prev.get("fills_confirmed") or [])),
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
    # 보유 소실 가드 — 체결 근거 없이 직전 보유가 사라지면 쓰지 않는다.
    prev_pos = {str(p.get("ticker")).upper(): int(p.get("qty") or 0)
                for p in (prev.get("positions") or []) if isinstance(p, dict) and p.get("ticker")}
    now_pos = {str(p["ticker"]).upper() for p in positions}
    lost = [t for t, q in prev_pos.items() if q > 0 and t not in now_pos]
    unexplained = [t for t in lost if not _sell_recorded(t, prev)]
    if unexplained:
        raise PositionLossError(
            f"체결 기록 없는 보유 소실({', '.join(unexplained)}) — 덮어쓰기 중단. "
            f"직전 보유 {prev_pos} / 이번 계산 {sorted(now_pos)}. 원장 원본 확인 필요")

    try:
        check = ledger_mod.validate(snapshot=snap, fix=True)
        payload["ledger_consistency"] = {"checked_at": check["checked_at_kst"], "status": check["status"],
                                         "issue_count": check["issue_count"]}
    except Exception:
        pass

    for p in (LIVE_PATH, LIVE_MIRROR):
        _atomic_write(p, payload)

    print(f"✅ live_portfolio synced {now_str} | equity ${equity_usd:,.2f} ({led.get('equity_krw'):,}원) | "
          f"cash ${led.get('cash_usd'):,.2f} ({led.get('cash_krw'):,}원) | fx {fx_rate} | {payload['ledger_consistency'].get('status')}")
    return payload


if __name__ == "__main__":
    try:
        sync_portfolio()
    except (LedgerReadError, PositionLossError) as e:
        print(f"❌ live_portfolio sync 중단 — {e}")
        sys.exit(2)
