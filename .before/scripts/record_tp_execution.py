#!/usr/bin/env python3
"""
Record SOXL TP1 and TP2 live execution results into trading calendar, cube report, and agent feed.
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
cal["monthly_summary"]["total_profit_usd"] = 5.67
cal["monthly_summary"]["total_profit_krw"] = 7654
cal["monthly_summary"]["monthly_return_pct"] = round((5.67 / 900.18) * 100, 2)  # +0.63% on total account
cal["monthly_summary"]["win_rate_pct"] = 100.0
cal["monthly_summary"]["total_trades"] = 2
cal["monthly_summary"]["wins"] = 2
cal["monthly_summary"]["losses"] = 0
cal["monthly_summary"]["account_status"] = "실전 1일차 · SOXL TP1/TP2 분할 익절 완료 (+5.67 USD / 승률 100%) · 본전 손절 무위험 전환"

# Update 2026-11-11 day record
day_rec = cal["daily_records"].get("2026-11-11", {})
day_rec["profit_usd"] = 5.67
day_rec["profit_krw"] = 7654
day_rec["return_pct"] = round((5.67 / 701.88) * 100, 2)  # +0.81% on deployed capital
day_rec["trades_count"] = 2
day_rec["remaining_orderable_usd"] = round(197.60 + 130.07, 2)  # $327.67
day_rec["trades"] = [
    {
        "ticker": "SOXL",
        "name": "디렉시온 반도체 3배 ETF (1차 익절)",
        "side": "SELL",
        "qty": 1,
        "entry_price": 62.20,
        "exit_price": 65.08,
        "profit_usd": 2.88,
        "profit_krw": 3888,
        "return_pct": 4.63,
        "exit_reason": "TP1_HIT (+4.6%)",
        "orr_no": "219833",
        "timestamp": "2026-11-12 00:36:52"
    },
    {
        "ticker": "SOXL",
        "name": "디렉시온 반도체 3배 ETF (2차 익절)",
        "side": "SELL",
        "qty": 1,
        "entry_price": 62.20,
        "exit_price": 64.99,
        "profit_usd": 2.79,
        "profit_krw": 3766,
        "return_pct": 4.49,
        "exit_reason": "TP2_HIT (+4.5%)",
        "orr_no": "222744",
        "timestamp": "2026-11-12 00:42:41"
    }
]

# Update positions
day_rec["positions"] = [
    {
        "ticker": "SOXL",
        "qty": 2,
        "avg_price": 62.20,
        "market_price": 64.99,
        "market_value_usd": 129.98,
        "unrealized_usd": 5.58,
        "note": "2주 샹들리에 ATR 트레일링 러너 (본전 손절 $62.20 무위험 보유)"
    },
    {
        "ticker": "TQQQ",
        "qty": 3,
        "avg_price": 59.75,
        "market_price": 59.98,
        "market_value_usd": 179.94,
        "unrealized_usd": 0.69,
        "note": "TP1 $60.95 감시 중"
    },
    {
        "ticker": "NVDA",
        "qty": 1,
        "avg_price": 173.30,
        "market_price": 174.64,
        "market_value_usd": 174.64,
        "unrealized_usd": 1.34,
        "note": "TP1 $177.63 감시 중"
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
    cr["account_status"]["funding_status"] = "실전 1일차 2건 분할 익절 완료 (SOXL TP1/TP2 +5.67 USD 실현, 승률 100%)"
    cr["account_status"]["cash_buffer_usd"] = 327.67
    es = cr["executive_summary"]
    es["daily_realized_pnl"] = "+$5.67 (+0.81%)"
    es["reinvestment_status"] = "SOXL 분할 익절 완료 ($130.07 회수) — 마감 시 SGOV 재투자 파이프라인 대기"
    es["live_orders_filled"] = 6  # 4 buys + 2 sells
    es["cash_buffer_usd"] = 327.67
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cr, f, ensure_ascii=False, indent=2)

# Insert Feed Post from Agent 10
feed_post = {
    "id": f"post-{int(datetime.now().timestamp())}-agent10-tp-hit",
    "agent_id": 10,
    "agent_name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
    "agent_role": "SHORT_TERM_EXECUTION_TRADER",
    "icon": "🎯",
    "avatar_bg": "from-emerald-400 to-green-600",
    "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    "timestamp": "2026-11-12 00:43",
    "time_ago": "방금 전",
    "category": "EXECUTION",
    "badge": "🎯 SOXL TP1·TP2 분할 익절 체결 (+4.56%)",
    "title": "[실전 익절 체결] SOXL 2주 분할 매도 완료 (주문 219833·222744) · 실현수익 +$5.67 달성",
    "content": (
        "장중 30분 브래킷 감시 중 SOXL 급등세를 포착하여 3단계 스마트 청산 룰을 성공적으로 집행했습니다.\n\n"
        "1. **1차 익절 (TP1) 체결**: 1주 @ $65.08 (주문 219833, +4.63%) — 매수가 $62.20 대비 +$2.88 실현\n"
        "   - Kahneman-Tversky 처분효과 극복: 체결 즉시 잔여 3주의 손절선을 본전($62.20)으로 상향하여 **무위험(Free-Ride)** 포지션으로 전환\n"
        "2. **2차 익절 (TP2) 체결**: 1주 @ $64.99 (주문 222744, +4.49%) — 매수가 $62.20 대비 +$2.79 실현\n"
        "   - 비대칭 손익비 확정: 총 실현 손익 **+$5.67 (약 +7,654원)**, 승률 100.0%\n"
        "3. **잔여 2주 운용 (무제한 러너)**: 샹들리에 ATR 트레일링 스탑 가동. 본전($62.20) 하향 이탈 없는 한 추가 상승 랠리 끝까지 추종\n\n"
        "현재 가용 주문가능 버퍼는 $327.67로 확충되었으며, 회수된 익절금은 06:00 정규장 마감 시 SGOV(연 4.52% 일복리) 자동 재투자 파이프라인으로 대기합니다."
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

print("✅ Live TP execution successfully recorded and synced across all ledgers!")
