"""
CUBE 10인 데스크 — 직원 프로필 정본(single source of truth).

동훈님 지시(2026-11-18 16:04): "CUBE 10명 직원에게 자연스러운 한국어 이름 + 전문가
프로필(담당 분야·핵심 지표·역할)을 부여하라."

이 모듈 하나만 고치면 오케스트레이터(cube_report.json), 업무보고(agent_feed.json),
10분 펄스(cube_pulse.json), 통합 대시보드(index.html)가 모두 같은 이름·프로필·
사무실 좌석 좌표를 쓴다. 좌석 좌표는 `docs/assets/office_day.png`(1536x1024)에서
책상 앞 의자 등받이 중심을 실측한 값이다(이미지 대비 %).

좌석 배치(2열 x 5석)
    BACK  (안쪽·창가 쪽)  : 1 한지우 · 2 이도현 · 3 박서연 · 4 최민준 · 5 정하린
    FRONT (문 쪽·앞줄)    : 6 강우진 · 7 윤태호 · 8 임수아 · 9 송재원 · 10 김시우
"""
from typing import Dict, Any, List

TEN_MIN_ROLE = "TEN_MIN_PULSE"

# ---------------------------------------------------------------- the ten people
EMPLOYEES: List[Dict[str, Any]] = [
    {
        "id": 1, "name": "한지우", "title": "수석 뉴스·센티먼트 분석가",
        "role": "NEWS_SENTIMENT_SPECIALIST", "category": "SENTIMENT",
        "specialty": "실시간 뉴스·월가 IB 인사이트·RSS 감성 분석",
        "bio": "Yahoo/CNBC/MarketWatch/Nasdaq/CoinDesk/Google News RSS를 1시간 단위로 훑어 "
               "종목별 관련도·감성·최신성으로 점수를 매기고, 시장 내재 리스크 점수와 블렌딩해 "
               "익절·손절 폭의 출발점을 만든다. 헤드라인은 출처와 링크까지 남긴다.",
        "metrics": ["종목 평균 센티먼트", "시장 내재 앵커", "24h 뉴스 커버리지", "섹터별 감성(주식·채권·외환·코인)"],
        "duty": "센티먼트·뉴스 충격 시 사다리 목표/손절 조정 의견 (단, NVDA 잠금 레벨은 제외)",
        "icon": "📰", "accent": "#22d3ee", "avatar_bg": "from-cyan-500 to-blue-600",
        "seat": {"x": 31.0, "y": 55.0, "row": "BACK", "no": 1},
    },
    {
        "id": 2, "name": "이도현", "title": "멀티에셋 기술적 분석가",
        "role": "TECH_MOMENTUM_MULTIASSET", "category": "TECHNICAL",
        "specialty": "가격 구조·변동성·모멘텀 (주식·채권·환율·코인)",
        "bio": "VWAP·RSI2·볼린저 %B·RVOL·ATR로 진입 게이트를 계산하고, 채권·외환·코인까지 "
               "같은 기준으로 레벨을 낸다. 사다리 폭과 손절 거리의 입력값을 만드는 사람.",
        "metrics": ["RSI2", "%B", "RVOL", "ATR%", "VWAP 이격"],
        "duty": "진입 게이트 계산 (VWAP·RSI2·%B·RVOL) 과 ATR 최신화 — 사다리 폭의 입력값 제공",
        "icon": "📈", "accent": "#60a5fa", "avatar_bg": "from-blue-600 to-indigo-600",
        "seat": {"x": 42.2, "y": 53.6, "row": "BACK", "no": 2},
    },
    {
        "id": 3, "name": "박서연", "title": "실적 촉매 분석가",
        "role": "FUNDAMENTALS_CATALYST_SPECIALIST", "category": "CATALYST",
        "specialty": "어닝 서프라이즈·실적 D-day·매크로 일정",
        "bio": "실적 캘린더와 컨센서스를 관리하고, 발표 전후 갭 시나리오와 강제청산 시각을 "
               "정한다. 실적 반응일에는 레버리지 신규 노출을 막는 가드의 근거를 댄다.",
        "metrics": ["실적 D-day", "컨센 EPS/매출", "발표 후 갭 시나리오", "매크로 일정"],
        "duty": "NVDA 3분기 실적(11-18 06:20 KST) D-day·컨센·발표 후 갭 시나리오 관리",
        "icon": "🏢", "accent": "#34d399", "avatar_bg": "from-emerald-500 to-teal-600",
        "seat": {"x": 53.2, "y": 52.2, "row": "BACK", "no": 3},
    },
    {
        "id": 4, "name": "최민준", "title": "글로벌 매크로·채권 전략가",
        "role": "GLOBAL_MACRO_RATES_FX_CRYPTO_LEI", "category": "MACRO",
        "specialty": "금리·달러·신용·경기선행지수",
        "bio": "CUBE-LEI(경기선행지수)·침체확률·장단기 금리차·HY OAS로 국면을 판정하고 "
               "헤지 게이트(VIX 24 이상 + 확인 2개)를 여닫는 판단을 맡는다. FRED 원계열을 직접 읽는다.",
        "metrics": ["CUBE-LEI / 침체확률", "10Y-3M·10Y-2Y", "HY OAS(bp)", "DXY·금리 국면"],
        "duty": "VIX·금리·달러 국면으로 헤지 게이트(VIX>=24+확인 2개) 판정",
        "icon": "🌐", "accent": "#a78bfa", "avatar_bg": "from-purple-600 to-violet-700",
        "seat": {"x": 64.2, "y": 50.8, "row": "BACK", "no": 4},
    },
    {
        "id": 5, "name": "정하린", "title": "스마트머니·기관 포지셔닝 분석가",
        "role": "SMART_MONEY_GAMMA_SPECIALIST", "category": "GAMMA",
        "specialty": "옵션 감마·기관 수급·13F 현금화",
        "bio": "QQQ 풋/콜 미결제약정과 NVDA 감마월, 대가들의 13F 현금화 지수로 기관 자금의 "
               "방향과 듀레이션을 읽는다. 반도체 수급 리스크를 가장 먼저 경고하는 자리.",
        "metrics": ["QQQ P/C", "NVDA 감마월", "13F 현금화 지수", "HY OAS·신용 흐름"],
        "duty": "기관·크레딧 포지셔닝으로 반도체 수급 리스크 점검",
        "icon": "🐋", "accent": "#fbbf24", "avatar_bg": "from-amber-500 to-orange-600",
        "seat": {"x": 74.4, "y": 49.4, "row": "BACK", "no": 5},
    },
    {
        "id": 6, "name": "강우진", "title": "퀀트 시나리오 모델러",
        "role": "QUANT_SCENARIO_MODELER", "category": "QUANT",
        "specialty": "확률 시나리오·레버리지 계수·종목 상한",
        "bio": "세션 안 상승/횡보/하락 확률과 사다리 도달확률을 다시 계산하고, 경기선행지수 "
               "필터로 레버리지 계수와 종목당 상한을 깎는다. 숫자가 안 맞으면 시나리오를 버린다.",
        "metrics": ["시나리오 확률", "레버리지 계수", "종목 상한%", "유효 전략 목록"],
        "duty": "세션 내 시나리오 확률(상승/횡보/하락)과 사다리 도달확률 재계산",
        "icon": "📐", "accent": "#818cf8", "avatar_bg": "from-indigo-500 to-fuchsia-600",
        "seat": {"x": 35.0, "y": 73.0, "row": "FRONT", "no": 1},
    },
    {
        "id": 7, "name": "윤태호", "title": "백테스트 검증 엔지니어",
        "role": "SHORT_TERM_BACKTESTER_ENGINEER", "category": "BACKTEST",
        "specialty": "실측 백테스트·기대값·손익비 검증",
        "bio": "모든 사다리와 전략을 시간봉 경로로 다시 돌려 기대값·손익비·표본 수를 낸다. "
               "표본이 20건에 못 미치면 파라미터 교체를 거부하는 것이 이 자리의 권한이다.",
        "metrics": ["기대값 %/건", "Profit Factor", "표본 수", "슬리피지·수수료 반영"],
        "duty": "사다리 손익비·도달확률 백테스트 검증 (data/avg_anchor_ladder_stats.json)",
        "icon": "🧪", "accent": "#fb7185", "avatar_bg": "from-rose-500 to-red-600",
        "seat": {"x": 46.0, "y": 71.4, "row": "FRONT", "no": 2},
    },
    {
        "id": 8, "name": "임수아", "title": "알파·리스크 평가자",
        "role": "CROSS_MARKET_ALPHA_EVALUATOR", "category": "ALPHA",
        "specialty": "기대 알파 대비 자본 배분·승인/폐기",
        "bio": "종목별 기대 알파와 사다리 기대값을 비교해 승인·폐기를 가르고, SGOV 캐리 같은 "
               "무위험 대안과 코인 주말 갭 리스크까지 함께 저울에 올린다.",
        "metrics": ["승인/폐기 시나리오", "SGOV 캐리 %/년", "코인 갭 β", "자본 배분 우선순위"],
        "duty": "종목별 기대 알파 대비 사다리 기대값 비교, 자본 배분 우선순위 판단",
        "icon": "🎯", "accent": "#2dd4bf", "avatar_bg": "from-teal-500 to-emerald-600",
        "seat": {"x": 57.8, "y": 69.9, "row": "FRONT", "no": 3},
    },
    {
        "id": 9, "name": "송재원", "title": "앙상블 융합 총괄 (수석)",
        "role": "CROSS_MARKET_ENSEMBLE", "category": "ENSEMBLE",
        "specialty": "5축 복합 점수·시장 태세·목표 비중",
        "bio": "아홉 사람의 결론을 5축(추세·변동성·신용·유동성·심리)으로 접어 복합 점수와 "
               "시장 태세를 정하고 목표 비중을 낸다. 데스크의 최종 의견을 만드는 자리.",
        "metrics": ["5축 복합 점수", "시장 태세", "목표 비중", "리스크 한도"],
        "duty": "10인 의견 융합 — 오늘 목표 달성 가능성과 리스크 한도 종합",
        "icon": "🧬", "accent": "#c084fc", "avatar_bg": "from-violet-500 to-purple-600",
        "seat": {"x": 69.5, "y": 68.4, "row": "FRONT", "no": 4},
    },
    {
        "id": 10, "name": "김시우", "title": "실전 집행관 (트레이더)",
        "role": "SHORT_TERM_EXECUTION_TRADER", "category": "EXECUTION",
        "specialty": "주문·체결·브래킷·C5 파일럿 집행",
        "bio": "승인된 계획만 NH투자증권(나무) API로 집행한다. 매도·손절·강제청산은 확인 없이 "
               "즉시, 신규 매수는 게이트를 통과할 때만. 1회 예산과 일일 손실한도를 넘기지 않는다.",
        "metrics": ["주문/체결", "브래킷·본전 상향", "C5 파일럿 상태", "당일 실현손익"],
        "duty": "사다리 브래킷 집행·본전 상향·05:40 실적 가드 청산 (data/active_brackets.json)",
        "icon": "⚡", "accent": "#fbbf24", "avatar_bg": "from-yellow-400 to-amber-600",
        "seat": {"x": 81.0, "y": 66.9, "row": "FRONT", "no": 5},
    },
]

BY_ID: Dict[int, Dict[str, Any]] = {e["id"]: e for e in EMPLOYEES}

# 역할별 담당 업무(오케스트레이터·10분 펄스가 공유)
ROLE_DUTIES: Dict[int, str] = {e["id"]: e["duty"] for e in EMPLOYEES}

# 업무보고 피드에서 쓰던 표시 이름 형식 유지: "Agent 3 · 박서연 (실적 촉매 분석가)"
DISPLAY_NAMES: Dict[int, str] = {
    e["id"]: f"Agent {e['id']} · {e['name']} ({e['title']})" for e in EMPLOYEES
}
SHORT_NAMES: Dict[int, str] = {e["id"]: e["name"] for e in EMPLOYEES}
ICONS: Dict[int, str] = {e["id"]: e["icon"] for e in EMPLOYEES}


def profile(agent_id: int) -> Dict[str, Any]:
    """없는 id면 빈 프로필 대신 최소 형태를 돌려준다(렌더가 깨지지 않게)."""
    e = BY_ID.get(int(agent_id))
    if e:
        return e
    return {"id": int(agent_id), "name": f"Agent {agent_id}", "title": "", "role": "",
            "category": "", "specialty": "", "bio": "", "metrics": [], "duty": "",
            "icon": "•", "accent": "#94a3b8", "avatar_bg": "from-slate-600 to-slate-700",
            "seat": {"x": 50.0, "y": 50.0, "row": "FRONT", "no": 0}}


def as_list() -> List[Dict[str, Any]]:
    """cube_report.json `team` 필드 — 대시보드가 그대로 그린다."""
    return [dict(e) for e in EMPLOYEES]


def office_layout() -> Dict[str, Any]:
    """사무실 이미지 규격 + 좌석 좌표(이미지 대비 %)."""
    return {
        "image": {"day": "assets/office_day.png", "night": "assets/office_night.png", "w": 1536, "h": 1024},
        "seats": [{"agent_id": e["id"], "name": e["name"], "x": e["seat"]["x"], "y": e["seat"]["y"],
                   "row": e["seat"]["row"], "no": e["seat"]["no"]} for e in EMPLOYEES],
    }
