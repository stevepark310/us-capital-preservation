"""
CUBE-M8 — 8대 거시·자산 경기선행지표 (Macro 8-Asset Leading Index)
==================================================================

동훈님 지시(2026-11-21 11:01):
    "비트코인. 골드. 유가. 금리. 기대인플레이션. 고용 수치. 소비자 물가. 생산자 물가
     등 여러가지 종목의 가격 추이를 바탕으로 경기 선행 지표를 만들고 이를 바탕으로
     주식시장의 움직임을 예측해서 매매 알고리즘에 판단기준으로 들어갈 수 있도록 …
     그리고 지속적으로 모든 정보를 자동으로 수집하고 알고리즘에 반영할 수 있도록"

8개 블록(각 블록은 2~3개 실측 성분)
------------------------------------------------
    1) crypto   비트코인        BTC-USD 모멘텀·200일선 괴리·실현변동성
    2) gold     골드            금/주식 상대강도·금 실현변동성
    3) oil      유가            WTI 모멘텀·실현변동성
    4) rates    금리           10Y-2Y 금리차·실질금리 변화·명목 10Y 변화
    5) inflation_expectations  5Y 손익분기·10Y 손익분기 변화·5y5y 포워드
    6) employment 고용         비농업고용 3개월 평균·실업률 3개월 변화·신규청구 YoY
    7) cpi      소비자물가      CPI YoY·근원 CPI 3개월 연율
    8) ppi      생산자물가      PPi 최종수요 YoY·근원 PPI 3개월 연율·PPI-CPI 갭

발표 시차(publication lag) — 룩어헤드 차단
-------------------------------------------
월간 지표는 **그 달의 값이 발표일에야 알려진다**. 이 모듈은 각 계열의 발표 지연
일수를 메타데이터로 들고, 값을 `기간말 + lag_days` 날짜에야 사용 가능한 것으로
정렬한다(`_align_availability`). 예: 2026-10 CPI는 2026-11-13부터 사용 가능.

    CPI / 근원CPI      13일 (BLS 통상 13~15일)
    PPI / 근원PPI      15~16일 (CPI 다음날)
    비농업고용/실업률   7일  (첫 금요일, 월말 +3~7일)
    신규 실업청구       6일  (주간, 토요일 기준 다음 목요일)
    일간 금리/손익분기   0일  (당일 종가 확정 → 신호는 다음 세션 적용)
    시장가격(BTC/금/유가) 0일 (당일 종가)

개정(revision) 주의: 무료 FRED CSV는 **최종 개정치**만 준다. ALFRED 빈티지 데이터는
API 키가 필요해 여기서는 쓸 수 없다. 그래서 (a) YoY·3개월 변화처럼 개정에 둔감한
변환을 쓰고, (b) 백테스트에서 **추가 시차(+10일) 강건성 검정**을 함께 돌려 결론이
발표시차 가정에 기대지 않는지 확인한다. 이 한계는 산출물에도 그대로 적는다.

방법론 근거
-----------
* Stock & Watson (1989) NBER Macroeconomics Annual 4:351-394 — 다변량 표준화 확산지수.
* Stock & Watson (2002) JBES 20(2):147-162 — 확산지수 예측력.
* Estrella & Mishkin (1998) REStat 80(1):45-61 — 장단기 금리차 침체 프로빗.
* Estrella & Hardouvelis (1991) Journal of Finance 46(2):555-576 — 금리차의 실물 선행성.
* Gilchrist & Zakrajšek (2012) AER 102(4):1692-1720 — 신용스프레드 경로.
* Hamilton & Lin (1996) "Stock market volatility and the business cycle" — 변동성 국면.
* Bhardwaj, Gorton & Rouwenhorst (2014) "Fooling Some of the People All of the Time:
  The Efficient Market Hypothesis and the Pricing of Gold" — 금의 위험회피 성격.
* Kilian (2009) AER 99(3):1053-1069 — 유가 충격의 수요/공급 분해(모멘텀 vs 변동성).
* Gürkaynak, Sack & Wright (2010) — 손익분기 인플레이션의 기대 인플레 정보.
* Bernanke & Kuttner (2005) — 금리 변화의 주가 반응(명목금리 상승 = 할인율 상승).

설계 원칙
---------
1) 저장된 숫자는 없다. 모든 값은 매 실행마다 원계열에서 재계산한다.
2) z-score는 **과거 창만** 쓴다(rolling). 미래 데이터를 쓰는 expanding/전체표본 통계 없음.
3) 부호(prior sign)는 논문·경제논리에 근거해 고정하고, 백테스트에서 **실측 IC와
   일치하는지** 별도로 보고한다(불일치는 숨기지 않는다).
4) 실전 반영은 `config/macro_leading_policy.json`의 `enabled_for_sizing`이 true일
   때만. 기본값은 shadow(계산·보고만, 주문 크기 불변).
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core import market_snapshot

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "macro_leading.json")
DOCS_PATH = os.path.join(BASE_DIR, "docs", "macro_leading.json")
POLICY_PATH = os.path.join(BASE_DIR, "config", "macro_leading_policy.json")
BACKTEST_PATH = os.path.join(BASE_DIR, "data", "macro_leading_backtest.json")
SIGNS_PATH = os.path.join(BASE_DIR, "data", "macro_leading_signs.json")

ENGINE = "CUBE-M8 v1"

# ---------------------------------------------------------------- series metadata
# lag_days: 기간말(월말/주말) 이후 이 일수가 지나야 값이 공표된다.
FRED_META: Dict[str, Dict[str, Any]] = {
    "CPIAUCSL": {"label": "미 CPI (계절조정)", "units": "index", "freq": "monthly", "lag_days": 13,
                 "release": "BLS CPI — 월말 +13일(통상 13~15일)"},
    "CPILFESL": {"label": "미 근원 CPI (식품·에너지 제외)", "units": "index", "freq": "monthly", "lag_days": 13,
                 "release": "BLS CPI — 월말 +13일"},
    "PPIACO": {"label": "미 PPI 전체 상품", "units": "index", "freq": "monthly", "lag_days": 15,
               "release": "BLS PPI — 월말 +15일"},
    "WPSFD49207": {"label": "미 PPI 최종수요", "units": "index", "freq": "monthly", "lag_days": 15,
                   "release": "BLS PPI 최종수요 — 월말 +15일"},
    "WPSFD4131": {"label": "미 근원 PPI 최종수요(식품·에너지 제외)", "units": "index", "freq": "monthly",
                  "lag_days": 16, "release": "BLS PPI — 월말 +16일"},
    "PAYEMS": {"label": "미 비농업 고용자수", "units": "천명", "freq": "monthly", "lag_days": 7,
               "release": "BLS 고용보고서 — 월말 +3~7일(첫 금요일)"},
    "UNRATE": {"label": "미 실업률", "units": "%", "freq": "monthly", "lag_days": 7,
               "release": "BLS 고용보고서 — 월말 +3~7일"},
    "ICSA": {"label": "신규 실업수당 청구건수", "units": "건", "freq": "weekly", "lag_days": 6,
             "release": "DOL — 주간(토요일 기준) 다음 목요일"},
    "T5YIE": {"label": "5년 기대인플레이션(손익분기)", "units": "%", "freq": "daily", "lag_days": 0,
              "release": "Treasury/FRED 일간 종가"},
    "T10YIE": {"label": "10년 기대인플레이션(손익분기)", "units": "%", "freq": "daily", "lag_days": 0,
               "release": "Treasury/FRED 일간 종가"},
    "DFII10": {"label": "10년 실질금리(TIPS)", "units": "%", "freq": "daily", "lag_days": 0,
               "release": "Treasury/FRED 일간 종가"},
    "DGS10": {"label": "미 10년 명목금리", "units": "%", "freq": "daily", "lag_days": 0,
              "release": "FRED 일간 종가"},
    "DGS2": {"label": "미 2년 명목금리", "units": "%", "freq": "daily", "lag_days": 0,
             "release": "FRED 일간 종가"},
    "T10Y2Y": {"label": "미 10Y-2Y 금리차", "units": "%p", "freq": "daily", "lag_days": 0,
               "release": "FRED 일간 종가"},
    "T10Y3M": {"label": "미 10Y-3M 금리차", "units": "%p", "freq": "daily", "lag_days": 0,
               "release": "FRED 일간 종가"},
    "DCOILWTICO": {"label": "WTI 현물(FRED)", "units": "$", "freq": "daily", "lag_days": 1,
                   "release": "EIA 일간 현물"},
}

PRICE_META: Dict[str, Dict[str, Any]] = {
    "BTC-USD": {"label": "비트코인", "units": "$", "freq": "daily", "lag_days": 0, "release": "Yahoo Finance 24/7"},
    "GC=F": {"label": "금 선물", "units": "$", "freq": "daily", "lag_days": 0, "release": "COMEX 일봉"},
    "CL=F": {"label": "WTI 원유 선물", "units": "$", "freq": "daily", "lag_days": 0, "release": "NYMEX 일봉"},
    "SPY": {"label": "S&P500 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "QQQ": {"label": "나스닥100 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "SOXX": {"label": "반도체 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "SOXL": {"label": "반도체 3배 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "TQQQ": {"label": "나스닥100 3배 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "SGOV": {"label": "0-3개월 T-bill ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "BIL": {"label": "1-3개월 T-bill ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "HYG": {"label": "하이일드 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
    "IEF": {"label": "7-10년 국채 ETF", "units": "$", "freq": "daily", "lag_days": 0, "release": "미국 정규장 일봉"},
}

FRED_IDS = list(FRED_META.keys())
PRICE_SYMBOLS = list(PRICE_META.keys())

# --------------------------------------------------------------------- block design
BLOCK_META: Dict[str, Dict[str, Any]] = {
    "crypto": {"name": "비트코인", "name_en": "Crypto / Liquidity", "weight": 0.10, "icon": "₿",
               "why": "24/7 유동성·위험선호의 가장 빠른 대리지표(주말에도 거래되는 유일한 위험자산)"},
    "gold": {"name": "골드", "name_en": "Gold / Real asset", "weight": 0.08, "icon": "🥇",
             "why": "금/주식 상대강도 상승 = 위험회피 로테이션(안전자산 수요)"},
    "oil": {"name": "유가", "name_en": "Crude oil", "weight": 0.10, "icon": "🛢",
            "why": "Kilian(2009) — 유가 상승이 수요 견인인지 공급 충격인지 모멘텀/변동성으로 분리"},
    "rates": {"name": "금리", "name_en": "Rates / Curve", "weight": 0.22, "icon": "📐",
              "why": "Estrella & Mishkin(1998)·Hardouvelis(1991) — 금리차·실질금리가 실물·주가를 선행"},
    "inflation_expectations": {"name": "기대인플레이션", "name_en": "Breakevens", "weight": 0.14, "icon": "🌡",
                              "why": "손익분기 상승 = 할인율·비용 상승 압력(주식 밸류에이션 역풍)"},
    "employment": {"name": "고용", "name_en": "Labor", "weight": 0.16, "icon": "👷",
                   "why": "Stock & Watson(1989) — 고용·실업청구는 경기 정점/저점의 후행·확인 신호"},
    "cpi": {"name": "소비자물가", "name_en": "CPI", "weight": 0.10, "icon": "🧾",
            "why": "CPI 모멘텀은 연준 정책 경로를 결정 — 정책 충격의 근원"},
    "ppi": {"name": "생산자물가", "name_en": "PPI", "weight": 0.10, "icon": "🏭",
            "why": "PPI는 CPI보다 앞서 움직이는 파이프라인 물가 — 마진 압박 선행"},
}

# member spec: native series → transform → rolling z(periods) → availability(lag) → sign
MEMBER_SPECS: List[Dict[str, Any]] = [
    # ---- crypto
    {"key": "btc_mom_60d", "block": "crypto", "label": "비트코인 60일 모멘텀", "source": "Yahoo BTC-USD",
     "freq": "daily", "sign": +1, "transform": ("mom", 60), "z_window": 756,
     "inputs": {"a": ("price", "BTC-USD")}},
    {"key": "btc_trend_200d", "block": "crypto", "label": "비트코인 200일선 괴리", "source": "Yahoo BTC-USD",
     "freq": "daily", "sign": +1, "transform": ("trend_dev", 200), "z_window": 756,
     "inputs": {"a": ("price", "BTC-USD")}},
    {"key": "btc_vol_20d", "block": "crypto", "label": "비트코인 20일 실현변동성", "source": "Yahoo BTC-USD",
     "freq": "daily", "sign": -1, "transform": ("vol", 20), "z_window": 756,
     "inputs": {"a": ("price", "BTC-USD")}},
    # ---- gold
    {"key": "gold_vs_spy_60d", "block": "gold", "label": "금/주식 60일 상대강도", "source": "Yahoo GC=F ÷ SPY",
     "freq": "daily", "sign": -1, "transform": ("rel_mom", 60), "z_window": 756,
     "inputs": {"a": ("price", "GC=F"), "b": ("price", "SPY")}},
    {"key": "gold_vol_20d", "block": "gold", "label": "금 20일 실현변동성", "source": "Yahoo GC=F",
     "freq": "daily", "sign": -1, "transform": ("vol", 20), "z_window": 756,
     "inputs": {"a": ("price", "GC=F")}},
    # ---- oil
    {"key": "oil_mom_20d", "block": "oil", "label": "WTI 20일 모멘텀", "source": "Yahoo CL=F",
     "freq": "daily", "sign": +1, "transform": ("mom", 20), "z_window": 756,
     "inputs": {"a": ("price", "CL=F")}},
    {"key": "oil_vol_20d", "block": "oil", "label": "WTI 20일 실현변동성", "source": "Yahoo CL=F",
     "freq": "daily", "sign": -1, "transform": ("vol", 20), "z_window": 756,
     "inputs": {"a": ("price", "CL=F")}},
    # ---- rates
    {"key": "curve_10y2y", "block": "rates", "label": "10Y-2Y 금리차", "source": "FRED T10Y2Y",
     "freq": "daily", "sign": +1, "transform": ("level",), "z_window": 756,
     "inputs": {"a": ("fred", "T10Y2Y")}},
    {"key": "real_rate_chg_60d", "block": "rates", "label": "10년 실질금리 60일 변화", "source": "FRED DFII10",
     "freq": "daily", "sign": -1, "transform": ("chg", 60), "z_window": 756,
     "inputs": {"a": ("fred", "DFII10")}},
    {"key": "nominal_10y_chg_20d", "block": "rates", "label": "10년 명목금리 20일 변화", "source": "FRED DGS10",
     "freq": "daily", "sign": -1, "transform": ("chg", 20), "z_window": 756,
     "inputs": {"a": ("fred", "DGS10")}},
    # ---- inflation expectations
    {"key": "be5y_level", "block": "inflation_expectations", "label": "5년 손익분기 수준", "source": "FRED T5YIE",
     "freq": "daily", "sign": -1, "transform": ("level",), "z_window": 756,
     "inputs": {"a": ("fred", "T5YIE")}},
    {"key": "be10y_chg_60d", "block": "inflation_expectations", "label": "10년 손익분기 60일 변화",
     "source": "FRED T10YIE", "freq": "daily", "sign": -1, "transform": ("chg", 60), "z_window": 756,
     "inputs": {"a": ("fred", "T10YIE")}},
    {"key": "be_5y5y", "block": "inflation_expectations", "label": "5y5y 포워드 기대인플레(10Y-5Y)",
     "source": "FRED T10YIE - T5YIE", "freq": "daily", "sign": -1, "transform": ("spread_level",),
     "z_window": 756, "inputs": {"a": ("fred", "T10YIE"), "b": ("fred", "T5YIE")}},
    # ---- employment
    {"key": "payrolls_3m", "block": "employment", "label": "비농업고용 3개월 평균 증가(천명)", "source": "FRED PAYEMS",
     "freq": "monthly", "sign": +1, "transform": ("diff_avg", 3), "z_window": 36,
     "inputs": {"a": ("fred", "PAYEMS")}},
    {"key": "unrate_3m_chg", "block": "employment", "label": "실업률 3개월 변화(%p)", "source": "FRED UNRATE",
     "freq": "monthly", "sign": -1, "transform": ("chg", 3), "z_window": 36,
     "inputs": {"a": ("fred", "UNRATE")}},
    {"key": "claims_4wk_yoy", "block": "employment", "label": "신규청구 4주평균 YoY", "source": "FRED ICSA",
     "freq": "weekly", "sign": -1, "transform": ("yoy_weekly",), "z_window": 104,
     "inputs": {"a": ("fred", "ICSA")}},
    # ---- cpi
    {"key": "cpi_yoy", "block": "cpi", "label": "CPI YoY", "source": "FRED CPIAUCSL",
     "freq": "monthly", "sign": -1, "transform": ("yoy",), "z_window": 36,
     "inputs": {"a": ("fred", "CPIAUCSL")}},
    {"key": "core_cpi_3m_ann", "block": "cpi", "label": "근원 CPI 3개월 연율", "source": "FRED CPILFESL",
     "freq": "monthly", "sign": -1, "transform": ("ann3m",), "z_window": 36,
     "inputs": {"a": ("fred", "CPILFESL")}},
    # ---- ppi
    {"key": "ppi_fd_yoy", "block": "ppi", "label": "PPI 최종수요 YoY", "source": "FRED WPSFD49207",
     "freq": "monthly", "sign": -1, "transform": ("yoy",), "z_window": 36,
     "inputs": {"a": ("fred", "WPSFD49207")}},
    {"key": "ppi_core_3m_ann", "block": "ppi", "label": "근원 PPI 3개월 연율", "source": "FRED WPSFD4131",
     "freq": "monthly", "sign": -1, "transform": ("ann3m",), "z_window": 36,
     "inputs": {"a": ("fred", "WPSFD4131")}},
    {"key": "ppi_cpi_gap", "block": "ppi", "label": "PPI-CPI YoY 갭(마진 압박)", "source": "FRED PPIACO - CPIAUCSL",
     "freq": "monthly", "sign": -1, "transform": ("gap_yoy",), "z_window": 36,
     "inputs": {"a": ("fred", "PPIACO"), "b": ("fred", "CPIAUCSL")}},
]

EXPOSURE_LINEAR = {"intercept": 0.75, "slope": 0.25, "floor": 0.35, "cap": 1.0}
REGIME_STEPS = [
    # (z 하한, 국면, 국면 한국어, 노출 계수)
    (0.75, "EXPANSION_STRONG", "확장(강)", 1.00),
    (0.25, "EXPANSION", "확장", 0.90),
    (-0.25, "NEUTRAL", "중립", 0.75),
    (-0.75, "SLOWDOWN", "둔화", 0.55),
    (-1.50, "CONTRACTION_WATCH", "수축 경계", 0.40),
]
REGIME_FLOOR = ("CONTRACTION", "수축", 0.25)

REFERENCES = [
    {"key": "stock_watson_1989", "cite": "Stock, J.H. & Watson, M.W. (1989). New Indexes of Coincident and "
                                          "Leading Economic Indicators. NBER Macroeconomics Annual 4, 351-394.",
     "used_for": "다변량 표준화 확산지수(성분 z 평균) 구성"},
    {"key": "stock_watson_2002", "cite": "Stock, J.H. & Watson, M.W. (2002). Macroeconomic Forecasting Using "
                                          "Diffusion Indexes. JBES 20(2), 147-162.",
     "used_for": "확산지수 예측력·IS/OOS 검증 틀"},
    {"key": "estrella_mishkin_1998", "cite": "Estrella, A. & Mishkin, F.S. (1998). Predicting U.S. Recessions: "
                                              "Financial Variables as Leading Indicators. REStat 80(1), 45-61.",
     "used_for": "10Y-2Y/3M 금리차 성분의 선행성"},
    {"key": "estrella_hardouvelis_1991", "cite": "Estrella, A. & Hardouvelis, G.A. (1991). The Term Structure as "
                                                  "a Predictor of Real Economic Activity. Journal of Finance 46(2), 555-576.",
     "used_for": "금리차 → 실물 선행 경로"},
    {"key": "gilchrist_zakrajsek_2012", "cite": "Gilchrist, S. & Zakrajšek, E. (2012). Credit Spreads and Business "
                                                 "Cycle Fluctuations. AER 102(4), 1692-1720.",
     "used_for": "신용경로(백테스트 벤치마크 CUBE-LEI3에 포함)"},
    {"key": "kilian_2009", "cite": "Kilian, L. (2009). Not All Oil Price Shocks Are Alike. AER 99(3), 1053-1069.",
     "used_for": "유가 블록을 모멘텀(수요)과 변동성(공급충격)으로 분리"},
    {"key": "gurkaynak_sack_wright_2010", "cite": "Gürkaynak, R., Sack, B. & Wright, J. (2010). The TIPS Yield "
                                                   "Curve and Inflation Compensation. Journal of Applied Econometrics.",
     "used_for": "손익분기 인플레이션 = 기대인플레 + 위험프리미엄"},
    {"key": "bernanke_kuttner_2005", "cite": "Bernanke, B. & Kuttner, K. (2005). What Explains the Stock Market's "
                                             "Reaction to Federal Reserve Policy? Journal of Finance 60(3), 1221-1257.",
     "used_for": "금리·실질금리 변화의 주가 할인율 경로"},
    {"key": "bhardwaj_gorton_rouwenhorst_2014", "cite": "Bhardwaj, G., Gorton, G. & Rouwenhorst, K.G. (2014). "
                                                         "Fooling Some of the People All of the Time: The Inefficient "
                                                         "Performance and Risk of Gold. NBER WP 20247.",
     "used_for": "금/주식 상대강도를 위험회피 로테이션 지표로 사용"},
]


# ------------------------------------------------------------------------- helpers
def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _now_str() -> str:
    return _now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _clip(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or POLICY_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "1.0", "mode": "shadow", "enabled_for_sizing": False,
                "exposure_linear": dict(EXPOSURE_LINEAR),
                "note": "정책 파일 없음 — 기본 shadow(주문 크기 불변)"}


def load() -> Dict[str, Any]:
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _latest_json_age_h() -> float:
    try:
        return (datetime.now(timezone.utc).replace(tzinfo=None) -
                datetime.fromtimestamp(os.path.getmtime(OUT_PATH), timezone.utc).replace(tzinfo=None)
                ).total_seconds() / 3600.0
    except Exception:
        return float("inf")


# --------------------------------------------------------------------- raw loading
def load_raw(force: bool = False, quiet: bool = True) -> Dict[str, Any]:
    """FRED 13계열 + Yahoo 13종목의 원계열. 실패는 available=False로 남기고 계속한다."""
    fred: Dict[str, pd.Series] = {}
    fred_meta: Dict[str, Dict[str, Any]] = {}
    try:
        data = market_snapshot.load_fred(series=FRED_IDS, force=force, include_history=True)
    except Exception as exc:                                   # noqa: BLE001
        data = {}
        if not quiet:
            print(f"[m8] FRED 로드 실패: {exc}")
    for sid in FRED_IDS:
        d = data.get(sid) or {}
        s = d.get("_series")
        if isinstance(s, pd.Series) and len(s) > 24:
            fred[sid] = s.astype(float).sort_index()
        fred_meta[sid] = {"available": bool(isinstance(s, pd.Series) and len(s) > 24),
                          "latest": d.get("latest"), "date": d.get("date"), "source": d.get("source")}

    prices: Dict[str, pd.DataFrame] = {}
    price_meta: Dict[str, Dict[str, Any]] = {}
    try:
        import yfinance as yf
        dl = yf.download(PRICE_SYMBOLS, period="max", interval="1d", progress=False,
                         auto_adjust=False, group_by="column", threads=True)
        prices = market_snapshot._normalize_download(dl, PRICE_SYMBOLS)
    except Exception as exc:                                   # noqa: BLE001
        if not quiet:
            print(f"[m8] Yahoo 로드 실패: {exc}")
    for sym in PRICE_SYMBOLS:
        f = prices.get(sym)
        price_meta[sym] = {"available": bool(f is not None and len(f) > 60),
                           "bars": int(len(f)) if f is not None else 0,
                           "last_date": str(f.index[-1].date()) if f is not None and len(f) else None,
                           "last": (round(float(f["Close"].iloc[-1]), 4)
                                    if f is not None and len(f) else None)}
    return {"fred": fred, "fred_meta": fred_meta, "prices": prices, "price_meta": price_meta}


def _series_of(raw: Dict[str, Any], kind: str, name: str) -> Optional[pd.Series]:
    if kind == "price":
        f = (raw.get("prices") or {}).get(name)
        if f is None or "Close" not in f:
            return None
        return f["Close"].astype(float).dropna()
    s = (raw.get("fred") or {}).get(name)
    return s.dropna() if isinstance(s, pd.Series) else None


# ------------------------------------------------------------------ transformations
def _transform(spec: Dict[str, Any], raw: Dict[str, Any]) -> Optional[pd.Series]:
    """native 주기 계열에 변환을 적용한다(가용성 정렬 전)."""
    name, args = spec["transform"][0], spec["transform"][1:]
    inp = spec.get("inputs") or {}
    a = _series_of(raw, *inp["a"]) if inp.get("a") else None
    b = _series_of(raw, *inp["b"]) if inp.get("b") else None
    if a is None:
        return None
    if name == "level":
        out = a
    elif name == "mom":
        out = a.pct_change(int(args[0])) * 100.0
    elif name == "chg":
        out = a.diff(int(args[0]))
    elif name == "trend_dev":
        n = int(args[0])
        out = (a / a.rolling(n).mean() - 1.0) * 100.0
    elif name == "vol":
        n = int(args[0])
        out = a.pct_change().rolling(n).std() * math.sqrt(252.0) * 100.0
    elif name == "rel_mom":
        if b is None:
            return None
        out = (a / b.reindex(a.index).ffill()).pct_change(int(args[0])) * 100.0
    elif name == "spread_level":
        if b is None:
            return None
        out = a - b.reindex(a.index).ffill()
    elif name == "diff_avg":
        out = a.diff().rolling(int(args[0])).mean()
    elif name == "yoy":
        out = a.pct_change(12) * 100.0
    elif name == "ann3m":
        out = ((a / a.shift(3)) ** 4 - 1.0) * 100.0
    elif name == "gap_yoy":
        if b is None:
            return None
        out = (a.pct_change(12) - b.reindex(a.index).ffill().pct_change(12)) * 100.0
    elif name == "yoy_weekly":
        ma4 = a.rolling(4).mean()
        out = (ma4 / ma4.shift(52) - 1.0) * 100.0
    else:                                                       # pragma: no cover
        return None
    out = out.replace([np.inf, -np.inf], np.nan).dropna()
    return out if len(out) else None


def _period_end(index: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if freq == "monthly":
        return idx + pd.offsets.MonthEnd(0)
    return idx


def _rolling_z(s: pd.Series, window: int) -> pd.Series:
    """과거 창만 쓰는 표준화(±3 클립). 미래 값은 쓰지 않는다."""
    min_p = max(12, window // 4)
    m = s.rolling(window, min_periods=min_p).mean()
    sd = s.rolling(window, min_periods=min_p).std()
    z = (s - m) / sd.replace(0, np.nan)
    return z.replace([np.inf, -np.inf], np.nan).clip(-3, 3)


def align_availability(series: pd.Series, freq: str, lag_days: int,
                       calendar: Optional[pd.DatetimeIndex] = None,
                       extra_lag_days: int = 0) -> pd.Series:
    """값을 `기간말 + lag_days` 날짜에야 알 수 있는 것으로 정렬한다.

    calendar가 주어지면 그 날짜들로 reindex+ffill(과거값만 사용). 없으면 원 지수 유지.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)
    avail = _period_end(series.index, freq) + pd.Timedelta(days=int(lag_days) + int(extra_lag_days))
    s = pd.Series(series.values, index=pd.DatetimeIndex(avail)).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if calendar is None:
        return s
    cal = pd.DatetimeIndex(calendar)
    return s.reindex(s.index.union(cal)).ffill().reindex(cal)


def load_sign_fit(path: Optional[str] = None) -> Dict[str, Any]:
    """백테스트가 IS 구간에서만 적합한 성분 부호(±1). 없으면 빈 dict(사전부호 사용)."""
    try:
        with open(path or SIGNS_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
        mult = doc.get("multipliers") or {}
        return {k: float(v) for k, v in mult.items() if float(v) in (-1.0, 1.0)}
    except Exception:
        return {}


def _lag_for(spec: Dict[str, Any]) -> int:
    """성분의 발표 지연일수 — FRED 계열 메타에서 읽고, 가격 성분은 0."""
    inp = spec.get("inputs") or {}
    a = inp.get("a") or ("", "")
    if a[0] == "fred":
        return int(FRED_META.get(a[1], {}).get("lag_days", 0))
    return 0


def build_panel(raw: Dict[str, Any], calendar: Optional[pd.DatetimeIndex] = None,
                extra_lag_days: int = 0,
                sign_fit: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """모든 성분의 z 패널·블록 점수·복합지수를 만든다(실전·백테스트 공용).

    Returns
        {"members": {key: {"z": Series, "value": Series(native), "spec": spec,
                            "available": bool, "known_from": date}},
         "member_z": DataFrame(index=calendar),
         "block_z": DataFrame, "composite": Series, "composite_equal": Series}
    """
    members: Dict[str, Dict[str, Any]] = {}
    z_cols: Dict[str, pd.Series] = {}
    fit = sign_fit if sign_fit is not None else load_sign_fit()
    for spec in MEMBER_SPECS:
        freq = spec["freq"]
        lag = _lag_for(spec)
        val = _transform(spec, raw)
        if val is None or len(val) < 24:
            members[spec["key"]] = {"available": False, "z": None,
                                    "error": "원계열/변환 없음", "spec": spec}
            continue
        z_native = _rolling_z(val, int(spec["z_window"]))
        avail = align_availability(z_native, freq, lag, calendar, extra_lag_days)
        mult = float(fit.get(spec["key"], 1.0))
        z_signed = avail * float(spec["sign"]) * mult
        z_cols[spec["key"]] = z_signed
        last_native_idx = val.index[-1]
        known_from = (_period_end(pd.DatetimeIndex([last_native_idx]), freq)[0]
                      + pd.Timedelta(days=int(lag) + int(extra_lag_days)))
        members[spec["key"]] = {
            "available": bool(z_signed.notna().any()),
            "z_native": z_native, "value_native": val, "z": z_signed,
            "last_period": str(pd.Timestamp(last_native_idx).date()),
            "known_from": str(known_from.date()),
            "lag_days": int(lag), "extra_lag_days": int(extra_lag_days),
            "sign_fit_multiplier": mult,
            "sign_effective": int(float(spec["sign"]) * mult),
            "latest_value": float(val.iloc[-1]), "latest_z_unsigned": float(z_native.iloc[-1])
            if pd.notna(z_native.iloc[-1]) else None,
            "spec": spec,
        }

    idx = calendar if calendar is not None else _union_index(z_cols)
    member_z = pd.DataFrame({k: v.reindex(idx) for k, v in z_cols.items()}, index=idx)

    block_z = pd.DataFrame(index=idx)
    for bkey in BLOCK_META:
        cols = [s["key"] for s in MEMBER_SPECS if s["block"] == bkey and s["key"] in member_z.columns]
        if not cols:
            continue
        block_z[bkey] = member_z[cols].mean(axis=1, skipna=True)
    weights = pd.Series({k: BLOCK_META[k]["weight"] for k in block_z.columns}, dtype=float)
    if len(weights) and weights.sum() > 0:
        denom = block_z.notna().mul(weights, axis=1).sum(axis=1)
        numer = block_z.mul(weights, axis=1).sum(axis=1, skipna=True)
        composite = (numer / denom.replace(0, np.nan)).clip(-3, 3)
        composite_equal = block_z.mean(axis=1, skipna=True).clip(-3, 3)
    else:
        composite = pd.Series(dtype=float)
        composite_equal = pd.Series(dtype=float)
    return {"members": members, "member_z": member_z, "block_z": block_z,
            "composite": composite, "composite_equal": composite_equal, "weights": weights}


def _union_index(z_cols: Dict[str, pd.Series]) -> pd.DatetimeIndex:
    idx: Optional[pd.DatetimeIndex] = None
    for s in z_cols.values():
        idx = s.index if idx is None else idx.union(s.index)
    return idx if idx is not None else pd.DatetimeIndex([])


# ------------------------------------------------------------------------ regime
def regime_for(z: Optional[float]) -> Tuple[str, str, float]:
    """복합 z → (국면 코드, 한국어, 단계 노출 계수)."""
    if z is None or not np.isfinite(z):
        return "UNKNOWN", "판정 불가", 0.5
    for lo, code, ko, scale in REGIME_STEPS:
        if z >= lo:
            return code, ko, scale
    return REGIME_FLOOR[0], REGIME_FLOOR[1], REGIME_FLOOR[2]


def linear_scale(z: Optional[float], params: Optional[Dict[str, float]] = None) -> float:
    """연속 노출 계수: clip(intercept + slope·z, floor, cap)."""
    p = dict(EXPOSURE_LINEAR)
    p.update(params or {})
    if z is None or not np.isfinite(z):
        return float(p["floor"])
    return _clip(float(p["intercept"]) + float(p["slope"]) * float(z), float(p["floor"]), float(p["cap"]))


def calibration() -> Dict[str, Any]:
    """백테스트가 저장한 IS 캘리브레이션(z → 20거래일 상승확률·기대수익)."""
    try:
        with open(BACKTEST_PATH, "r", encoding="utf-8") as f:
            bt = json.load(f)
        return bt.get("calibration") or {}
    except Exception:
        return {}


def outlook(z: Optional[float]) -> Dict[str, Any]:
    cal = calibration()
    out: Dict[str, Any] = {"horizon_days": cal.get("horizon_days", 20), "source": "none"}
    if z is None or not np.isfinite(z):
        return out
    if cal.get("logit_intercept") is not None and cal.get("logit_slope") is not None:
        p = 1.0 / (1.0 + math.exp(-(float(cal["logit_intercept"]) + float(cal["logit_slope"]) * float(z))))
        out.update({"p_up": round(p * 100, 1), "source": "IS logit 캘리브레이션(In-Sample 적합 → OOS 별도 보고)"})
        if cal.get("ret_intercept") is not None and cal.get("ret_slope") is not None:
            out["expected_return_pct"] = round(float(cal["ret_intercept"]) + float(cal["ret_slope"]) * float(z), 2)
    return out


# ------------------------------------------------------------------------ compute
def compute(raw: Optional[Dict[str, Any]] = None, force: bool = False,
            quiet: bool = True, extra_lag_days: int = 0) -> Dict[str, Any]:
    raw = raw or load_raw(force=force, quiet=quiet)
    calendar = None
    spy = (raw.get("prices") or {}).get("SPY")
    if spy is not None and len(spy) > 60:
        calendar = pd.DatetimeIndex(spy.index)
    panel = build_panel(raw, calendar, extra_lag_days=extra_lag_days)
    return summarize(panel, raw, extra_lag_days=extra_lag_days, quiet=quiet)


def summarize(panel: Dict[str, Any], raw: Dict[str, Any], extra_lag_days: int = 0,
              quiet: bool = True) -> Dict[str, Any]:
    member_z: pd.DataFrame = panel["member_z"]
    block_z: pd.DataFrame = panel["block_z"]
    comp: pd.Series = panel["composite"].dropna()
    notes: List[str] = []

    as_of = str(comp.index[-1].date()) if len(comp) else None
    z = float(comp.iloc[-1]) if len(comp) else None
    z_prev = float(comp.iloc[-2]) if len(comp) > 1 else None
    z_3m_ago = _value_asof(comp, _shift_months(comp.index[-1], -3)) if len(comp) else None

    pol = load_policy()
    regime, regime_ko, step_scale = regime_for(z)
    lin_scale = linear_scale(z, (pol.get("exposure_linear") or {}))

    blocks: List[Dict[str, Any]] = []
    for bkey, meta in BLOCK_META.items():
        bz = None
        if bkey in block_z.columns and len(block_z[bkey].dropna()):
            bz = float(block_z[bkey].dropna().iloc[-1])
        members_out = []
        for spec in MEMBER_SPECS:
            if spec["block"] != bkey:
                continue
            m = panel["members"].get(spec["key"]) or {}
            if not m.get("available"):
                members_out.append({"key": spec["key"], "label": spec["label"], "source": spec["source"],
                                    "available": False, "error": m.get("error"),
                                    "freq": spec["freq"], "sign": spec["sign"]})
                continue
            zs = m["z"].dropna()
            members_out.append({
                "key": spec["key"], "label": spec["label"], "source": spec["source"],
                "freq": spec["freq"], "sign": spec["sign"],
                "sign_effective": m.get("sign_effective", spec["sign"]),
                "sign_fit_flipped": m.get("sign_fit_multiplier", 1.0) < 0,
                "transform": spec["transform"][0] + (f"({spec['transform'][1]})" if len(spec["transform"]) > 1 else ""),
                "lag_days": m["lag_days"], "known_from": m["known_from"], "last_period": m["last_period"],
                "latest_value": round(float(m["latest_value"]), 4),
                "z_unsigned": round(float(m["latest_z_unsigned"]), 2) if m["latest_z_unsigned"] is not None else None,
                "z_signed": round(float(zs.iloc[-1]), 2) if len(zs) else None,
                "available": True,
            })
        blocks.append({
            "key": bkey, "name": meta["name"], "name_en": meta["name_en"], "icon": meta["icon"],
            "weight": meta["weight"], "why": meta["why"],
            "z": round(bz, 2) if bz is not None and np.isfinite(bz) else None,
            "direction": ("RISK_ON" if (bz or 0) > 0.15 else "RISK_OFF" if (bz or 0) < -0.15 else "NEUTRAL"),
            "contribution": round(meta["weight"] * bz, 3) if bz is not None and np.isfinite(bz) else None,
            "members": members_out,
        })

    available_members = sum(1 for b in blocks for m in b["members"] if m.get("available"))
    total_members = sum(len(b["members"]) for b in blocks)

    # 월별 복합지수 이력(최근 24개월, 같은 과거창 z)
    monthly_hist = []
    if len(comp):
        try:
            me = comp.resample("ME").last().dropna().tail(24)
            monthly_hist = [{"month": ix.strftime("%Y-%m"), "z": round(float(v), 3)} for ix, v in me.items()]
        except Exception:
            monthly_hist = []

    enabled = bool(pol.get("enabled_for_sizing"))
    applied_scale = lin_scale if enabled else 1.0
    fit = load_sign_fit()

    out = {
        "engine": ENGINE,
        "generated_at_kst": _now_str(),
        "as_of": as_of,
        "extra_lag_days": int(extra_lag_days),
        "composite": {
            "z": round(z, 3) if z is not None else None,
            "index": round(100 + 10 * z, 1) if z is not None else None,
            "index_scale": "100 = 장기추세, 1σ = 10pt (OECD CLI 진폭조정과 동일 규약)",
            "score": round(_phi(z) * 100, 1) if z is not None else None,
            "score_scale": "0-100 (표준정규 CDF)",
            "change_20d": round(z - z_prev, 3) if z is not None and z_prev is not None else None,
            "change_3m": round(z - z_3m_ago, 3) if z is not None and z_3m_ago is not None else None,
            "equal_weight_z": round(float(panel["composite_equal"].dropna().iloc[-1]), 3)
            if len(panel["composite_equal"].dropna()) else None,
            "regime": regime, "regime_ko": regime_ko,
            "sign_source": ("IS_FIT" if fit else "PRIOR"),
            "sign_source_note": ("성분 부호를 IS(2015-2021) IC로 재적합한 값(data/macro_leading_signs.json). "
                                 "OOS(2022~)에서 별도 검증됨." if fit else
                                 "논문·경제논리 사전부호 — 백테스트 부호 적합 파일이 없을 때의 기본값."),
            "step_exposure_scale": step_scale,
            "linear_exposure_scale": round(lin_scale, 3),
            "applied_exposure_scale": round(applied_scale, 3),
            "components_available": available_members, "components_total": total_members,
            "monthly_history": monthly_hist,
        },
        "blocks": blocks,
        "market_outlook": outlook(z),
        "policy": {
            "mode": pol.get("mode", "shadow"),
            "enabled_for_sizing": enabled,
            "risk_scale_if_enabled": round(lin_scale, 3),
            "risk_scale_applied": round(applied_scale, 3),
            "formula": "scale = clip(0.75 + 0.25·z, 0.35, 1.0) — 진입 예산·건당 리스크 상한에 곱함",
            "entry_filter": pol.get("entry_filter") or {"enabled": False, "min_z": None},
            "note": ("shadow — 계산·보고만 하며 주문 크기를 바꾸지 않는다. "
                     "동훈님 승인 후 config/macro_leading_policy.json의 enabled_for_sizing을 true로."
                     if not enabled else
                     "ACTIVE — 이 계수가 진입 예산에 실제로 곱해진다(백테스트 검증 범위)."),
            "approval_required": not enabled,
            "backtest_evidence": (calibration().get("verdict") or {}),
        },
        "methodology": {
            "blocks": {k: {"name": v["name"], "weight": v["weight"], "why": v["why"]}
                       for k, v in BLOCK_META.items()},
            "member_count": len(MEMBER_SPECS),
            "z": "rolling z-score(과거 창만: 일간 756거래일 / 월간 36개월 / 주간 104주), ±3 클립",
            "publication_lag": "값은 '기간말 + lag_days'부터 사용 가능 — look-ahead 차단",
            "composite": "Z = Σ w_block · block_z / Σ w_block (block_z = 성분 z 평균, 부호 반영)",
            "regime": "z ≥0.75 확장(강) / ≥0.25 확장 / ≥-0.25 중립 / ≥-0.75 둔화 / ≥-1.5 수축경계 / 그 외 수축",
            "revision_caveat": ("무료 FRED CSV는 최종 개정치다. ALFRED 빈티지(API 키 필요)를 쓰지 않으므로 "
                                "발표 당시 값과 다를 수 있다. YoY·3개월 변화 위주로 개정 둔감하게 설계했고, "
                                "백테스트는 +10일 추가 시차 강건성 검정을 함께 보고한다."),
            "references": REFERENCES,
        },
        "data_provenance": {
            "fred": {sid: {"latest": (raw.get("fred_meta", {}).get(sid) or {}).get("latest"),
                           "date": (raw.get("fred_meta", {}).get(sid) or {}).get("date"),
                           "source": (raw.get("fred_meta", {}).get(sid) or {}).get("source"),
                           "lag_days": FRED_META[sid]["lag_days"]} for sid in FRED_IDS},
            "prices": {sym: (raw.get("price_meta", {}).get(sym) or {}) for sym in PRICE_SYMBOLS},
            "hardcoded_values": False,
        },
        "notes": notes,
    }
    if not quiet:
        print(f"✅ CUBE-M8 z {z:+.2f} · {regime_ko} · 성분 {available_members}/{total_members} · "
              f"노출계수(선형) {lin_scale:.2f} (mode={pol.get('mode', 'shadow')})")
    return out


def _value_asof(s: pd.Series, ts: pd.Timestamp) -> Optional[float]:
    sub = s[s.index <= ts]
    return float(sub.iloc[-1]) if len(sub) else None


def _shift_months(ts: pd.Timestamp, months: int) -> pd.Timestamp:
    try:
        return pd.Timestamp(ts) + pd.DateOffset(months=months)
    except Exception:                                            # pragma: no cover
        return pd.Timestamp(ts) - pd.Timedelta(days=90)


def export(result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    res = result or compute(quiet=False)
    payload = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    for p in (OUT_PATH, DOCS_PATH):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)
    return res


# ------------------------------------------------------- trading-algorithm interface
def risk_scale(result: Optional[Dict[str, Any]] = None,
               policy_path: Optional[str] = None) -> Tuple[float, Dict[str, Any]]:
    """매매 알고리즘용 노출 계수.

    shadow(기본)면 1.0을 돌려주어 주문 크기가 **전혀** 바뀌지 않는다. 계수 자체는
    meta에 그대로 남겨 대시보드·보고에 쓴다. `enabled_for_sizing`이 true일 때만 곱한다.
    """
    res = result if result is not None else load()
    pol = load_policy(policy_path)
    comp = (res.get("composite") or {}) if res else {}
    z = comp.get("z")
    scale = linear_scale(z, (pol.get("exposure_linear") or {}))
    meta = {"mode": pol.get("mode", "shadow"), "z": z,
            "regime": comp.get("regime"), "regime_ko": comp.get("regime_ko"),
            "shadow_scale": round(scale, 3), "applied_scale": 1.0,
            "as_of": res.get("as_of"), "generated_at_kst": res.get("generated_at_kst"),
            "source": "data/macro_leading.json"}
    if not pol.get("enabled_for_sizing"):
        return 1.0, meta
    meta["applied_scale"] = round(scale, 3)
    return round(scale, 3), meta


def attach_to_lei(lei_out: Dict[str, Any], max_age_h: float = 6.0,
                  compute_if_stale: bool = False) -> Dict[str, Any]:
    """CUBE-LEI 산출물에 M8 블록을 붙인다.

    기본은 **파일 재사용**이다(수집은 전용 워크플로/스크립트가 담당). 10분 펄스·
    오케스트레이터가 매번 FRED·Yahoo를 다시 때리지 않게 하고, `compute_if_stale`을
    주면 오래된 경우에만 다시 계산한다.
    """
    cached = load()
    if cached.get("composite"):
        age = _latest_json_age_h()
        cached["stale"] = bool(age > max_age_h)
        cached["age_h"] = round(age, 2)
        lei_out["macro8"] = _compact(cached)
        if not cached["stale"] or not compute_if_stale:
            return lei_out
    elif not compute_if_stale:
        lei_out["macro8"] = {"error": "CUBE-M8 미수집 — scripts/sync_macro_leading.py 실행 필요"}
        return lei_out
    try:
        fresh = compute(quiet=True)
        lei_out["macro8"] = _compact(fresh)
    except Exception as exc:                                     # noqa: BLE001
        if cached.get("composite"):
            cached["stale"] = True
            cached["error"] = type(exc).__name__
            lei_out["macro8"] = _compact(cached)
        else:
            lei_out["macro8"] = {"error": f"CUBE-M8 미확보: {type(exc).__name__}"}
    return lei_out


def _compact(res: Dict[str, Any]) -> Dict[str, Any]:
    """대시보드·다른 에이전트가 쓰는 축약본(성분 상세 포함, 원계열 제외)."""
    comp = res.get("composite") or {}
    return {
        "engine": res.get("engine"), "as_of": res.get("as_of"),
        "generated_at_kst": res.get("generated_at_kst"),
        "z": comp.get("z"), "index": comp.get("index"), "score": comp.get("score"),
        "regime": comp.get("regime"), "regime_ko": comp.get("regime_ko"),
        "change_20d": comp.get("change_20d"), "change_3m": comp.get("change_3m"),
        "step_exposure_scale": comp.get("step_exposure_scale"),
        "linear_exposure_scale": comp.get("linear_exposure_scale"),
        "applied_exposure_scale": comp.get("applied_exposure_scale"),
        "components_available": comp.get("components_available"),
        "components_total": comp.get("components_total"),
        "monthly_history": comp.get("monthly_history"),
        "blocks": res.get("blocks"), "market_outlook": res.get("market_outlook"),
        "policy": res.get("policy"), "notes": res.get("notes"),
        "stale": res.get("stale", False),
    }


if __name__ == "__main__":
    export(compute(quiet=False))
