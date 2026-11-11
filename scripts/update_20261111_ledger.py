#!/usr/bin/env python3
"""One-off ledger update: record 2026-11-11 23:34 KST live order submissions and fills."""

import json
import os
import shutil

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ORDERS = [
    {"ticker": "SGOV", "name": "아이셰어즈 0-3개월 미국 국채 ETF", "side": "BUY", "qty": 1,
     "limit_price": 100.55, "orr_no": "174377", "rsp_cd": "00171", "status": "FILLED",
     "filled_qty": 1, "filled_price": 100.53, "notional_usd": 100.55,
     "bracket": "캐리 파킹 (연 4.52% 무위험 이자)"},
    {"ticker": "TQQQ", "name": "프로셰어즈 QQQ 3배 ETF", "side": "BUY", "qty": 3,
     "limit_price": 59.75, "orr_no": "174395", "rsp_cd": "00171", "status": "PENDING",
     "filled_qty": 0, "filled_price": 0.0, "notional_usd": 179.25,
     "bracket": "TP1 60.95 (+2.0%, 1주) / TP2 61.84 (+3.5%, 1주) / SL 58.85 (-1.5%)"},
    {"ticker": "NVDA", "name": "엔비디아", "side": "BUY", "qty": 1,
     "limit_price": 173.30, "orr_no": "174411", "rsp_cd": "00171", "status": "PENDING",
     "filled_qty": 0, "filled_price": 0.0, "notional_usd": 173.30,
     "bracket": "TP1 177.63 (+2.5%) / SL 170.70 (-1.5%)"},
    {"ticker": "SOXL", "name": "디렉시온 반도체 3배 ETF", "side": "BUY", "qty": 4,
     "limit_price": 62.20, "orr_no": "174442", "rsp_cd": "00171", "status": "PENDING",
     "filled_qty": 0, "filled_price": 0.0, "notional_usd": 248.80,
     "bracket": "TP1 63.57 (+2.2%, 1주) / TP2 64.69 (+4.0%, 1주) / 잔여 2주 ATR 트레일링 / SL 61.27 (-1.5%)"},
]

REFERENCE = {"SOXL": 63.85, "TQQQ": 59.96, "NVDA": 173.61, "SGOV": 100.52}

day_record = {
    "day": 11,
    "is_today": True,
    "is_trading_day": True,
    "balance_krw": 1209215,
    "orderable_usd": 900.18,
    "reserved_usd": 702.58,
    "remaining_orderable_usd": 197.60,
    "equity_usd": 900.18,
    "profit_usd": 0.00,
    "profit_krw": 0,
    "return_pct": 0.00,
    "trades_count": 0,
    "trades": [],
    "orders": ORDERS,
    "positions": [{"ticker": "SGOV", "qty": 1, "avg_price": 100.53, "market_price": 100.52,
                   "market_value_usd": 100.52}],
    "reference_prices_at_open": REFERENCE,
    "note": ("실전 1일차: 23:30 정규장 개장 직후 NH 오픈API로 4대 자산 지정가 매수 4건 발주"
             "(응답코드 00171). SGOV 1주 체결가 $100.53, SOXL·TQQQ·NVDA 지정가 대기."),
    "execution_notes": [
        "원화 증거금(wtm_cur_knd_cd=2) 주문은 주문가능금액 부족(16156)으로 거부 → USD 증거금(1)으로 발주해 NH 자동환전 적용",
        "계획서 지정가(SOXL 29.80 / TQQQ 78.50 / NVDA 147.20)는 개장 시세와 큰 괴리 → 실시간가 기준으로 수량 재산정(총 $709.41 계획, 실제 주문증거금 $702.58)",
        "SOXL은 시가 +14% 갭업(전일 56.06 → 시가 64.12)으로 추격 회피, $62.20 눌림 대기 지정가",
    ],
}

for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    d["monthly_summary"].update({
        "current_balance_krw": 1209215,
        "current_balance_usd": 900.18,
        "account_status": "실전 1일차 · 23:34 4대 자산 4건 발주 접수 (SGOV 1주 체결 / 3건 지정가 대기)",
    })
    d["daily_records"]["2026-11-11"] = day_record
    for k in ("2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"):
        d["daily_records"][k] = {"day": int(k[-2:]), "is_trading_day": k != "2026-11-14" and k != "2026-11-15",
                                 "future": True}
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    d["account_status"].update({
        "usd_deposit": 900.18,
        "total_equity_usd": 900.18,
        "last_inquiry_time": "2026-11-11 23:34:10",
        "funding_status": "실전 4건 발주 완료 (SGOV 체결 · 3건 지정가 대기, 잔여 주문가능 $197.60)",
    })
    es = d["executive_summary"]
    es["daily_realized_pnl"] = "+$0.00 (0.00%)"
    es["reinvestment_status"] = "익절 미발생 — 실현 이익 발생 시 100% SGOV 자동 파킹 파이프라인 대기"
    es["live_orders_submitted"] = len(ORDERS)
    es["live_orders_filled"] = 1
    es["cash_buffer_usd"] = 197.60
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# Feed post: real execution report from Agent 10
post = {
    "id": "post-2334-agent10-live-execution",
    "agent_id": 10,
    "agent_name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
    "agent_role": "SHORT_TERM_EXECUTION_TRADER",
    "icon": "⚡",
    "avatar_bg": "from-yellow-400 to-amber-600",
    "badge_color": "bg-amber-500/10 text-amber-400 border-amber-500/20",
    "timestamp": "2026-11-11 23:34",
    "time_ago": "방금 전",
    "category": "EXECUTION",
    "badge": "⚡ 실전 4건 발주 접수 (SGOV 체결)",
    "title": "[23:34 실전집행] NH 오픈API 4대 자산 지정가 매수 4건 접수 (rsp_cd 00171) · SGOV 1주 $100.53 체결",
    "content": (
        "미국 정규장 개장(23:30) 직후 NH투자증권 실전 계좌 202-05-134849로 4건을 발주했습니다.\n\n"
        "1. **SGOV 1주 @ $100.55 (주문 174377)**: 체결 완료 $100.53 — 연 4.52% 국채 캐리 파킹\n"
        "2. **SOXL 4주 @ $62.20 (주문 174442)**: 지정가 대기. 시가 $64.12(+14% 갭업) 추격 회피, TP1 $63.57 / TP2 $64.69 / SL $61.27\n"
        "3. **TQQQ 3주 @ $59.75 (주문 174395)**: 지정가 대기(실시간 $59.96). TP1 $60.95 / TP2 $61.84 / SL $58.85\n"
        "4. **NVDA 1주 @ $173.30 (주문 174411)**: 지정가 대기(실시간 $173.61). TP1 $177.63 / SL $170.70\n\n"
        "주문증거금 $702.58 집행, 주문가능 잔여 $197.60(버퍼). 원화 증거금 주문은 16156으로 거부되어 USD 증거금(자동환전) 경로로 발주했습니다."
    ),
}

for rel in ("data/agent_feed.json", "docs/agent_feed.json"):
    p = os.path.join(base, rel)
    d = json.load(open(p, encoding="utf-8"))
    d["posts"] = [x for x in d["posts"] if x.get("id") != post["id"]]
    d["posts"].insert(0, post)
    d["last_updated"] = "2026-11-11 23:34:10"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("ledgers updated")
