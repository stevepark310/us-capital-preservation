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

## 2. System Architecture

```
us-capital-preservation/
├── config/
│   └── settings.yaml          # Risk limits, universe, volatility targets
├── core/
│   ├── hrp.py                 # Hierarchical Risk Parity optimizer
│   ├── regime.py              # S&P 200 DMA + VIX Regime classification
│   ├── signals.py             # Dual Momentum & Volatility Targeting
│   └── risk.py                # Max Drawdown & CVaR Circuit Breakers
├── data/
│   └── fetcher.py             # Market data ingestion (Yahoo Finance / FRED)
├── backtest/
│   └── engine.py              # Vectorized backtesting & performance metrics
├── execution/
│   └── alpaca_trader.py       # Alpaca Paper & Live API execution
├── docs/
│   └── index.html             # Standalone Web Dashboard (GitHub Pages ready)
├── .github/workflows/
│   └── rebalance.yml          # Daily Automated GitHub Actions Workflow
├── main.py                    # Pipeline Entrypoint
└── requirements.txt
```

---

## 3. Web Dashboard (Free Cloud Hosting)

This project includes a fully responsive **HTML5/Tailwind/Chart.js Web Dashboard** under `docs/index.html`.

### How to host for FREE on GitHub Pages:
1. Push this repository to your GitHub account: `stevepark310/us-capital-preservation`.
2. Go to **Settings > Pages**.
3. Under **Build and deployment > Branch**, select `main` and folder `/docs`.
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
