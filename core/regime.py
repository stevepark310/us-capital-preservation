"""
Macro & Volatility Regime Detection Engine
Detects structural market phases to dynamically trigger capital preservation protocols.
"""

from enum import Enum
import numpy as np
import pandas as pd


class MarketRegime(Enum):
    BULL_CALM = "BULL_CALM"          # Strong trend, low volatility: Maximum risk-asset exposure
    BULL_VOLATILE = "BULL_VOLATILE"  # Strong trend, high volatility: Vol-scaled down, hedges on
    BEAR_CALM = "BEAR_CALM"          # Downtrend, low volatility: Rotate to Treasuries / Gold
    BEAR_VOLATILE = "BEAR_VOLATILE"  # Crash / Liquidity panic: 100% Cash / SGOV preservation


class RegimeDetector:
    def __init__(
        self,
        vix_calm_threshold: float = 20.0,
        vix_panic_threshold: float = 30.0,
        trend_lookback_dma: int = 200,
    ):
        self.vix_calm = vix_calm_threshold
        self.vix_panic = vix_panic_threshold
        self.trend_lookback = trend_lookback_dma

    def detect(self, spy_prices: pd.Series, vix_prices: pd.Series) -> tuple[MarketRegime, dict]:
        current_spy = spy_prices.iloc[-1]
        spy_dma200 = spy_prices.rolling(window=self.trend_lookback).mean().iloc[-1]
        is_above_trend = current_spy >= spy_dma200

        current_vix = vix_prices.iloc[-1] if not vix_prices.empty else 18.0
        vix_ma20 = vix_prices.rolling(window=20).mean().iloc[-1] if len(vix_prices) >= 20 else current_vix

        # Diagnostics metrics
        diagnostics = {
            "spy_price": float(current_spy),
            "spy_200dma": float(spy_dma200),
            "trend_status": "BULLISH" if is_above_trend else "BEARISH",
            "vix_current": float(current_vix),
            "vix_20dma": float(vix_ma20),
            "volatility_state": "CALM" if current_vix < self.vix_calm else ("PANIC" if current_vix > self.vix_panic else "ELEVATED")
        }

        # Determine regime
        if current_vix >= self.vix_panic:
            regime = MarketRegime.BEAR_VOLATILE
        elif is_above_trend:
            if current_vix < self.vix_calm:
                regime = MarketRegime.BULL_CALM
            else:
                regime = MarketRegime.BULL_VOLATILE
        else:
            if current_vix < self.vix_calm:
                regime = MarketRegime.BEAR_CALM
            else:
                regime = MarketRegime.BEAR_VOLATILE

        diagnostics["regime"] = regime.value
        return regime, diagnostics
