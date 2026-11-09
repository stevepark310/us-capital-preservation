"""
Downside Protection & Tail-Risk Circuit Breaker
Enforces the core motto: 'Never Lose Capital'.
"""

import numpy as np
import pandas as pd


class RiskManager:
    def __init__(
        self,
        mdd_soft_limit: float = 0.08,    # 8% drawdown: reduce risky weight by 50%
        mdd_hard_stop: float = 0.12,     # 12% drawdown: liquidate all risk to cash
        cash_symbol: str = "SGOV",
    ):
        self.mdd_soft = mdd_soft_limit
        self.mdd_hard = mdd_hard_stop
        self.cash_symbol = cash_symbol

    def evaluate_circuit_breaker(
        self,
        equity_curve: pd.Series,
        proposed_weights: pd.Series,
    ) -> tuple[pd.Series, dict]:
        """
        Calculates peak-to-trough drawdown. If breaker is hit, overrides weights.
        """
        peak = equity_curve.cummax().iloc[-1]
        current = equity_curve.iloc[-1]
        current_dd = (peak - current) / peak

        status = {
            "current_drawdown": float(current_dd),
            "peak_equity": float(peak),
            "current_equity": float(current),
            "circuit_breaker_active": False,
            "action": "NORMAL"
        }

        adjusted_weights = proposed_weights.copy()

        if current_dd >= self.mdd_hard:
            status["circuit_breaker_active"] = True
            status["action"] = "HARD_STOP_100_PERCENT_CASH"
            adjusted_weights = pd.Series(0.0, index=proposed_weights.index)
            adjusted_weights[self.cash_symbol] = 1.0

        elif current_dd >= self.mdd_soft:
            status["circuit_breaker_active"] = True
            status["action"] = "SOFT_LIMIT_50_PERCENT_DELEVERAGE"
            # Halve all risky assets, park the other 50% in cash
            adjusted_weights = adjusted_weights * 0.5
            adjusted_weights[self.cash_symbol] = adjusted_weights.get(self.cash_symbol, 0.0) + 0.5

        return adjusted_weights, status

    @staticmethod
    def compute_cvar(returns: pd.Series, alpha: float = 0.05) -> float:
        """
        Computes 95% Conditional Value at Risk (Expected Shortfall).
        """
        if len(returns) < 20:
            return 0.0
        var_threshold = np.percentile(returns, alpha * 100)
        cvar = returns[returns <= var_threshold].mean()
        return float(abs(cvar))
