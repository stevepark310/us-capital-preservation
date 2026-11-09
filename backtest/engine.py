"""
Vectorized Backtest Engine with Full Realistic Constraints
Evaluates CAGR, Sharpe, Sortino, Calmar, and Left-Tail Drawdowns.
"""

import numpy as np
import pandas as pd


class BacktestEngine:
    def __init__(
        self,
        prices: pd.DataFrame,
        weights: pd.DataFrame,
        rebalance_cost_bps: float = 5.0,     # 0.05% slippage + trading costs
        risk_free_rate: float = 0.03,         # 3% annual cash rate
    ):
        self.prices = prices
        self.weights = weights
        self.cost_rate = rebalance_cost_bps / 10000.0
        self.rf = risk_free_rate

    def run(self) -> tuple[pd.DataFrame, dict]:
        aligned_weights = self.weights.reindex(self.prices.index).ffill().fillna(0.0)
        daily_returns = self.prices.pct_change().fillna(0.0)

        # Portfolio return before friction
        gross_returns = (aligned_weights.shift(1) * daily_returns).sum(axis=1)

        # Rebalancing turnover and transaction costs
        weight_changes = aligned_weights.diff().abs().sum(axis=1)
        friction = weight_changes * self.cost_rate
        net_returns = gross_returns - friction

        # Equity curve
        equity = (1.0 + net_returns).cumprod()
        peak = equity.cummax()
        drawdown = (equity - peak) / peak

        results_df = pd.DataFrame({
            "portfolio_return": net_returns,
            "equity_curve": equity,
            "peak": peak,
            "drawdown": drawdown,
        })

        # Calculate performance metrics
        trading_days = 252
        n_days = len(net_returns)
        total_return = equity.iloc[-1] - 1.0
        cagr = (equity.iloc[-1] ** (trading_days / max(1, n_days))) - 1.0
        ann_vol = net_returns.std() * np.sqrt(trading_days)

        excess_returns = net_returns - (self.rf / trading_days)
        sharpe = (excess_returns.mean() / max(1e-8, net_returns.std())) * np.sqrt(trading_days)

        downside_returns = net_returns[net_returns < 0.0]
        downside_vol = downside_returns.std() * np.sqrt(trading_days)
        sortino = (excess_returns.mean() / max(1e-8, downside_vol)) * np.sqrt(trading_days)

        max_dd = drawdown.min()
        calmar = cagr / max(1e-8, abs(max_dd))

        metrics = {
            "Total Return (%)": round(total_return * 100, 2),
            "CAGR (%)": round(cagr * 100, 2),
            "Annualized Volatility (%)": round(ann_vol * 100, 2),
            "Sharpe Ratio": round(sharpe, 2),
            "Sortino Ratio": round(sortino, 2),
            "Calmar Ratio": round(calmar, 2),
            "Max Drawdown (%)": round(max_dd * 100, 2),
            "Win Rate (%)": round((net_returns > 0).mean() * 100, 2),
        }

        return results_df, metrics
