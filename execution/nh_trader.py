"""
NH Investment & Securities (NHPLUG / Namuh) Broker Execution Interface
Translates target portfolio weights into production rebalancing orders for US Stocks (Global Stock API).
Supports OAuth2 24h token caching, live/mock environments, portfolio inquiry, and pre-execution dry-run sanity checks.
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _load_env_fallback():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k and k not in os.environ:
                        os.environ[k] = v


class NHBroker:
    LIVE_REST_URL = "https://api.nhplug.com:8443"
    MOCK_REST_URL = "https://moapi.nhplug.com:8443"
    TOKEN_URL = "https://api.nhplug.com:8443/oauth2/token"

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        act_no: Optional[str] = None,
        is_mock: bool = False,
        dry_run: bool = True,
        token_cache_file: str = ".nh_token_cache.json",
    ):
        _load_env_fallback()
        self.app_key = app_key or os.getenv("NH_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("NH_APP_SECRET", "")
        self.act_no = act_no or os.getenv("NH_ACT_NO", "")
        env_is_mock = os.getenv("NH_IS_MOCK", "").lower() in ("true", "1", "yes")
        self.is_mock = is_mock or env_is_mock
        self.dry_run = dry_run
        self.base_url = self.MOCK_REST_URL if self.is_mock else self.LIVE_REST_URL
        if token_cache_file == ".nh_token_cache.json":
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.token_cache_file = os.path.join(project_root, ".nh_token_cache.json")
        else:
            self.token_cache_file = token_cache_file
        self.access_token: Optional[str] = None

        if self.app_key and self.app_secret:
            self._ensure_access_token()

    def _ensure_access_token(self) -> str:
        """Load cached token or issue a new 24h OAuth token."""
        if os.path.exists(self.token_cache_file):
            try:
                with open(self.token_cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                    if time.time() < cache.get("expires_at", 0) - 300:
                        self.access_token = cache.get("access_token")
                        return self.access_token
            except Exception:
                pass

        # Issue new token via POST /oauth2/token
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecretkey": self.app_secret,
            "scope": "oob",
        }).encode("utf-8")

        req = urllib.request.Request(self.TOKEN_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                self.access_token = body.get("access_token")
                expires_in = body.get("expires_in", 86400)
                cache_data = {
                    "access_token": self.access_token,
                    "expires_at": time.time() + expires_in,
                    "issued_at": datetime.now().isoformat(),
                }
                with open(self.token_cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, indent=2)
                return self.access_token
        except Exception as e:
            print(f"[NH Broker] Token issuance failed: {e}")
            raise

    def _call_api(self, path: str, input_0: dict) -> dict:
        """Execute a POST REST API call with Bearer authentication."""
        if not self.access_token:
            self._ensure_access_token()

        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }
        body = json.dumps({"Input_0": input_0}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                return res_json
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"[NH API Error] {e.code} on {path}: {err_body}")
            raise

    def get_accounts(self) -> list[dict]:
        """Inquire list of accounts belonging to this AppKey."""
        res = self._call_api("/n2/acctinfo", {})
        accounts = res.get("Output_0", [])
        return accounts

    def get_portfolio_state(self) -> dict:
        """
        Fetch US stock balance and USD cash from NH Global Stock balance API.
        Endpoint: POST /gbstock/inquiry/v1/balance
        """
        if self.dry_run and not (self.app_key and self.app_secret and self.act_no):
            # Simulated portfolio for dry-run
            return {
                "equity": 100000.0,
                "cash": 100000.0,
                "weights": {},
                "positions": {},
            }

        input_data = {
            "act_no": self.act_no,
            "fc_mkt_dit_cd": "200",  # 200: USA
        }
        res = self._call_api("/gbstock/inquiry/v1/balance", input_data)
        out_0 = res.get("Output_0", {}) or {}
        out_1 = res.get("Output_1", []) or []

        # Parse Equity & Cash (USD)
        total_eval_usd = float(out_0.get("fc_eal_amt", 0.0) or 0.0)
        cash_usd = float(out_0.get("fc_dca", 0.0) or 0.0)
        total_equity = float(out_0.get("fc_aet_amt", 0.0) or (total_eval_usd + cash_usd))

        # Also query KRW balance from domestic stock balance API
        krw_cash = 0
        krw_total_aet = 0
        try:
            res_kr = self._call_api("/krstock/inquiry/v1/balance", {
                "act_no": self.act_no,
                "bnc_bse_cd": "1",
                "ltg_aot_dit_cd": "9",
                "aet_bse": "1",
                "qut_dit_cd": "UNT"
            })
            out_kr = res_kr.get("Output_0", {}) or {}
            dca_val = int(out_kr.get("dca", 0) or 0)
            krw_total_aet = int(out_kr.get("tot_aet_amt", 0) or dca_val)
            # Use total asset value if cash is swept into CMA/RP overnight
            krw_cash = max(dca_val, krw_total_aet)
        except Exception as e:
            pass

        usd_from_krw = round(krw_cash / 1400.0, 2)
        if total_equity <= 0.0:
            if krw_cash > 0:
                total_equity = usd_from_krw
                cash_usd = usd_from_krw
            elif self.dry_run:
                print(f"[NH Broker] Account {self.act_no} has $0.00 USD balance. Simulating with $10,000.00 for Dry-Run rebalance preview.")
                total_equity = 10000.0
                cash_usd = 10000.0
            else:
                total_equity = 1.0  # Prevent division by zero

        weights = {}
        positions = {}
        for item in out_1:
            sym = item.get("iem_cd", "").strip()
            qty = int(item.get("cns_bse_bnc_qty", 0) or 0)
            eval_amt = float(item.get("fc_eal_amt", 0.0) or 0.0)
            price = float(item.get("fc_sec_end_pr", 0.0) or 0.0)

            if sym and qty > 0:
                weights[sym] = eval_amt / total_equity
                positions[sym] = {
                    "qty": qty,
                    "price": price,
                    "market_value": eval_amt,
                }

        return {
            "equity": total_equity,
            "cash": cash_usd,
            "cash_usd": cash_usd,
            "cash_krw": krw_cash,
            "krw_total_aet": krw_total_aet,
            "weights": weights,
            "positions": positions,
        }

    def execute_rebalance(
        self,
        target_weights: dict[str, float],
        market_prices: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        """
        Translates target weights into integer share buy/sell orders.
        Executes orders via POST /gbstock/order/v1/sell and POST /gbstock/order/v1/buy.
        """
        state = self.get_portfolio_state()
        equity = state["equity"]
        current_weights = state["weights"]
        positions = state.get("positions", {})
        orders_to_execute = []

        all_symbols = set(target_weights.keys()).union(set(current_weights.keys()))

        print("\n" + "=" * 55)
        print("🏛️ [NH투자증권] Generating Rebalance Orders")
        print(f"Account: {self.act_no or 'SIMULATED'} | Total Equity: ${equity:,.2f}")
        print("=" * 55)

        # 1. Execute SELL orders first to free up purchasing power
        sell_orders = []
        buy_orders = []

        for sym in sorted(all_symbols):
            if sym == "SGOV":
                continue  # SGOV acts as cash parking or cash equivalent

            target_w = target_weights.get(sym, 0.0)
            curr_w = current_weights.get(sym, 0.0)
            delta_w = target_w - curr_w
            target_notional = delta_w * equity

            curr_price = (
                market_prices.get(sym)
                if market_prices and sym in market_prices
                else positions.get(sym, {}).get("price", 100.0)
            )
            if curr_price <= 0:
                curr_price = 100.0

            if abs(target_notional) > 50.0:  # Minimum trade threshold ($50)
                shares = int(abs(target_notional) / curr_price)
                if shares > 0:
                    order_info = {
                        "symbol": sym,
                        "side": "sell" if delta_w < 0 else "buy",
                        "shares": shares,
                        "price": round(curr_price, 2),
                        "estimated_notional": round(shares * curr_price, 2),
                        "current_weight": round(curr_w, 4),
                        "target_weight": round(target_w, 4),
                    }
                    if order_info["side"] == "sell":
                        sell_orders.append(order_info)
                    else:
                        buy_orders.append(order_info)

        ordered_queue = sell_orders + buy_orders

        for order in ordered_queue:
            orders_to_execute.append(order)
            sym = order["symbol"]
            side = order["side"]
            shares = order["shares"]
            price = order["price"]

            if self.dry_run:
                print(
                    f"[DRY-RUN] {side.upper():<4} {shares:>4} shares of {sym:<5} "
                    f"@ ${price:<7.2f} (~${order['estimated_notional']:,.2f}) "
                    f"[{order['current_weight']:.1%} -> {order['target_weight']:.1%}]"
                )
            else:
                endpoint = "/gbstock/order/v1/sell" if side == "sell" else "/gbstock/order/v1/buy"
                payload = {
                    "act_no": self.act_no,
                    "fc_sec_trd_nat_cd": "200",  # USA
                    "iem_cd": sym,
                    "orr_qty": shares,
                    "fc_orr_uit_pr": price,
                    "ahi_nmn_pr_tp_cd": "00",  # 00: 지정가
                }
                if side == "buy":
                    payload["wtm_cur_knd_cd"] = "1"  # 1: USD

                try:
                    res = self._call_api(endpoint, payload)
                    rsp_msg = res.get("rsp_msg", "OK")
                    orr_no = res.get("Output_0", {}).get("orr_no", "N/A")
                    print(f"[LIVE] {side.upper()} order submitted for {shares}x {sym} (Order No: {orr_no}, {rsp_msg})")
                except Exception as e:
                    print(f"[ERROR] Failed to execute {side} for {sym}: {e}")

        return orders_to_execute
