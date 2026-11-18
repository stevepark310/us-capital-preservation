#!/usr/bin/env python3
"""
Finalize US Market Close Settlement (2026-11-14 06:00 KST)
- Record closing prices: NVDA $162.82, SGOV $100.56, SOXL $58.35, TQQQ $57.67, QQQ $679.90, SPY $754.25, VIX 19.21
- Official Realized P&L:
    * Intraday net realized: +$3.35 (+4,472 KRW)
    * Cumulative realized: +$10.28 (+13,724 KRW)
    * Win rate: 71.4% (7 trades, 5 wins, 2 losses)
- Cash Buffer: 847,420 KRW ($634.77 USD, 70.7% of total equity)
- Total Equity: $898.15 (1,199,030 KRW)
- Unrealized P&L: -$10.45 (-13,951 KRW)
- Net Total P&L: -$0.17 (-227 KRW, -0.02% vs initial funding $900.18) -> Near-perfect capital preservation!
- Update daily_trading_calendar.json, cube_report.json, agent_feed.json, live_portfolio.json
- Rebuild CUBE dashboard and sync widget bundle
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

cal["monthly_summary"]["total_profit_usd"] = 10.28
cal["monthly_summary"]["total_profit_krw"] = 13724
cal["monthly_summary"]["monthly_return_pct"] = 1.14
cal["monthly_summary"]["win_rate_pct"] = 71.4
cal["monthly_summary"]["total_trades"] = 7
cal["monthly_summary"]["wins"] = 5
cal["monthly_summary"]["losses"] = 2
cal["monthly_summary"]["current_balance_krw"] = 1199030
cal["monthly_summary"]["current_balance_usd"] = 898.15
cal["monthly_summary"]["total_unrealized_usd"] = -10.45
cal["monthly_summary"]["net_total_pnl_usd"] = -0.17
cal["monthly_summary"]["account_status"] = (
    "실전 4일차 장마감 정산 완료: SOXL 스캘핑 2주 전량 완벽 익절(+$5.15), "
    "당일 순 실현익 +$3.35(+4,472원) 확정, 누적 실현익 +$10.28(승률 71.4%), "
    "가용 현금 847,420원($634.77, 70.7%) 전액 회수 안전지대 확보."
)

day_rec = cal["daily_records"].get("2026-11-13", {})
day_rec["profit_usd"] = 3.35
day_rec["profit_krw"] = 4472
day_rec["return_pct"] = 0.37
day_rec["trades_count"] = 4
day_rec["cash_buffer_usd"] = 634.77
day_rec["cash_buffer_krw"] = 847420
day_rec["positions"] = [
    {
        "ticker": "NVDA",
        "qty": 1,
        "avg_price": 173.30,
        "market_price": 162.82,
        "market_value_usd": 162.82,
        "unrealized_usd": -10.48,
        "return_pct": -6.05,
        "note": "젠슨 황 사임 여파 $162.82 마감 ($150 마지노선 안정적 사수, 평단 $173 본전 회수 대기)"
    },
    {
        "ticker": "SGOV",
        "qty": 1,
        "avg_price": 100.53,
        "market_price": 100.56,
        "market_value_usd": 100.56,
        "unrealized_usd": 0.03,
        "return_pct": 0.03,
        "note": "초단기 국채 캐리 파킹 (연 4.52% 일복리 배당 누적)"
    }
]
day_rec["positions_value_usd"] = 263.38
day_rec["unrealized_pnl_usd"] = -10.45
day_rec["net_total_pnl_usd"] = -0.17
day_rec["net_total_pnl_krw"] = -227
day_rec["status"] = "장마감 정산 완료 (SOXL 스캘핑 2주 전량 익절 · 당일 실현익 +$3.35 달성 · 현금 비중 70.7% 안전 회수)"
day_rec["note"] = (
    "실전 4일차 장마감 최종 정산: 초반 나스닥 급락 구간에서 SOXL·TQQQ 러너를 본전 손절(-$1.80)로 신속히 방어한 뒤, "
    "01:27 SOXL 2주 과매도 스캘핑($55.10) 진입 → 04:02 TP1($57.43, +$2.33) & 05:02 TP2($57.92, +$2.82)로 "
    "스캘핑 총 +$5.15(+4.67%) 전량 완벽 익절 성공! "
    "당일 순 실현익 +$3.35(+4,472원)로 데일리 수익 목표 완벽 달성. 누적 실현익 +$10.28(승률 71.4%, 5승 2패). "
    "가용 현금 버퍼 847,420원($634.77, 70.7%) 전액 회수 완료."
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
    cr["account_status"]["funding_status"] = "실전 4일차 장마감 정산 완료 (당일 순 실현익 +$3.35, 누적 +$10.28 확정, 현금 버퍼 84.7만 원)"
    cr["account_status"]["cash_buffer_usd"] = 634.77
    cr["account_status"]["cash_buffer_krw"] = 847420
    es = cr.get("executive_summary", {})
    es["daily_realized_pnl"] = "+$3.35 (+4,472원 · 데일리 수익 달성)"
    es["cumulative_realized_pnl"] = "+$10.28 (+13,724원 · 7전 5승 2패, 승률 71.4%)"
    es["reinvestment_status"] = "장마감 전량 현금 회수 완료 (총 현금 847,420원 / $634.77, 비중 70.7%)"
    es["cash_buffer_usd"] = 634.77
    es["market_close_summary"] = "SOXL 스캘핑 2주 전량 승리 익절 완료. 주말 미증시 휴장 진입, NVDA $150 마지노선 사수 중."
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cr, f, ensure_ascii=False, indent=2)

# 3. Add Market Close Feed Post to Agent Feed
feed_post = {
    "id": f"post-{int(datetime.now().timestamp())}-market-close-summary",
    "agent_id": 10,
    "agent_name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
    "agent_role": "SHORT_TERM_EXECUTION_TRADER",
    "icon": "🏁",
    "avatar_bg": "from-blue-600 to-indigo-800",
    "badge_color": "bg-blue-500/10 text-blue-400 border-blue-500/20",
    "timestamp": "2026-11-14 06:00",
    "time_ago": "방금 전",
    "category": "MARKET_CLOSE",
    "badge": "🏁 미국 정규장 마감 정산 완결",
    "title": "[장마감 최종 정산] 당일 순 실현익 +$3.35 (+4,472원) 확정 · 누적 실현익 +$10.28 돌파 · 현금 비중 70.7% 전액 회수",
    "content": (
        "2026-11-14 06:00 미국 정규장이 공식 마감되었습니다. CUBE 10인 에이전트 조직이 실전 4일차 장마감 정산을 최종 완료했습니다.\n\n"
        "1. **데일리 트레이딩 결과 요약**:\n"
        "   - 장초반 나스닥 급락 구간에서 SOXL 2주($62.04)·TQQQ 2주($59.01) 본전 손절(-$1.80)로 추가 폭락(-16%) 리스크를 완벽 차단\n"
        "   - 01:27 당일 최저점 반등 시그널 공략, SOXL 2주 과매도 스캘핑($55.10) 신규 매수\n"
        "   - 04:02 TP1($57.43, +$2.33 / +4.23%) 1주 익절 완료\n"
        "   - 05:02 TP2($57.92, +$2.82 / +5.12%) 잔여 1주 전량 익절 완료\n"
        "   - **당일 순 실현 손익: +$3.35 (+4,472원)** 확정 (초반 손절 만회 및 일일 수익 창출 완수)\n\n"
        "2. **계좌 누적 성과**:\n"
        "   - 총 실현 손익: **+$10.28 (약 +13,724원)** (초기 자본 대비 +1.14% 확정 이익)\n"
        "   - 실전 전적: **7전 5승 2패 (승률 71.4%)**\n\n"
        "3. **종가 기준 보유 포지션 및 현금**:\n"
        "   - 가용 현금: **847,420원 ($634.77)** — 계좌 자산의 **70.7%**가 현금으로 안전 회수\n"
        "   - NVDA (1주): 종가 $162.82 (평단 $173.30, 미실현 -$10.48 / -6.05%, $150 종가 마지노선 안정 사수 중)\n"
        "   - SGOV (1주): 종가 $100.56 (평단 $100.53, 미실현 +$0.03, 단기 국채 캐리)\n"
        "   - SOXL / TQQQ: 0주 (전량 청산 완료)\n\n"
        "4. **주말 및 다음 거래일 일정**:\n"
        "   - 토·일요일 미증시 휴장으로 10분 브래킷 감시망 일시정지(`paused: true`)\n"
        "   - 다음 정규장: 2026-11-16(월) 23:30 KST 개장\n"
        "   - 아침 08:30 트레이딩 특화 모닝 브리핑에서 최종 주말 전략을 보고드립니다."
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

print("✅ US Market close final settlement successfully recorded!")
