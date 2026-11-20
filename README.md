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
├── index.html                 # 🏛️ 통합 대시보드 (유일한 정본 — 개요·사무실·매크로·크로스마켓·시나리오·전략·업무보고)
├── dashboard/template.html    # 대시보드 UI 원본 (HTML/CSS/JS, __SNAP_JSON__ 자리표시자)
├── assets/office_day.png      # 아이소메트릭 사무실(낮) — 10석 실측 좌표로 직원 배치
├── assets/office_night.png    # 아이소메트릭 사무실(밤) — 06:00~18:00 KST 자동 전환
├── cube.html / feed.html      # ↪ 통합 대시보드로 리다이렉트 (옛 링크 호환)
├── strategy.html / scenarios.html  # ↪ 통합 대시보드로 리다이렉트 (내용은 탭으로 흡수)
├── docs/                      # GitHub Pages 배포 디렉터리 (index.html은 ../index.html로 리다이렉트)
├── main.py                    # Pipeline Entrypoint (--mode cube, daily, rebalance, backtest)
└── requirements.txt
```

---

## 3. Web Dashboards & CUBE Terminal

**통합 대시보드**: `https://stevepark310.github.io/us-capital-preservation/`

2026-11-18 개편으로 페이지는 `index.html` 한 장이다. `cube.html`·`feed.html`·`strategy.html`·
`scenarios.html`·`docs/index.html`은 모두 이 페이지의 해당 탭으로 리다이렉트된다(중복 제거).

| 탭 | 내용 |
|---|---|
| 🏛️ 개요 | 실전 계좌·손익·보유 포지션·무인 집행 상태·시스템 상태·**최신화 점검 보고** |
| 🏢 사무실 | 아이소메트릭 10석 디오라마(낮/밤 자동·수동), 직원 클릭 → 프로필 + 그 직원의 업무보고 스크롤 |
| 🌐 매크로 레이더 | VIX·금리·HY OAS·DXY·CUBE-LEI·침체확률, 선행지수 구성요소, FRED 원계열 |
| 🔀 크로스마켓 | 주식·ETF·선물, 채권 ETF·수익률 곡선, 외환·코인·원자재 |
| 📐 시나리오 | 세션 시나리오 확률, 승인/폐기 전략, 목표 자산 배분 |
| 🧭 전략·정책 | 투자 원칙, 4계층 파이프라인, 국면 매트릭스, 실전 가드레일·파라미터, 10인 조직 체계 |
| 💬 업무보고 | 전체 피드 — 직원·유형 필터, 검색, 본문 펼치기 |

**CUBE 10인 직원** (이름·직함·좌석은 `agents/agent_profiles.py`가 정본):

| # | 이름 | 직함 | 좌석 |
|---|---|---|---|
| 1 | 한지우 | 수석 뉴스·센티먼트 분석가 | 안쪽 1 |
| 2 | 이도현 | 멀티에셋 기술적 분석가 | 안쪽 2 |
| 3 | 박서연 | 실적 촉매 분석가 | 안쪽 3 |
| 4 | 최민준 | 글로벌 매크로·채권 전략가 | 안쪽 4 |
| 5 | 정하린 | 스마트머니·기관 포지셔닝 분석가 | 안쪽 5 |
| 6 | 강우진 | 퀀트 시나리오 모델러 | 앞줄 1 |
| 7 | 윤태호 | 백테스트 검증 엔지니어 | 앞줄 2 |
| 8 | 임수아 | 알파·리스크 평가자 | 앞줄 3 |
| 9 | 송재원 | 앙상블 융합 총괄 (수석) | 앞줄 4 |
| 10 | 김시우 | 실전 집행관 (트레이더) | 앞줄 5 |

**최신화(Refresh)** — 헤더의 `↻ 전체 최신화` 또는 직원별 `↻ 최신화` 버튼은
`docs/agent_feed.json`·`cube_report.json`·`live_portfolio.json`·`market_board.json`·
`leading_indicators.json`·`autotrader_status.json`·`cube_pulse.json`을 `?ts=` 캐시 우회로 다시 받아,
마지막 점검 시점 이후 바뀐 항목(자산·손익·VIX·LEI·집행 모드·신규 보고)과 10인 상태를 다시 보고한다.
워크플로 파일은 수정하지 않으며, `고급 → GitHub Actions 원격 트리거`에서 PAT(actions:write)로
`workflow_dispatch`를 호출하는 옵션만 클라이언트에 둔다(토큰은 저장하지 않음).

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
python3 scripts/run_full_pipeline.py     # 원장 동기화 → 10인 파이프라인·피드 → 통합 대시보드 → 위젯 복사
python3 scripts/build_dashboard.py       # index.html만 다시 렌더 (데이터 수정 후)
python3 scripts/run_1y_backtest.py       # 개선 전/후 + DIP_SWING + 숏 트리거 스윕 (수 분 소요)
python3 scripts/reconcile_ledger.py --fix
python3 scripts/monitor_and_exit.py --dry-run
```

---

## 5-b. 클라우드 상시 자동매매 · 10인 협업 빌드업 (2026-11-19 개편)

동훈님 지시(2026-11-19): *"결함 해결해주고 피클 토큰을 다 써도 장중 자동으로 매수매도가 되도록
모든 직원이 상시로 계속 일하면서 서로 업무 결과를 주고 받고 지속 빌드업 하면서 투자 수익을
지속 향상시킬 수 있도록 만들어줘"* — 피클 앱과 무관하게 GitHub Actions만으로 도는 실행 구조와,
직원 간 **실제 핸드오프**(가설 → 실측 검증 → 승인/보류 → 적용·복기)를 넣었습니다.
자세한 내용은 [`docs/cloud_automation.md`](docs/cloud_automation.md).

| 구성 | 파일 | 역할 |
|---|---|---|
| 장중 10분 집행 | `.github/workflows/market_autotrade.yml` | 정규장이면 `autotrader --execute` + `monitor_and_exit --execute` + 원장·상태 동기화, `data/`·`docs/` 커밋 |
| 24/7 협업 펄스 | `.github/workflows/cube_pulse.yml` | 30분마다 원장 → 상태 → 10인 업무보고 → 협업·빌드업 → 대시보드 |
| 실행기 | `scripts/cloud_cycle.py` | `market` / `pulse` / `status` — 단계 순서와 실패 처리를 한 곳에 |
| 시장 시계 | `core/market_clock.py` | 미국 동부 DST 규칙으로 세션 판정(EDT 22:30 KST 개장까지 자동) |
| 유령 포지션 정리 | `core/position_reconciler.py` | 원장(`live_portfolio.json`)만 보유 정본 — 사다리·브래킷·대기주문을 맞춘다 |
| 집행 상태 최신화 | `core/autotrader.py::refresh_status` | 시계·phase·보유·예산을 매 사이클 다시 쓴다(14:32 정지 결함) |
| 협업 버스 | `core/collab_bus.py` | 10인의 발신/수신 메시지·스레드(`data/agent_collab_bus.json`) |
| 검증 실험실 | `core/desk_lab.py` | 2년 일봉 캐시로 In/Out-of-Sample 실측(왕복 0.24% 차감) |
| 빌드업 장부 | `core/buildup.py` | 가설·측정·게이트·적용 이력 + 하드 락(`data/strategy_buildup.json`) |

```bash
python3 scripts/cloud_cycle.py status        # 클라우드 가동 상태
python3 scripts/run_collab_cycle.py          # 협업 한 사이클(가설→검증→승인/보류)
python3 -m core.buildup                      # 누적 가설·적용·승인대기 요약
python3 scripts/autotrader.py --refresh-status    # 상태 파일(autotrader_status.json) 최신화
```

**자동 개선의 범위**: OOS 게이트(표본 20건·OOS 8건·ΔEV +0.10%p·PF 1.2·MDD 악화 2%p 이내)를
통과한 **진입 필터 조이기**만 자동 적용합니다. 손절·익절 폭 같은 브래킷 규격은 측정·기록만 하고
**사용자 승인 대기(RECOMMENDED)** 로 남깁니다. RVOL 필수·손절 상한 15%·3세션·현금 $150·예산 $220·
일일 손실 -$25·NVDA 잠금·물타기 금지·프리마켓 금지는 하드 락으로 어떤 경로로도 완화되지 않습니다.

### 매매 로직 변경 요약
- 브래킷을 **계획가가 아니라 실제 체결가**에 고정하고, 목표 폭을 종목 ATR에 비례해 재산정.
- TP1 이후 **본전+수수료(0.34%) 래칫**, TP2 이후 러너는 **샹들리에(0.9ATR)와 EMA20 중 타이트한 쪽**으로 추적하며 최소 +1.5% 이익을 잠금.
- 진입: 돌파 추격 단독 → (돌파 + 과매도 반등) 결합, 레짐·센티먼트에 따라 비중·목표 가변.
- 숏 엔진: VIX 트리거를 18~24 구간으로 스윕해 Calmar 기준 최적값 선택(결과는 `data/1y_backtest_report.json`).
- 검증 결과 장중 회전은 마찰 후 기대값이 음수였고, 과매도 매수·2~3세션 보유(DIP_SWING)가
  1년 PF 1.98 / MDD -6.63%, 2년 PF 2.04 / MDD -6.64%로 검증되었습니다(시뮬레이션).
  DIP_SWING은 **오버나이트 보유 승인 대기** 상태로, 승인 전에는 집행하지 않습니다.

---

## 5-c. 손실 재발방지 가드레일 · 신규 모델 (2026-11-20)

11-17~19 사고(SOXL/TQQQ -$8.43, NVDA -$24.10, 합 -$32.53)의 감사와 대책은
[`docs/incident_audit_20261120.md`](docs/incident_audit_20261120.md)에, 기존 전략 전반의 재검토는
[`docs/strategy_review_20261120.md`](docs/strategy_review_20261120.md)에 실측 근거와 함께 정리돼 있습니다.

| 가드레일 | 파일 | 동작 |
|---|---|---|
| 시세·VIX 정합성 (fail-closed) | `core/live_integrity.py` | VIX는 일봉·대체소스·**같은 세션 고가** 중 최댓값으로 게이팅(11-19: 27.14 → 28.99). stale/불일치면 VIX 99로 신규 진입 차단. 청산 가격은 소스 중 최저값 |
| VIX ≥ 28 신규 롱 하드 차단 | `core/autotrader.py` | 보수적 VIX로 `vix_riskoff_scale` 적용 — 27.x에서 0.5배 진입하던 구멍을 닫음 |
| 포지션 손실 하드캡 | `core/risk_guard.py` | 자동 포지션 **-8% 또는 -$25** 중 먼저 닿는 쪽에서 전량 청산. 사용자 잠금 레벨은 5% 경고·15% 재난 캡만 |
| 이벤트 리스크 필터 | `core/risk_guard.py` | 실적 전 24h·후 6h 신규 진입 금지(보유는 보고) |
| 청산 규칙 단일화 | `core/exit_authority.py` | 브래킷마다 소유자 1명 — 장중·헤지·파일럿 규격과 사용자 잠금은 평단 사다리가 덮어쓰지 않음 |
| 강제청산 페일세이프 | `core/exit_failsafe.py`, `scripts/exit_watchdog.py` | 05:30 선행 발동, 놓친 건은 다음 정규장 첫 사이클에 즉시 청산, 사이클 45분 공백 시 워치독이 `gh workflow run` |
| 손절 체결 우선 | `core/autotrader.py` | 손절 지정가 -30bp → **-120bp** |

```bash
python3 scripts/exit_watchdog.py --record     # 연체 강제청산·클라우드 공백 상태
python3 scripts/exit_watchdog.py --check      # 조치 필요 여부(스케줄 체크용)
python3 scripts/run_regime_ab.py              # 신규 모델 A/B(IS/OOS·비용민감도)
python3 scripts/run_regime_ab.py --register   # 빌드업 가설·10인 버스 반영
```

**신규 모델** `REGIME_ADAPTIVE_DIP_V1`(`core/regime_model.py`): 다중 확인(추세 또는 VIX<20, 추세 없는
RISK_OFF 배제) + ATR 손절 8% 하드캡 + 리스크 1.5%/건. 5년 일봉 IS/OOS 실측에서 기존 대비
기대값 +1.21→+2.12%/건, PF 1.28→1.66, MDD -19.53→-12.84%, Sharpe 0.46→0.72, OOS 기대값 +3.43→+4.48%
([`docs/regime_model_20261120.md`](docs/regime_model_20261120.md)). 실집행 전환은 **사용자 승인 대기**이며
지금은 `autotrader_status.json`의 `shadow_model`(그림자 판정)로만 쓰입니다.

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

## 7. 투자 대가 13F 스마트머니 (2026-11-17 확장)

분기 공시(13F-HR)를 **영상·기사 요약이 아니라 SEC EDGAR 정보 테이블 XML 원문**으로 파싱해 구조화한다.

```bash
python3 scripts/build_superinvestor_13f.py --refresh   # EDGAR 6개 기관 Q3/Q2 원문 → data/superinvestor_13f.json
python3 scripts/run_13f_ab_backtest.py --refresh       # 개선안 A/B(2년 일봉 + 1년 60분봉, 왕복 0.24%) → data/13f_ab_backtest.json
```

- 대상: 버크셔 해서웨이(0001067983) · 듀케인 패밀리오피스(0001536411) · 퍼싱 스퀘어(0001336528, 13F-NT만 제출) ·
  틸 매크로(0001562087) · 시추에이셔널 어웨어니스(0002045724) · 국민연금(0001608046)
- 산출: 기관별 신규/전량매도/증액/감축, **주식수 변화 × Q3 가격** 순현금흐름(가격효과 제거),
  섹터별 대가 순매수/매도 스코어(**기관 동일가중** — NPS $159B가 액티브 기관을 덮지 않게),
  현금화 압력지수(0~100, 흐름축 50% + 매도폭 50%), 영상 주장 대조표(`video_claims_check`)
- 소비: `Agent 5`가 `superinvestor_13f` 블록으로 리포트·피드에 표시하고, `Agent 9`는 참고 블록만 갖는다.
- **사이징에는 자동 적용하지 않는다**: 13F는 분기 1회·롱 온리·현금 미공개라 타이밍 검정력이 없다.
  A/B에서 고정 축소는 PF 불변(정보가치 0), VIX 20~24 구간 0.75배만 PF/MDD를 개선해 그쪽을 채택했다
  (`config/swing_approval.json` → `smart_money_13f_review`).
- 13F 매집 종목(SOXX·AMZN·GOOGL·STX 등)의 DIP_SWING 신호는 `data/13f_candidates.json`에 **제안 전용**으로 남는다
  (신규 종목 편입은 사용자 승인 항목).
