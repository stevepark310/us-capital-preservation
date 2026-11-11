#!/usr/bin/env python3
"""
Intraday Bracket Monitor & Auto-Execution Engine for US Trading
Checks live market quotes against Smart Exit Brackets.
If Take-Profit or Stop-Loss is hit, automatically places sell order via NH OpenAPI.
"""

import os
import sys
import json
from datetime import datetime
import yfinance as yf

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

from execution.nh_trader import NHBroker

BRACKETS_FILE = os.path.join(base_dir, "data", "active_brackets.json")
ORDERS_LOG = os.path.join(base_dir, "data", "live_order_responses.jsonl")

DEFAULT_BRACKETS = {
    "SOXL": {
        "bought_qty": 4,
        "entry_price": 62.20,
        "tp1": 63.57,
        "tp1_qty": 1,
        "tp1_filled": False,
        "tp2": 64.69,
        "tp2_qty": 1,
        "tp2_filled": False,
        "sl": 61.27,
        "runner_qty": 2
    },
    "TQQQ": {
        "bought_qty": 3,
        "entry_price": 59.75,
        "tp1": 60.95,
        "tp1_qty": 1,
        "tp1_filled": False,
        "tp2": 61.84,
        "tp2_qty": 1,
        "tp2_filled": False,
        "sl": 58.85,
        "runner_qty": 1
    },
    "NVDA": {
        "bought_qty": 1,
        "entry_price": 173.30,
        "tp1": 177.63,
        "tp1_qty": 1,
        "tp1_filled": False,
        "sl": 170.70,
        "runner_qty": 0
    },
    "SGOV": {
        "bought_qty": 1,
        "entry_price": 100.53,
        "note": "CARRY_PARKING"
    }
}

def load_brackets():
    if os.path.exists(BRACKETS_FILE):
        try:
            with open(BRACKETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_BRACKETS

def save_brackets(b):
    os.makedirs(os.path.dirname(BRACKETS_FILE), exist_ok=True)
    with open(BRACKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)

def place_sell(broker, ticker, qty, price, reason):
    payload = {
        "act_no": broker.act_no,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": ticker,
        "orr_qty": qty,
        "fc_orr_uit_pr": round(float(price), 2),
        "ahi_nmn_pr_tp_cd": "00",
        "wtm_cur_knd_cd": "1"
    }
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "side": "SELL",
        "ticker": ticker,
        "qty": qty,
        "price": round(float(price), 2),
        "reason": reason,
        "payload": payload
    }
    try:
        res = broker._call_api("/gbstock/order/v1/sell", payload)
        record["response"] = res
        record["status"] = "SUBMITTED" if res.get("rsp_cd") in ("00171", "00166", "00000") else "REJECTED"
    except Exception as e:
        record["status"] = "ERROR"
        record["error"] = str(e)
    with open(ORDERS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[SELL EXECUTED] {ticker} {qty}x @ ${price:.2f} ({reason}) -> {record['status']}")
    return record

def run_check():
    brackets = load_brackets()
    tickers = ["SOXL", "TQQQ", "NVDA", "SGOV"]
    df = yf.download(tickers, period="1d", interval="1m", progress=False)
    if df.empty or "Close" not in df:
        print("[Warning] Could not fetch real-time quotes.")
        return []

    broker = NHBroker(dry_run=False)
    actions_taken = []

    for sym in ["SOXL", "TQQQ", "NVDA"]:
        cfg = brackets.get(sym, {})
        cur_price = float(df["Close"][sym].dropna().iloc[-1])
        print(f"[{sym}] Current: ${cur_price:.2f} | Entry: ${cfg['entry_price']:.2f}")

        # 1. Stop-Loss Check
        if cur_price <= cfg["sl"] and not cfg.get("sl_triggered", False):
            remaining_qty = cfg["bought_qty"] - (cfg.get("tp1_qty", 0) if cfg.get("tp1_filled") else 0) - (cfg.get("tp2_qty", 0) if cfg.get("tp2_filled") else 0)
            if remaining_qty > 0:
                rec = place_sell(broker, sym, remaining_qty, cur_price, f"STOP_LOSS_HIT (${cur_price:.2f} <= ${cfg['sl']:.2f})")
                cfg["sl_triggered"] = True
                actions_taken.append(rec)

        # 2. TP1 Check
        elif cur_price >= cfg["tp1"] and not cfg.get("tp1_filled", False):
            rec = place_sell(broker, sym, cfg["tp1_qty"], cur_price, f"TP1_HIT (+{((cur_price/cfg['entry_price'])-1)*100:.1f}%)")
            cfg["tp1_filled"] = True
            # Raise SL to breakeven
            cfg["sl"] = cfg["entry_price"]
            print(f"[{sym}] Breakeven stop active! SL raised to ${cfg['sl']:.2f}")
            actions_taken.append(rec)

        # 3. TP2 Check
        elif "tp2" in cfg and cur_price >= cfg["tp2"] and not cfg.get("tp2_filled", False):
            rec = place_sell(broker, sym, cfg["tp2_qty"], cur_price, f"TP2_HIT (+{((cur_price/cfg['entry_price'])-1)*100:.1f}%)")
            cfg["tp2_filled"] = True
            actions_taken.append(rec)

    save_brackets(brackets)
    return actions_taken

if __name__ == "__main__":
    actions = run_check()
    print(f"Total actions taken: {len(actions)}")
