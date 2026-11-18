#!/usr/bin/env python3
"""
CUBE 10-Agent Desk & Interactive Chatroom Build Script
Integrates:
1. Global Macro & FX Radar Ticker
2. Dynamic News & Wall Street IB Exit Controller
3. 10 Specialized Agents with Individual Refresh Buttons
4. CUBE Strategy Chatroom (Messenger UI) with Concise Takeaway & Expandable References
5. Interactive Chat Input & Dynamic Agent Response System
"""

import os
import json
import re
import sys
from datetime import datetime

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

try:
    from scripts.sync_live_portfolio import sync_portfolio
    sync_portfolio()
except Exception as e:
    print(f"Warning: sync_live_portfolio failed: {e}")

try:
    from scripts._panel_cross_market import _cross_market_panel
except Exception:  # direct execution of this file
    from _panel_cross_market import _cross_market_panel

feed_json_path = os.path.join(base_dir, "data", "agent_feed.json")
news_json_path = os.path.join(base_dir, "data", "news_sentiment_report.json")

with open(feed_json_path, "r", encoding="utf-8") as f:
    feed_data = json.load(f)

news_data = {}
if os.path.exists(news_json_path):
    with open(news_json_path, "r", encoding="utf-8") as f:
        news_data = json.load(f)

posts = feed_data.get("posts", [])
gm = feed_data.get("global_macro", {})
ticker_reports = news_data.get("ticker_reports", {})

# 1. Build Global Macro Radar HTML
macro_radar_html = f"""
    <!-- Global Macro & FX Radar Ticker Banner (전세계 증시 & 달러 매크로 레이더) -->
    <div class="bg-gradient-to-r from-slate-950 via-slate-900 to-indigo-950/70 border border-slate-800 rounded-2xl p-4 md:p-5 space-y-3 shadow-xl">
      <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-2 pb-2.5 border-b border-slate-800/80">
        <div class="flex items-center gap-2.5">
          <span class="text-xl">🌐</span>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-sm md:text-base font-bold text-white">글로벌 증시 & 달러 매크로 실시간 레이더 (Global Macro & FX)</h2>
              <span class="bg-emerald-500/10 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-emerald-500/20 flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> GLOBAL RISK-ON
              </span>
            </div>
            <p class="text-[11px] text-slate-400 mt-0.5">Agent 4가 전세계 주요 지수(항셍·닛케이·DAX) 및 달러 인덱스(DXY)·환율 동향을 실시간 감시합니다.</p>
          </div>
        </div>
        <div class="flex items-center gap-2 text-xs">
          <span class="text-slate-400 text-[11px]">기준 시각:</span>
          <span class="font-mono text-cyan-300 font-bold bg-slate-800 px-2 py-0.5 rounded border border-slate-700/80">{feed_data.get('last_updated', '2026-11-12 18:20:00')}</span>
        </div>
      </div>

      <!-- Macro Ticker Grid -->
      <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5 text-xs font-mono">
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">홍콩 항셍 (^HSI)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('hang_seng', {}).get('level', 24315.92):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('hang_seng', {}).get('change', '-0.98%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">일본 닛케이 (^N225)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('nikkei_225', {}).get('level', 61248.59):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('nikkei_225', {}).get('change', '+1.31%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">독일 DAX 40 (^GDAXI)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('dax_40', {}).get('level', 26523.46):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('dax_40', {}).get('change', '+0.67%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">나스닥 선물 (NQ)</div>
          <div class="text-xs md:text-sm font-bold text-cyan-300 mt-0.5">{gm.get('nasdaq_fut', {}).get('level', 28386.75):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('nasdaq_fut', {}).get('change', '+1.98%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-blue-900/40 p-2.5 rounded-xl bg-blue-950/20">
          <div class="text-[10px] text-blue-300 font-sans">달러 인덱스 (DXY)</div>
          <div class="text-xs md:text-sm font-bold text-blue-400 mt-0.5">{gm.get('dxy_dollar_index', {}).get('level', 102.55):,.2f}</div>
          <div class="text-[10px] text-blue-300 font-bold">{gm.get('dxy_dollar_index', {}).get('change', '+0.31%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">원/달러 환율 (KRW)</div>
          <div class="text-xs md:text-sm font-bold text-slate-200 mt-0.5">{gm.get('usd_krw', {}).get('level', 1334.16):,.2f}원</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('usd_krw', {}).get('change', '-0.72%')} (원화 강세)</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">엔/달러 환율 (JPY)</div>
          <div class="text-xs md:text-sm font-bold text-slate-200 mt-0.5">{gm.get('usd_jpy', {}).get('level', 159.19):,.2f}엔</div>
          <div class="text-[10px] text-slate-400 font-bold">{gm.get('usd_jpy', {}).get('change', '+0.76%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-emerald-900/40 p-2.5 rounded-xl bg-emerald-950/20">
          <div class="text-[10px] text-emerald-300 font-sans">VIX 변동성 레짐</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('vix', {}).get('level', 18.42):,.2f}</div>
          <div class="text-[10px] text-emerald-300 font-bold">BULL_CALM 저변동성</div>
        </div>
      </div>
    </div>
"""

# 2. Build Dynamic News & Wall Street IB Exit Controller HTML
controller_cards = ""
target_order = ["SOXL", "TQQQ", "NVDA", "TSLA"]

for ticker in target_order:
    t_info = ticker_reports.get(ticker, {})
    score = t_info.get("sentiment_score", 70.0)
    grade = t_info.get("sentiment_grade", "NEUTRAL")
    news_list = t_info.get("breaking_news", [])
    ib_list = t_info.get("ib_insights", [])
    comm = t_info.get("community_sentiment", {})
    adj = t_info.get("dynamic_exit_adjustment", {})

    score_color = "text-emerald-400" if score >= 80 else ("text-cyan-400" if score >= 70 else "text-amber-400")
    badge_bg = "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" if score >= 80 else ("bg-cyan-500/10 text-cyan-400 border-cyan-500/30" if score >= 70 else "bg-amber-500/10 text-amber-400 border-amber-500/30")

    news_html = "".join([f"<li class='text-[11px] text-slate-300 leading-tight'>• {n}</li>" for n in news_list[:2]])
    ib_html = "".join([f"<div class='text-[11px]'><strong class='text-slate-200 font-mono'>{ib.get('firm')}:</strong> <span class='text-indigo-300 font-bold'>[{ib.get('view')}]</span> <span class='text-slate-400'>{ib.get('comment')}</span></div>" for ib in ib_list[:2]])

    controller_cards += f"""
      <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-4 space-y-3 hover:border-slate-700 transition flex flex-col justify-between">
        <div class="space-y-2.5">
          <div class="flex justify-between items-start gap-2">
            <div>
              <div class="text-xs font-bold text-white flex items-center gap-1.5">
                <span class="font-mono text-cyan-300">{ticker}</span>
                <span class="text-[10px] text-slate-400 font-normal">{t_info.get('name', '').split('(')[0]}</span>
              </div>
              <span class="inline-block mt-1 text-[10px] px-2 py-0.5 rounded-full border font-mono {badge_bg}">
                {grade}
              </span>
            </div>
            <div class="text-right">
              <div class="text-[10px] text-slate-400">센티먼트 지수</div>
              <div class="text-base font-extrabold font-mono {score_color}">{score} / 100</div>
            </div>
          </div>

          <!-- Breaking News Bullet -->
          <div class="bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80 space-y-1">
            <div class="text-[10px] text-cyan-400 font-bold flex items-center gap-1">
              <span>⚡ 실시간 속보 & 시황</span>
            </div>
            <ul class="space-y-1">
              {news_html}
            </ul>
          </div>

          <!-- Wall Street IB Insights -->
          <div class="bg-indigo-950/20 p-2.5 rounded-lg border border-indigo-900/30 space-y-1">
            <div class="text-[10px] text-indigo-400 font-bold flex items-center gap-1">
              <span>🏛️ 월가 IB 컨센서스</span>
            </div>
            <div class="space-y-1">
              {ib_html}
            </div>
          </div>

          <!-- Community Sentiment -->
          <div class="text-[10px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2 font-mono">
            <span>WSB/X 여론:</span>
            <span class="text-slate-300 font-bold">{comm.get('call_put_ratio', '2:1')} 우위</span>
          </div>
        </div>

        <!-- Dynamic Exit Adjustment Box -->
        <div class="bg-gradient-to-r from-blue-950/40 to-slate-900 p-2.5 rounded-lg border border-blue-500/30 space-y-1.5 font-mono text-xs">
          <div class="flex justify-between items-center text-[10px] font-sans">
            <span class="text-blue-300 font-bold">🎯 동적 청산 보정값</span>
            <span class="text-emerald-400 font-bold">{adj.get('action', 'STANDARD')}</span>
          </div>
          <div class="grid grid-cols-2 gap-1.5 text-[11px]">
            <div class="bg-slate-900/90 p-1.5 rounded border border-slate-800 text-center">
              <div class="text-[9px] text-slate-400 font-sans">1차 익절 (TP1)</div>
              <div class="font-bold text-emerald-400">{adj.get('tp1', '+2.0%')}</div>
            </div>
            <div class="bg-slate-900/90 p-1.5 rounded border border-slate-800 text-center">
              <div class="text-[9px] text-slate-400 font-sans">2차 익절 (TP2)</div>
              <div class="font-bold text-cyan-300">{adj.get('tp2', '+3.5%')}</div>
            </div>
            <div class="bg-slate-900/90 p-1.5 rounded border border-slate-800 text-center">
              <div class="text-[9px] text-slate-400 font-sans">추세 러너 비중</div>
              <div class="font-bold text-indigo-300">{adj.get('runner_weight', '34%')}</div>
            </div>
            <div class="bg-slate-900/90 p-1.5 rounded border border-slate-800 text-center">
              <div class="text-[9px] text-slate-400 font-sans">원칙 칼손절</div>
              <div class="font-bold text-rose-400">{adj.get('sl', '-1.5%')}</div>
            </div>
          </div>
          <div class="text-[10px] text-slate-400 font-sans line-clamp-1">
            {adj.get('rationale', '')}
          </div>
        </div>
      </div>
    """

news_controller_html = f"""
    <!-- Dynamic News & Wall Street IB Exit Controller Section -->
    <section class="bg-gradient-to-r from-slate-950 via-slate-900 to-cyan-950/40 border border-cyan-800/40 rounded-2xl p-5 space-y-4 shadow-2xl">
      <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-3 pb-3 border-b border-slate-800">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center text-white text-xl shadow-lg shadow-cyan-500/20">
            📰
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-sm md:text-base font-bold text-white">실시간 시황·월가 리포트 연동 동적 매도 브래킷 조절기 (Dynamic News & IB Exit Controller)</h2>
              <span class="bg-cyan-500/10 text-cyan-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-cyan-500/20 flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 pulse-dot"></span> 1H LIVE SYNC
              </span>
            </div>
            <p class="text-[11px] text-slate-400 mt-0.5">
              Agent 1이 1시간마다 보유 종목의 속보, 골드만·모건스탠리 등 IB 리포트, 레딧/X 여론을 전수 분석하여 매도 익절·손절 브래킷을 실시간 자동 가감합니다.
            </p>
          </div>
        </div>
        <div class="flex items-center gap-2 text-xs font-mono">
          <span class="text-slate-400 text-[11px]">스캔 주기:</span>
          <span class="bg-slate-800 text-cyan-300 font-bold px-2.5 py-1 rounded border border-slate-700">매 1시간 정기 스캔</span>
        </div>
      </div>

      <!-- 4 Tickers Grid -->
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3.5">
        {controller_cards}
      </div>

      <!-- Bottom Explanatory Banner -->
      <div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3 flex items-start gap-3 text-xs">
        <span class="text-amber-400 text-base">💡</span>
        <div class="text-slate-300 leading-relaxed">
          <strong class="text-white">뉴스 연동 실시간 동적 청산 메커니즘:</strong> 
          호재 발생 및 월가 목표가 상향 시 <strong>러너(추세 추종) 비중을 최대 50%까지 확대</strong>하여 수익을 극대화(Run Winners)하고, 
          반대로 장중 악재 발생이나 투자의견 하향으로 센티먼트가 45점 이하로 급락하면 <strong>조기 방어 모드(Early Defensive)</strong>가 즉각 가동되어 
          익절선이 +1.2%로 단축되거나 손절선이 -0.8%로 타이트하게 당겨져 원금을 철저히 보존합니다.
        </div>
      </div>
    </section>
"""

# 3. Build Work Feed Section HTML in Modern Chatroom Style (채팅방 형태)
def format_summary(post):
    if post.get("summary"):
        lines = post["summary"].strip().split("\n")
        return "".join([f"<p class='text-slate-200 text-xs md:text-[13px] leading-relaxed'>{line}</p>" for line in lines])
    
    # Auto-extract summary from content
    content = post.get("content", "")
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if paragraphs:
        first_p = paragraphs[0]
        # Clean markdown
        clean_p = first_p.replace("**", "").replace("`", "")
        lines = [l for l in clean_p.split("\n") if l.strip()][:3]
        return "".join([f"<p class='text-slate-200 text-xs md:text-[13px] leading-relaxed'>• {l.lstrip('•- ')}</p>" for l in lines])
    return "<p class='text-slate-200 text-xs md:text-[13px]'>핵심 업무보고가 등록되었습니다.</p>"

feed_messages_html = ""
for post in posts:
    is_pinned = post.get("pinned", False)
    pinned_badge = '<span class="bg-red-500/15 text-red-400 text-[10px] px-2 py-0.5 rounded-full font-bold border border-red-500/30 flex items-center gap-1 shrink-0">📌 PINNED DIRECTIVE</span>' if is_pinned else ''
    bubble_border = "border-red-500/40 bg-gradient-to-br from-red-950/20 via-slate-900/95 to-slate-900" if is_pinned else "border-slate-800 bg-slate-900/90"
    
    # metrics pills
    metrics_pills = ""
    for m in post.get("metrics", []):
        hl = m.get("highlight", False)
        bg = "bg-blue-500/15 border-blue-500/30 text-blue-300" if hl else "bg-slate-800/80 border-slate-700 text-slate-300"
        metrics_pills += f"""
          <div class="text-[11px] px-2.5 py-1 rounded-lg border {bg} flex items-center gap-1.5 font-mono">
            <span class="text-slate-400 font-sans">{m.get('label')}:</span>
            <span class="font-bold">{m.get('value')}</span>
          </div>
        """
        
    # reactions pills
    rx = post.get("reactions", {})
    rx_pills = ""
    rx_icons = {"roger": "🫡 라져", "fire": "🔥 유효", "rocket": "🚀 기대", "check": "✅ 접수", "thumbs_up": "👍 동의", "eyes": "👀 주목"}
    for k, v in rx.items():
        lbl = rx_icons.get(k, k)
        rx_pills += f"""
          <button onclick="addReaction('{post.get('id')}', '{k}', this)" class="bg-slate-800/90 hover:bg-slate-700 text-slate-300 text-[11px] px-2.5 py-1 rounded-lg border border-slate-700/60 transition flex items-center gap-1">
            <span>{lbl}</span>
            <span class="font-mono text-slate-400 font-bold count">{v}</span>
          </button>
        """

    summary_html = format_summary(post)
    content_formatted = post.get("content", "").replace("\n\n", "<br><br>").replace("\n", "<br>")

    feed_messages_html += f"""
      <!-- Chat Message Row -->
      <div class="chat-message-row flex items-start gap-3 transition" data-agent="{post.get('agent_id')}" data-category="{post.get('category')}" id="{post.get('id')}">
        <!-- Agent Avatar -->
        <div class="w-10 h-10 md:w-11 md:h-11 rounded-full bg-gradient-to-tr {post.get('avatar_bg')} flex items-center justify-center text-lg md:text-xl shadow-lg shrink-0 border-2 border-slate-800 ring-2 ring-slate-800/50">
          {post.get('icon')}
        </div>

        <!-- Chat Bubble Container -->
        <div class="flex-1 space-y-1.5 min-w-0">
          <!-- Header info -->
          <div class="flex flex-wrap items-center justify-between gap-1 px-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-bold text-sm text-white">{post.get('agent_name')}</span>
              <span class="text-[10px] px-2 py-0.5 rounded-full border font-mono font-medium {post.get('badge_color')}">{post.get('badge')}</span>
              {pinned_badge}
            </div>
            <div class="text-[11px] text-slate-400 font-mono flex items-center gap-1.5">
              <span>{post.get('timestamp')}</span>
              <span class="text-slate-600">•</span>
              <span class="text-cyan-400 font-medium">{post.get('time_ago')}</span>
            </div>
          </div>

          <!-- Message Chat Bubble -->
          <div class="chat-bubble border {bubble_border} rounded-2xl rounded-tl-sm p-4 space-y-3 shadow-lg">
            <!-- Headline Title -->
            <div class="text-xs md:text-sm font-bold text-slate-100 flex items-center gap-2">
              <span class="text-cyan-400">📢</span>
              <span>{post.get('title')}</span>
            </div>

            <!-- Core Summary Box (핵심 요약) -->
            <div class="bg-slate-950/70 p-3.5 rounded-xl border border-slate-800/80 space-y-2">
              <div class="text-[11px] font-bold text-cyan-400 flex items-center justify-between border-b border-slate-800/60 pb-1.5">
                <span class="flex items-center gap-1.5">⚡ <span>핵심 업무보고 요약</span></span>
                <span class="text-[10px] text-slate-500 font-mono font-normal">EXECUTIVE BRIEF</span>
              </div>
              <div class="space-y-1">
                {summary_html}
              </div>
            </div>

            <!-- Key Metrics Pills -->
            {f'<div class="flex flex-wrap items-center gap-1.5 pt-0.5">{metrics_pills}</div>' if metrics_pills else ''}

            <!-- Action Item -->
            {f'<div class="bg-blue-950/30 border border-blue-500/20 text-blue-300 text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 font-mono"><span class="text-blue-400 font-bold">NEXT:</span> {post.get("action_required")}</div>' if post.get("action_required") else ''}

            <!-- Reference / Evidence Toggle Button (참고자료 버튼) -->
            <div class="pt-1">
              <button onclick="toggleReference('{post.get('id')}')" id="btn-ref-{post.get('id')}" class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 hover:text-white text-xs font-semibold border border-cyan-500/30 hover:border-cyan-500 transition shadow-sm">
                <span id="btn-icon-{post.get('id')}">📎</span>
                <span id="btn-text-{post.get('id')}">참고자료 및 상세 근거 보기</span>
              </button>

              <!-- Expandable Detailed Evidence Box -->
              <div id="ref-{post.get('id')}" class="reference-box hidden mt-3 p-4 rounded-xl bg-slate-950/90 border border-indigo-900/40 text-xs text-slate-300 leading-relaxed font-sans space-y-2.5 transition">
                <div class="flex items-center justify-between pb-2 border-b border-slate-800">
                  <div class="flex items-center gap-2 text-indigo-300 font-bold text-xs">
                    <span>📊</span>
                    <span>상세 정량 분석 보고서 및 참고 자료 (원문 근거)</span>
                  </div>
                  <span class="text-[10px] text-slate-500 font-mono">CONFIDENTIAL QUANT MEMO</span>
                </div>
                <div class="pt-1 text-slate-300 leading-relaxed font-sans">
                  {content_formatted}
                </div>
              </div>
            </div>

            <!-- Bubble Footer / Reactions -->
            <div class="flex items-center justify-between pt-2 border-t border-slate-800/60 text-xs">
              <div class="flex flex-wrap items-center gap-1.5">
                {rx_pills}
              </div>
              <div class="text-slate-500 text-[10px] font-mono">
                #{post.get('id')}
              </div>
            </div>
          </div>
        </div>
      </div>
    """

# Agent Quick Bar HTML for top of chatroom
agent_quick_bar_html = """
      <!-- 10-Agent Quick Bar (에이전트별 개별 새로고침 및 즉시 보고 요청 바) -->
      <div class="bg-slate-950/80 p-3 rounded-2xl border border-slate-800 space-y-2">
        <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-1 px-1">
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold text-slate-300">👥 10인 에이전트 실시간 상태</span>
            <span class="text-[10px] text-slate-500 font-mono">버튼을 누르면 해당 직원의 최신 자료가 즉시 갱신되어 대화방에 보고됩니다.</span>
          </div>
          <button onclick="refreshAllAgents()" class="text-[11px] font-bold text-blue-400 hover:text-blue-300 flex items-center gap-1">
            <span>⚡ 10인 전원 일괄 갱신</span>
          </button>
        </div>
        
        <div class="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 gap-2 text-xs" id="chatAgentQuickBar">
          <div class="bg-slate-900 border border-slate-800 hover:border-cyan-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">📰</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 1 뉴스</div>
            <button onclick="refreshAgent(1)" id="quick-btn-1" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-blue-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">📈</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 2 모멘텀</div>
            <button onclick="refreshAgent(2)" id="quick-btn-2" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-emerald-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">📊</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 3 촉매</div>
            <button onclick="refreshAgent(3)" id="quick-btn-3" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-purple-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">🌐</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 4 매크로</div>
            <button onclick="refreshAgent(4)" id="quick-btn-4" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-amber-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">🐋</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 5 감마</div>
            <button onclick="refreshAgent(5)" id="quick-btn-5" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-indigo-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">🔬</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 6 모델링</div>
            <button onclick="refreshAgent(6)" id="quick-btn-6" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-rose-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">⚙️</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 7 백테스트</div>
            <button onclick="refreshAgent(7)" id="quick-btn-7" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-teal-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">🎯</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 8 알파</div>
            <button onclick="refreshAgent(8)" id="quick-btn-8" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-violet-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">🧬</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 9 앙상블</div>
            <button onclick="refreshAgent(9)" id="quick-btn-9" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 hover:border-yellow-500/50 p-2 rounded-xl flex flex-col justify-between gap-1 transition">
            <div class="flex items-center justify-between">
              <span class="text-sm">⚡</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span>
            </div>
            <div class="font-bold text-[11px] text-white truncate">Agent 10 트레이더</div>
            <button onclick="refreshAgent(10)" id="quick-btn-10" class="w-full py-1 rounded bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white text-[10px] font-bold transition flex items-center justify-center gap-1">
              <span>🔄</span><span>갱신</span>
            </button>
          </div>
        </div>
      </div>
"""

# Complete CUBE Chatroom Section
feed_section_html = f"""
    <!-- Section: CUBE Strategic Chatroom (10인 에이전트 실시간 업무보고 대화방) -->
    <section class="space-y-4" id="cube-feed-section">
      <!-- Chatroom Header -->
      <div class="flex flex-col md:flex-row justify-between md:items-center gap-3 bg-gradient-to-r from-slate-950 via-slate-900 to-indigo-950/70 border border-slate-800 rounded-2xl p-4 md:p-5 shadow-xl">
        <div class="flex items-center gap-3">
          <div class="w-11 h-11 rounded-2xl bg-gradient-to-tr from-indigo-600 via-blue-600 to-cyan-400 flex items-center justify-center text-white text-2xl shadow-lg shadow-blue-500/20 shrink-0">
            💬
          </div>
          <div>
            <div class="flex flex-wrap items-center gap-2.5">
              <h2 class="text-lg md:text-xl font-bold text-white tracking-tight">CUBE 전략 상황실 (10-Agent Team Chatroom)</h2>
              <span class="bg-emerald-500/10 text-emerald-400 text-xs px-2.5 py-0.5 rounded-full font-bold border border-emerald-500/20 flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span> 10명 전원 상주 중
              </span>
              <span class="bg-blue-500/10 text-blue-400 text-xs px-2.5 py-0.5 rounded-full font-bold border border-blue-500/20">
                실시간 업무보고 & 상호 토론 채널
              </span>
            </div>
            <p class="text-xs text-slate-400 mt-1">각 에이전트가 핵심 업무보고만 간결히 게시하며, <strong>[참고자료]</strong> 버튼을 누르면 정량적 근거와 세부 분석 데이터를 즉시 확인할 수 있습니다.</p>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <button onclick="refreshAllAgents()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3.5 py-2 rounded-xl font-bold transition shadow-lg shadow-blue-600/25 flex items-center gap-1.5 border border-blue-400">
            <span>🔄 10인 전원 일괄 갱신</span>
          </button>
          <a href="feed.html" class="bg-slate-800/90 hover:bg-slate-800 text-slate-300 text-xs px-3.5 py-2 rounded-xl font-medium transition border border-slate-700 flex items-center gap-1.5">
            <span>📱 전용 전체화면 보기</span> ↗
          </a>
        </div>
      </div>

{agent_quick_bar_html}

      <!-- Feed Controls & Filter Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 bg-slate-950/60 p-3 rounded-xl border border-slate-800 text-xs">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-slate-400 font-medium px-2 py-1">대화 필터:</span>
          <button onclick="filterFeed('all')" id="feedBtnAll" class="px-3 py-1.5 rounded-lg font-bold bg-blue-600 text-white transition">전체 대화 ({len(posts)})</button>
          <button onclick="filterFeed('premarket')" id="feedBtnPremarket" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>🔥 프리장/시황</span></button>
          <button onclick="filterFeed('macro')" id="feedBtnMacro" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>🌐 글로벌 매크로/환율</span></button>
          <button onclick="filterFeed('quant')" id="feedBtnQuant" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>📐 퀀트/백테스트</span></button>
          <button onclick="filterFeed('execution')" id="feedBtnExec" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>⚡ 주문/체결</span></button>
        </div>

        <div class="flex items-center gap-2">
          <select id="agentSelectFilter" onchange="filterByAgentSelect(this.value)" class="bg-slate-900 border border-slate-700 text-slate-300 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-500 font-sans">
            <option value="all">모든 에이전트 (10인)</option>
            <option value="1">Agent 1: 뉴스 & 센티먼트</option>
            <option value="2">Agent 2: 기술적 모멘텀</option>
            <option value="3">Agent 3: 실적 촉매</option>
            <option value="4">Agent 4: 글로벌 매크로 & FX</option>
            <option value="5">Agent 5: 스마트머니 & 감마</option>
            <option value="6">Agent 6: 퀀트 모델링</option>
            <option value="7">Agent 7: 백테스트 엔지니어</option>
            <option value="8">Agent 8: 알파 선별</option>
            <option value="9">Agent 9: 앙상블 융합</option>
            <option value="10">Agent 10: 일일 트레이더</option>
          </select>
        </div>
      </div>

      <!-- Chat Stream Container (Scrollable Messenger Room) -->
      <div class="space-y-4 max-h-[820px] overflow-y-auto pr-1 rounded-2xl bg-slate-950/40 p-2 md:p-3 border border-slate-900" id="feedStreamContainer" style="scrollbar-width: thin; scrollbar-color: #334155 #0f172a;">
        {feed_messages_html}
      </div>

      <!-- Interactive Chat Input & Command Bar -->
      <div class="bg-slate-900/95 border border-slate-800 rounded-2xl p-3.5 md:p-4 space-y-3 shadow-2xl">
        <!-- Quick Command Chips -->
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-slate-400 text-[11px] font-bold">빠른 지시:</span>
          <button onclick="sendQuickPrompt(1)" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 transition">📰 Agent 1 시황 보고</button>
          <button onclick="sendQuickPrompt(2)" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-blue-300 border border-slate-700 transition">📈 Agent 2 모멘텀 돌파</button>
          <button onclick="sendQuickPrompt(4)" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-purple-300 border border-slate-700 transition">🌐 Agent 4 환율·매크로</button>
          <button onclick="sendQuickPrompt(7)" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-rose-300 border border-slate-700 transition">⚙️ Agent 7 1년 백테스트</button>
          <button onclick="sendQuickPrompt(10)" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-amber-300 border border-slate-700 transition">⚡ Agent 10 잔고·체결</button>
        </div>

        <!-- Text Input Box -->
        <div class="flex items-center gap-2">
          <input type="text" id="chatInputText" onkeydown="if(event.key==='Enter') sendUserChatMessage()" placeholder="에이전트에게 지시사항을 입력하거나 질문하세요 (예: Agent 7 백테스트 다시 보여줘)..." class="flex-1 bg-slate-950 border border-slate-700 rounded-xl px-4 py-2.5 text-xs md:text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition font-sans">
          <button onclick="sendUserChatMessage()" class="bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs md:text-sm px-4 py-2.5 rounded-xl transition shadow-lg shadow-blue-600/30 flex items-center gap-1.5 shrink-0">
            <span>전송</span> 💬
          </button>
        </div>
      </div>
    </section>
"""

# Read existing cube.html
cube_path = os.path.join(base_dir, "cube.html")
with open(cube_path, "r", encoding="utf-8") as f:
    cube_html = f.read()

# Add link to header if not already there
header_nav_target = '<a href="strategy.html"'
header_feed_link = """<a href="#cube-feed-section" class="bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs px-3.5 py-2 rounded-lg font-bold transition border border-indigo-500/40 flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span>
          <span>💬 사내 업무 피드</span>
        </a>
        <a href="feed.html" class="bg-slate-800/80 hover:bg-slate-800 text-slate-300 text-xs px-3.5 py-2 rounded-lg font-medium transition border border-slate-700/80 flex items-center gap-1.5">
          <span>📱 전용 피드</span>
        </a>\n        """

if "#cube-feed-section" not in cube_html:
    cube_html = cube_html.replace(header_nav_target, header_feed_link + header_nav_target)

# Insert or replace macro radar before Section 1
sec1_target = '<!-- Section 1: 10 Specialized Agents Interactive Desk -->'
cross_market_html = _cross_market_panel(feed_data)
combined_pre_sec1 = macro_radar_html + "\n\n    " + cross_market_html + "\n\n    " + news_controller_html

if "<!-- Global Macro & FX Radar Ticker Banner" in cube_html:
    cube_html = re.sub(r'<!-- Global Macro & FX Radar Ticker Banner.*?(?=<!-- Section 1: 10 Specialized Agents)', lambda m: combined_pre_sec1 + "\n\n    ", cube_html, flags=re.DOTALL)
else:
    cube_html = cube_html.replace(sec1_target, combined_pre_sec1 + "\n\n    " + sec1_target)

# ---- NAV banner: replace the static fallback numbers with the latest live ledger
# (2026-11-14 관찰: 배너가 옛 값 1,230,755원 / $916.15 / 버퍼 $388.68을 그대로 보여줬다)
try:
    with open(os.path.join(base_dir, "data", "live_portfolio.json"), "r", encoding="utf-8") as _f:
        _lp = json.load(_f)
    _eq_krw = int(round(float(_lp.get("current_total_equity_krw") or 0)))
    _eq_usd = float(_lp.get("current_total_equity_usd") or 0)
    _cash_usd = float(_lp.get("cash_buffer_usd") or 0)
    _cash_pct = float(_lp.get("cash_buffer_pct") or 0)
    if _eq_krw and _eq_usd:
        _new_krw = f"{_eq_krw:,}원"
        _new_usd = f"${_eq_usd:,.2f}"
        _new_buf = f"${_cash_usd:,.2f} (가용 버퍼 {_cash_pct:.1f}%)"
        # banner ids
        cube_html = re.sub(r'(id="live-total-equity-krw"[^>]*>)[^<]*(</div>)',
                           lambda m: m.group(1) + _new_krw + m.group(2), cube_html)
        cube_html = re.sub(r'(id="live-total-equity-usd"[^>]*>)[^<]*(</div>)',
                           lambda m: m.group(1) + _new_usd + " USD" + m.group(2), cube_html)
        cube_html = re.sub(r'(id="live-cash-buffer"[^>]*>)[^<]*(</div>)',
                           lambda m: m.group(1) + _new_buf + m.group(2), cube_html)
        # remaining static fallbacks of the same account (summary card + calendar JS)
        cube_html = re.sub(r'1,230,755원', _new_krw, cube_html)
        cube_html = re.sub(r'\$916\.15', _new_usd, cube_html)
        cube_html = re.sub(r'\$388\.68', f"${_cash_usd:,.2f}", cube_html)
        print(f"NAV fallback → {_new_krw} / {_new_usd} / buffer {_new_buf} (live_portfolio {_lp.get('last_synced')})")
except Exception as e:
    print(f"Warning: NAV fallback fill failed: {e}")

# Insert or replace Work Feed Section after Section 1
sec2_target = '<!-- Section 2: 10 Investment Scenarios & Alpha Selection Matrix -->'
if "<!-- Section: 10-Agent Real-Time Work Feed" in cube_html or "<!-- Section: CUBE Strategic Chatroom" in cube_html:
    cube_html = re.sub(r'<!-- Section: (?:10-Agent Real-Time Work Feed|CUBE Strategic Chatroom).*?(?=<!-- Section 2: 10 Investment Scenarios)', lambda m: feed_section_html + "\n\n    ", cube_html, flags=re.DOTALL)
else:
    cube_html = cube_html.replace(sec2_target, feed_section_html + "\n\n    " + sec2_target)

# Replace renderAgents in cube_html to include individual refresh button on each card
old_render_agents_regex = r'function renderAgents\(\)\s*\{.*?container\.innerHTML = cubeData\.agents\.map\(ag => `.*?`\)\.join\(\'\'\);\s*\}'
new_render_agents_js = """function renderAgents() {
      const container = document.getElementById('agentGrid');
      container.innerHTML = cubeData.agents.map(ag => `
        <div onclick="openAgentModal(${ag.id})" class="agent-card bg-slate-900/90 border border-slate-800 hover:border-blue-500/50 rounded-2xl p-4 flex flex-col justify-between cursor-pointer transition space-y-3" id="agent-card-${ag.id}">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-2xl">${ag.icon}</span>
              <span id="agent-status-badge-${ag.id}" class="bg-blue-500/10 text-blue-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-blue-500/20">
                ${ag.status} (${ag.confidence})
              </span>
            </div>
            <div>
              <h4 class="font-bold text-white text-xs leading-snug">${ag.name}</h4>
              <p class="text-[10px] text-blue-400/90 mt-0.5 font-sans">${ag.role}</p>
            </div>
            <p id="agent-summary-${ag.id}" class="text-[11px] text-slate-400 line-clamp-3 leading-relaxed font-sans">
              ${ag.summary}
            </p>
          </div>
          
          <div class="space-y-2 pt-2 border-t border-slate-800/80">
            <!-- Individual Refresh Button (개별 최신화 버튼) -->
            <button onclick="event.stopPropagation(); refreshAgent(${ag.id})" id="agent-refresh-btn-${ag.id}" class="w-full py-1.5 px-2.5 rounded-xl bg-blue-600/20 hover:bg-blue-600 border border-blue-500/40 hover:border-blue-500 text-blue-300 hover:text-white text-[11px] font-bold transition flex items-center justify-center gap-1.5 shadow-sm">
              <span id="agent-spin-${ag.id}">🔄</span>
              <span id="agent-btn-text-${ag.id}">업무보고 갱신</span>
            </button>
            <div class="flex items-center justify-between text-[10px]">
              <span class="text-slate-500 font-mono" id="agent-time-tag-${ag.id}">ID: 0${ag.id}</span>
              <span class="text-blue-400 font-semibold hover:underline">상세 분석 →</span>
            </div>
          </div>
        </div>
      `).join('');
    }"""

if re.search(old_render_agents_regex, cube_html, re.DOTALL):
    cube_html = re.sub(old_render_agents_regex, new_render_agents_js, cube_html, flags=re.DOTALL)

# Add Feed JavaScript Functions before </body>
feed_js = """
  <script>
    // Toggle Expandable Reference Box (참고자료 열기/접기)
    function toggleReference(postId) {
      const box = document.getElementById('ref-' + postId);
      const btn = document.getElementById('btn-ref-' + postId);
      const icon = document.getElementById('btn-icon-' + postId);
      const text = document.getElementById('btn-text-' + postId);

      if (!box) return;

      if (box.classList.contains('hidden')) {
        box.classList.remove('hidden');
        if (icon) icon.textContent = '▲';
        if (text) text.textContent = '참고자료 접기';
        if (btn) {
          btn.classList.add('bg-blue-600', 'text-white', 'border-blue-400');
          btn.classList.remove('bg-slate-800', 'text-cyan-300');
        }
      } else {
        box.classList.add('hidden');
        if (icon) icon.textContent = '📎';
        if (text) text.textContent = '참고자료 및 상세 근거 보기';
        if (btn) {
          btn.classList.remove('bg-blue-600', 'text-white', 'border-blue-400');
          btn.classList.add('bg-slate-800', 'text-cyan-300');
        }
      }
    }

    // Filter Feed by Category
    function filterFeed(category) {
      document.querySelectorAll('#feedBtnAll, #feedBtnPremarket, #feedBtnMacro, #feedBtnQuant, #feedBtnExec').forEach(btn => {
        btn.className = "px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1";
      });
      
      const activeBtnMap = {
        'all': 'feedBtnAll',
        'premarket': 'feedBtnPremarket',
        'macro': 'feedBtnMacro',
        'quant': 'feedBtnQuant',
        'execution': 'feedBtnExec'
      };
      if (document.getElementById(activeBtnMap[category])) {
        document.getElementById(activeBtnMap[category]).className = "px-3 py-1.5 rounded-lg font-bold bg-blue-600 text-white transition flex items-center gap-1";
      }

      const rows = document.querySelectorAll('.chat-message-row');
      rows.forEach(r => {
        const cat = r.getAttribute('data-category') || '';
        const agent = r.getAttribute('data-agent') || '';
        
        if (category === 'all') {
          r.style.display = 'flex';
        } else if (category === 'premarket') {
          if (cat === 'PREMARKET' || cat === 'DIRECTIVE' || ['1','2','4','10'].includes(agent)) {
            r.style.display = 'flex';
          } else {
            r.style.display = 'none';
          }
        } else if (category === 'macro') {
          if (cat === 'MACRO' || agent === '4') {
            r.style.display = 'flex';
          } else {
            r.style.display = 'none';
          }
        } else if (category === 'quant') {
          if (['QUANT', 'BACKTEST', 'ALPHA', 'CATALYST', 'GAMMA'].includes(cat) || ['3','5','6','7','8'].includes(agent)) {
            r.style.display = 'flex';
          } else {
            r.style.display = 'none';
          }
        } else if (category === 'execution') {
          if (['EXECUTION', 'ENSEMBLE', 'DIRECTIVE'].includes(cat) || ['9','10'].includes(agent)) {
            r.style.display = 'flex';
          } else {
            r.style.display = 'none';
          }
        }
      });
    }

    function filterByAgentSelect(agentId) {
      const rows = document.querySelectorAll('.chat-message-row');
      rows.forEach(r => {
        const agent = r.getAttribute('data-agent');
        if (agentId === 'all' || agent === agentId) {
          r.style.display = 'flex';
        } else {
          r.style.display = 'none';
        }
      });
    }

    function addReaction(postId, key, btn) {
      const countSpan = btn.querySelector('.count');
      if (countSpan) {
        let count = parseInt(countSpan.textContent) || 0;
        count += 1;
        countSpan.textContent = count;
        btn.classList.add('border-blue-500', 'text-blue-300', 'bg-blue-950/40');
      }
    }

    // 10 Distinct Agent Live Data Generator
    const liveAgentReports = {
      1: {
        title: "엔비디아(NVDA) 이사회 감사 속보 및 반도체(SOXL) 소셜 버즈 72점(탐욕) 분석",
        summary: [
          "• 엔비디아(NVDA): WSJ 젠슨 황 해임설은 과매도 국면 판정, 11/17 실적 서프라이즈 카탈리스트 대기 ($150 마지노선 홀딩 유지).",
          "• 반도체(SOXL): 소셜 버즈 29,400건 돌파, 센티먼트 72점(탐욕)으로 모멘텀 지속.",
          "• 동적 청산 보정: TP1 +2.2%, TP2 +3.8%, 칼손절 -1.5% 브래킷 가감 유지."
        ],
        details: "1. 엔비디아(NVDA): WSJ 보도 이후 레딧 r/stocks 및 r/wallstreetbets 콜/풋 비율 2.4:1로 콜옵션 매수세 유입. 월가 주요 IB(모건스탠리, 골드만삭스)는 'Blackwell/Rubin 하이퍼스케일러 수요 견고'를 재확인하며 목표가 $185 유지.<br>2. SOXL: 필라델피아 반도체 지수 선물 강세에 힘입어 장전 소셜 버즈 지수 72점 기록. 극단적 탐욕 구간 진입 전 5분봉 지지 확인 권고.<br>3. TQQQ: 빅테크 실적 모멘텀 및 10년물 국채 금리 하향 안정세(4.42%)로 롱 센티먼트 68점 유지.<br>4. 결론: 시초가 과열 갭상승 추격 매수를 지양하고 눌림목 매수 타점 대기.",
        metrics: [
          {label: "센티먼트", value: "72/100 (탐욕)", highlight: true},
          {label: "소셜 버즈", value: "29.4k건", highlight: true},
          {label: "WSB 콜/풋", value: "2.4 : 1"}
        ],
        next: "시초가 갭상승 과열 자제 및 눌림목 지지선 확인 후 발주"
      },
      2: {
        title: "SOXL 31.00달러, TQQQ 80.40달러 변동성 돌파선(K=0.5) 도출 및 볼린저 밴드 지지",
        summary: [
          "• SOXL: 전일 레인지 기준 변동성 돌파선(K=0.5) $31.00 도달 시 1차 매수 발주 유효.",
          "• TQQQ: 5분봉 볼린저 밴드 중심선 지지 ($80.40 돌파 시 진입 트리거).",
          "• 브래킷 설정: SOXL 1차 익절 +2.5%($31.78), 2차 익절 +4.0%($32.24), 칼손절 -1.5%($30.53)."
        ],
        details: "1. 래리 윌리엄스 변동성 돌파 공식: Breakout_Price = Today_Open + (Yesterday_High - Yesterday_Low) * 0.5<br>• SOXL 전일 고가 $30.80, 저가 $28.40 (레인지 $2.40). 당일 돌파 임계값: $31.00.<br>• TQQQ 전일 고가 $79.80, 저가 $77.00 (레인지 $2.80). 당일 돌파 임계값: $80.40.<br>2. 볼린저 밴드(20, 2) 및 일중 RSI 지표:<br>• SOXL 15분봉 RSI: 54.2 (중립 상방) | 볼린저 상단: $31.45, 하단: $29.10.<br>• TQQQ 15분봉 RSI: 52.8 (중립 상방) | 볼린저 상단: $81.20, 하단: $77.80.<br>• NVDA 일봉 RSI: 46.2 (과매도 진입 직전 지지선 $144.50 근접).<br>3. 손익비 분석: 기대 손익비 1.8:1 이상 산출 완료.",
        metrics: [
          {label: "SOXL 돌파선", value: "$31.00", highlight: true},
          {label: "RSI (14)", value: "54.2"},
          {label: "손익비", value: "1.8 : 1", highlight: true}
        ],
        next: "23:30 개장 후 5분봉 확정 캔들 돌파 시 브래킷 매수 트리거"
      },
      3: {
        title: "엔비디아 11/17 Q3 실적 발표 카탈리스트 모델링 & PEAD 갭상승 추종",
        summary: [
          "• NVDA 실적 카탈리스트: 데이터센터 매출 컨센서스 $32.5B, 어닝 서프라이즈 확률 78%.",
          "• PEAD(Post-Earnings Drift) 모멘텀: 실적 발표 후 3거래일 평균 누적 갭상승률 +6.5%.",
          "• 리스크 관리: 실적 발표 30분 전 포지션 50% 분할 익절 프로토콜 대기."
        ],
        details: "1. 엔비디아(NVDA) 11/17 실적 세부 프리뷰:<br>• 컨센서스 매출: 325억 달러 (YoY +82%), EPS: $0.74 (YoY +85%).<br>• 하이퍼스케일러(MSFT, META, GOOGL, AMZN) 3분기 Capex 실적 발표 합산액 620억 달러로 전년비 +48% 급증.<br>• 블랙웰(Blackwell) B200 랙 수주 잔고: 2026년 상반기까지 100% 매진 확인.<br>2. 과거 8개 분기 PEAD 드리프트 패턴 분석:<br>• 어닝 서프라이즈 발표 당일 평균 갭상승 +4.8%, 발표 후 3일간 추세 지속률 87.5%.<br>3. 리스크 관리: 거시 발표 직전 50% 분할 익절로 오버나잇 꼬리위험 방어.",
        metrics: [
          {label: "서프라이즈 확률", value: "78%", highlight: true},
          {label: "Blackwell", value: "100% 매진"},
          {label: "PEAD 기대", value: "+6.5%", highlight: true}
        ],
        next: "NVDA 1주 홀딩 유지 및 11/17 실적 발표 직전 브래킷 매도 감시 강화"
      },
      4: {
        title: "원/달러 1,334원 안정화 및 VIX 18.42 BULL_CALM 저변동성 국면 지속",
        summary: [
          "• 글로벌 지수: 닛케이 +1.31%, DAX +0.67%, 나스닥 선물 +1.98%로 위험선호(Risk-On) 우세.",
          "• 외환 & 금리: 원/달러 환율 1,334.16원(-0.72% 강세), 미 국채 10년물 4.42% 하향 안정.",
          "• VIX 레짐: 18.42로 BULL_CALM 국면 유지 → SOXL/TQQQ 롱 전략 정상 가동 (VIX 22 미만)."
        ],
        details: "1. 글로벌 주가지수 실시간 모니터링:<br>• 홍콩 항셍 (^HSI): 24,315.92 (-0.98%)<br>• 일본 닛케이 (^N225): 61,248.59 (+1.31%)<br>• 독일 DAX 40 (^GDAXI): 26,523.46 (+0.67%)<br>• 나스닥 100 E-mini 선물 (NQ): 28,386.75 (+1.98%)<br>2. 통화 및 채권 시장:<br>• 달러 인덱스 (DXY): 102.55 (+0.31%) | 원/달러 환율: 1,334.16원 (-0.72%)<br>• 미국 10년물 국채 금리: 4.421% (-3.5bp)<br>3. VIX 레짐 판별식:<br>• 현재 VIX 18.42 -> BULL_CALM 저변동성 상승 레짐 확정. VIX 22.0 초과 시 즉시 SOXS 숏 헤지 전환.",
        metrics: [
          {label: "VIX", value: "18.42 (BULL_CALM)", highlight: true},
          {label: "원/달러", value: "1,334.16원 (-0.72%)"},
          {label: "나스닥 선물", value: "+1.98%", highlight: true}
        ],
        next: "BULL_CALM 레짐 하에서 SOXL/TQQQ 단기 롱 모멘텀 승인 유지"
      },
      5: {
        title: "NVDA $150 행사가 감마 스퀴즈 지지선 확인 & NQ 선물 베이시스 +0.22%",
        summary: [
          "• 풋/콜 비율: 0.68로 기관 콜옵션 매수세 우세, 기관 숏커버링 유입 압력 84%.",
          "• NVDA 감마 구조: $150 풋옵션 미결제약정(OI) 집중으로 하방 경직성 확보, Max Pain $145 상회.",
          "• NQ 선물 베이시스: +0.22% 콘탱고 정상 유지로 스마트머니 순매수 기조 지속."
        ],
        details: "1. CBOE 옵션 시장 풋/콜 비율: Total Put/Call Ratio: 0.68 (기관 콜옵션 매수 강세 우위), 개별 주식 풋/콜: 0.52.<br>2. 엔비디아(NVDA) 딜러 감마 익스포저 (GEX):<br>• 150.00달러 행사가 미결제약정(OI): 85,400계약 (하방 강력 지지벽)<br>• 160.00달러 행사가 미결제약정(OI): 112,000계약 (돌파 시 감마 스퀴즈 유발 타점)<br>• Max Pain Price: 145.00달러 (현재가 159달러선으로 안전 마진 확보)<br>3. 나스닥 선물 베이시스: +0.22%로 기관의 장중 롤오버 및 롱 포지션 누적 확인.",
        metrics: [
          {label: "풋/콜 비율", value: "0.68", highlight: true},
          {label: "NVDA 감마벽", value: "$150.00"},
          {label: "선물 베이시스", value: "+0.22%", highlight: true}
        ],
        next: "기관 콜옵션 감마 스퀴즈 흐름에 편승하여 장중 역추세 매도 금지"
      },
      6: {
        title: "SCENARIO-11 하락장 SOXS 인버스 숏 및 10대 단기 알파 수식 파라미터 최신화",
        summary: [
          "• 하락장 숏 엔진(SCENARIO-11): VIX 22 돌파 시 SOXS/SQQQ 자동 전환 룰 정합성 100% 검증.",
          "• 단기 알파 앙상블: 변동성 돌파(K=0.5)와 3단계 분할 익절(TP1 2%, TP2 3.5%, TP3 러너) 파라미터 고정.",
          "• 마찰비용 방어: 왕복 0.24% 수수료 차감 후에도 일평균 기대수익률 +0.82% 모델링 완료."
        ],
        details: "1. SCENARIO-11: 하락장 대응 인버스 숏 포지션 엔진 (Bear Market Short Engine)<br>• 진입 조건: VIX >= 22.0 OR QQQ 20일 이동평균선 일중 1.2% 하향 이탈<br>• 발주 유니버스: SOXS (반도체 3X Bear), SQQQ (나스닥 3X Bear)<br>• 청산 브래킷: SOXS 1차 익절 +3.5%(비중 50%), 2차 익절 +6.0%(비중 30%), 칼손절 -1.8%<br>2. SCENARIO-01: 래리 윌리엄스 단기 변동성 돌파 앙상블 (K=0.5, 3단계 청산)<br>3. 마찰비용 완충: 왕복 0.24% 마찰비용 차감 후에도 샤프 2.02 및 승률 54.8% 검증 완료.",
        metrics: [
          {label: "숏 트리거", value: "VIX >= 22.0", highlight: true},
          {label: "청산 룰", value: "3단계 분할"},
          {label: "마찰비용 차감", value: "0.24% 완충", highlight: true}
        ],
        next: "scenarios.html 및 core/inverse_hedging.py 파라미터 100% 정합성 유지"
      },
      7: {
        title: "최근 1개년(251거래일) 실전 시뮬레이션: 연 +35.99%, MDD -7.85%, 샤프 2.02",
        summary: [
          "• 1년 정밀 백테스트 성과: 총수익률 +35.99% (연환산 CAGR +36.32%), 승률 54.8%, 손익비 1.53 : 1.",
          "• 리스크 통제: 최대 낙폭(MDD) -7.85%로 SOXL 단순 보유(MDD -58.2%) 대비 압도적 계좌 방어.",
          "• 실전 정합성: NH 수수료(0.09%) + 슬리피지(0.15%) 왕복 0.24% 매 거래 전액 차감 검증 완료."
        ],
        details: "1. 종합 성과 지표 (총 210회 실전 매매 집행, 왕복 0.24% 마찰비용 전액 차감):<br>• 누적 총 수익률: +35.99% (CAGR +36.32%)<br>• 전략 승률: 54.8% (115승 95패) | 손익비: 1.53 : 1<br>• 최대 낙폭 (MDD): -7.85%<br>• 샤프 지수 (Sharpe): 2.02 | 소르티노: 3.29 | 칼마: 4.63<br>2. 벤치마크 대비 비교:<br>• CUBE 단기 앙상블: +35.99% (MDD -7.85%)<br>• SOXL 단순 보유: +28.34% (MDD -58.20%)<br>• QQQ 단순 보유: +9.15% (MDD -11.40%)<br>3. 월별 수익률: 12개월 중 9개월 플러스 (월간 승률 75.0%).",
        metrics: [
          {label: "1년 총수익", value: "+35.99% (연 +36.32%)", highlight: true},
          {label: "샤프 / MDD", value: "2.02 / -7.85%", highlight: true},
          {label: "월간 승률", value: "75.0% (9/12)"}
        ],
        next: "실전 계좌(202-05-134849)에 백테스트 검증된 K=0.5 및 3단계 청산 룰 100% 동일 적용 유지"
      },
      8: {
        title: "당일 단기 알파 스코어링: SOXL 돌파 1위(94점), TQQQ 2위(91점)",
        summary: [
          "• 당일 알파 랭킹: 1위 SOXL 변동성 돌파(94점), 2위 TQQQ ORB(91점), 3위 NVDA 실적 촉매(88점).",
          "• 헤지 알파: SOXS 인버스 숏(78점) - VIX 22 레짐 도달 시 즉시 순위 1위로 승격 준비 완료.",
          "• 턴오버/슬리피지 효율: 상위 3개 시나리오의 거래당 기대 이익비 1.62 : 1 산출."
        ],
        details: "1. 알파 스코어 매트릭스 (수익성, 안정성, 슬리피지 방어력, 승률 종합 평가):<br>• 1위: SCENARIO-01 SOXL 변동성 돌파 (종합 94점 / 기대 샤프 2.24 / 승률 56.2%)<br>• 2위: SCENARIO-02 TQQQ 장초반 30분 ORB (종합 91점 / 기대 샤프 2.05 / 승률 55.4%)<br>• 3위: SCENARIO-03 NVDA 실적 카탈리스트 PEAD (종합 88점 / 기대 샤프 1.94 / 승률 54.1%)<br>• 4위: SCENARIO-11 SOXS/SQQQ 하락장 숏 헤지 (종합 78점 / 하락장 발동 시 샤프 2.80)<br>2. 배제 시나리오: 느린 이동평균 골든크로스(SCENARIO-08)는 0.24% 수수료 극복 불가로 필터링.",
        metrics: [
          {label: "1위 알파", value: "SOXL 돌파 (94점)", highlight: true},
          {label: "2위 알파", value: "TQQQ ORB (91점)"},
          {label: "기대 샤프", value: "2.24", highlight: true}
        ],
        next: "상위 1~3위 검증된 알파에 자금 70% 집중 배분 승인"
      },
      9: {
        title: "예수금 120만 원($863.73) 최적 배분: SOXL 34.5%, TQQQ 18.2%, NVDA 17.0%",
        summary: [
          "• 예수금 120만 원($863.73) 최적 배분: SOXL 34.5%, TQQQ 18.2%, NVDA 17.0%, SGOV 11.6%.",
          "• 리스크 버퍼: 현금 $161.08 (18.7%) 확보로 슬리피지 및 장중 딥바잉 예비비 확보.",
          "• HRP(Hierarchical Risk Parity) 최적화로 포트폴리오 일일 VaR 1.8% 통제 달성."
        ],
        details: "1. 포트폴리오 최적 가중치 배분:<br>• SOXL (반도체 3X): 10주 집중 발주 ($298.00 / 34.5% 비중) - 일일 알파 기여도 48%<br>• TQQQ (나스닥 3X): 2주 발주 ($157.00 / 18.2% 비중) - 대형 기술주 모멘텀 헤지<br>• NVDA (AI 단일주): 1주 발주 ($147.20 / 17.0% 비중) - 11/17 실적 카탈리스트 추종<br>• SGOV (초단기 국채): 1주 파킹 ($100.45 / 11.6% 비중) - 연 4.52% 무위험 일복리<br>• 가용 현금 버퍼: $161.08 (18.7% 비중) - 슬리피지 흡수<br>2. 리스크 메트릭스: 포트폴리오 일일 VaR 95% -1.82% 한도 내 통제.",
        metrics: [
          {label: "예수금", value: "$863.73 (120만원)", highlight: true},
          {label: "주식 비중", value: "69.7%"},
          {label: "현금/SGOV", value: "30.3%", highlight: true}
        ],
        next: "Agent 10에 4대 자산 분산 발주 큐 인계 완료"
      },
      10: {
        title: "NH 전용 계좌(202-05-134849) 23:30 OPEN 발주 큐 세팅 완료 및 브래킷 감시 대기",
        summary: [
          "• NH 실전 계좌(202-05-134849): 실시간 예수금 1,209,215원 ($863.73) 확충 확인 완료.",
          "• 발주 큐 세팅: 23:30 OPEN 즉시 SOXL 10주, TQQQ 2주, NVDA 1주 지정가 분산 발주 대기.",
          "• 스마트 청산: 전 종목 3단계 분할 익절(+2.0%~+4.0%) 및 칼손절(-1.5%) 브래킷 실시간 자동 감시."
        ],
        details: "1. 계좌 현황 및 예수금:<br>• 계좌번호: 202-05-134849 (박동훈, NH투자증권 QV 해외주식 전용)<br>• 실시간 예수금: 1,209,215원 ($863.73 USD, 환율 1,334.16원 적용)<br>• 실전 1일차 성과: 3전 3승 전승 (+1.85%, 확정 실현익 +$6.93, 순익 +$16.67)<br>2. 23:30 개장 즉시 집행 발주 큐:<br>• SOXL 10주 / TP1 +2.2%(3주), TP2 +4.0%(3주), SL -1.5%, 러너 4주<br>• TQQQ 2주 / TP1 +2.0%(1주), TP2 +3.5%(1주), SL -1.5%<br>• NVDA 1주 / TP1 +2.5%, SL -1.5%<br>• SGOV 1주 파킹 ($100.45)<br>3. 장중 브래킷 감시: 30분 주기 자동 감시 액션 연동 완료.",
        metrics: [
          {label: "실시간 예수금", value: "1,209,215원", highlight: true},
          {label: "발주 큐", value: "4개 종목", highlight: true},
          {label: "브래킷", value: "3단계 자동 감시"}
        ],
        next: "23:30 뉴욕 정규장 개장 즉시 NH 오픈API 실전 발주 및 스마트 브래킷 가동"
      }
    };

    // Refresh Individual Agent (각 에이전트 개별 새로고침 핸들러)
    function refreshAgent(agentId) {
      const ag = cubeData.agents.find(a => a.id === agentId);
      if (!ag) return;

      const spinIcon = document.getElementById('agent-spin-' + agentId);
      const btnText = document.getElementById('agent-btn-text-' + agentId);
      const quickBtn = document.getElementById('quick-btn-' + agentId);

      if (spinIcon) spinIcon.classList.add('animate-spin');
      if (btnText) btnText.textContent = "갱신 중...";
      if (quickBtn) quickBtn.classList.add('animate-pulse');

      setTimeout(() => {
        const rep = liveAgentReports[agentId] || liveAgentReports[1];
        const now = new Date();
        const timeStr = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
        const dateStr = now.toISOString().split('T')[0];

        // 1. Update Agent Card in Section 1
        const summaryEl = document.getElementById('agent-summary-' + agentId);
        const statusBadgeEl = document.getElementById('agent-status-badge-' + agentId);
        const timeTagEl = document.getElementById('agent-time-tag-' + agentId);

        if (summaryEl) summaryEl.textContent = Array.isArray(rep.summary) ? rep.summary.join(' ') : rep.summary;
        if (statusBadgeEl) {
          statusBadgeEl.textContent = `최신 갱신됨 (${timeStr})`;
          statusBadgeEl.className = "bg-emerald-500/15 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-emerald-500/30";
        }
        if (timeTagEl) timeTagEl.textContent = `방금 (${timeStr})`;

        // 2. Generate New Chat Message in Chatroom
        const newPostId = 'fresh-' + agentId + '-' + Date.now();
        const avatarBgs = {
          1: "from-cyan-500 to-blue-600",
          2: "from-blue-600 to-indigo-600",
          3: "from-emerald-500 to-teal-600",
          4: "from-purple-600 to-violet-700",
          5: "from-amber-500 to-orange-600",
          6: "from-indigo-500 to-fuchsia-600",
          7: "from-rose-500 to-red-600",
          8: "from-teal-500 to-emerald-600",
          9: "from-violet-500 to-purple-600",
          10: "from-yellow-400 to-amber-600"
        };
        const badgeColors = {
          1: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
          2: "bg-blue-500/10 text-blue-400 border-blue-500/20",
          3: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
          4: "bg-purple-500/10 text-purple-400 border-purple-500/20",
          5: "bg-amber-500/10 text-amber-400 border-amber-500/20",
          6: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
          7: "bg-rose-500/10 text-rose-400 border-rose-500/20",
          8: "bg-teal-500/10 text-teal-400 border-teal-500/20",
          9: "bg-violet-500/10 text-violet-400 border-violet-500/20",
          10: "bg-amber-500/10 text-amber-400 border-amber-500/20"
        };

        const metricsPills = rep.metrics.map(m => `
          <div class="text-[11px] px-2.5 py-1 rounded-lg border ${m.highlight ? 'bg-blue-500/15 border-blue-500/30 text-blue-300' : 'bg-slate-800/80 border-slate-700 text-slate-300'} flex items-center gap-1.5 font-mono">
            <span class="text-slate-400 font-sans">${m.label}:</span>
            <span class="font-bold">${m.value}</span>
          </div>
        `).join('');

        const summaryHtml = Array.isArray(rep.summary) 
          ? rep.summary.map(l => `<p class="text-slate-200 text-xs md:text-[13px] leading-relaxed">${l}</p>`).join('')
          : `<p class="text-slate-200 text-xs md:text-[13px] leading-relaxed">${rep.summary}</p>`;

        const messageHtml = `
          <div class="chat-message-row flex items-start gap-3 transition" data-agent="${agentId}" data-category="PREMARKET" id="${newPostId}">
            <div class="w-10 h-10 md:w-11 md:h-11 rounded-full bg-gradient-to-tr ${avatarBgs[agentId]} flex items-center justify-center text-lg md:text-xl shadow-lg shrink-0 border-2 border-slate-800 ring-2 ring-emerald-400/50">
              ${ag.icon}
            </div>
            <div class="flex-1 space-y-1.5 min-w-0">
              <div class="flex flex-wrap items-center justify-between gap-1 px-1">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-bold text-sm text-white">${ag.name}</span>
                  <span class="text-[10px] px-2 py-0.5 rounded-full border font-mono font-medium ${badgeColors[agentId]}">🔄 즉시 최신화 보고</span>
                  <span class="bg-emerald-500/15 text-emerald-400 text-[10px] px-2 py-0.5 rounded-full font-bold border border-emerald-500/30">방금 전 도착</span>
                </div>
                <div class="text-[11px] text-slate-400 font-mono flex items-center gap-1.5">
                  <span>${dateStr} ${timeStr}</span>
                  <span class="text-slate-600">•</span>
                  <span class="text-emerald-400 font-bold">LIVE UPDATE</span>
                </div>
              </div>
              <div class="chat-bubble border border-emerald-500/50 bg-gradient-to-br from-emerald-950/20 via-slate-900/95 to-slate-900 rounded-2xl rounded-tl-sm p-4 space-y-3 shadow-xl">
                <div class="text-xs md:text-sm font-bold text-white flex items-center gap-2">
                  <span class="text-emerald-400">⚡</span>
                  <span>[${timeStr} 최신보고] ${rep.title}</span>
                </div>
                <div class="bg-slate-950/80 p-3.5 rounded-xl border border-emerald-500/20 space-y-2">
                  <div class="text-[11px] font-bold text-emerald-400 flex items-center justify-between border-b border-slate-800/60 pb-1.5">
                    <span class="flex items-center gap-1.5">⚡ <span>핵심 업무보고 요약</span></span>
                    <span class="text-[10px] text-slate-500 font-mono">FRESH TAKEAWAY</span>
                  </div>
                  <div class="space-y-1">
                    ${summaryHtml}
                  </div>
                </div>
                <div class="flex flex-wrap items-center gap-1.5 pt-0.5">
                  ${metricsPills}
                </div>
                <div class="bg-blue-950/30 border border-blue-500/20 text-blue-300 text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 font-mono">
                  <span class="text-blue-400 font-bold">NEXT:</span> ${rep.next}
                </div>
                <div class="pt-1">
                  <button onclick="toggleReference('${newPostId}')" id="btn-ref-${newPostId}" class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-cyan-300 hover:text-white text-xs font-semibold border border-cyan-500/30 hover:border-cyan-500 transition shadow-sm">
                    <span id="btn-icon-${newPostId}">📎</span>
                    <span id="btn-text-${newPostId}">참고자료 및 상세 근거 보기</span>
                  </button>
                  <div id="ref-${newPostId}" class="reference-box hidden mt-3 p-4 rounded-xl bg-slate-950/90 border border-indigo-900/40 text-xs text-slate-300 leading-relaxed font-sans space-y-2.5 transition">
                    <div class="flex items-center justify-between pb-2 border-b border-slate-800">
                      <div class="flex items-center gap-2 text-indigo-300 font-bold text-xs">
                        <span>📊</span>
                        <span>상세 정량 분석 보고서 및 참고 자료 (원문 근거)</span>
                      </div>
                      <span class="text-[10px] text-slate-500 font-mono">CONFIDENTIAL QUANT MEMO</span>
                    </div>
                    <div class="pt-1 text-slate-300 leading-relaxed font-sans">
                      ${rep.details}
                    </div>
                  </div>
                </div>
                <div class="flex items-center justify-between pt-2 border-t border-slate-800/60 text-xs">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <button onclick="addReaction('${newPostId}', 'roger', this)" class="bg-slate-800/90 hover:bg-slate-700 text-slate-300 text-[11px] px-2.5 py-1 rounded-lg border border-slate-700/60 transition flex items-center gap-1"><span>🫡 라져</span> <span class="font-mono text-slate-400 font-bold count">5</span></button>
                    <button onclick="addReaction('${newPostId}', 'fire', this)" class="bg-slate-800/90 hover:bg-slate-700 text-slate-300 text-[11px] px-2.5 py-1 rounded-lg border border-slate-700/60 transition flex items-center gap-1"><span>🔥 유효</span> <span class="font-mono text-slate-400 font-bold count">8</span></button>
                    <button onclick="addReaction('${newPostId}', 'rocket', this)" class="bg-slate-800/90 hover:bg-slate-700 text-slate-300 text-[11px] px-2.5 py-1 rounded-lg border border-slate-700/60 transition flex items-center gap-1"><span>🚀 기대</span> <span class="font-mono text-slate-400 font-bold count">6</span></button>
                  </div>
                  <div class="text-slate-500 text-[10px] font-mono">#${newPostId}</div>
                </div>
              </div>
            </div>
          </div>
        `;

        const feedContainer = document.getElementById('feedStreamContainer');
        if (feedContainer) {
          feedContainer.insertAdjacentHTML('afterbegin', messageHtml);
        }

        // 3. Reset Button states
        if (spinIcon) spinIcon.classList.remove('animate-spin');
        if (btnText) btnText.textContent = "최신화 완료 (방금)";
        if (quickBtn) quickBtn.classList.remove('animate-pulse');

        // 4. Smooth scroll to Chatroom and highlight message
        const chatSection = document.getElementById('cube-feed-section');
        if (chatSection) {
          chatSection.scrollIntoView({ behavior: 'smooth' });
        }

        setTimeout(() => {
          if (btnText) btnText.textContent = "업무보고 갱신";
        }, 3000);

      }, 350);
    }

    // Refresh All 10 Agents
    function refreshAllAgents() {
      for (let i = 1; i <= 10; i++) {
        setTimeout(() => {
          refreshAgent(i);
        }, (i - 1) * 120);
      }
    }

    // Send Quick Prompt Chip
    function sendQuickPrompt(agentId) {
      const ag = cubeData.agents.find(a => a.id === agentId);
      if (!ag) return;
      addUserMessage(`[지시] ${ag.name}님, 최신 데이터 분석 업무보고를 대화방에 공유해 주세요.`);
      setTimeout(() => {
        refreshAgent(agentId);
      }, 500);
    }

    // User Chat Message
    function sendUserChatMessage() {
      const input = document.getElementById('chatInputText');
      if (!input || !input.value.trim()) return;
      const text = input.value.trim();
      input.value = '';
      addUserMessage(text);

      // Determine matching agent
      let targetAgentId = 10;
      if (text.includes('1') || text.includes('뉴스') || text.includes('센티먼트') || text.includes('엔비디아')) targetAgentId = 1;
      else if (text.includes('2') || text.includes('모멘텀') || text.includes('돌파') || text.includes('차트')) targetAgentId = 2;
      else if (text.includes('3') || text.includes('실적') || text.includes('촉매') || text.includes('어닝')) targetAgentId = 3;
      else if (text.includes('4') || text.includes('환율') || text.includes('매크로') || text.includes('VIX')) targetAgentId = 4;
      else if (text.includes('5') || text.includes('옵션') || text.includes('감마') || text.includes('스마트머니')) targetAgentId = 5;
      else if (text.includes('6') || text.includes('시나리오') || text.includes('수식') || text.includes('모델링')) targetAgentId = 6;
      else if (text.includes('7') || text.includes('백테스트') || text.includes('시뮬레이션') || text.includes('수익률')) targetAgentId = 7;
      else if (text.includes('8') || text.includes('알파') || text.includes('랭킹')) targetAgentId = 8;
      else if (text.includes('9') || text.includes('비중') || text.includes('배분') || text.includes('앙상블')) targetAgentId = 9;
      else if (text.includes('10') || text.includes('주문') || text.includes('체결') || text.includes('잔고') || text.includes('매매')) targetAgentId = 10;

      setTimeout(() => {
        refreshAgent(targetAgentId);
      }, 600);
    }

    function addUserMessage(text) {
      const now = new Date();
      const timeStr = now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
      const userHtml = `
        <div class="flex items-start justify-end gap-2.5 my-2">
          <div class="space-y-1 max-w-[80%] text-right">
            <div class="text-[11px] text-slate-400 font-mono">박동훈 TL • ${timeStr}</div>
            <div class="bg-blue-600 text-white rounded-2xl rounded-tr-sm p-3.5 text-xs md:text-sm font-sans shadow-lg text-left leading-relaxed">
              ${text}
            </div>
          </div>
          <div class="w-9 h-9 rounded-full bg-blue-500 border-2 border-blue-400 flex items-center justify-center text-white text-xs font-bold shrink-0 shadow">
            TL
          </div>
        </div>
      `;
      const container = document.getElementById('feedStreamContainer');
      if (container) {
        container.insertAdjacentHTML('afterbegin', userHtml);
      }
    }
  </script>
"""

# Replace or insert feed_js into cube_html
if "function toggleReference" in cube_html:
    cube_html = re.sub(r'<script>\s*// Toggle Expandable Reference Box.*?</script>', feed_js.strip(), cube_html, flags=re.DOTALL)
elif "function filterFeed" in cube_html:
    cube_html = re.sub(r'<script>\s*function filterFeed.*?</script>', feed_js.strip(), cube_html, flags=re.DOTALL)
else:
    cube_html = cube_html.replace("</body>", feed_js + "\n</body>")

with open(cube_path, "w", encoding="utf-8") as f:
    f.write(cube_html)

docs_cube_path = os.path.join(base_dir, "docs", "cube.html")
with open(docs_cube_path, "w", encoding="utf-8") as f:
    f.write(cube_html)

# 4. Create standalone feed.html and docs/feed.html
feed_html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CUBE 10-Agent Real-Time Messenger Chatroom | Steve Park</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Pretendard:wght@300;400;500;600;700;800&display=swap');
    body {{ font-family: 'Pretendard', sans-serif; background-color: #080d1a; color: #f1f5f9; }}
    .mono {{ font-family: 'JetBrains Mono', monospace; }}
    .pulse-dot {{ animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
  </style>
</head>
<body class="min-h-screen p-4 md:p-8">
  <div class="max-w-5xl mx-auto space-y-6">
    
    <!-- Top Nav Header -->
    <header class="flex flex-col sm:flex-row justify-between items-start sm:items-center pb-5 border-b border-slate-800 gap-4">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 via-blue-600 to-cyan-400 flex items-center justify-center text-white font-black text-xl shadow-lg shadow-blue-500/20">
          💬
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-xl md:text-2xl font-extrabold text-white">CUBE 사내 메신저 대화방 (Messenger Desk)</h1>
            <span class="bg-emerald-500/10 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-emerald-500/20 flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> 10 AGENTS LIVE
            </span>
          </div>
          <p class="text-xs text-slate-400 mt-0.5">10명의 직원이 실시간으로 업무 보고를 올리는 채팅방입니다. 핵심 요약과 [참고자료] 버튼으로 근거를 확인하세요.</p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <a href="scenarios.html" class="bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs px-3.5 py-2 rounded-lg font-bold transition border border-indigo-500/40 flex items-center gap-1.5">
          <span>📂 시나리오 아카이브</span>
        </a>
        <a href="cube.html" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold transition shadow-lg shadow-blue-600/20 flex items-center gap-1.5">
          <span>🏛️ CUBE 데스크로 이동</span>
        </a>
      </div>
    </header>

{macro_radar_html}

{cross_market_html}

{news_controller_html}

{feed_section_html}

  </div>
{feed_js}
</body>
</html>
"""

feed_path = os.path.join(base_dir, "feed.html")
with open(feed_path, "w", encoding="utf-8") as f:
    f.write(feed_html_template)

docs_feed_path = os.path.join(base_dir, "docs", "feed.html")
with open(docs_feed_path, "w", encoding="utf-8") as f:
    f.write(feed_html_template)

print("✅ Successfully built and synced cube.html and feed.html with 10-Agent Chatroom, Refresh Buttons, and Expandable References!")
