#!/usr/bin/env python3
"""Second sync: TQQQ 3주 전량 체결 반영."""

import json
import os

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rep(text, old, new, label=""):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"[FAIL] {label}: expected 1, found {n}")
    return text.replace(old, new)


# cube.html (root + docs)
for rel in ("cube.html", "docs/cube.html"):
    p = os.path.join(base, rel)
    h = open(p, encoding="utf-8").read()
    h = rep(h, '<span class="bg-purple-500/20 text-purple-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>\n                  <span class="font-bold text-white font-mono">TQQQ 3주</span>',
            '<span class="bg-emerald-500/20 text-emerald-400 font-bold px-2 py-0.5 rounded text-[10px]">체결 완료</span>\n                  <span class="font-bold text-white font-mono">TQQQ 3주</span>', label=f"{rel} tqqq badge")
    h = rep(h, '<div class="text-[10px] text-slate-400 mt-0.5">실시간 $59.96 근접 — 체결 임박</div>',
            '<div class="text-[10px] text-emerald-400 mt-0.5">체결가 $59.75 · 3주 전량 체결 (23:38 KST)</div>', label=f"{rel} tqqq note")
    h = rep(h, '<span class="text-purple-400 font-bold">미체결</span>\n                <span class="text-[10px] text-slate-500 block">지정가 59.75</span>',
            '<span class="text-emerald-400 font-bold">체결</span>\n                <span class="text-[10px] text-slate-500 block">3주 @ $59.75</span>', label=f"{rel} tqqq state")
    h = rep(h, '"TQQQ 3주 $59.75 대기 (주문 174395)"', '"TQQQ 3주 $59.75 → 체결 $59.75 (주문 174395)"', label=f"{rel} calendar tqqq")
    open(p, "w", encoding="utf-8").write(h)

# index.html (root + docs)
for rel in ("index.html", "docs/index.html"):
    p = os.path.join(base, rel)
    h = open(p, encoding="utf-8").read()
    h = rep(h, '<span class="font-bold text-purple-300 text-sm">TQQQ 3주 · 지정가 대기</span>\n            <span class="text-[11px] font-mono text-purple-400 font-bold">$59.75 (주문 174395)</span>',
            '<span class="font-bold text-purple-300 text-sm">TQQQ 3주 · 체결 완료</span>\n            <span class="text-[11px] font-mono text-emerald-400 font-bold">$179.25 체결 (주문 174395)</span>', label=f"{rel} tqqq")
    h = rep(h, '- ORB 돌파 지정가, 실시간 $59.96 근접<br>',
            '- 지정가 $59.75 · 3주 전량 체결 (23:38 KST)<br>', label=f"{rel} tqqq note")
    open(p, "w", encoding="utf-8").write(h)

# JSON ledgers
pos = [
    {"ticker": "SGOV", "qty": 1, "avg_price": 100.53, "market_price": 100.53, "market_value_usd": 100.53},
    {"ticker": "TQQQ", "qty": 3, "avg_price": 59.75, "market_price": 59.46, "market_value_usd": 178.38},
]
for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    rec = d["daily_records"]["2026-11-11"]
    for o in rec["orders"]:
        if o["ticker"] == "TQQQ":
            o.update({"status": "FILLED", "filled_qty": 3, "filled_price": 59.75})
    rec["positions"] = pos
    rec["positions_value_usd"] = 278.91
    rec["unrealized_pnl_usd"] = -0.87
    rec["note"] = ("실전 1일차: 23:30 정규장 개장 직후 NH 오픈API 4대 자산 지정가 매수 4건 발주(응답코드 00171). "
                   "SGOV 1주 $100.53·TQQQ 3주 $59.75 체결, SOXL 4주·NVDA 1주 지정가 대기.")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    es = d["executive_summary"]
    es["live_orders_filled"] = 2
    es["filled_notional_usd"] = 279.78
    es["unrealized_pnl_usd"] = -0.87
    d["account_status"]["funding_status"] = "실전 4건 발주 완료 (SGOV·TQQQ 체결 · SOXL·NVDA 지정가 대기, 잔여 주문가능 $197.60)"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/agent_feed.json", "docs/agent_feed.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    for post in d["posts"]:
        if post.get("id") == "post-2334-agent10-live-execution":
            post["badge"] = "⚡ 실전 4건 발주 · SGOV·TQQQ 체결"
            post["title"] = "[23:38 실전집행] NH 오픈API 4대 자산 지정가 매수 4건 접수 (rsp_cd 00171) · SGOV 1주 $100.53·TQQQ 3주 $59.75 체결"
            post["content"] = post["content"].replace(
                "3. **TQQQ 3주 @ $59.75 (주문 174395)**: 지정가 대기(실시간 $59.96). TP1 $60.95 / TP2 $61.84 / SL $58.85",
                "3. **TQQQ 3주 @ $59.75 (주문 174395)**: 체결 완료 $59.75 — TP1 $60.95 / TP2 $61.84 / SL $58.85")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("second sync done")
