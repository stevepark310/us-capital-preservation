"""
Main Orchestrator for US Capital Preservation Quant System
"""

import argparse
import os
import sys
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from core.regime import RegimeDetector, MarketRegime
from core.hrp import HierarchicalRiskParity
from core.signals import MomentumEngine, VolatilityTargeting
from core.risk import RiskManager
from data.fetcher import MarketDataFetcher
from backtest.engine import BacktestEngine
from execution.alpaca_trader import AlpacaBroker
from execution.nh_trader import NHBroker


def load_config(config_path: str = "config/settings.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_pipeline(mode: str = "backtest", broker_override: str = None):
    cfg = load_config()
    print("=" * 60)
    print("🛡️ US Capital Preservation Quant Trading Engine")
    print(f"Mode: {mode.upper()}")
    print("=" * 60)

    # 1. Prepare Universe
    eq = cfg["universe"]["equities"]
    bonds = cfg["universe"]["safe_havens"]
    alts = cfg["universe"]["alternatives"]
    full_universe = list(set(eq + bonds + alts + ["SPY", "^VIX"]))

    # 2. Fetch Market Data
    fetcher = MarketDataFetcher()
    prices = fetcher.fetch_history(full_universe)

    # 3. Detect Current Market Regime
    regime_detector = RegimeDetector(
        vix_calm_threshold=cfg["regime"]["vix_threshold_calm"],
        vix_panic_threshold=cfg["regime"]["vix_threshold_panic"],
    )
    current_regime, diag = regime_detector.detect(prices["SPY"], prices["^VIX"])
    print(f"\n[Regime Status] Current Regime: {current_regime.value}")
    print(f" - S&P 500: ${diag['spy_price']:.2f} (200 DMA: ${diag['spy_200dma']:.2f}, {diag['trend_status']})")
    print(f" - VIX: {diag['vix_current']:.2f} ({diag['volatility_state']})")

    # 4. Filter Assets and Optimize with HRP
    tradeable_assets = [s for s in (eq + bonds + alts) if s in prices.columns]
    returns = prices[tradeable_assets].pct_change().dropna()
    cov_matrix = returns.iloc[-63:].cov() * 252

    hrp = HierarchicalRiskParity(cov_matrix)
    hrp_weights = hrp.allocate()

    # 5. Apply Regime Adjustment & Vol Targeting
    vol_target = VolatilityTargeting(cfg["risk_management"]["target_annual_volatility"])
    vol_scalar = vol_target.compute_portfolio_scalar(returns, hrp_weights)

    if current_regime == MarketRegime.BEAR_VOLATILE:
        final_weights = {s: 0.0 for s in tradeable_assets}
        final_weights["SGOV"] = 1.0
        print("\n[CRISIS PROTOCOL ACTIVE] 100% Capital allocated to SGOV / Cash.")
    else:
        scaled_weights = hrp_weights * vol_scalar
        cash_allocation = 1.0 - scaled_weights.sum()
        final_weights = scaled_weights.to_dict()
        final_weights["SGOV"] = final_weights.get("SGOV", 0.0) + max(0.0, cash_allocation)

    print("\n[Target Portfolio Allocation]")
    for asset, w in sorted(final_weights.items(), key=lambda x: x[1], reverse=True):
        if w > 0.001:
            print(f"  {asset:<6}: {w:>6.2%}")

    # 6. Execution or Backtest
    if mode == "rebalance":
        broker_type = broker_override or cfg.get("execution", {}).get("broker", "nh")
        dry_run = cfg["execution"]["dry_run"]
        if broker_type == "nh":
            broker = NHBroker(dry_run=dry_run, is_mock=cfg.get("execution", {}).get("is_mock", False))
            latest_prices = {s: float(prices[s].iloc[-1]) for s in tradeable_assets if s in prices.columns}
            broker.execute_rebalance(final_weights, market_prices=latest_prices)
        else:
            broker = AlpacaBroker(dry_run=dry_run)
            broker.execute_rebalance(final_weights)
    elif mode == "backtest":
        print("\n[Backtest Evaluation (5-Year Historical Performance)]")
        # Generate periodic rolling weights
        weights_df = returns.copy()
        for col in weights_df.columns:
            weights_df[col] = final_weights.get(col, 0.0)
        engine = BacktestEngine(prices[tradeable_assets], weights_df)
        _, metrics = engine.run()
        for k, v in metrics.items():
            print(f"  {k:<28}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["backtest", "rebalance"], default="backtest")
    parser.add_argument("--broker", choices=["nh", "alpaca"], default=None, help="Broker execution provider (default: settings.yaml)")
    args = parser.parse_args()
    run_pipeline(args.mode, broker_override=args.broker)
