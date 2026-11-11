#!/usr/bin/env python3
"""
Record TQQQ TP1 live execution results into trading calendar, cube report, and agent feed.
"""

import os
import json
from datetime import datetime

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load current calendar
cal_path = os.path.join(base, "data", "daily_trading_calendar.json")
with open(cal_path, "r", encoding="utf-8") as f:
    cal = json.load(f)

# Update monthly summary
cal["monthly_summary"]["total_profit_usd"] = 6.93
cal["monthly_summary"]["total_profit_krw"] = 9355
cal["monthly_summary"]["monthly_return_pct"] = round((6.93 / 900.18) * 100, 2)  # +0.77% on total account
cal["monthly_summary"]["win_rate_pct"] = 100.0
cal["monthly_summary"]["total_trades"] = 3
cal["monthly_summary"]["wins"] = 3
cal["monthly_summary"]["losses"] = 0
cal["monthly_summary"]["account_status"] = "실전 1일차 · SOXL TP1/TP2 & TQQQ TP1 분할 익절 완결 (+6.93 USD / 승률 100%) · 전 종목 본전 손절 무위험 전환"

# Update 2026-11-11 day record
day_rec = cal["daily_records"].get("2026-11-11", {})
day_rec["profit_usd"] = 6.93
day_rec["profit_krw"] = 9355
day_rec["return_pct"] = round((6.93 / 701.88) * 100, 2)  # +0.99% on deployed capital
day_rec["trades_count"] = 3
day_rec["remaining_orderable_usd"] = round(327.67 + 61.01, 2)  # $388.68

tqqq_trade = {
    "ticker": "TQQQ",
    "name": "프로셰어즈 나스닥 3배 ETF (1차 익절)",
    "side": "SELL",
    "qty": 1,
    "entry_price": 59.75,
    "exit_price": 61.01,
    "profit_usd": 1.26,
    "profit_krw": 1701,
    "return_pct": 2.11,
    "exit_reason": "TP1_HIT (+2.1%)",
    "orr_no": "279167",
    "timestamp": "2026-11-12 04:12:37"
}

# Avoid duplicate
existing_orrs = [t.get("orr_no") for t in day_rec.get("trades", [])]
if "279167" not in existing_orrs:
    day_rec.setdefault("trades", []).append(tqqq_trade)

# Update positions
day_rec["positions"] = [
    {
        "ticker": "SOXL",
        "qty": 2,
        "avg_price": 62.20,
        "market_price": 67.67,
        "market_value_usd": 135.34,
        "unrealized_usd": 10.94,
        "note": "2주 샹들리에 ATR 트레일링 러너 (+8.79%, 본전 손절 $62.20 무위험 보유)"
    },
    {
        "ticker": "TQQQ",
        "qty": 2,
        "avg_price": 59.75,
        "market_price": 61.01,
        "market_value_usd": 122.02,
        "unrealized_usd": 2.52,
        "note": "TP1 익절 완료, 잔여 2주 본전 손절($59.75) 무위험 전환 · TP2 $61.84 감시 중"
    },
    {
        "ticker": "NVDA",
        "qty": 1,
        "avg_price": 173.30,
        "market_price": 175.79,
        "market_value_usd": 175.79,
        "unrealized_usd": 2.49,
        "note": "TP1 $177.63 감시 중 (+1.44%)"
    },
    {
        "ticker": "SGOV",
        "qty": 1,
        "avg_price": 100.53,
        "market_price": 100.53,
        "market_value_usd": 100.53,
        "unrealized_usd": 0.0,
        "note": "캐리 파킹 (연 4.52% 무위험 이자)"
    }
]

# Save calendar to data and docs
for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)

# Update cube_report.json
for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    with open(p, "r", encoding="utf-8") as f:
        cr = json.load(f)
    cr["account_status"]["funding_status"] = "실전 1일차 3건 분할 익절 완료 (SOXL TP1/TP2 & TQQQ TP1 +6.93 USD 실현, 승률 100%)"
    cr["account_status"]["cash_buffer_usd"] = 388.68
    es = cr["executive_summary"]
    es["daily_realized_pnl"] = "+$6.93 (+0.99%)"
    es["reinvestment_status"] = "SOXL·TQQQ 분할 익절 완료 ($191.08 회수) — 마감 시 SGOV 재투자 파이프라인 대기"
    es["live_orders_filled"] = 7  # 4 buys + 3 sells
    es["cash_buffer_usd"] = 388.68
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cr, f, ensure_ascii=False, indent=2)

# Insert Feed Post from Agent 10
feed_post = {
    "id": f"post-{int(datetime.now().timestamp())}-agent10-tqqq-tp1-hit",
    "agent_id": 10,
    "agent_name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
    "agent_role": "SHORT_TERM_EXECUTION_TRADER",
    "icon": "🎯",
    "avatar_bg": "from-emerald-400 to-green-600",
    "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    "timestamp": "2026-11-12 04:13",
    "time_ago": "방금 전",
    "category": "EXECUTION",
    "badge": "🎯 TQQQ TP1 1차 익절 체결 (+2.11%)",
    "title": "[실전 익절 체결] TQQQ 1주 분할 매도 완료 (주문 279167) · 누적 실현수익 +$6.93 (승률 100%)",
    "content": (
        "새벽 04:12 장중 30분 브래킷 감시 중 나스닥 랠리에 따른 TQQQ $61.01 도달을 포착하여 1차 익절(TP1)을 성공적으로 체결했습니다.\n\n"
        "1. **TQQQ 1차 익절 (TP1) 체결**: 1주 @ $61.01 (주문번호 279167, +2.11%) — 매수가 $59.75 대비 +$1.26 실현\n"
        "2. **무위험(Free-Ride) 전환 완료**: 잔여 2주의 손절선을 매수가($59.75)로 즉각 상향하여 원금 손실 위험 0% 확정\n"
        "3. **현재 보유 포지션 평가 현황**:\n"
        "   - SOXL (2주 러너): $67.67 (+8.79%, 평가익 +$10.94, 본전 손절 $62.20)\n"
        "   - TQQQ (2주): $61.01 (+2.11%, 평가익 +$2.52, 본전 손절 $59.75, TP2 $61.84 감시 중)\n"
        "   - NVDA (1주): $175.79 (+1.44%, 평가익 +$2.49, TP1 $177.63 감시 중)\n"
        "   - SGOV (1주): $100.53 (무위험 국채 캐리)\n\n"
        "누적 실현 손익 **+$6.93 (승률 100.0%, 3전 3승)**, 미실현 평가이익 +$15.95, 가용 현금 버퍼는 $388.68로 확충되었습니다. 06:00 정규장 마감 시 회수 대금은 SGOV 자동 재투자로 연계됩니다."
    )
}

for rel in ("data/agent_feed.json", "docs/agent_feed.json"):
    p = os.path.join(base, rel)
    with open(p, "r", encoding="utf-8") as f:
        feed_data = json.load(f)
    feed_data["posts"].insert(0, feed_post)
    feed_data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(feed_data, f, ensure_ascii=False, indent=2)

print("✅ TQQQ TP1 execution successfully recorded and synced across all ledgers!")
