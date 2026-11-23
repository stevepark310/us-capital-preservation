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


def _num(value, default=0.0) -> float:
    """NH API returns numbers as str/int/float/'' — coerce safely."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _int(value, default=0) -> int:
    return int(_num(value, default))


# --- NH 해외주식 호가유형 코드(ahi_nmn_pr_tp_cd) -----------------------------
# 00: 정규장 지정가(기본값, 기존 동작) · 61: 프리마켓 지정가 · 62: 애프터마켓 지정가
ORDER_TYPE_CODES = {
    "00": ("regular_limit", "정규장 지정가"),
    "61": ("premarket_limit", "프리마켓 지정가"),
    "62": ("aftermarket_limit", "애프터마켓 지정가"),
    "63": ("daytime_limit", "주간거래 지정가"),
}
ORDER_TYPE_ALIASES = {
    "00": "00", "regular": "00", "regularlimit": "00", "normal": "00", "limit": "00",
    "정규": "00", "정규장": "00", "정규장지정가": "00",
    "61": "61", "premarket": "61", "premarketlimit": "61", "pre": "61",
    "프리": "61", "프리장": "61", "프리마켓": "61",
    "62": "62", "aftermarket": "62", "aftermarketlimit": "62", "after": "62",
    "afterhours": "62", "애프터": "62", "애프터마켓": "62", "시간외": "62",
    "63": "63", "daytime": "63", "daytimelimit": "63", "day": "63", "daymarket": "63",
    "주간": "63", "주간거래": "63", "주간거래지정가": "63",
}


def normalize_order_type(order_type=None, ahi_nmn_pr_tp_cd=None) -> str:
    """호가유형 입력(코드/이름)을 NH 코드('00'|'61'|'62')로 정규화한다.

    `ahi_nmn_pr_tp_cd`가 주어지면 그쪽을 우선한다(원본 API 필드명 호환).
    모르는 값은 조용히 기본값으로 떨어뜨리지 않고 ValueError로 막는다 —
    실주문에서 조용한 폴백은 의도하지 않은 정규장 주문을 만들 수 있다.
    """
    raw = ahi_nmn_pr_tp_cd if ahi_nmn_pr_tp_cd not in (None, "") else order_type
    if raw in (None, ""):
        return "00"
    key = str(raw).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    code = ORDER_TYPE_ALIASES.get(key)
    if code is None:
        raise ValueError(
            f"Unsupported order type: {raw!r} (use '00' 정규장, '61' 프리마켓, '62' 애프터마켓, '63' 주간거래)")
    return code


def _redact(obj):
    """Mask account numbers and any credential-looking key before logging."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(t in lk for t in ("appkey", "app_key", "secret", "token", "password")):
                out[k] = "***"
            elif lk in ("act_no",) and isinstance(v, (str, int)):
                s = str(v)
                out[k] = ("*" * max(len(s) - 2, 0)) + s[-2:]
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


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
        # NH expects an 11-digit account number (acct_no from /n2/acctinfo)
        self.act_no_norm = (self.act_no or "").replace("-", "").strip()
        self.last_response: Optional[dict] = None

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
                self.last_response = res_json
                print(f"[NH] POST {path} -> rsp_cd={res_json.get('rsp_cd')} rsp_msg={res_json.get('rsp_msg')}")
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

    def get_balance(self, include_raw: bool = True) -> dict:
        """Alias used by the remote dispatch workflow; includes redacted raw payloads."""
        return self.get_portfolio_state(include_raw=include_raw)

    def get_recent_executions(self, days: int = 3) -> dict:
        """Order/execution history (해외주식 주문체결내역) for the last N days — used to
        confirm whether limit orders actually filled. Returns redacted raw responses."""
        now_kst = datetime.utcnow() + timedelta(hours=9)
        out = {}
        for back in range(days):
            d = (now_kst - timedelta(days=back)).strftime("%Y%m%d")
            try:
                res = self._call_api("/gbstock/inquiry/v1/unexecuted", {
                    "orr_dt": d,
                    "act_no": self._acct(),
                    "oss_sby_dit_cd": "0",  # 0: 전체
                    "sot_dit": "1",         # 1: 주문번호 역순
                    "ost_cns_dit": "0",     # 0: 전체 (1 체결 / 2 미체결)
                })
                out[d] = _redact(res)
            except Exception as e:
                out[d] = {"error": str(e)}
        return out

    def place_order(
        self,
        ticker: str,
        side: str,
        qty: int,
        price: float = 0.0,
        order_type: str = "00",
        ahi_nmn_pr_tp_cd: Optional[str] = None,
    ) -> dict:
        """
        Submit a single US stock limit order via NH Global Stock Order API.

        side: 'BUY' or 'SELL'
        order_type / ahi_nmn_pr_tp_cd:
          '00' 정규장 지정가(기본값, 기존 동작과 동일)
          '61' 프리마켓 지정가 (KST 18:00~23:30 / ET 04:00~09:30)
          '62' 애프터마켓 지정가 (KST 06:00~07:50 / ET 16:00~20:00)
        이름('premarket' 등)도 허용한다. 시장가 코드는 지원하지 않는다 — 세 유형 모두
        지정가이며 price <= 0은 거부된다.
        """
        side_norm = side.strip().upper()
        if side_norm not in ("BUY", "SELL"):
            raise ValueError(f"Unsupported side: {side}")
        if qty <= 0:
            raise ValueError(f"qty must be positive: {qty}")
        code = normalize_order_type(order_type, ahi_nmn_pr_tp_cd)
        if price <= 0:
            raise ValueError(f"price must be > 0 for limit order (ahi_nmn_pr_tp_cd='{code}')")
        type_name = ORDER_TYPE_CODES[code][0]

        if self.dry_run:
            return {
                "dry_run": True,
                "ticker": ticker.upper(),
                "side": side_norm,
                "qty": qty,
                "price": round(float(price), 2),
                "order_type": type_name,
                "ahi_nmn_pr_tp_cd": code,
            }

        endpoint = "/gbstock/order/v1/sell" if side_norm == "SELL" else "/gbstock/order/v1/buy"
        payload = {
            "act_no": self._acct(),
            "fc_sec_trd_nat_cd": "200",
            "iem_cd": ticker.strip().upper(),
            "orr_qty": int(qty),
            "fc_orr_uit_pr": round(float(price), 2),
            "ahi_nmn_pr_tp_cd": code,
        }
        if side_norm == "BUY":
            payload["wtm_cur_knd_cd"] = "1"
        return self._call_api(endpoint, payload)

    def get_order_capacity(self, ticker: str, side: str, price: float) -> dict:
        """Fresh NH orderable funds / sellable shares for the *exact* symbol and limit.

        NH's overseas inquiry endpoint uses pcs_dit=1 for buyable amount and
        pcs_dit=3 for sellable quantity. All cash amounts must be USD; if the
        server returns another currency or omits it, block rather than convert.
        """
        side = str(side or "").upper()
        if side not in ("BUY", "SELL") or not ticker or price <= 0:
            raise ValueError("Valid side, ticker and limit price are required")
        payload = {
            "act_no": self._acct(),
            "pcs_dit": "1" if side == "BUY" else "3",
            "fc_sec_trd_nat_cd": "200",
            "iem_cd": ticker.upper(),
            "wtm_cur_knd_cd": "1",  # USD only; do not convert KRW buying power implicitly
            "oss_orr_knd_cd": "1",
            "ahi_nmn_pr_tp_cd": "00",
            "fc_orr_uit_pr": round(float(price), 2),
        }
        response = self._call_api("/gbstock/inquiry/v1/buyableAmount", payload)
        out = response.get("Output_0") or {}
        if not isinstance(out, dict):
            out = {}
        result = {"rsp_cd": str(response.get("rsp_cd") or ""),
                  "rsp_msg": response.get("rsp_msg"), "side": side}
        if side == "BUY":
            if out.get("orr_pbl_amt") is not None:
                result["orderable_usd"] = _num(out["orr_pbl_amt"])
            result["currency"] = str(out.get("wtm_cur_cd") or "")
        else:
            if out.get("sll_pbl_qty") is not None:
                result["sellable_qty"] = _int(out["sll_pbl_qty"])
        return result

    def _acct(self) -> str:
        """11-digit account number for API payloads (dashes stripped)."""
        return self.act_no_norm or (self.act_no or "").replace("-", "").strip()

    def _query_gb_balance(self, cur_cd: str = "USD") -> dict:
        """POST /gbstock/inquiry/v1/balance — required Input_0 per NH Open API spec:
        act_no, qut_iqr_dit_cd(1 정규장 / 9 전체), fc_sec_trd_nat_cd(200 미국), cur_cd(KRW 전체/통화).
        The previous payload sent a non-existent `fc_mkt_dit_cd`, so the server answered with an
        empty Output_0/Output_1 and the parser fell back to KRW cash only.
        """
        return self._call_api("/gbstock/inquiry/v1/balance", {
            "act_no": self._acct(),
            "qut_iqr_dit_cd": "9",       # 9: 전체(정규장+시간외)
            "fc_sec_trd_nat_cd": "200",  # 200: 미국
            "cur_cd": cur_cd,             # USD / KRW(전체)
            "xns_dit_cd": "0",           # 0: 비용 미포함
        })

    def get_portfolio_state(self, include_raw: bool = False) -> dict:
        """
        Fetch US stock balance, USD cash and KRW cash from NH Open API.
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

        diag = {}
        res = self._query_gb_balance("USD")
        out_0 = res.get("Output_0", {}) or {}
        out_1 = res.get("Output_1", []) or []
        diag["balance_usd"] = {
            "rsp_cd": res.get("rsp_cd"), "rsp_msg": res.get("rsp_msg"),
            "output_0_keys": sorted(out_0.keys()),
            "output_1_count": len(out_1) if isinstance(out_1, list) else 1,
        }

        # If the USD slice came back empty, retry with the whole-currency view (KRW = 전체)
        if not out_1 and not _num(out_0.get("fc_dca")) and not _num(out_0.get("fc_aet_amt")):
            res_all = self._query_gb_balance("KRW")
            out_0_all = res_all.get("Output_0", {}) or {}
            out_1_all = res_all.get("Output_1", []) or []
            diag["balance_all_ccy"] = {"rsp_cd": res_all.get("rsp_cd"), "rsp_msg": res_all.get("rsp_msg"),
                                       "output_0": _redact(out_0_all), "output_1": _redact(out_1_all)}
            if out_1_all:
                res, out_0, out_1 = res_all, out_0_all, out_1_all

        # ---- Output_0: 계좌 레벨 집계 (외화예수금 / 평가 / 자산 / 총자산)
        cash_usd = _num(out_0.get("fc_dca"))
        eval_usd = _num(out_0.get("fc_eal_amt"))
        asset_usd = _num(out_0.get("fc_aet_amt")) or (cash_usd + eval_usd)
        cost_basis_usd = _num(out_0.get("fc_abk_amt"))
        unrealized_usd_acct = _num(out_0.get("fc_eal_pls_amt"))
        krw_dca_from_gb = _int(out_0.get("krw_dca"))
        tot_aet_amt_krw = _int(out_0.get("tot_aet_amt"))

        # ---- Output_1: 종목별 잔고
        weights = {}
        positions = {}
        positions_eval = 0.0
        items = out_1 if isinstance(out_1, list) else [out_1]
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("iem_cd", "") or "").strip()
            qty = _int(item.get("cns_bse_bnc_qty"))
            price = _num(item.get("fc_sec_end_pr"))
            eval_amt = _num(item.get("fc_eal_amt"))
            avg_price = _num(item.get("fc_phs_uit_pr")) or _num(item.get("fc_avg_phs_pr"))
            if not avg_price and qty:
                avg_price = _num(item.get("fc_cns_bse_phs_xps")) / qty
            if sym and qty > 0:
                positions[sym] = {
                    "qty": qty,
                    "price": price,
                    "market_value": eval_amt,
                    "avg_price": round(avg_price, 4) if avg_price else None,
                    "cost_basis": round(_num(item.get("fc_cns_bse_phs_xps")), 2) or None,
                    "unrealized": round(_num(item.get("fc_eal_pls_amt")), 2),
                    "return_pct": round(_num(item.get("eal_pft_rt")), 2),
                    "name": item.get("iem_nm") or item.get("oss_iem_eng_nm"),
                    "krw_eval": _int(item.get("krw_eal_amt")),
                    "sellable_qty": _int(item.get("sll_pbl_qty1")),
                }
                positions_eval += eval_amt

        # ---- KRW 예수금/총자산 (국내 잔고 API 교차 확인)
        krw_cash = krw_dca_from_gb
        krw_total_aet = tot_aet_amt_krw
        try:
            res_kr = self._call_api("/krstock/inquiry/v1/balance", {
                "act_no": self._acct(),
                "bnc_bse_cd": "1",
                "ltg_aot_dit_cd": "9",
                "aet_bse": "1",
                "qut_dit_cd": "UNT"
            })
            out_kr = res_kr.get("Output_0", {}) or {}
            dca_val = _int(out_kr.get("dca"))
            krw_total_aet = _int(out_kr.get("tot_aet_amt")) or krw_total_aet or dca_val
            krw_cash = max(krw_cash, dca_val, krw_total_aet)
            diag["krw_balance"] = {"rsp_cd": res_kr.get("rsp_cd"), "rsp_msg": res_kr.get("rsp_msg"),
                                   "output_0": _redact(out_kr)}
        except Exception as e:
            diag["krw_balance"] = {"error": str(e)}

        if asset_usd <= 0.0 and not positions:
            if krw_cash > 0:
                asset_usd = round(krw_cash / 1400.0, 2)
                cash_usd = cash_usd or asset_usd
            elif self.dry_run:
                print(f"[NH Broker] Account has $0.00 USD balance. Simulating $10,000.00 for dry-run preview.")
                asset_usd = 10000.0
                cash_usd = 10000.0

        if asset_usd > 0:
            for sym, p in positions.items():
                weights[sym] = round(p["market_value"] / asset_usd, 4)

        state = {
            "equity": asset_usd,
            "cash": cash_usd,
            "cash_usd": cash_usd,
            "positions_market_value_usd": round(positions_eval, 2),
            "cost_basis_usd": round(cost_basis_usd, 2),
            "unrealized_usd": round(unrealized_usd_acct or sum(p["unrealized"] for p in positions.values()), 2),
            "cash_krw": krw_cash,
            "krw_total_aet": krw_total_aet,
            "weights": weights,
            "positions": positions,
            "rsp_cd": res.get("rsp_cd"),
            "rsp_msg": res.get("rsp_msg"),
        }
        if include_raw:
            state["diagnostics"] = diag
            state["raw_balance"] = _redact({"Output_0": out_0, "Output_1": items,
                                            "rsp_cd": res.get("rsp_cd"), "rsp_msg": res.get("rsp_msg")})
            try:
                state["recent_executions"] = self.get_recent_executions(days=3)
            except Exception as e:
                state["recent_executions"] = {"error": str(e)}
        return state

    def execute_rebalance(
        self,
        target_weights: dict[str, float],
        market_prices: Optional[dict[str, float]] = None,
        order_type: str = "00",
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
                    "act_no": self._acct(),
                    "fc_sec_trd_nat_cd": "200",  # USA
                    "iem_cd": sym,
                    "orr_qty": shares,
                    "fc_orr_uit_pr": price,
                    "ahi_nmn_pr_tp_cd": normalize_order_type(order_type),
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
