#!/usr/bin/env python3
"""Third sync: NVDA 1주 $173.30 체결 반영."""

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
    h = rep(h, '<span class="bg-indigo-500/20 text-indigo-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>\n                  <span class="font-bold text-white font-mono">NVDA 1주</span>',
            '<span class="bg-emerald-500/20 text-emerald-400 font-bold px-2 py-0.5 rounded text-[10px]">체결 완료</span>\n                  <span class="font-bold text-white font-mono">NVDA 1주</span>', label=f"{rel} nvda badge")
    h = rep(h, '<div class="text-[10px] text-slate-400 mt-0.5">실시간 $173.61 — 체결 임박</div>',
            '<div class="text-[10px] text-emerald-400 mt-0.5">체결가 $173.30 · 1주 체결 (23:39 KST)</div>', label=f"{rel} nvda note")
    h = rep(h, '<span class="text-indigo-400 font-bold">미체결</span>\n                <span class="text-[10px] text-slate-500 block">지정가 173.30</span>',
            '<span class="text-emerald-400 font-bold">체결</span>\n                <span class="text-[10px] text-slate-500 block">1주 @ $173.30</span>', label=f"{rel} nvda state")
    h = rep(h, '"NVDA 1주 $173.30 대기 (주문 174411)"', '"NVDA 1주 $173.30 → 체결 $173.30 (주문 174411)"', label=f"{rel} calendar nvda")
    open(p, "w", encoding="utf-8").write(h)

for rel in ("index.html", "docs/index.html"):
    p = os.path.join(base, rel)
    h = open(p, encoding="utf-8").read()
    h = rep(h, '<span class="font-bold text-indigo-300 text-sm">NVDA 1주 · 지정가 대기</span>\n            <span class="text-[11px] font-mono text-indigo-400 font-bold">$173.30 (주문 174411)</span>',
            '<span class="font-bold text-indigo-300 text-sm">NVDA 1주 · 체결 완료</span>\n            <span class="text-[11px] font-mono text-emerald-400 font-bold">$173.30 체결 (주문 174411)</span>', label=f"{rel} nvda")
    h = rep(h, '- 모멘텀 지지선 근접 지정가, 실시간 $173.61<br>',
            '- 지정가 $173.30 · 1주 체결 (23:39 KST)<br>', label=f"{rel} nvda note")
    open(p, "w", encoding="utf-8").write(h)

pos = [
    {"ticker": "SGOV", "qty": 1, "avg_price": 100.53, "market_price": 100.53, "market_value_usd": 100.53},
    {"ticker": "TQQQ", "qty": 3, "avg_price": 59.75, "market_price": 59.46, "market_value_usd": 178.38},
    {"ticker": "NVDA", "qty": 1, "avg_price": 173.30, "market_price": 173.59, "market_value_usd": 173.59},
]
for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    rec = d["daily_records"]["2026-11-11"]
    for o in rec["orders"]:
        if o["ticker"] == "NVDA":
            o.update({"status": "FILLED", "filled_qty": 1, "filled_price": 173.30})
    rec["positions"] = pos
    rec["positions_value_usd"] = 452.50
    rec["unrealized_pnl_usd"] = -0.58
    rec["invested_usd"] = 453.08
    rec["note"] = ("실전 1일차: 23:30 정규장 개장 직후 NH 오픈API 4대 자산 지정가 매수 4건 발주(응답코드 00171). "
                   "SGOV 1주 $100.53·TQQQ 3주 $59.75·NVDA 1주 $173.30 체결, SOXL 4주 $62.20 지정가 대기.")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    es = d["executive_summary"]
    es["live_orders_filled"] = 3
    es["filled_notional_usd"] = 453.08
    es["unrealized_pnl_usd"] = -0.58
    d["account_status"]["funding_status"] = "실전 4건 발주 완료 (SGOV·TQQQ·NVDA 체결 · SOXL 지정가 대기)"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/agent_feed.json", "docs/agent_feed.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    for post in d["posts"]:
        if post.get("id") == "post-2334-agent10-live-execution":
            post["badge"] = "⚡ 실전 4건 발주 · SGOV·TQQQ·NVDA 체결"
            post["title"] = "[23:39 실전집행] NH 오픈API 4대 자산 매수 4건 접수 · SGOV $100.53·TQQQ $59.75·NVDA $173.30 체결, SOXL 4주 대기"
            post["content"] = post["content"].replace(
                "4. **NVDA 1주 @ $173.30 (주문 174411)**: 지정가 대기(실시간 $173.61). TP1 $177.63 / SL $170.70",
                "4. **NVDA 1주 @ $173.30 (주문 174411)**: 체결 완료 $173.30 — TP1 $177.63 / SL $170.70")
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("third sync done")
