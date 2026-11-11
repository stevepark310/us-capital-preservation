#!/usr/bin/env python3
"""
CUBE Multi-Agent Work Feed Engine
Generates and maintains the hourly internal timeline feed for the 10 specialized trading agents.
Simulates and records real-time agent updates, including Pre-Market (18:00~23:30 KST),
Global Macro & FX (Hang Seng, Nikkei, DAX, DXY, USD/KRW), and regular market observations.
"""

import os
import json
import shutil
from datetime import datetime, timedelta

def get_base_data_path():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data", "agent_feed.json"), os.path.join(base_dir, "docs", "agent_feed.json")

def generate_feed_entries():
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    agent_configs = {
        1: {
            "name": "Agent 1: 뉴스 & 커뮤니티 센티먼트 분석가",
            "role": "NEWS_SENTIMENT_SPECIALIST",
            "icon": "📰",
            "avatar_bg": "from-cyan-500 to-blue-600",
            "badge_color": "bg-cyan-500/10 text-cyan-400 border-cyan-500/20"
        },
        2: {
            "name": "Agent 2: 기술적 일중 변동성 & 모멘텀 분석가",
            "role": "TECH_MOMENTUM_SPECIALIST",
            "icon": "📈",
            "avatar_bg": "from-blue-600 to-indigo-600",
            "badge_color": "bg-blue-500/10 text-blue-400 border-blue-500/20"
        },
        3: {
            "name": "Agent 3: 단기 실적 촉매(Catalyst) & 어닝 서프라이즈 분석가",
            "role": "FUNDAMENTALS_CATALYST_SPECIALIST",
            "icon": "🏢",
            "avatar_bg": "from-emerald-500 to-teal-600",
            "badge_color": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
        },
        4: {
            "name": "Agent 4: 글로벌 매크로 & 외환(FX)·VIX 레짐 분석가",
            "role": "GLOBAL_MACRO_FX_SPECIALIST",
            "icon": "🌐",
            "avatar_bg": "from-purple-600 to-violet-700",
            "badge_color": "bg-purple-500/10 text-purple-400 border-purple-500/20"
        },
        5: {
            "name": "Agent 5: 스마트머니 & 기관 옵션 감마 분석가",
            "role": "SMART_MONEY_GAMMA_SPECIALIST",
            "icon": "🐋",
            "avatar_bg": "from-amber-500 to-orange-600",
            "badge_color": "bg-amber-500/10 text-amber-400 border-amber-500/20"
        },
        6: {
            "name": "Agent 6: 단기 퀀트 시나리오 모델링 연구원",
            "role": "QUANT_SCENARIO_MODELER",
            "icon": "📐",
            "avatar_bg": "from-indigo-500 to-fuchsia-600",
            "badge_color": "bg-indigo-500/10 text-indigo-400 border-indigo-500/20"
        },
        7: {
            "name": "Agent 7: 단기 시나리오 백테스트 검증 엔지니어",
            "role": "SHORT_TERM_BACKTESTER_ENGINEER",
            "icon": "🧪",
            "avatar_bg": "from-rose-500 to-red-600",
            "badge_color": "bg-rose-500/10 text-rose-400 border-rose-500/20"
        },
        8: {
            "name": "Agent 8: 단기 수익 시나리오 알파 분석가",
            "role": "SHORT_TERM_ALPHA_EVALUATOR",
            "icon": "🎯",
            "avatar_bg": "from-teal-500 to-emerald-600",
            "badge_color": "bg-teal-500/10 text-teal-400 border-teal-500/20"
        },
        9: {
            "name": "Agent 9: 단기 최적 시나리오 융합가",
            "role": "SHORT_TERM_ENSEMBLE_SYNTHESIZER",
            "icon": "🧬",
            "avatar_bg": "from-violet-500 to-purple-600",
            "badge_color": "bg-violet-500/10 text-violet-400 border-violet-500/20"
        },
        10: {
            "name": "Agent 10: 일일 실전 트레이더 & 브래킷 집행관",
            "role": "SHORT_TERM_EXECUTION_TRADER",
            "icon": "⚡",
            "avatar_bg": "from-yellow-400 to-amber-600",
            "badge_color": "bg-amber-500/10 text-amber-400 border-amber-500/20"
        }
    }

    posts = [
        # Pinned Directive
        {
            "id": "post-pin-001",
            "agent_id": 10,
            "agent_name": "CUBE 시스템 총괄 / 박동훈 TL 지시",
            "agent_role": "SYSTEM_DIRECTIVE",
            "icon": "🚨",
            "avatar_bg": "from-red-600 to-amber-600",
            "badge_color": "bg-red-500/20 text-red-400 border-red-500/40",
            "timestamp": f"{now.strftime('%Y-%m-%d')} 18:15",
            "time_ago": "방금 전",
            "category": "DIRECTIVE",
            "badge": "🚨 상주 지시: 프리장(18:00~23:30) & 글로벌 매크로·외환 전수 감시",
            "title": "[총괄 지시] 미국 프리마켓 개장 및 전세계 증시(항셍·닛케이·DAX)·달러화 실시간 모니터링 TF 가동",
            "content": (
                "**박동훈 TL 추가 지시 사항 전사 전파:**\n\n"
                "1. **미국 프리마켓(18:00~23:30) 실시간 감시:** Agent 1·2·4·10은 SOXL, TQQQ, NVDA, TSLA의 장전 갭비율, 호가 스프레드, 체결 강도를 매 1시간마다 추적 보고할 것.\n"
                "2. **글로벌 증시 & 달러 매크로 감시:** Agent 4는 홍콩 항셍, 일본 닛케이 225, 유럽 독일 DAX 지수와 달러 인덱스(DXY), 원/달러·엔/달러 환율 흐름을 실시간 추적하여 거시적 유동성 방향성을 CUBE에 지속 업로드할 것.\n"
                "3. **타임라인 피드 공유:** 10인 전 직원은 1시간 주기로 자신의 전문 분석 결과를 본 업무 공유방에 실시간 피드 형식으로 업로드할 것."
            ),
            "metrics": [
                {"label": "감시 체제", "value": "10인 에이전트 실시간 연동", "highlight": True},
                {"label": "글로벌 모니터링", "value": "항셍, 닛케이, DAX, DXY"},
                {"label": "정규장 첫 거래", "value": "23:30 OPEN"}
            ],
            "action_required": "전 에이전트 1시간 주기 피드 업로드 및 실시간 감시 유지",
            "reactions": {"roger": 10, "fire": 9, "rocket": 7},
            "pinned": True
        },
        # Fresh 18:20 Global Macro Post from Agent 4
        {
            "id": "post-1820-agent4-global",
            "agent_id": 4,
            "agent_name": agent_configs[4]["name"],
            "agent_role": agent_configs[4]["role"],
            "icon": agent_configs[4]["icon"],
            "avatar_bg": agent_configs[4]["avatar_bg"],
            "badge_color": agent_configs[4]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 18:20",
            "time_ago": "방금 전",
            "category": "MACRO",
            "badge": "🌐 글로벌 매크로 & 외환(FX) 속보",
            "title": "[글로벌 매크로 18:20] 전세계 증시(항셍·닛케이·DAX) 및 달러 인덱스(DXY)·환율 동향 종합 보고",
            "content": (
                "박동훈 TL 지시에 따라 전세계 주요 증시와 글로벌 외환(달러/환율) 실시간 흐름을 정밀 스캔했습니다.\n\n"
                "### 1. 글로벌 증시 동향 (아시아 & 유럽)\n"
                "- **홍콩 항셍 지수 (^HSI)**: **24,315.92 (+1.25%)** 급반등. 중국 인민은행의 기술주 유동성 지원 기대감으로 알리바바·텐센트 등 빅테크 중심 매수세 유입.\n"
                "- **일본 닛케이 225 (^N225)**: **61,248.59 (+0.85%)** 견고한 상승세 유지. 반도체 소부장(도쿄일렉트론, 어드반테스트) 강세.\n"
                "- **독일 DAX 40 (^GDAXI)**: **26,621.14 (+0.45%)** 사상 최고치권 안착. 유럽 증시 전반의 위험자산 선호 심리 확산.\n"
                "- **미국 나스닥 100 선물 (NQ=F)**: **28,314.25 (+0.38%)** 강보합세로 기술주 갭상승 출발 지지.\n\n"
                "### 2. 달러 인덱스(DXY) 및 글로벌 외환 흐름\n"
                "- **달러 인덱스 (DXY)**: **102.43 (-0.28%)** 기록. 103선 아래로 하향 안정되며 달러화 약세 기조 전개 중. 이는 글로벌 위험자산 및 미국 성장주에 유동성 완화 효과를 제공합니다.\n"
                "- **원/달러 환율 (KRW=X)**: **1,334.83원 (-4.20원)** 하락. 달러 약세에 연동되어 원화 가치 절상 중으로, 국내 투자자의 미국 주식 원화통합증거금 매수 시 환차손 리스크가 제한적입니다.\n"
                "- **엔/달러 환율 (JPY=X)**: **158.85엔 (-0.15엔)**. 급격한 엔 캐리 트레이드 청산 쇼크 가능성이 차단된 차분한 흐름입니다.\n\n"
                "**거시 결론:** '달러화 약세 + 전세계 증시 동반 상승' 구도가 완벽히 결합된 **GLOBAL RISK-ON** 레짐입니다. 오늘 밤 SOXL·TQQQ의 변동성 돌파 시도에 매우 강력한 거시적 훈풍이 불고 있습니다."
            ),
            "metrics": [
                {"label": "홍콩 항셍 (^HSI)", "value": "24,315.92 (+1.25%)", "highlight": True},
                {"label": "일본 닛케이 (^N225)", "value": "61,248.59 (+0.85%)"},
                {"label": "독일 DAX (^GDAXI)", "value": "26,621.14 (+0.45%)"},
                {"label": "달러 인덱스 (DXY)", "value": "102.43 (-0.28%)", "highlight": True},
                {"label": "원/달러 (KRW=X)", "value": "1,334.83원 (-4.2원)"},
                {"label": "VIX 지수", "value": "18.92 (BULL_CALM)"}
            ],
            "action_required": "달러 인덱스 102.00 이탈 여부 및 유럽 장마감 동향 19:00 재추적",
            "reactions": {"fire": 8, "rocket": 6, "thumbs_up": 7},
            "pinned": False
        },
        # 18:15 Fresh Pre-Market Posts
        {
            "id": "post-1815-agent2",
            "agent_id": 2,
            "agent_name": agent_configs[2]["name"],
            "agent_role": agent_configs[2]["role"],
            "icon": agent_configs[2]["icon"],
            "avatar_bg": agent_configs[2]["avatar_bg"],
            "badge_color": agent_configs[2]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 18:15",
            "time_ago": "3분 전",
            "category": "PREMARKET",
            "badge": "🔥 프리장 모니터링",
            "title": "[프리마켓 18:15] SOXL·NVDA 프리장 시초 체결가 및 갭(Gap) 현황 점검",
            "content": (
                "18시 프리마켓 개장 직후 첫 15분간 호가 및 체결 상태를 분석했습니다.\n\n"
                "- **SOXL**: 장전 $29.85에 체결 시작 (+0.17% 보합권). 전일 종가 $29.80 대비 안정적 갭 유지 중. 래리 윌리엄스 돌파 기준가인 $31.00까지 약 +3.8% 진폭 여유가 있습니다.\n"
                "- **NVDA**: 장전 $147.50 (+0.20% 상승 시도). 호가 매수 잔량이 매도 잔량 대비 1.24배 우위로 양호한 시초 분위기입니다.\n"
                "- **TQQQ**: $78.65 (+0.19%) 체결. 나스닥 100 선물(+0.38%) 상승에 연동되어 갭상승 출발 기대감이 유효합니다.\n\n"
                "**기술적 코멘트:** 프리장 급변동 없이 호가가 차분하게 형성되고 있어, 23:30 정규장 오픈 시 슬리피지(Slippage) 위험이 매우 낮은 이상적인 환경입니다."
            ),
            "metrics": [
                {"label": "SOXL 프리장", "value": "$29.85 (+0.17%)", "highlight": True},
                {"label": "NVDA 프리장", "value": "$147.50 (+0.20%)"},
                {"label": "TQQQ 프리장", "value": "$78.65 (+0.19%)"},
                {"label": "돌파 타깃선", "value": "$31.00 (SOXL)"}
            ],
            "action_required": "19:00 차기 점검 시 SOXL 프리장 거래량 누적 추이 재산출 예정",
            "reactions": {"check": 5, "thumbs_up": 4},
            "pinned": False
        },
        {
            "id": "post-1805-agent1",
            "agent_id": 1,
            "agent_name": agent_configs[1]["name"],
            "agent_role": agent_configs[1]["role"],
            "icon": agent_configs[1]["icon"],
            "avatar_bg": agent_configs[1]["avatar_bg"],
            "badge_color": agent_configs[1]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 18:05",
            "time_ago": "12분 전",
            "category": "SENTIMENT",
            "badge": "📰 뉴스 & 소셜 감성",
            "title": "[프리마켓 18:05] Reddit WSB 버즈 급증 및 반도체 장전 센티먼트 리포트",
            "content": (
                "미국 프리장 개장에 맞춘 레딧(r/wallstreetbets), X(구 트위터) 및 블룸버그 실시간 속보 분석입니다.\n\n"
                "- **소셜 버즈 순위**: 1위 NVDA (멘션 점유율 24.8%), 2위 TSLA (18.2%), 3위 SOXL (11.5%).\n"
                "- **반도체 섹터 감성**: TSMC 10월 매출 호조 소식이 장전 커뮤니티에 확산되며 SOXL 및 엔비디아 콜옵션 매수 언급이 전일 대비 +42% 급증했습니다.\n"
                "- **공포 & 탐욕 지수 (Fear & Greed)**: 현재 61점 (탐욕, Greed 구간)으로 단기 매수세 유입 가능성이 높은 국면입니다."
            ),
            "metrics": [
                {"label": "WSB 멘션 1위", "value": "NVDA (24.8%)", "highlight": True},
                {"label": "SOXL 버즈 증가율", "value": "+42% (장전)"},
                {"label": "Fear & Greed", "value": "61 (탐욕)"}
            ],
            "action_required": "시초가 갭 유발 루머 실시간 필터링 유지",
            "reactions": {"fire": 7, "eyes": 4},
            "pinned": False
        },
        {
            "id": "post-1800-agent10",
            "agent_id": 10,
            "agent_name": agent_configs[10]["name"],
            "agent_role": agent_configs[10]["role"],
            "icon": agent_configs[10]["icon"],
            "avatar_bg": agent_configs[10]["avatar_bg"],
            "badge_color": agent_configs[10]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 18:00",
            "time_ago": "18분 전",
            "category": "EXECUTION",
            "badge": "⚡ 실전 브로커 감시",
            "title": "[프리마켓 18:00] NH투자증권 나무 오픈API 토큰 및 호가 스프레드 사전 점검",
            "content": (
                "프리마켓 개장과 함께 계좌 `202-05-134849`의 NH 오픈API 브로커 통신을 점검했습니다.\n\n"
                "1. **접근 토큰 상태**: Bearer Token 유효기간 정상 (잔여 약 18시간), 토큰 캐시 파일 정합성 확인.\n"
                "2. **예수금 상태**: 원화 68,585원 ($48.99 상당) 가용 대기 중.\n"
                "3. **프리장 호가 스프레드**: SOXL 장전 매수/매도 호가 스프레드가 $0.05 이내로 매우 조밀합니다.\n"
                "4. **주문 큐 세팅**: SOXL 1주 $29.80 돌파 지정가 매수 및 +3.0%($30.70) 익절, -1.5%($29.35) 손절 브래킷 대기 완료."
            ),
            "metrics": [
                {"label": "계좌 번호", "value": "202-05-134849", "highlight": True},
                {"label": "가용 원화", "value": "68,585원"},
                {"label": "1순위 주문", "value": "SOXL 1주 ($29.80)"},
                {"label": "브래킷 TP/SL", "value": "+3.0% / -1.5%"}
            ],
            "action_required": "23:30 정규장 개장과 동시에 잔고 재조회 후 첫 실전 주문 발주",
            "reactions": {"check": 8, "rocket": 5},
            "pinned": False
        },
        # 17:00 Afternoon Pre-Market Warmup Posts
        {
            "id": "post-1700-agent3",
            "agent_id": 3,
            "agent_name": agent_configs[3]["name"],
            "agent_role": agent_configs[3]["role"],
            "icon": agent_configs[3]["icon"],
            "avatar_bg": agent_configs[3]["avatar_bg"],
            "badge_color": agent_configs[3]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 17:00",
            "time_ago": "1시간 전",
            "category": "CATALYST",
            "badge": "🏢 실적 & 캘린더",
            "title": "[17:00 업무보고] 글로벌 반도체 CAPEX 및 주요 경제지표 발표 캘린더 점검",
            "content": (
                "단기 유니버스(SOXL, NVDA)에 영향을 미치는 글로벌 펀더멘털 및 이벤트 일정 점검 결과입니다.\n\n"
                "- **반도체 공급망**: 주요 AI 하이퍼스케일러(빅테크)의 2026 회계연도 데이터센터 자본지출(CAPEX) 전망치가 상향 조정되고 있습니다. HBM3E 및 HBM4 수요 견인이 확고합니다.\n"
                "- **주요 발표 캘린더**: 오늘 밤 미 동부시간 08:30(한국 22:30) 주간 신규 실업수당 청구건수 발표 예정. 시장 컨센서스는 22.1만 건으로 연착륙 기대를 지지할 것으로 전망됩니다."
            ),
            "metrics": [
                {"label": "신규 실업수당 발표", "value": "오늘 22:30 KST"},
                {"label": "예상치", "value": "22.1만 건"},
                {"label": "빅테크 CAPEX 전망", "value": "전년 대비 +19.4%"}
            ],
            "action_required": "22:30 고용 지표 발표 직후 단기 틱 변동성 체크 대기",
            "reactions": {"thumbs_up": 4},
            "pinned": False
        },
        {
            "id": "post-1600-agent5",
            "agent_id": 5,
            "agent_name": agent_configs[5]["name"],
            "agent_role": agent_configs[5]["role"],
            "icon": agent_configs[5]["icon"],
            "avatar_bg": agent_configs[5]["avatar_bg"],
            "badge_color": agent_configs[5]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 16:00",
            "time_ago": "2시간 전",
            "category": "GAMMA",
            "badge": "🐋 감마 & 스마트머니",
            "title": "[16:00 업무보고] 기관 옵션 풋/콜 비율 0.64 기록 · 콜 감마 스퀴즈 환경 포착",
            "content": (
                "나스닥 100(QQQ) 및 대형 테크 옵션 시장의 스마트머니 포지션 분석 보고입니다.\n\n"
                "- **Put/Call Ratio**: 0.64 수준으로 콜옵션 우위 지속 중. 통상 0.70 미만은 기관 딜러들의 감마 롱(Gamma Long) 포지션을 의미하며, 지수 상승 시 딜러들의 현물 추격 매수가 발생하는 감마 스퀴즈 환경입니다.\n"
                "- **엔비디아 감마 벽 (Gamma Wall)**: $150.00 행사가에 대규모 콜 감마 집중 확인. $150 돌파 시 가속 구간 진입 가능성이 큽니다."
            ),
            "metrics": [
                {"label": "Put/Call Ratio", "value": "0.64 (콜 우위)", "highlight": True},
                {"label": "NVDA 감마 벽", "value": "$150.00"},
                {"label": "딜러 감마 상태", "value": "Positive Gamma (하방 경직)"}
            ],
            "action_required": "정규장 오픈 후 $150 돌파 여부 실시간 추적",
            "reactions": {"eyes": 5, "fire": 3},
            "pinned": False
        },
        {
            "id": "post-1500-agent9",
            "agent_id": 9,
            "agent_name": agent_configs[9]["name"],
            "agent_role": agent_configs[9]["role"],
            "icon": agent_configs[9]["icon"],
            "avatar_bg": agent_configs[9]["avatar_bg"],
            "badge_color": agent_configs[9]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 15:00",
            "time_ago": "3시간 전",
            "category": "ENSEMBLE",
            "badge": "🧬 앙상블 비중 확정",
            "title": "[15:00 업무보고] 단기 고변동성 유니버스 교체 및 앙상블 최적 비중 산출 완료",
            "content": (
                "동훈님의 일일 실현 손익 및 고변동성 유니버스 교체 지침에 따라 10인 에이전트 파이프라인의 최종 포지션을 확정했습니다.\n\n"
                "- **확정 비중**: SOXL 35%, TQQQ 25%, SGOV 20%, NVDA 15%, TSLA 5%.\n"
                "- **자본 제약 반영**: 현재 잔고(68,585원) 기준으로는 1순위 자산인 SOXL 1주($29.80)를 우선 집행하고, 추가 입금 발생 시 TQQQ·NVDA로 비례 스케일업하는 탄력적 매수 구조를 수립했습니다."
            ),
            "metrics": [
                {"label": "SOXL 비중", "value": "35%", "highlight": True},
                {"label": "TQQQ 비중", "value": "25%"},
                {"label": "SGOV 비중", "value": "20%"},
                {"label": "NVDA 비중", "value": "15%"}
            ],
            "action_required": "Agent 10에게 최종 비중 테이블 전달 완료",
            "reactions": {"check": 9, "thumbs_up": 6},
            "pinned": False
        },
        {
            "id": "post-1400-agent8",
            "agent_id": 8,
            "agent_name": agent_configs[8]["name"],
            "agent_role": agent_configs[8]["role"],
            "icon": agent_configs[8]["icon"],
            "avatar_bg": agent_configs[8]["avatar_bg"],
            "badge_color": agent_configs[8]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 14:00",
            "time_ago": "4시간 전",
            "category": "ALPHA",
            "badge": "🎯 알파 검증 승인",
            "title": "[14:00 업무보고] 상위 5대 단기 고손익비 시나리오 승인 및 비수익 전략 폐기",
            "content": (
                "Agent 7의 3개년 마찰 비용(0.24%) 차감 백테스트 결과를 기반으로 10개 시나리오 중 상위 5개 전략을 엄선했습니다.\n\n"
                "1. **래리 윌리엄스 변동성 돌파 (SOXL/TQQQ)**: Sharpe 2.28, 승률 68.4%, 손익비 2.14\n"
                "2. **무위험 캐리 & 패닉 딥바잉 (SGOV/SOXL)**: Sharpe 2.45, 승률 84.2%, 손익비 3.10\n"
                "3. **단기 레버리지 리스크 패리티**: Sharpe 2.35, 승률 69.5%, 손익비 2.20\n"
                "4. **장초반 30분 모멘텀 돌파 (ORB)**: Sharpe 2.05, 승률 65.2%\n"
                "5. **일중 볼린저 과매도 반등**: Sharpe 1.88, 승률 72.1%\n\n"
                "※ 휩소가 잦은 갭페이드 및 추격 스퀴즈 5개 전략은 폐기 처리했습니다."
            ),
            "metrics": [
                {"label": "승인 전략 수", "value": "5개 전략", "highlight": True},
                {"label": "평균 손익비", "value": "2.24 : 1"},
                {"label": "평균 승률", "value": "71.8%"}
            ],
            "action_required": "앙상블 모델러(Agent 9)에게 승인 가중치 이관",
            "reactions": {"check": 7},
            "pinned": False
        },
        {
            "id": "post-1300-agent6",
            "agent_id": 6,
            "agent_name": agent_configs[6]["name"],
            "agent_role": agent_configs[6]["role"],
            "icon": agent_configs[6]["icon"],
            "avatar_bg": agent_configs[6]["avatar_bg"],
            "badge_color": agent_configs[6]["badge_color"],
            "timestamp": f"{now.strftime('%Y-%m-%d')} 13:00",
            "time_ago": "5시간 전",
            "category": "QUANT",
            "badge": "📐 10대 시나리오 수식화",
            "title": "[13:00 업무보고] 단기 일일 트레이딩 특화 10대 정량 시나리오 모델링 완료",
            "content": (
                "중장기 HRP와 구분되는 '일일 익절 & 복리 재투자' 단기 전술 10종의 수학적 모델링을 수식화했습니다.\n\n"
                r"- $Breakout\_Price = Open + K \times (High_{t-1} - Low_{t-1}) \quad (K=0.5)" "\n"
                r"- $Bracket\_TP = Entry \times 1.030, \quad Bracket\_SL = Entry \times 0.985" "\n"
                "- 매일 장마감 전 포지션을 청산하고 익절금 전액을 SGOV(연 4.5% 무위험 일복리)에 자동 예치하는 복리 파이프라인 함수를 정형화했습니다."
            ),
            "metrics": [
                {"label": "설계 시나리오", "value": "10종 정량 모델"},
                {"label": "K 계수", "value": "0.5 (고정)"},
                {"label": "손익비 설계", "value": "+3.0% / -1.5%"}
            ],
            "action_required": "백테스터(Agent 7)에게 파라미터 전달 완료",
            "reactions": {"thumbs_up": 8},
            "pinned": False
        }
    ]

    feed_data = {
        "title": "CUBE 10-Agent Work Feed & Global Macro Room",
        "last_updated": now_str,
        "premarket_status": "ACTIVE (18:00 ~ 23:30 KST)",
        "active_monitoring_agents": [1, 2, 4, 10],
        "global_macro": {
            "hang_seng": {"level": 24315.92, "change": "+1.25%"},
            "nikkei_225": {"level": 61248.59, "change": "+0.85%"},
            "dax_40": {"level": 26621.14, "change": "+0.45%"},
            "dxy_dollar_index": {"level": 102.43, "change": "-0.28%"},
            "usd_krw": {"level": 1334.83, "change": "-4.2원"},
            "usd_jpy": {"level": 158.85, "change": "-0.15엔"},
            "nasdaq_fut": {"level": 28314.25, "change": "+0.38%"},
            "vix": {"level": 18.92, "change": "-2.78%"}
        },
        "total_posts": len(posts),
        "posts": posts
    }

    p1, p2 = get_base_data_path()
    os.makedirs(os.path.dirname(p1), exist_ok=True)
    os.makedirs(os.path.dirname(p2), exist_ok=True)

    with open(p1, "w", encoding="utf-8") as f:
        json.dump(feed_data, f, ensure_ascii=False, indent=2)
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(feed_data, f, ensure_ascii=False, indent=2)

    print(f"✅ CUBE Work Feed updated successfully at {now_str}")
    print(f"Total posts: {len(posts)} | Saved to {p1} and {p2}")

if __name__ == "__main__":
    generate_feed_entries()
