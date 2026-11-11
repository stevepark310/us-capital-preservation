#!/usr/bin/env python3
"""One-off patch: write 2026-11-11 live order execution results into the dashboards."""

import json
import os

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rep(text, old, new, count=1, label=""):
    n = text.count(old)
    if n != count:
        raise SystemExit(f"[FAIL] {label}: expected {count} match(es), found {n}")
    return text.replace(old, new)


# ---------------------------------------------------------------- cube.html
p = os.path.join(base, "cube.html")
html = open(p, encoding="utf-8").read()

# header card
html = rep(html, '<div class="text-base md:text-lg font-bold text-cyan-300 font-mono mt-0.5">$863.73 USD</div>',
           '<div class="text-base md:text-lg font-bold text-cyan-300 font-mono mt-0.5">$900.18 USD</div>', label="cube header usd")
html = rep(html, '<div class="text-[11px] text-slate-400">정규장 첫 거래 예정</div>\n          <div class="text-xs font-bold text-blue-300 font-mono mt-1">오늘 밤 23:30 OPEN</div>',
           '<div class="text-[11px] text-slate-400">실전 발주</div>\n          <div class="text-xs font-bold text-emerald-400 font-mono mt-1">23:34 4건 접수 · SGOV 체결</div>', label="cube header status")

# monthly summary balance card
html = rep(html, '<div class="text-xl md:text-2xl font-black text-emerald-400 font-mono mt-1">207,395원</div>\n            <div class="text-[10px] text-cyan-300 font-mono mt-0.5">202-05-134849 ($148.14 USD)</div>',
           '<div class="text-xl md:text-2xl font-black text-emerald-400 font-mono mt-1">1,209,215원</div>\n            <div class="text-[10px] text-cyan-300 font-mono mt-0.5">202-05-134849 ($900.18 주문가능 · 잔여 $197.60)</div>', label="cube monthly balance")

# realized P&L card (fabricated figure -> real)
html = rep(html, '<span class="mono font-bold text-emerald-400 text-sm">+$48.60 (+$0.49%)</span>',
           '<span class="mono font-bold text-slate-300 text-sm">+$0.00 (0.00%)</span>', label="cube pnl")
html = rep(html, '<span class="mono font-bold text-cyan-400 text-sm">$48.60 (100.0%)</span>',
           '<span class="mono font-bold text-cyan-400 text-sm">$0.00 · 익절 발생 시 100% 자동 파킹</span>', label="cube reinvest")

# order queue block
start_anchor = '          <div class="space-y-2 text-xs">\n            <div class="p-3 rounded-xl bg-slate-950/70 border border-blue-500/40 flex items-center justify-between">'
end_anchor = '        <!-- Right: Daily Realized Trades & Compounding Engine (6 Cols) -->'
i, j = html.find(start_anchor), html.find(end_anchor)
if i < 0 or j < 0 or j <= i:
    raise SystemExit("[FAIL] cube queue block anchors")
new_queue = '''          <div class="space-y-2 text-xs">
            <div class="p-3 rounded-xl bg-slate-950/70 border border-emerald-500/40 flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="bg-emerald-500/20 text-emerald-400 font-bold px-2 py-0.5 rounded text-[10px]">체결 완료</span>
                  <span class="font-bold text-white font-mono">SGOV 1주</span>
                  <span class="text-slate-400 text-[11px]">아이셰어즈 0-3개월 미국 국채 ETF (캐리 파킹)</span>
                </div>
                <div class="text-[11px] text-slate-300 mt-1">지정가 $100.55 · 주문번호 <span class="font-mono text-white">174377</span> · <span class="text-emerald-400 font-bold">체결가 $100.53</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">연 4.52% 무위험 캐리 · 실현 수익금 자동 파킹 자산</div>
              </div>
              <div class="text-right font-mono">
                <span class="text-emerald-400 font-bold">체결</span>
                <span class="text-[10px] text-slate-500 block">23:34 KST</span>
              </div>
            </div>

            <div class="p-3 rounded-xl bg-slate-950/70 border border-blue-500/40 flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="bg-blue-500/20 text-blue-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>
                  <span class="font-bold text-white font-mono">SOXL 4주</span>
                  <span class="text-slate-400 text-[11px]">디렉시온 반도체 3X (변동성 돌파)</span>
                </div>
                <div class="text-[11px] text-slate-300 mt-1">지정가 $62.20 · 주문번호 <span class="font-mono text-white">174442</span> · $248.80 · <span class="text-emerald-400 font-bold">1차 익절 $63.57 (+2.2%)</span> / <span class="text-blue-400 font-bold">2차 $64.69 (+4.0%)</span> / <span class="text-rose-400 font-bold">손절 $61.27 (-1.5%)</span></div>
                <div class="text-[10px] text-amber-300 mt-0.5">※ 개장 +14% 갭업(시가 $64.12) 추격 회피 — 첫 눌림목 대기 지정가</div>
              </div>
              <div class="text-right font-mono">
                <span class="text-blue-400 font-bold">미체결</span>
                <span class="text-[10px] text-slate-500 block">잔여 2주 ATR 트레일링</span>
              </div>
            </div>

            <div class="p-3 rounded-xl bg-slate-950/70 border border-purple-500/40 flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="bg-purple-500/20 text-purple-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>
                  <span class="font-bold text-white font-mono">TQQQ 3주</span>
                  <span class="text-slate-400 text-[11px]">프로셰어즈 QQQ 3X (ORB 돌파)</span>
                </div>
                <div class="text-[11px] text-slate-300 mt-1">지정가 $59.75 · 주문번호 <span class="font-mono text-white">174395</span> · $179.25 · <span class="text-emerald-400 font-bold">1차 익절 $60.95 (+2.0%)</span> / <span class="text-blue-400 font-bold">2차 $61.84 (+3.5%)</span> / <span class="text-rose-400 font-bold">손절 $58.85 (-1.5%)</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">실시간 $59.96 근접 — 체결 임박</div>
              </div>
              <div class="text-right font-mono">
                <span class="text-purple-400 font-bold">미체결</span>
                <span class="text-[10px] text-slate-500 block">지정가 59.75</span>
              </div>
            </div>

            <div class="p-3 rounded-xl bg-slate-950/70 border border-indigo-500/40 flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="bg-indigo-500/20 text-indigo-400 font-bold px-2 py-0.5 rounded text-[10px]">지정가 대기</span>
                  <span class="font-bold text-white font-mono">NVDA 1주</span>
                  <span class="text-slate-400 text-[11px]">엔비디아 (모멘텀 지지 매수)</span>
                </div>
                <div class="text-[11px] text-slate-300 mt-1">지정가 $173.30 · 주문번호 <span class="font-mono text-white">174411</span> · $173.30 · <span class="text-emerald-400 font-bold">1차 익절 $177.63 (+2.5%)</span> / <span class="text-rose-400 font-bold">손절 $170.70 (-1.5%)</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">실시간 $173.61 — 체결 임박</div>
              </div>
              <div class="text-right font-mono">
                <span class="text-indigo-400 font-bold">미체결</span>
                <span class="text-[10px] text-slate-500 block">지정가 173.30</span>
              </div>
            </div>

            <div class="p-3 rounded-xl bg-slate-950/70 border border-cyan-500/30 flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <span class="bg-cyan-500/20 text-cyan-400 font-bold px-2 py-0.5 rounded text-[10px]">버퍼</span>
                  <span class="font-bold text-white font-mono">현금 $197.60</span>
                  <span class="text-slate-400 text-[11px]">슬리피지 방어 &amp; 장중 딥바잉 예비금</span>
                </div>
                <div class="text-[11px] text-slate-400 mt-1">4건 주문 증거금 $702.58 · 주문가능액 $900.18 중 78.0% 집행</div>
              </div>
              <div class="text-right font-mono">
                <span class="text-cyan-400 font-bold">18~22%</span>
                <span class="text-[10px] text-slate-500 block">딥바잉 대기</span>
              </div>
            </div>
          </div>
        </div>

'''
html = html[:i] + new_queue + html[j:]

# "나무 Open API 자동 주문 상태" description
html = rep(html, '<div class="text-[11px] text-slate-400">모의투자 장종료 대기 (정규장 23:30 오픈 시 자동 체결)</div>',
           '<div class="text-[11px] text-slate-400">실전 4건 접수 완료 · SGOV 1주 체결 / SOXL·TQQQ·NVDA 지정가 대기</div>', label="cube api status")

# NEXT banner
html = rep(html, '<span class="text-blue-400 font-bold">NEXT:</span> 23:30 정규장 개장 즉시 잔고 207,395원 기반 1차 실전 매수 집행',
           '<span class="text-blue-400 font-bold">DONE:</span> 23:34 실전 4건 발주 접수 (SGOV 체결 · 잔여 3건 지정가 대기)', label="cube next banner")

# calendar JS data
html = rep(html, '''        balance_krw: "207,395원",
        status: "실전 거래 1일차 (23:30 정규장 첫 매수 대기)"''',
           '''        balance_krw: "1,209,215원",
        status: "실전 1일차 · 23:34 4건 발주 (SGOV 1주 체결, 3건 지정가 대기)"''', label="cube calendar summary")
html = rep(html, '''        11: { day: 11, is_today: true, is_trade: true, bal: "207,395원", profit: "$0.00", ret: "0.00%", p_class: "text-slate-400", bg: "bg-blue-950/40 border border-blue-500/50", note: "신규 전용 계좌(202-05-134849) 잔고 207,395원($148.14) 확충 및 오늘 밤 23:30 정규장 첫 매수 대기", trades: ["SOXL 2주 ($112.12) 돌파 매수 대기", "TQQQ 1주 ($56.56) 듀얼 모멘텀 예비 대기", "실현 익절금 전액 SGOV 복리 재투자"] },''',
           '''        11: { day: 11, is_today: true, is_trade: true, bal: "1,209,215원", profit: "$0.00", ret: "0.00%", p_class: "text-slate-400", bg: "bg-blue-950/40 border border-blue-500/50", note: "실전 1일차 · 23:34 NH 오픈API 4건 지정가 발주 접수 (주문가능 $900.18, 집행 $702.58, 버퍼 $197.60)", trades: ["SGOV 1주 $100.55 → 체결 $100.53 (주문 174377)", "SOXL 4주 $62.20 대기 (주문 174442)", "TQQQ 3주 $59.75 대기 (주문 174395)", "NVDA 1주 $173.30 대기 (주문 174411)"] },''', label="cube calendar day11")
open(p, "w", encoding="utf-8").write(html)

# --------------------------------------------------------------- index.html
p = os.path.join(base, "index.html")
html = open(p, encoding="utf-8").read()
html = rep(html, '<div class="text-base md:text-lg font-bold text-cyan-300 font-mono mt-0.5">$863.73 USD</div>',
           '<div class="text-base md:text-lg font-bold text-cyan-300 font-mono mt-0.5">$900.18 USD</div>', label="index header usd")
html = rep(html, '<div class="text-[11px] text-slate-400">1차 주문 규모</div>\n          <div class="text-xs font-bold text-blue-300 font-mono mt-1">SOXL 10주, TQQQ 2주, NVDA 1주</div>',
           '<div class="text-[11px] text-slate-400">실전 발주 현황</div>\n          <div class="text-xs font-bold text-emerald-400 font-mono mt-1">4건 접수 · SGOV 1주 체결</div>', label="index header queue")
html = rep(html, '<div class="text-xs text-emerald-400 mt-1 flex items-center gap-1 font-medium font-mono">$863.73 USD <span class="text-slate-500 font-normal font-sans">(4대 자산 분산 운용)</span></div>',
           '<div class="text-xs text-emerald-400 mt-1 flex items-center gap-1 font-medium font-mono">$900.18 USD <span class="text-slate-500 font-normal font-sans">(주문가능 · 잔여 $197.60)</span></div>', label="index nav usd")
html = rep(html, '<p class="text-[11px] text-slate-400 mt-0.5">계좌 202-05-134849 (예수금 207,395원 / $148.14 기준)</p>',
           '<p class="text-[11px] text-slate-400 mt-0.5">계좌 202-05-134849 (예수금 1,209,215원 / 주문가능 $900.18 기준)</p>', label="index alloc sub")
html = rep(html, '<span class="text-xs text-blue-400 font-mono font-bold">23:30 정규장 OPEN 대기</span>',
           '<span class="text-xs text-emerald-400 font-mono font-bold">23:34 실전 발주 완료</span>', label="index alloc status")
html = rep(html, '<span>⚡</span> 오늘 밤 23:30 첫 실전 주문 대기열 (Execution Queue)',
           '<span>⚡</span> 23:34 실전 주문 집행 결과 (Execution Queue)', label="index queue title")
html = rep(html, '예수금 207,395원 매칭 완료', '4건 접수 · 주문증거금 $702.58', label="index queue badge")

start_anchor = '        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-blue-500/40 space-y-2">'
end_anchor = '    <!-- Equity Curve Chart -->'
i, j = html.find(start_anchor), html.find(end_anchor)
if i < 0 or j < 0 or j <= i:
    raise SystemExit("[FAIL] index queue anchors")
new_cards = '''        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-emerald-500/40 space-y-1.5">
          <div class="flex justify-between items-center">
            <span class="font-bold text-emerald-300 text-sm">SGOV 1주 · 체결 완료</span>
            <span class="text-[11px] font-mono text-emerald-400 font-bold">$100.53 체결</span>
          </div>
          <p class="text-slate-400 leading-relaxed">
            - 지정가 $100.55 · 주문번호 <span class="font-mono text-white">174377</span><br>
            - 초단기 미국채 캐리 파킹 (연 4.52%) · 실현 수익금 자동 예치 자산
          </p>
        </div>

        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-blue-500/40 space-y-1.5">
          <div class="flex justify-between items-center">
            <span class="font-bold text-blue-300 text-sm">SOXL 4주 · 지정가 대기</span>
            <span class="text-[11px] font-mono text-blue-400 font-bold">$62.20 (주문 174442)</span>
          </div>
          <p class="text-slate-400 leading-relaxed">
            - 개장 +14% 갭업 추격 회피, 첫 눌림목 대기 지정가<br>
            - <strong>1차 익절</strong> $63.57 (+2.2%, 1주) / <strong>2차</strong> $64.69 (+4.0%, 1주) / <strong>손절</strong> $61.27 (-1.5%)
          </p>
        </div>

        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-purple-500/40 space-y-1.5">
          <div class="flex justify-between items-center">
            <span class="font-bold text-purple-300 text-sm">TQQQ 3주 · 지정가 대기</span>
            <span class="text-[11px] font-mono text-purple-400 font-bold">$59.75 (주문 174395)</span>
          </div>
          <p class="text-slate-400 leading-relaxed">
            - ORB 돌파 지정가, 실시간 $59.96 근접<br>
            - <strong>1차 익절</strong> $60.95 (+2.0%) / <strong>2차</strong> $61.84 (+3.5%) / <strong>손절</strong> $58.85 (-1.5%)
          </p>
        </div>

        <div class="p-3.5 rounded-xl bg-slate-950/70 border border-indigo-500/40 space-y-1.5">
          <div class="flex justify-between items-center">
            <span class="font-bold text-indigo-300 text-sm">NVDA 1주 · 지정가 대기</span>
            <span class="text-[11px] font-mono text-indigo-400 font-bold">$173.30 (주문 174411)</span>
          </div>
          <p class="text-slate-400 leading-relaxed">
            - 모멘텀 지지선 근접 지정가, 실시간 $173.61<br>
            - <strong>1차 익절</strong> $177.63 (+2.5%) / <strong>손절</strong> $170.70 (-1.5%)
          </p>
        </div>
      </div>
    </div>

'''
html = html[:i] + new_cards + html[j:]
open(p, "w", encoding="utf-8").write(html)
print("dashboards patched")
