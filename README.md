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
