# 🛡️ US Capital Preservation Quant Engine

> **Motto:** *"Rule No. 1: Never lose money. Rule No. 2: Never forget rule No. 1."*  
> **Production Quantitative Auto-Trading System for US Equities & Multi-Asset ETFs**

---

## 1. Overview & Research Philosophy

This system is engineered for **absolute capital preservation and asymmetric upside capture**. Instead of maximizing unconstrained returns through excessive beta, the engine enforces strict downside tail-risk boundaries while compounding returns in calm regimes.

### Core Mathematical & Empirical Foundations:
1. **Hierarchical Risk Parity (HRP)** (*López de Prado, 2016*):
   - Overcomes Markowitz's ill-conditioned covariance matrix inversion instability via single-linkage tree clustering and recursive bisection.
2. **Dual Momentum & Time-Series Momentum (TSMOM)** (*Gary Antonacci 2014, Moskowitz et al. 2012*):
   - Absolute momentum ensures the engine holds risk assets only when they outperform cash proxies.
3. **Macro & Volatility Regime-Switching** (*Hamilton 1989*):
   - Categorizes market into 4 distinct phases (`BULL_CALM`, `BULL_VOLATILE`, `BEAR_CALM`, `BEAR_VOLATILE`).
   - Liquidates to 100% Cash/SGOV during panic crash regimes.
4. **Target Volatility & Drawdown Circuit Breaker**:
   - Caps annualized portfolio volatility at 10%.
   - Automated 8% soft de-leveraging and 12% hard circuit-breaker stop.

---

## 2. System Architecture & CUBE 10-Agent Organization

```
us-capital-preservation/
├── agents/                     # 🏛️ CUBE 10-Agent Autonomous Organization
│   ├── base.py                 # Abstract Base Agent definition
│   ├── agent1_news_sentiment.py # 📰 Agent 1: News & Reddit/X Social Sentiment Specialist
│   ├── agent2_tech_momentum.py # 📈 Agent 2: Technical Price & Momentum Specialist (MA/RSI/BB)
│   ├── agent3_fundamentals.py  # 📊 Agent 3: Earnings Consensus & Market Fundamentals Specialist
│   ├── agent4_macro_rates.py   # 🌐 Agent 4: Macroeconomics, Rates, Yield Curve & Tariffs Specialist
│   ├── agent5_superinvestor.py # 👑 Agent 5: Superinvestor 13F Tracker (Buffett, Druckenmiller)
│   ├── agent6_quant_scenarios.py # 🔬 Agent 6: Quant Academic Research & 10 Scenarios Modeler
│   ├── agent7_backtester.py    # ⚙️ Agent 7: 10 Scenarios Historical Backtesting Engineer
│   ├── agent8_alpha_evaluator.py # 💎 Agent 8: Profitable Scenario Alpha & Risk Evaluator
│   ├── agent9_ensemble_synthesizer.py # 🧬 Agent 9: Optimal Scenario Ensemble & Weight Synthesizer
│   ├── agent10_daily_trader.py # ⚡ Agent 10: Daily Execution, Profit-Taking & Reinvestment Trader
│   └── cube_orchestrator.py    # CUBE Orchestrator coordinating all 10 agents
├── config/
│   └── settings.yaml          # Risk limits, universe, volatility targets
├── core/
│   ├── hrp.py                 # Hierarchical Risk Parity optimizer
│   ├── regime.py              # S&P 200 DMA + VIX Regime classification
│   ├── signals.py             # Dual Momentum & Volatility Targeting
│   └── risk.py                # Max Drawdown & CVaR Circuit Breakers
├── data/
│   ├── fetcher.py             # Market data ingestion (Yahoo Finance / FRED)
│   └── cube_report.json       # Consolidated 10-agent output report
├── backtest/
│   └── engine.py              # Vectorized backtesting & performance metrics
├── execution/
│   ├── nh_trader.py           # NH Investment & Securities (Namuh) OpenAPI broker
│   └── alpaca_trader.py       # Alpaca Paper & Live API execution
├── cube.html                  # 🏛️ CUBE 10-Agent Interactive Trading Dashboard
├── strategy.html              # 📖 Comprehensive Quantitative Strategy & Logic Guide
├── index.html                 # 📊 HRP Portfolio Live Dashboard
├── docs/                      # GitHub Pages deployment directory (cube, strategy, index)
├── main.py                    # Pipeline Entrypoint (--mode cube, daily, rebalance, backtest)
└── requirements.txt
```

---

## 3. Web Dashboards & CUBE Terminal

1. **🏛️ CUBE 10-Agent Trading Desk**: `https://stevepark310.github.io/us-capital-preservation/cube.html`
   - Real-time work reporting of all 10 specialized agents
   - 10 investment scenarios backtest matrix & profitable scenario selection
   - Ensemble scenario blending & daily target asset allocation
   - Daily profit-taking execution terminal & 100% automated compounding reinvestment
2. **📖 전략 상세 가이드 (Strategy Spec)**: `https://stevepark310.github.io/us-capital-preservation/strategy.html`
3. **📊 메인 대시보드 (HRP Monitor)**: `https://stevepark310.github.io/us-capital-preservation/`

---

## 4. Execution Commands

```bash
# Run CUBE 10-Agent Pipeline & update dashboard report
python main.py --mode cube

# Run Live Daily Trading & Profit Realization via NH Open API
python main.py --mode daily --broker nh

# Run Legacy HRP Rebalancing / Backtest
python main.py --mode rebalance
python main.py --mode backtest
```
4. Click **Save**. Your free live URL will be:  
   👉 `https://stevepark310.github.io/us-capital-preservation/`

---

## 4. Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run historical simulation & performance metrics
python main.py --mode backtest

# 3. Run live regime analysis and paper rebalancing
python main.py --mode rebalance
```

---

## 5. CUBE 10-Agent 24/7 Architecture (2026-11-14 개편)

모든 세션(정규장·프리마켓·시간외·주말·휴장일)에서 10인 전원이 **실데이터**로 보고합니다.
정적 템플릿과 하드코딩된 시세/뉴스는 제거되었습니다.

### 데이터 경로 (한 번 수집 → 전원 공유)
| 모듈 | 역할 |
|---|---|
| `core/market_clock.py` | 세션 판정(REGULAR/PRE_MARKET/POST_MARKET/OFF_HOURS/WEEKEND_PREP/HOLIDAY_PREP), 다음 개장·프리마켓 시각 |
| `core/market_snapshot.py` | Yahoo Finance 일봉(1년)·분봉(5m)·지수선물·금리·환율·크립토 단일 스냅샷 (`data/market_snapshot.json`) |
| `core/news_engine.py` | RSS 8종 + Google News 6쿼리 + yfinance 뉴스 API → 관련도·감성·최신성 점수, IB 액션 헤드라인 추출 (`data/news_feed_raw.json`) |
| `core/technical.py` | ATR/RSI2/BB %B/RVOL/EMA/주간 피벗, 진입 점수, 월요일 갭 시나리오 |
| `core/exit_planner.py` | **체결가 기준** ATR 동적 브래킷(TP1/TP2/SL), 본전+수수료 래칫, 샹들리에+EMA 이중 트레일 러너 |
| `core/ledger.py` | 실전 원장 단일 환율·현금 버퍼·정합성 검증 (`data/ledger_consistency.json`) |
| `core/backtest_lab.py` | 60분봉 경로 백테스트(왕복 0.24%, 손절 우선) 개선 전/후 + ablation + 숏 트리거 스윕 |
| `core/swing_engine.py` | DIP_SWING(과매도 패닉 매수·2~3세션) 신호·포트폴리오 시뮬레이션 |

### 에이전트별 산출물 (모든 모드 공통)
| Agent | 입력 | 산출 |
|---|---|---|
| 1 뉴스/센티먼트 | RSS·뉴스 API·실시간 시세 | 종목별 센티먼트 점수, 실제 헤드라인·IB 액션, 브래킷 보정, 주말 크립토/외환 블록 |
| 2 기술적 | 일봉 OHLCV | ATR·RSI2·%B·RVOL·돌파/이탈 레벨, 진입 점수, DIP_SWING 워치리스트, 갭 시나리오 |
| 3 촉매 | yfinance 실적 캘린더 + 공식 일정 | 실적 D-day(KST 변환), 매크로 이벤트, 헤드라인 촉매 |
| 4 매크로 | VIX·DXY·10Y/3M·금리·FX·지수·선물·크립토 | 레짐 판정, 리스크오프 트리거, 레버리지 허용 여부 |
| 5 스마트머니 | 실제 옵션 체인 | Put/Call OI·거래량비, 감마월, 맥스페인 |
| 6 시나리오 | 10개 시나리오 + 실측 ATR | ATR 재정규화 브래킷, 월요일 갭업/갭다운/보합 플레이북 |
| 7 백테스트 | `data/1y_backtest_report.json` | 개선 전/후·DIP_SWING 실측 성과, 숏 트리거 최적화 |
| 8 알파 | Agent 7 지표 | PF·Sharpe·MDD·거래수 기준 승인/폐기, 실행 모드 |
| 9 앙상블 | 승인 전략·센티먼트·레짐·원장 | 목표 비중과 USD/KRW 배분 |
| 10 집행 | 원장·브래킷·계획 | 정합성 검증, 체결가 기준 브래킷 재산정, 다음 개장 대기 계획 |

### 실행
```bash
python3 scripts/run_full_pipeline.py     # 원장 동기화 → 10인 파이프라인·피드 → HTML → 위젯 복사
python3 scripts/run_1y_backtest.py       # 개선 전/후 + DIP_SWING + 숏 트리거 스윕 (수 분 소요)
python3 scripts/reconcile_ledger.py --fix
python3 scripts/monitor_and_exit.py --dry-run
```

### 매매 로직 변경 요약
- 브래킷을 **계획가가 아니라 실제 체결가**에 고정하고, 목표 폭을 종목 ATR에 비례해 재산정.
- TP1 이후 **본전+수수료(0.34%) 래칫**, TP2 이후 러너는 **샹들리에(0.9ATR)와 EMA20 중 타이트한 쪽**으로 추적하며 최소 +1.5% 이익을 잠금.
- 진입: 돌파 추격 단독 → (돌파 + 과매도 반등) 결합, 레짐·센티먼트에 따라 비중·목표 가변.
- 숏 엔진: VIX 트리거를 18~24 구간으로 스윕해 Calmar 기준 최적값 선택(결과는 `data/1y_backtest_report.json`).
- 검증 결과 장중 회전은 마찰 후 기대값이 음수였고, 과매도 매수·2~3세션 보유(DIP_SWING)가
  1년 PF 1.98 / MDD -6.63%, 2년 PF 2.04 / MDD -6.64%로 검증되었습니다(시뮬레이션).
  DIP_SWING은 **오버나이트 보유 승인 대기** 상태로, 승인 전에는 집행하지 않습니다.

---

## 6. 채권 · 환율 · 코인 · 경기선행지표 (2026-11-14 확장)

사용자 지시(2026-11-14 21:31): *"코인과 미국 국채 등 채권 그리고 환율 시장에서도 모니터링과 분석이 필요해.
경기 선행지표를 각종 논문과 경제학 서적을 참고해서 산출하고 ... 각 담당 에이전트 직원들에게 감시하고 분석해서
업무보고를 올리도록 해줘."* — 각 에이전트의 정식 직무와 업무보고에 4대 영역이 편입되었습니다.

### 6.1 실데이터 수집 확장 (`core/market_snapshot.py`)
| 영역 | 심볼 |
|---|---|
| 미국 국채/채권 | `^TNX`(10Y) `^IRX`(3M) `^FVX`(5Y) `^TYX`(30Y) `TLT` `IEF` `SHY` `SGOV` `TIP` `HYG` `LQD` |
| 외환 | `DX-Y.NYB` `KRW=X` `JPY=X` `EURUSD=X` `CNY=X` |
| 코인 | `BTC-USD` `ETH-USD` `SOL-USD` `XRP-USD` `IBIT` `COIN` |
| 경기순환 프록시 | `HG=F`(구리) `GC=F`(금) `XLY` `XLP` `IWM` |

FRED 공개 CSV(`fredgraph.csv`, API 키 불필요) 로더 추가: `T10Y3M` `T10Y2Y` `DGS2` `DGS10`
`BAMLH0A0HYM2`(HY OAS) `NFCI` `ICSA` `UMCSENT`. `data_cache/fred/*.csv`에 TTL 6시간 캐시,
실패 시 이전 캐시 → 스냅샷 값 순으로 폴백하며 각 값에 `source`를 남깁니다.

### 6.2 경기선행지표 엔진 (`core/leading_indicators.py`)
| 방법론 | 출처 | 적용 |
|---|---|---|
| 12개월 침체 프로빗 | Estrella & Mishkin (1998) *REStat* 80(1):45-61 / Estrella & Hardouvelis (1991) *JF* 46(2) | `P = Φ(-0.6045 - 0.8180 × (10Y-3M))` |
| 다변량 선행지수 | Stock & Watson (1989) *NBER Macro Annual* 4 / (2002) *JBES* 20(2) | 표준화 성분 가중합 + 동일가중 확산지수 비교 |
| 신용·유동성 | Gilchrist & Zakrajšek (2012) *AER* 102(4) | HY OAS 수준·z + HYG/IEF 상대강도 (EBP 프록시) |
| 국면·합성 | Burns & Mitchell (1946) / Conference Board LEI / OECD CLI | 4국면 판정, 1σ = 10pt 진폭조정 |

- `CUBE-LEI = 100 + 10·Z` (Z는 5년 표준화 성분의 가중합), `score = Φ(Z)·100`.
- 성분 9개: 10Y-3M · 10Y-2Y · HY OAS · NFCI · 실업수당 YoY · S&P500 12M · 미시간 소비자심리 · 구리/금 · XLY/XLP.
- 검증 가능한 값만 사용하며, 미확보 성분은 가중치 재정규화 후 보고서에 명시합니다.

### 6.3 에이전트별 반영
| Agent | 확장된 담당 업무 |
|---|---|
| 1 | 채권/연준 · 외환 · 크립토 섹터 센티먼트 **분리 보고** (`sector_sentiment`) |
| 2 | TLT·IEF·SHY·HYG·LQD, DXY·원/달러·엔/달러·유로·위안, BTC·ETH·SOL·XRP·IBIT 기술적 레벨 (`cross_asset_levels`) |
| 4 | 4대 영역 통합(선행지표·채권·환율·코인) + `handoff_to_agents` |
| 5 | HYG/LQD 크레딧, TLT 듀레이션 흐름, IBIT/BTC 기관 크립토 흐름 |
| 6 | 국면별 시나리오 확률 재배분 + 레버리지 상한 계수·종목 상한 |
| 7 | 선행지표·금리차 필터의 MDD/Sharpe 방어효과 5년 실측 검증 (`lei_filter_backtest`) |
| 8 | 환노출 민감도·일간 VaR, SGOV 캐리 vs 현금 기회비용, BTC 주말→월요일 QQQ 갭 회귀 |
| 9 | 5축(주식·채권·외환·코인·사이클) 융합 → `RISK_ON` / `CAUTIOUS_ROTATION` / `DEFENSIVE_TBILL` / `BEAR_HEDGE` |
| 10 | 국면별 사이징·종목 상한, SGOV/SHY 파킹 제안, 환율 코멘트, 코인 주말 경보 (DIP_SWING 승인·NVDA $173/$150·BEAR ALPHA 게이트 보존) |

### 6.4 실행
```bash
python3 core/leading_indicators.py          # CUBE-LEI 단독 산출 → data/leading_indicators.json
python3 scripts/run_lei_filter_backtest.py  # 필터 방어효과 5년 시뮬 → data/lei_filter_backtest.json
python3 scripts/run_full_pipeline.py        # 위 두 단계 포함 전체 파이프라인
```
