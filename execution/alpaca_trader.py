"""
Alpaca Broker Execution Interface
Translates target portfolio weights into production rebalancing orders.
Supports fractional shares, paper trading, and pre-execution sanity checks.
"""

import os
from typing import Optional


class AlpacaBroker:
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: str = "https://paper-api.alpaca.markets",
        dry_run: bool = True,
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = base_url
        self.dry_run = dry_run
        self.client = None

        if self.api_key and self.secret_key:
            try:
                import alpaca_trade_api as tradeapi
                self.client = tradeapi.REST(
                    self.api_key, self.secret_key, self.base_url, api_version="v2"
                )
            except ImportError:
                print("alpaca_trade_api not installed. Running in simulation mode.")

    def get_portfolio_state(self) -> dict:
        if self.client and not self.dry_run:
            account = self.client.get_account()
            positions = self.client.list_positions()
            portfolio_value = float(account.portfolio_value)
            pos_dict = {p.symbol: float(p.market_value) / portfolio_value for p in positions}
            return {
                "equity": portfolio_value,
                "cash": float(account.cash),
                "weights": pos_dict
            }
        else:
            # Simulated portfolio for dry run
            return {
                "equity": 100000.0,
                "cash": 100000.0,
                "weights": {}
            }

    def execute_rebalance(self, target_weights: dict[str, float]) -> list[dict]:
        state = self.get_portfolio_state()
        equity = state["equity"]
        current_weights = state["weights"]
        orders_to_execute = []

        all_symbols = set(target_weights.keys()).union(set(current_weights.keys()))

        print("\n--- Generating Rebalance Orders ---")
        for sym in sorted(all_symbols):
            target_w = target_weights.get(sym, 0.0)
            curr_w = current_weights.get(sym, 0.0)
            delta_w = target_w - curr_w
            target_notional = delta_w * equity

            if abs(target_notional) > 50.0:  # Minimum trade threshold ($50)
                side = "buy" if target_notional > 0 else "sell"
                order = {
                    "symbol": sym,
                    "side": side,
                    "notional": round(abs(target_notional), 2),
                    "current_weight": round(curr_w, 4),
                    "target_weight": round(target_w, 4),
                }
                orders_to_execute.append(order)

                if self.dry_run:
                    print(f"[DRY-RUN] {side.upper()} ${order['notional']} of {sym} (Weight: {curr_w:.2%} -> {target_w:.2%})")
                elif self.client:
                    try:
                        self.client.submit_order(
                            symbol=sym,
                            notional=order["notional"],
                            side=side,
                            type="market",
                            time_in_force="day",
                        )
                        print(f"[LIVE] Submitted {side} order for ${order['notional']} of {sym}")
                    except Exception as e:
                        print(f"[ERROR] Failed to execute order for {sym}: {e}")

        return orders_to_execute
