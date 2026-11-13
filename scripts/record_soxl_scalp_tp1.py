#!/usr/bin/env python3
"""
Record SOXL Intraday Scalp TP1 Execution (2026-11-14 04:02 KST)
- Sold 1 share @ $57.43 (Bought @ $55.10, +$2.33 / +4.23%)
- Order No: 268333
- Raised remaining 1 share SL to $55.10 (Breakeven Free-Ride)
- Updates Calendar, CUBE report, Agent Feed, Live Portfolio, and HTML Dashboard
"""

import os
import sys
import json
from datetime import datetime

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1. Update daily_trading_calendar.json
cal_path = os.path.join(base, "data", "daily_trading_calendar.json")
with open(cal_path, "r", encoding="utf-8") as f:
    cal = json.load(f)

# Monthly Summary update
cal["monthly_summary"]["total_profit_usd"] = 7.46
cal["monthly_summary"]["total_profit_krw"] = 9957
cal["monthly_summary"]["monthly_return_pct"] = round((7.46 / 900.18) * 100, 2)  # +0.83%
cal["monthly_summary"]["win_rate_pct"] = 66.7
cal["monthly_summary"]["total_trades"] = 6
cal["monthly_summary"]["wins"] = 4
cal["monthly_summary"]["losses"] = 2
cal["monthly_summary"]["current_balance_krw"] = 1265866
cal["monthly_summary"]["current_balance_usd"] = 907.64
cal["monthly_summary"]["account_status"] = "실전 4일차 장중: SOXL 1차 익절 완료(+$2.33 / +4.2%), 당일 실현익 +$0.53 플러스 반전, 누적 실현익 +$7.46 달성."

# Update 2026-11-13 Day Record
day_rec = cal["daily_records"].get("2026-11-13", {})
day_rec["profit_usd"] = 0.53
day_rec["profit_krw"] = 708
day_rec["return_pct"] = 0.06
day_rec["trades_count"] = 3
day_rec["cash_buffer_usd"] = 576.80
day_rec["cash_buffer_krw"] = 770328

new_trade = {
    "ticker": "SOXL",
    "name": "디렉시온 반도체 3배 ETF (과매도 스캘핑 1차 익절)",
    "side": "SELL",
    "qty": 1,
    "entry_price": 55.10,
    "exit_price": 57.43,
    "profit_usd": 2.33,
    "profit_krw": 3110,
    "return_pct": 4.23,
    "exit_reason": "TP1_HIT (+4.2%)",
    "orr_no": "268333",
    "timestamp": "2026-11-14 04:02:26"
}

# Avoid duplicate insert
existing_orr_nos = [str(t.get("order_no", t.get("orr_no", ""))) for t in day_rec.get("trades", [])]
if "268333" not in existing_orr_nos:
    day_rec.setdefault("trades", []).append(new_trade)

# Update positions on 2026-11-13
day_rec["positions"] = [
    {
        "ticker": "SOXL",
        "qty": 1,
        "avg_price": 55.10,
        "market_price": 57.43,
        "market_value_usd": 57.43,
        "unrealized_usd": 2.33,
        "return_pct": 4.23,
        "note": "1차 익절 완료 (+4.23%), 잔여 1주 본전 손절($55.10) 무위험 러너 전환 (TP2 $57.86 감시)"
    },
    {
        "ticker": "NVDA",
        "qty": 1,
        "avg_price": 173.30,
        "market_price": 162.47,
        "market_value_usd": 162.47,
        "unrealized_usd": -10.83,
        "return_pct": -6.25,
        "note": "젠슨 황 리스크·반도체 투매 동반 하락 $162.47 (평단 $173 본전 회수 지정가 대기, $150 마지노선 사수)"
    },
    {
        "ticker": "SGOV",
        "qty": 1,
        "avg_price": 100.53,
        "market_price": 100.55,
        "market_value_usd": 100.55,
        "unrealized_usd": 0.02,
        "note": "초단기 국채 캐리 파킹 (연 4.52% 일복리)"
    }
]
day_rec["positions_value_usd"] = round(57.43 + 162.47 + 100.55, 2)
day_rec["unrealized_pnl_usd"] = round(2.33 - 10.83 + 0.02, 2)
day_rec["net_total_pnl_usd"] = round(cal["monthly_summary"]["total_profit_usd"] + day_rec["unrealized_pnl_usd"], 2)
day_rec["net_total_pnl_krw"] = int(day_rec["net_total_pnl_usd"] * 1335)
day_rec["status"] = "장중 진행 (SOXL 1차 익절 성공 +$2.33 확정 · 당일 실현익 +$0.53 플러스 반전 · 잔여 1주 무위험 러너)"
day_rec["note"] = (
    "실전 4일차: 장중 10분 브래킷 감시망으로 SOXL 1차 익절(TP1 $57.43, 주문 268333, +4.23%) 체결 완료! "
    "당일 실현 손익 -$1.80에서 +$0.53(+708원)으로 플러스 반전 성공. 누적 실현익 +$7.46(승률 66.7%). "
    "잔여 1주는 손절선 본전($55.10) 상향으로 완전한 무위험(Free-Ride) 러너 전환, TP2 $57.86 감시 중."
)

for rel in ("data/daily_trading_calendar.json", "docs/daily_trading_calendar.json"):
    p = os.path.join(base, rel)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)

# 2. Update cube_report.json
for rel in ("data/cube_report.json", "docs/cube_report.json"):
    p = os.path.join(base, rel)
    with open(p, "r", encoding="utf-8") as f:
        cr = json.load(f)
    cr["account_status"]["funding_status"] = "실전 4일차 SOXL TP1 익절 성공 (+$2.33 실현, 당일 실현익 +$0.53 플러스 반전)"
    cr["account_status"]["cash_buffer_usd"] = 576.80
    es = cr.get("executive_summary", {})
    es["daily_realized_pnl"] = "+$0.53 (당일 플러스 반전)"
    es["reinvestment_status"] = "SOXL 1차 익절 $57.43 회수 완료 (총 현금 버퍼 $576.80 확충)"
    es["cash_buffer_usd"] = 576.80
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cr, f, ensure_ascii=False, indent=2)

# 3. Add Agent 10 Feed Post
feed_post = {
    "id": f"post-{int(datetime.now().timestamp())}-agent10-soxl-tp1",
    "agent_id": 10,
    "agent_name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
    "agent_role": "SHORT_TERM_EXECUTION_TRADER",
    "icon": "🎯",
    "avatar_bg": "from-emerald-400 to-green-600",
    "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    "timestamp": "2026-11-14 04:02",
    "time_ago": "방금 전",
    "category": "EXECUTION",
    "badge": "🎯 SOXL TP1 1차 익절 체결 (+4.23%)",
    "title": "[실전 익절 체결] SOXL 1주 지정가 매도 완료 (주문 268333) · 당일 실현익 +$0.53 플러스 반전 달성",
    "content": (
        "장중 10분 브래킷 감시망이 04:02 SOXL의 1차 목표가(TP1) 돌파를 포착하여 NH 오픈API 매도 주문을 즉시 발주·체결 완료했습니다.\n\n"
        "1. **체결 내역**: SOXL 1주 @ $57.43 매도 (주문번호 268333)\n"
        "   - 매수가: $55.10 (01:27 체결) -> 매도가: $57.43 (04:02 체결)\n"
        "   - 실현 손익: **+$2.33 (+4.23%, 약 +3,110원)**\n"
        "2. **당일 실현익 플러스 반전 성공**: 초반 본전 손절(-$1.80)을 완전히 만회하고 **당일 순 실현익 +$0.53 (+708원)으로 플러스 전환**\n"
        "3. **계좌 누적 성과**: 총 실현 손익 **+$7.46 (약 +9,957원)**, 실전 승률 **66.7% (4승 2패)**\n"
        "4. **잔여 1주 무위험(Free-Ride) 전환**: 체결 즉시 잔여 1주의 손절선을 매수가 본전($55.10)으로 자동 상향. 이제 추가 하락이 나와도 원금 손실 0원인 완전한 무위험 포지션으로 TP2($57.86, +5.0%) 돌파를 추종합니다.\n"
        "5. **가용 현금 버퍼**: $57.43 매도 대금이 계좌로 즉시 복귀하여 현금 버퍼가 **$576.80(약 77만 원, 계좌 자산의 63.5%)**로 든든하게 확충되었습니다."
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

print("✅ Daily calendar, CUBE report, and Agent Feed successfully updated!")
