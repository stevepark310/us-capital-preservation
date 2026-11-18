"""
Market Data Ingestion & Alignment Engine
Fetches adjusted close prices and volume for equities, safe havens, and volatility indices.
"""

from datetime import datetime, timedelta
import os
import pandas as pd
import yfinance as yf


class MarketDataFetcher:
    def __init__(self, cache_dir: str = "data_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def fetch_history(
        self,
        symbols: list[str],
        start_date: str = None,
        end_date: str = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        if start_date is None:
            # Default to 5 years of daily data
            start_date = (datetime.now() - timedelta(days=5 * 365)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")

        unique_symbols = sorted(list(set(symbols)))
        cache_file = os.path.join(self.cache_dir, f"prices_{start_date}_{end_date}.parquet")

        if use_cache and os.path.exists(cache_file):
            df = pd.read_parquet(cache_file)
            missing = [s for s in unique_symbols if s not in df.columns]
            if not missing:
                return df[unique_symbols]

        print(f"Fetching market data for {len(unique_symbols)} assets from {start_date} to {end_date}...")
        data = yf.download(unique_symbols, start=start_date, end=end_date, progress=False)

        if "Adj Close" in data:
            price_df = data["Adj Close"]
        elif "Close" in data:
            price_df = data["Close"]
        else:
            raise ValueError("No Close or Adj Close found in downloaded data.")

        # Forward fill and drop leading NaNs
        price_df = price_df.ffill().dropna()

        try:
            price_df.to_parquet(cache_file)
        except Exception:
            pass

        return price_df
