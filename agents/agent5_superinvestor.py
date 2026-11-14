"""
Agent 5: Smart Money & Options Positioning Specialist

Replaces the hard-coded "Put/Call 0.68, 콜 감마 스퀴즈" copy with the real option
chain: open-interest and volume put/call ratios, the strike carrying the largest
call open interest (gamma-wall proxy) and an open-interest max-pain level for QQQ
and NVDA. Cached 30 minutes; on weekends it is explicitly labelled as the last
Friday snapshot.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from agents.base import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data", "options_snapshot.json")


def _load_cache(max_age_min: float = 30) -> Dict[str, Any]:
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["fetched_at"])).total_seconds() / 60
        if age < max_age_min:
            return c
    except Exception:
        pass
    return {}


def _compute(symbol: str) -> Dict[str, Any]:
    import yfinance as yf
    t = yf.Ticker(symbol)
    exps = t.options
    if not exps:
        return {}
    expiry = exps[0]
    ch = t.option_chain(expiry)
    calls, puts = ch.calls, ch.puts
    call_oi = float(calls.openInterest.fillna(0).sum())
    put_oi = float(puts.openInterest.fillna(0).sum())
    call_vol = float(calls.volume.fillna(0).sum())
    put_vol = float(puts.volume.fillna(0).sum())
    wall = calls.loc[calls.openInterest.idxmax()] if len(calls) and calls.openInterest.notna().any() else None
    both = (calls.set_index("strike").openInterest.fillna(0) + puts.set_index("strike").openInterest.fillna(0))
    max_pain = float(both.idxmax()) if len(both) else None
    return {
        "symbol": symbol, "expiry": expiry,
        "call_oi": int(call_oi), "put_oi": int(put_oi),
        "put_call_oi": round(put_oi / call_oi, 2) if call_oi else None,
        "call_volume": int(call_vol), "put_volume": int(put_vol),
        "put_call_volume": round(put_vol / call_vol, 2) if call_vol else None,
        "gamma_wall_strike": float(wall["strike"]) if wall is not None else None,
        "gamma_wall_oi": int(wall["openInterest"]) if wall is not None else None,
        "max_pain_strike": max_pain,
        "strikes": int(len(calls)),
    }


class SuperinvestorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=5,
            name="Agent 5: 스마트머니 & 기관 옵션 감마 분석가",
            code="SMART_MONEY_GAMMA_SPECIALIST",
            role="실제 옵션 체인 기반 Put/Call·감마월·맥스페인 산출",
            description="QQQ·NVDA 옵션 체인의 미결제약정과 거래량으로 기관 포지셔닝을 측정합니다. 소셜 버즈 데이터는 연동되어 있지 않아 사용하지 않습니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        clock = context.get("clock") or {}
        cached = _load_cache(30)
        chains = cached.get("chains") if cached else None
        if not chains:
            chains = {}
            errors = {}
            for sym in ("QQQ", "NVDA", "SOXL"):
                try:
                    d = _compute(sym)
                    if d:
                        chains[sym] = d
                except Exception as e:
                    errors[sym] = str(e)
            if chains:
                with open(CACHE, "w", encoding="utf-8") as f:
                    json.dump({"fetched_at": datetime.now().isoformat(), "chains": chains}, f,
                              ensure_ascii=False, indent=2)
        qqq = chains.get("QQQ", {})
        nvda = chains.get("NVDA", {})
        pc = qqq.get("put_call_oi")
        bias = ("PUT_HEAVY (하방 헤지 우위)" if pc and pc > 1.0 else
                "CALL_HEAVY (상방 베팅 우위)" if pc and pc < 0.8 else "BALANCED")

        # ---- 2026-11-14 확장: 크레딧 · 듀레이션 · 기관 크립토 자금흐름
        snap = context.get("snapshot") or {}
        q = snap.get("quotes", {})
        leading = context.get("leading") or {}
        credit_obs = leading.get("credit") or {}

        def g(sym, key="last"):
            return (q.get(sym) or {}).get(key)

        hyg_ief = ((leading.get("bonds", {}) or {}).get("etfs") or {})

        def ret20(sym):
            return hyg_ief.get(sym, {}).get("ret_20d") if hyg_ief.get(sym) else None

        hyg_r20, lqd_r20 = ret20("HYG"), ret20("LQD")
        tlt_rvol = g("TLT", "rvol")
        tlt_r20 = ret20("TLT")
        ief_r20 = ret20("IEF")
        credit_flow = {
            "metric": "HYG/LQD 상대강도 · HY OAS 수준(GZ 2012 프록시) · HYG RVOL",
            "hyg_ret_20d_pct": hyg_r20, "lqd_ret_20d_pct": lqd_r20,
            "hyg_vs_lqd_20d_pp": (round((hyg_r20 or 0) - (lqd_r20 or 0), 2) if hyg_r20 is not None and lqd_r20 is not None else None),
            "hyg_rvol": g("HYG", "rvol"), "hyg_last": g("HYG"), "lqd_last": g("LQD"),
            "hy_oas_bp": credit_obs.get("hy_oas_bp"), "hy_oas_z_5y": credit_obs.get("hy_oas_z_5y"),
            "credit_level": credit_obs.get("level"), "gz_proxy_z": credit_obs.get("gz_proxy_z"),
            "interpretation": ("하이일드 우위 = 신용 리스크온(기관 리스크 수용 확대)" if (hyg_r20 is not None and lqd_r20 is not None and hyg_r20 > lqd_r20)
                               else "투자등급 우위 = 신용 리스크오프(안전자산 선호)" if hyg_r20 is not None and lqd_r20 is not None else "데이터 부족"),
        }
        credit_flow["reading_ko"] = (
            f"HY OAS {credit_flow['hy_oas_bp']}bp({credit_flow['credit_level']}), "
            f"HYG-LQD 20일 {credit_flow['hyg_vs_lqd_20d_pp']}%p → {credit_flow['interpretation']}"
            if credit_flow["hy_oas_bp"] is not None else "크레딧 데이터 미확보")

        duration_flow = {
            "metric": "TLT 20일 수익률·RVOL·ATR(듀레이션 리스크) vs IEF",
            "tlt_ret_20d_pct": tlt_r20, "ief_ret_20d_pct": ief_r20,
            "tlt_rvol": tlt_rvol, "tlt_atr_pct": ((context.get("technical") or {}).get("TLT") or {}).get("atr_pct"),
            "tlt_last": g("TLT"), "ief_last": g("IEF"),
            "long_duration_demand": ("INFLOW (장듀레이션 매수 우위)" if (tlt_r20 is not None and ief_r20 is not None and tlt_r20 > ief_r20)
                                     else "OUTFLOW (장듀레이션 회피)" if tlt_r20 is not None and ief_r20 is not None else "UNKNOWN"),
        }
        duration_flow["reading_ko"] = (
            f"TLT 20일 {duration_flow['tlt_ret_20d_pct']}% (IEF {duration_flow['ief_ret_20d_pct']}%, RVOL {tlt_rvol}) → {duration_flow['long_duration_demand']}"
            if tlt_r20 is not None else "듀레이션 데이터 미확보")

        ibit_r20 = g("IBIT", "change_pct")
        btc_m = (snap.get("crypto_market") or {}).get("btc") or {}
        btc_r20 = btc_m.get("ret_30d")
        ibit_vs_btc = None
        try:
            hist = context.get("history", {}) or {}
            ib_hist = hist.get("IBIT")
            if ib_hist is not None and len(ib_hist) > 21:
                c = ib_hist["Close"].astype(float)
                ibit_r20 = round((float(c.iloc[-1]) / float(c.iloc[-21]) - 1) * 100, 2)
            # BTC도 같은 20거래일 창으로 재산출해야 비교가 성립한다
            b_hist = hist.get("BTC-USD")
            if b_hist is not None and len(b_hist) > 21:
                bc = b_hist["Close"].astype(float)
                btc_r20 = round((float(bc.iloc[-1]) / float(bc.iloc[-21]) - 1) * 100, 2)
            if ibit_r20 is not None and btc_r20 is not None:
                ibit_vs_btc = round(ibit_r20 - btc_r20, 2)
        except Exception:
            pass
        crypto_flow = {
            "metric": "IBIT 20거래일 수익률 - BTC 20거래일 수익률(현물 ETF 수요 프록시) · IBIT/COIN RVOL",
            "ibit_last": g("IBIT"), "ibit_rvol": g("IBIT", "rvol"), "ibit_ret_20d_pct": ibit_r20,
            "coin_last": g("COIN"), "coin_chg_pct": g("COIN", "change_pct"),
            "btc_last": btc_m.get("last"), "btc_ret_20d_pct": btc_r20,
            "btc_ret_30d_pct_ref": btc_m.get("ret_30d"),
            "ibit_minus_btc_pp": ibit_vs_btc,
            "institutional_demand": ("ETF_CREATION_DEMAND (기관 현물 수요 우위)" if (ibit_vs_btc or 0) > 0.5
                                     else "ETF_REDEMPTION_PRESSURE (기관 환매 우위)" if (ibit_vs_btc or 0) < -0.5
                                     else "NEUTRAL (추적오차 범위)"),
            "note": "IBIT-NAV 괴리 데이터는 무료 소스에 없어 가격 상대성과로 대체했습니다(지표명에 '프록시' 표기).",
        }
        crypto_flow["reading_ko"] = (
            f"IBIT 20일 {crypto_flow['ibit_ret_20d_pct']}% vs BTC 20일 {crypto_flow['btc_ret_20d_pct']}% "
            f"(차이 {crypto_flow['ibit_minus_btc_pp']}%p) → {crypto_flow['institutional_demand']}")

        self.output_data = {
            "phase": clock.get("phase"),
            "expiry_used": qqq.get("expiry"),
            "data_as_of": cached.get("fetched_at") if cached else self.last_run,
            "stale_note": "주말·휴장일에는 마지막 금요일 체인 값입니다." if clock.get("is_off_hours") else "정규장 실시간 체인",
            "chains": chains,
            "qqq_put_call_oi": pc,
            "qqq_put_call_volume": qqq.get("put_call_volume"),
            "nvda_gamma_wall": nvda.get("gamma_wall_strike"),
            "nvda_max_pain": nvda.get("max_pain_strike"),
            "institutional_bias": bias,
            "credit_flow": credit_flow,
            "duration_flow": duration_flow,
            "crypto_institutional_flow": crypto_flow,
            "key_takeaway": (
                f"QQQ Put/Call OI {pc} · 거래량비 {qqq.get('put_call_volume')} → {bias}. "
                f"NVDA 감마월 ${nvda.get('gamma_wall_strike')} (OI {nvda.get('gamma_wall_oi')}), 맥스페인 ${nvda.get('max_pain_strike')}. "
                f"[신용] {credit_flow.get('reading_ko')} [듀레이션] {duration_flow.get('reading_ko')} [크립토] {crypto_flow.get('reading_ko')}"
            ) if chains else "옵션 체인 조회 실패 — 다음 주기 재시도",
        }
        self.confidence_score = 0.9 if chains else 0.4
        self.status = "COMPLETED"
        return self.output_data
