#!/usr/bin/env python3
"""
Build script to integrate the Global Macro & FX Radar, 10-Agent Real-Time Work Feed,
and generate the standalone feed.html for CUBE.
"""

import os
import json
import re

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
feed_json_path = os.path.join(base_dir, "data", "agent_feed.json")

with open(feed_json_path, "r", encoding="utf-8") as f:
    feed_data = json.load(f)

posts = feed_data.get("posts", [])
gm = feed_data.get("global_macro", {})

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
          <span class="font-mono text-cyan-300 font-bold bg-slate-800 px-2 py-0.5 rounded border border-slate-700/80">{feed_data.get('last_updated', '2026-11-11 18:20:00')}</span>
        </div>
      </div>

      <!-- Macro Ticker Grid -->
      <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2.5 text-xs font-mono">
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">홍콩 항셍 (^HSI)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('hang_seng', {}).get('level', 24315.92):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('hang_seng', {}).get('change', '+1.25%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">일본 닛케이 (^N225)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('nikkei_225', {}).get('level', 61248.59):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('nikkei_225', {}).get('change', '+0.85%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">독일 DAX 40 (^GDAXI)</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('dax_40', {}).get('level', 26621.14):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('dax_40', {}).get('change', '+0.45%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">나스닥 선물 (NQ)</div>
          <div class="text-xs md:text-sm font-bold text-cyan-300 mt-0.5">{gm.get('nasdaq_fut', {}).get('level', 28314.25):,.2f}</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('nasdaq_fut', {}).get('change', '+0.38%')}</div>
        </div>
        <div class="bg-slate-900/90 border border-blue-900/40 p-2.5 rounded-xl bg-blue-950/20">
          <div class="text-[10px] text-blue-300 font-sans">달러 인덱스 (DXY)</div>
          <div class="text-xs md:text-sm font-bold text-blue-400 mt-0.5">{gm.get('dxy_dollar_index', {}).get('level', 102.43):,.2f}</div>
          <div class="text-[10px] text-blue-300 font-bold">{gm.get('dxy_dollar_index', {}).get('change', '-0.28%')} (약세)</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">원/달러 환율 (KRW)</div>
          <div class="text-xs md:text-sm font-bold text-slate-200 mt-0.5">{gm.get('usd_krw', {}).get('level', 1334.83):,.2f}원</div>
          <div class="text-[10px] text-emerald-400 font-bold">{gm.get('usd_krw', {}).get('change', '-4.2원')} (원화 강세)</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">
          <div class="text-[10px] text-slate-400 font-sans">엔/달러 환율 (JPY)</div>
          <div class="text-xs md:text-sm font-bold text-slate-200 mt-0.5">{gm.get('usd_jpy', {}).get('level', 158.85):,.2f}엔</div>
          <div class="text-[10px] text-slate-400 font-bold">{gm.get('usd_jpy', {}).get('change', '-0.15엔')}</div>
        </div>
        <div class="bg-slate-900/90 border border-emerald-900/40 p-2.5 rounded-xl bg-emerald-950/20">
          <div class="text-[10px] text-emerald-300 font-sans">VIX 변동성 레짐</div>
          <div class="text-xs md:text-sm font-bold text-emerald-400 mt-0.5">{gm.get('vix', {}).get('level', 18.92):,.2f}</div>
          <div class="text-[10px] text-emerald-300 font-bold">BULL_CALM 저변동성</div>
        </div>
      </div>
    </div>
"""

# 2. Build Work Feed Section HTML
feed_cards_html = ""
for post in posts:
    is_pinned = post.get("pinned", False)
    pinned_border = "border-red-500/50 bg-gradient-to-br from-red-950/30 via-slate-900 to-slate-900" if is_pinned else "border-slate-800 bg-slate-900/80"
    
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
          <button onclick="addReaction('{post.get('id')}', '{k}', this)" class="bg-slate-800 hover:bg-slate-700/80 text-slate-300 text-[11px] px-2.5 py-1 rounded-lg border border-slate-700/60 transition flex items-center gap-1">
            <span>{lbl}</span>
            <span class="font-mono text-slate-400 font-bold count">{v}</span>
          </button>
        """

    content_formatted = post.get("content", "").replace("\n\n", "<br><br>").replace("\n", "<br>")

    feed_cards_html += f"""
      <article class="feed-post border {pinned_border} rounded-xl p-4 md:p-5 space-y-3 transition hover:border-slate-700" data-agent="{post.get('agent_id')}" data-category="{post.get('category')}">
        <div class="flex items-start justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-tr {post.get('avatar_bg')} flex items-center justify-center text-lg shadow-md shrink-0">
              {post.get('icon')}
            </div>
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-bold text-sm text-white">{post.get('agent_name')}</span>
                <span class="text-[11px] px-2 py-0.5 rounded-full border font-mono font-medium {post.get('badge_color')}">{post.get('badge')}</span>
              </div>
              <div class="text-[11px] text-slate-400 mt-0.5 font-mono flex items-center gap-2">
                <span>{post.get('timestamp')}</span>
                <span>•</span>
                <span class="text-cyan-400">{post.get('time_ago')}</span>
              </div>
            </div>
          </div>
          <div class="text-xs text-slate-500 font-mono">
            #{post.get('id')}
          </div>
        </div>

        <h3 class="text-sm md:text-base font-bold text-slate-100 flex items-center gap-1.5">
          {post.get('title')}
        </h3>

        <div class="text-xs md:text-[13px] text-slate-300 leading-relaxed font-sans bg-slate-950/40 p-3.5 rounded-xl border border-slate-800/80">
          {content_formatted}
        </div>

        {f'<div class="flex flex-wrap items-center gap-2 pt-1">{metrics_pills}</div>' if metrics_pills else ''}

        {f'<div class="bg-blue-950/30 border border-blue-500/20 text-blue-300 text-xs px-3 py-1.5 rounded-lg flex items-center gap-2 font-mono"><span class="text-blue-400 font-bold">NEXT:</span> {post.get("action_required")}</div>' if post.get("action_required") else ''}

        <div class="flex items-center justify-between pt-2 border-t border-slate-800/60 text-xs">
          <div class="flex flex-wrap items-center gap-1.5">
            {rx_pills}
          </div>
          <div class="text-slate-500 text-[11px]">
            <span>1시간마다 자동 갱신됨</span>
          </div>
        </div>
      </article>
    """

feed_section_html = f"""
    <!-- Section: 10-Agent Real-Time Work Feed (10인 실시간 사내 업무 공유방) -->
    <section class="space-y-4" id="cube-feed-section">
      <div class="flex flex-col md:flex-row justify-between md:items-center gap-3 bg-slate-900/90 border border-slate-800 rounded-2xl p-4 md:p-5">
        <div>
          <div class="flex items-center gap-2.5">
            <span class="text-2xl">💬</span>
            <div>
              <div class="flex items-center gap-2.5">
                <h2 class="text-lg md:text-xl font-bold text-white">10인 에이전트 실시간 사내 업무 공유 피드 (Work Feed)</h2>
                <span class="bg-emerald-500/10 text-emerald-400 text-xs px-2.5 py-0.5 rounded-full font-bold border border-emerald-500/20 flex items-center gap-1.5">
                  <span class="w-2 h-2 rounded-full bg-emerald-400 pulse-dot"></span> 매 1시간 자동 업로드 가동 중
                </span>
              </div>
              <p class="text-xs text-slate-400 mt-0.5">각 직원이 1시간마다 분석한 프리장·글로벌 매크로·외환·퀀트 시나리오 업무 현황을 사내 피드로 공유합니다. 스크롤하여 실시간 보고서를 열람하세요.</p>
            </div>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <button onclick="refreshFeedNow()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold transition shadow-lg shadow-blue-600/20 flex items-center gap-1.5">
            <span>🔄 지금 즉시 갱신</span>
          </button>
          <a href="feed.html" class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-3.5 py-2 rounded-lg font-medium transition border border-slate-700 flex items-center gap-1.5">
            <span>📱 전용 전체화면 피드 보기</span> ↗
          </a>
        </div>
      </div>

      <!-- Feed Controls & Filter Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 bg-slate-950/60 p-3 rounded-xl border border-slate-800 text-xs">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-slate-400 font-medium px-2 py-1">필터:</span>
          <button onclick="filterFeed('all')" id="feedBtnAll" class="px-3 py-1.5 rounded-lg font-bold bg-blue-600 text-white transition">전체 피드 ({len(posts)})</button>
          <button onclick="filterFeed('premarket')" id="feedBtnPremarket" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>🔥 프리장 감시팀</span></button>
          <button onclick="filterFeed('macro')" id="feedBtnMacro" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>🌐 글로벌 매크로·외환</span></button>
          <button onclick="filterFeed('quant')" id="feedBtnQuant" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>📐 퀀트·백테스트</span></button>
          <button onclick="filterFeed('execution')" id="feedBtnExec" class="px-3 py-1.5 rounded-lg font-medium bg-slate-800 text-slate-300 hover:text-white transition flex items-center gap-1"><span>⚡ 주문 집행</span></button>
        </div>

        <div class="flex items-center gap-2">
          <select id="agentSelectFilter" onchange="filterByAgentSelect(this.value)" class="bg-slate-900 border border-slate-700 text-slate-300 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-blue-500">
            <option value="all">모든 직원 (10인)</option>
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

      <!-- Feed Stream Container (Scrollable Room) -->
      <div class="space-y-3.5 max-h-[750px] overflow-y-auto pr-1 rounded-2xl" id="feedStreamContainer" style="scrollbar-width: thin; scrollbar-color: #334155 #0f172a;">
        {feed_cards_html}
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
if "<!-- Global Macro & FX Radar Ticker Banner" in cube_html:
    cube_html = re.sub(r'<!-- Global Macro & FX Radar Ticker Banner.*?(?=<!-- Section 1: 10 Specialized Agents)', lambda m: macro_radar_html + "\n\n    ", cube_html, flags=re.DOTALL)
else:
    cube_html = cube_html.replace(sec1_target, macro_radar_html + "\n\n    " + sec1_target)

# Insert or replace Work Feed Section after Section 1
sec2_target = '<!-- Section 2: 10 Investment Scenarios & Alpha Selection Matrix -->'
if "<!-- Section: 10-Agent Real-Time Work Feed" in cube_html:
    cube_html = re.sub(r'<!-- Section: 10-Agent Real-Time Work Feed.*?(?=<!-- Section 2: 10 Investment Scenarios)', lambda m: feed_section_html + "\n\n    ", cube_html, flags=re.DOTALL)
else:
    cube_html = cube_html.replace(sec2_target, feed_section_html + "\n\n    " + sec2_target)

# Add Feed JavaScript Functions before </body>
feed_js = """
  <script>
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

      const posts = document.querySelectorAll('.feed-post');
      posts.forEach(p => {
        const cat = p.getAttribute('data-category') || '';
        const agent = p.getAttribute('data-agent') || '';
        
        if (category === 'all') {
          p.style.display = 'block';
        } else if (category === 'premarket') {
          if (cat === 'PREMARKET' || cat === 'DIRECTIVE' || ['1','2','4','10'].includes(agent)) {
            p.style.display = 'block';
          } else {
            p.style.display = 'none';
          }
        } else if (category === 'macro') {
          if (cat === 'MACRO' || agent === '4') {
            p.style.display = 'block';
          } else {
            p.style.display = 'none';
          }
        } else if (category === 'quant') {
          if (['QUANT', 'BACKTEST', 'ALPHA', 'CATALYST', 'GAMMA'].includes(cat) || ['3','5','6','7','8'].includes(agent)) {
            p.style.display = 'block';
          } else {
            p.style.display = 'none';
          }
        } else if (category === 'execution') {
          if (['EXECUTION', 'ENSEMBLE', 'DIRECTIVE'].includes(cat) || ['9','10'].includes(agent)) {
            p.style.display = 'block';
          } else {
            p.style.display = 'none';
          }
        }
      });
    }

    function filterByAgentSelect(agentId) {
      const posts = document.querySelectorAll('.feed-post');
      posts.forEach(p => {
        const agent = p.getAttribute('data-agent');
        if (agentId === 'all' || agent === agentId) {
          p.style.display = 'block';
        } else {
          p.style.display = 'none';
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

    function refreshFeedNow() {
      const container = document.getElementById('feedStreamContainer');
      container.style.opacity = '0.5';
      setTimeout(() => {
        container.style.opacity = '1';
        alert('✅ CUBE 사내 업무 피드가 최신 1시간 보고 내용으로 새로고침되었습니다.');
      }, 400);
    }
  </script>
"""

if "function filterFeed" not in cube_html:
    cube_html = cube_html.replace("</body>", feed_js + "\n</body>")

with open(cube_path, "w", encoding="utf-8") as f:
    f.write(cube_html)

# Also create docs/cube.html
docs_cube_path = os.path.join(base_dir, "docs", "cube.html")
with open(docs_cube_path, "w", encoding="utf-8") as f:
    f.write(cube_html)

# 3. Create standalone feed.html and docs/feed.html
feed_html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CUBE 10-Agent Real-Time Work Feed | Steve Park</title>
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
    
    <!-- Header -->
    <header class="flex flex-col sm:flex-row justify-between items-start sm:items-center pb-5 border-b border-slate-800 gap-4">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 via-blue-600 to-cyan-400 flex items-center justify-center text-white font-black text-xl shadow-lg shadow-blue-500/20">
          💬
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-xl md:text-2xl font-extrabold text-white">CUBE 사내 업무 공유 피드 (Work Feed)</h1>
            <span class="bg-emerald-500/10 text-emerald-400 text-[10px] font-bold px-2 py-0.5 rounded-full border border-emerald-500/20 flex items-center gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot"></span> LIVE 1H
            </span>
          </div>
          <p class="text-xs text-slate-400 mt-0.5">10명의 직원이 1시간마다 프리장·글로벌 매크로·외환·퀀트 시나리오 업무 현황을 업로드하는 타임라인입니다.</p>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <a href="scenarios.html" class="bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 text-xs px-3.5 py-2 rounded-lg font-bold transition border border-indigo-500/40 flex items-center gap-1.5">
          <span>📂 시나리오 아카이브</span>
        </a>
        <a href="cube.html" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold transition shadow-lg shadow-blue-600/20 flex items-center gap-1.5">
          <span>🏛️ CUBE 데스크로 돌아가기</span>
        </a>
      </div>
    </header>

{macro_radar_html}

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

print("✅ Successfully built and synced cube.html and feed.html with Global Macro Radar & Work Feed!")
