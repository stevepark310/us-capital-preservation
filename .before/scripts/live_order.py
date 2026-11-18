"""
Live US-stock buy order helper for NH OpenAPI (NHPLUG).
Places a limit buy on the global stock order endpoint and records the raw response.

Usage:
  python scripts/live_order.py --ticker SGOV --qty 1 --price 100.55 --currency krw
  python scripts/live_order.py --ticker SOXL --qty 4 --price 63.50 --currency krw --note "re-sized"

wtm_cur_knd_cd: "1" = 해당통화(USD), "2" = 원화 (broker auto-converts KRW margin).
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from execution.nh_trader import NHBroker  # noqa: E402

ORDERS_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "live_order_responses.jsonl"
)


def place_buy(ticker: str, qty: int, price: float, currency: str = "krw", note: str = "") -> dict:
    broker = NHBroker(dry_run=False)
    payload = {
        "act_no": broker.act_no,
        "fc_sec_trd_nat_cd": "200",          # USA
        "iem_cd": ticker,
        "orr_qty": qty,
        "fc_orr_uit_pr": round(float(price), 2),
        "ahi_nmn_pr_tp_cd": "00",            # 00: 지정가
        "wtm_cur_knd_cd": "2" if currency == "krw" else "1",
    }
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "qty": qty,
        "limit_price": round(float(price), 2),
        "currency": currency,
        "note": note,
        "payload": payload,
    }
    try:
        res = broker._call_api("/gbstock/order/v1/buy", payload)
        record["response"] = res
        record["status"] = "SUBMITTED" if res.get("rsp_cd") in ("00171", "00166", "00000", "0000") else "REJECTED"
    except Exception as e:
        record["status"] = "ERROR"
        record["error"] = f"{type(e).__name__}: {e}"
    os.makedirs(os.path.dirname(ORDERS_LOG), exist_ok=True)
    with open(ORDERS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--currency", choices=["krw", "usd"], default="krw")
    p.add_argument("--note", default="")
    a = p.parse_args()
    place_buy(a.ticker, a.qty, a.price, a.currency, a.note)
