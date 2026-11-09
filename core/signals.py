"""
Alpha Generation & Volatility Targeting Engine
Implements Gary Antonacci Dual Momentum & AQR Time-Series Volatility Targeting.
"""

import numpy as np
import pandas as pd


class MomentumEngine:
    def __init__(
        self,
        lookbacks: list[int] = [63, 126, 252],
        weights: list[float] = [0.2, 0.3, 0.5],
        skip_recent: int = 5,
    ):
        self.lookbacks = lookbacks
        self.weights = weights
        self.skip_recent = skip_recent

    def compute_weighted_momentum(self, price_df: pd.DataFrame) -> pd.Series:
        """
        Computes weighted multi-horizon momentum skipping the last 5 days
        to remove short-term microstructure reversal noise.
        """
        scores = pd.Series(0.0, index=price_df.columns)
        effective_df = price_df.iloc[:-self.skip_recent] if len(price_df) > self.skip_recent else price_df

        for lb, w in zip(self.lookbacks, self.weights):
            if len(effective_df) > lb:
                ret = (effective_df.iloc[-1] / effective_df.iloc[-lb]) - 1.0
                scores += ret * w

        return scores

    def filter_positive_momentum(self, momentum_scores: pd.Series, cash_proxy_score: float = 0.0) -> list[str]:
        """
        Absolute Momentum Filter: Asset must outperform cash proxy (T-Bills/SGOV).
        """
        return momentum_scores[momentum_scores > cash_proxy_score].index.tolist()


class VolatilityTargeting:
    def __init__(self, target_annual_vol: float = 0.10, trading_days: int = 252):
        self.target_vol = target_annual_vol
        self.trading_days = trading_days

    def compute_portfolio_scalar(self, returns_df: pd.DataFrame, weights: pd.Series, window: int = 21) -> float:
        """
        Computes the volatility scaling factor:
        Scalar = min(1.0, target_vol / realized_portfolio_vol)
        """
        recent_returns = returns_df.iloc[-window:]
        cov_matrix = recent_returns.cov() * self.trading_days

        w_vec = weights.reindex(cov_matrix.columns).fillna(0.0).values
        realized_variance = float(w_vec @ cov_matrix.values @ w_vec)
        realized_vol = np.sqrt(max(1e-8, realized_variance))

        if realized_vol <= 0.001:
            return 1.0

        scalar = min(1.0, self.target_vol / realized_vol)
        return float(scalar)
