#!/usr/bin/env python3
"""Final sync: SOXL 4주 $62.20 체결 — 4건 전량 체결."""

import json
import os

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rep(text, old, new, label=""):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"[FAIL] {label}: expected 1, found {n}")
    return text.replace(old, new)


for rel in ("cube.html", "docs/cube.html"):
    p = os.path.join(base, rel)
    h = open(p, encoding="utf-8").read()
    h = rep(h, '<span class="bg-blue-500/20 text-blue-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>\n                  <span class="font-bold text-white font-mono">SOXL 4주</span>',
            '<span class="bg-emerald-500/20 text-emerald-400 font-bold px-2 py-0.5 rounded text-[10px]">체결 완료</span>\n                  <span class="font-bold text-white font-mono">SOXL 4주</span>', label=f"{rel} soxl badge")
    h = rep(h, '<div class="text-[10px] text-amber-300 mt-0.5">※ 개장 +14% 갭업(시가 $64.12) 추격 회피 — 첫 눌림목 대기 지정가</div>',
            '<div class="text-[10px] text-emerald-400 mt-0.5">체결가 $62.20 · 4주 전량 체결 (23:37 KST) — 시가 $64.12 갭업 대비 $1.92 유리한 눌림 진입</div>', label=f"{rel} soxl note")
    h = rep(h, '<span class="text-blue-400 font-bold">미체결</span>\n                <span class="text-[10px] text-slate-500 block">잔여 2주 ATR 트레일링</span>',
            '<span class="text-emerald-400 font-bold">체결</span>\n                <span class="text-[10px] text-slate-500 block">4주 @ $62.20 · 잔여 2주 트레일링</span>', label=f"{rel} soxl state")
    h = rep(h, '"SOXL 4주 $62.20 대기 (주문 174442)"', '"SOXL 4주 $62.20 → 체결 $62.20 (주문 174442)"', label=f"{rel} calendar soxl")
    h = rep(h, '<span class="font-bold">SOXL 4주</span>', '<span class="font-bold">SOXL 4주</span>', label=f"{rel} noop") if False else h
    open(p, "w", encoding="utf-8").write(h)

for rel in ("index.html", "docs/index.html"):
    p = os.path.join(base, rel)
    h = open(p, encoding="utf-8").read()
    h = rep(h, '<span class="font-bold text-blue-300 text-sm">SOXL 4주 · 지정가 대기</span>\n            <span class="text-[11px] font-mono text-blue-400 font-bold">$62.20 (주문 174442)</span>',
            '<span class="font-bold text-blue-300 text-sm">SOXL 4주 · 체결 완료</span>\n            <span class="text-[11px] font-mono text-emerald-400 font-bold">$248.80 체결 (주문 174442)</span>', label=f"{rel} soxl")
    h = rep(h, '- 개장 +14% 갭업 추격 회피, 첫 눌림목 대기 지정가<br>',
            '- 지정가 $62.20 · 4주 전량 체결 (시가 $64.12 대비 눌림 진입)<br>', label=f"{rel} soxl note")
    open(p, "w", encoding="utf-8").write(h)

pos = [
    {"ticker": "SGOV", "qty": 1, "avg_price": 100.53, "market_price": 100.53, "market_value_usd": 100.53, "unrealized_usd": 0.00},
    {"ticker": "TQQQ", "qty": 3, "avg_price": 59.75, "market_price": 59.42, "market_value_usd": 178.26, "unrealized_usd": -0.99},
    {"ticker": "NVDA", "qty": 1, "avg_price": 173.30, "market_price": 173.88, "market_value_usd": 173.88, "unrealized_usd": 0.58},
    {"ticker": "SOXL", "qty": 4, "avg_price": 62.20, "market_price": 62.46, "market_value_usd": 249.84, "unrealized_usd": 1.04},
]
for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    rec = d["daily_records"]["2026-11-11"]
    for o in rec["orders"]:
        if o["ticker"] == "SOXL":
            o.update({"status": "FILLED", "filled_qty": 4, "filled_price": 62.20})
    rec["positions"] = pos
    rec["positions_value_usd"] = 702.51
    rec["unrealized_pnl_usd"] = 0.63
    rec["invested_usd"] = 701.88
    rec["note"] = ("실전 1일차: 23:30 정규장 개장 직후 NH 오픈API 4대 자산 지정가 매수 4건 전량 체결"
                   "(SGOV 1주 $100.53 / TQQQ 3주 $59.75 / NVDA 1주 $173.30 / SOXL 4주 $62.20).")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    es = d["executive_summary"]
    es["live_orders_filled"] = 4
    es["filled_notional_usd"] = 701.88
    es["unrealized_pnl_usd"] = 0.63
    d["account_status"]["funding_status"] = "실전 4건 전량 체결 (SGOV·SOXL·TQQQ·NVDA, 투입 $701.88 · 잔여 주문가능 약 $198)"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/agent_feed.json", "docs/agent_feed.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    for post in d["posts"]:
        if post.get("id") == "post-2334-agent10-live-execution":
            post["badge"] = "⚡ 실전 4건 전량 체결 (투입 $701.88)"
            post["title"] = "[23:37 실전집행] 4대 자산 지정가 매수 4건 전량 체결 — SGOV $100.53·SOXL $62.20·TQQQ $59.75·NVDA $173.30"
            post["content"] = post["content"].replace(
                "2. **SOXL 4주 @ $62.20 (주문 174442)**: 지정가 대기. 시가 $64.12(+14% 갭업) 추격 회피, TP1 $63.57 / TP2 $64.69 / SL $61.27",
                "2. **SOXL 4주 @ $62.20 (주문 174442)**: 체결 완료 $62.20 (시가 $64.12 갭업 대비 눌림 진입) — TP1 $63.57 / TP2 $64.69 / SL $61.27")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("final sync done")
