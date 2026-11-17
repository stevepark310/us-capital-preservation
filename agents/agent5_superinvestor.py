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
from typing import Dict, Any, List, Optional

from agents.base import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data", "options_snapshot.json")
THIRTEENF_PATH = os.path.join(BASE_DIR, "data", "superinvestor_13f.json")
CAND_PATH = os.path.join(BASE_DIR, "data", "13f_candidates.json")
CAND_TTL_MIN = 30

# 13F에서 대가들이 실제로 움직인 종목 중 DIP_SWING 백테스트가 끝난 감시 목록
# (scripts/run_13f_ab_backtest.py — 2년 일봉 + 1년 60분봉)
WATCH_13F = ["SOXX", "AMZN", "GOOGL", "STX", "MSFT", "LEN", "NFLX", "ALC", "DDOG", "U", "GLD"]

# A/B 결과(2026-11-17): 후보 확장은 실전 편입 기각 — 제안 카드로만 쓴다
DIP_EV_13F = {
    "SOXX": {"ev_daily_pct": 1.411, "ev_60m_pct": 1.084, "n_daily": 19, "n_60m": 20, "pf": 2.07},
    "AMZN": {"ev_daily_pct": 1.158, "ev_60m_pct": 0.363, "n_daily": 22, "n_60m": 23, "pf": 2.1},
    "GOOGL": {"ev_daily_pct": 0.091, "ev_60m_pct": 0.6, "n_daily": 24, "n_60m": 27, "pf": 1.06},
    "STX": {"ev_daily_pct": 1.049, "ev_60m_pct": 1.751, "n_daily": 13, "n_60m": 13, "pf": 1.47},
}


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


# ---------------------------------------------------------------- 13F 스마트머니
def load_13f(max_age_days: float = 200) -> Dict[str, Any]:
    """13F 데이터셋 로드 — 없으면 빈 dict(집계 생략)."""
    try:
        with open(THIRTEENF_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        ts = datetime.fromisoformat(str(d.get("as_of_kst")))
        age_days = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9) - ts).total_seconds() / 86400
        d["age_days"] = round(age_days, 1)
        d["stale"] = age_days > max_age_days
        return d
    except Exception:
        return {}


def build_superinvestor_block(si: Dict[str, Any], candidates: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """13F 원문 데이터 → Agent 5 보고용 스마트머니 컨센서스 블록(순수 함수)."""
    if not si:
        return {"available": False,
                "note_ko": "13F 데이터셋 없음 — scripts/build_superinvestor_13f.py 미실행 또는 파싱 실패"}
    investors = si.get("investors") or []
    ok = [i for i in investors if i.get("data_available")]
    cash = si.get("cashization") or {}
    sectors = si.get("sector_consensus") or []
    idx = cash.get("index")
    regime_ko = ("현금 확보 우위" if idx is not None and idx >= 60
                 else "중립" if idx is not None and idx > 40 else "주식 비중 확대 우위")
    positioning = [{
        "key": i["key"], "name": i["name"], "pm": i.get("pm"),
        "data_available": i.get("data_available"),
        "positions": (f"{i.get('positions_q2')}→{i.get('positions_q3')}"
                      if i.get("data_available") else None),
        "form": i.get("form"),
        "value_usd_b": round((i.get("value_q3_usd") or 0) / 1e9, 2),
        "net_flow_pct_of_value": i.get("net_flow_pct_of_value"),
        "counts": i.get("counts"), "stance": i.get("stance"),
        "top_holdings": [t.get("ticker") or t.get("issuer") for t in (i.get("top_holdings") or [])[:5]],
        "video_note": i.get("video_note"),
    } for i in investors]
    tick = (si.get("ticker_flows") or [])[:8]
    consensus = [{"ticker": t.get("ticker"), "sector": t.get("sector"),
                  "net_flow_usd_m": round((t.get("net_flow_usd") or 0) / 1e6, 1),
                  "buyers": t.get("buyers"), "sellers": t.get("sellers")} for t in tick]
    top = sectors[0] if sectors else {}
    bottom = sectors[-1] if sectors else {}
    reading = (f"Q3 13F 원문 {len(ok)}인 · 현금화 압력 {idx}/100({regime_ko}) · "
               f"섹터 최우호 {top.get('sector_ko')} {top.get('score')} / 최비호 {bottom.get('sector_ko')} {bottom.get('score')}"
               if idx is not None else "13F 집계 불가")
    return {
        "available": True,
        "quarter": si.get("quarter"),
        "as_of_kst": si.get("as_of_kst"),
        "age_days": si.get("age_days"),
        "stale": si.get("stale"),
        "source": "SEC EDGAR 13F-HR 정보테이블 원문 파싱(scripts/build_superinvestor_13f.py)",
        "covered": len(ok), "requested": len(investors),
        "excluded": [i["name"] for i in investors if not i.get("data_available")],
        "cashization": {
            "index": idx, "regime_ko": regime_ko,
            "net_flow_usd_b": round((cash.get("net_flow_usd") or 0) / 1e9, 2),
            "components": cash.get("components"),
            "formula": cash.get("formula"),
            "reading_ko": cash.get("reading_ko"),
            "applied_to_sizing": False,
            "note_ko": (
                "현금화 지수는 보고 전용입니다. 분기 1회·원문 5인 표본으로는 타이밍 검정력이 없어 "
                "사이즈 계수로 자동 적용하지 않습니다(A/B: 고정 0.7배 축소는 PF 2.04→2.04 변화 없음 = 정보가치 0)."),
        },
        "sector_consensus": sectors[:5],
        "sector_laggards": sectors[-2:] if len(sectors) > 1 else [],
        "investors": positioning,
        "ticker_consensus": consensus,
        "nvidia_governance": si.get("nvidia_governance"),
        "candidate_signals": candidates or [],
        "candidate_policy": ("13F 매집 종목의 DIP_SWING 신호는 '제안'으로만 올립니다. "
                             "신규 종목 편입은 사용자 승인 항목이라 자동 주문 대상이 아닙니다."),
        "reading_ko": reading,
    }


def scan_candidates_13f(max_age_min: float = CAND_TTL_MIN) -> List[Dict[str, Any]]:
    """13F 감시 종목의 현재 DIP_SWING 신호(승인 규격 RSI2≤20·%B≤0.15·RVOL≥1.0).

    30분 캐시. 네트워크·데이터 실패는 조용히 빈 리스트로 떨어뜨린다(보고는 계속).
    """
    try:
        if os.path.exists(CAND_PATH):
            with open(CAND_PATH, "r", encoding="utf-8") as f:
                c = json.load(f)
            age = (datetime.now() - datetime.fromisoformat(c["fetched_at"])).total_seconds() / 60
            if age < max_age_min:
                return c.get("signals") or []
    except Exception:
        pass
    out: List[Dict[str, Any]] = []
    try:
        import yfinance as yf
        from core import swing_engine  # noqa: WPS433 (지연 임포트: 파일 I/O·판다스 비용 회피)
        raw = yf.download(WATCH_13F, period="6mo", interval="1d", progress=False,
                          auto_adjust=False, group_by="ticker")
        for sym in WATCH_13F:
            try:
                df = raw[sym].dropna(subset=["Close"])
                if len(df) < 40:
                    continue
                d = swing_engine.prepare(df)
                row = d.iloc[-1]
                sig = swing_engine.signal_row(row)
                atr_pct = float(row["ATR_PCT"])
                item = {
                    "ticker": sym, "date": str(d.index[-1].date()),
                    "close": round(float(row["Close"]), 2), "atr_pct": round(atr_pct, 2),
                    "rsi2": round(float(row["RSI2"]), 1), "pct_b": round(float(row["PCT_B"]), 3),
                    "rvol": round(float(row["RVOL"]), 2) if row["RVOL"] == row["RVOL"] else None,
                    "signal": bool(sig),
                    "validated_tp": round(float(row["Close"]) + 1.5 * float(row["ATR"]), 2),
                    "validated_sl": round(float(row["Close"]) - 1.5 * float(row["ATR"]), 2),
                    "backtest": DIP_EV_13F.get(sym),
                }
                if sig:
                    item.update({"entry_rule": "다음 세션 시가 · 최대 3세션", "score": sig.get("score")})
                out.append(item)
            except Exception:
                continue
    except Exception:
        out = []
    try:
        with open(CAND_PATH, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": datetime.now().isoformat(), "signals": out}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return out


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

        # ---- 2026-11-17 확장: 13F 스마트머니 컨센서스(Q3 원문 파싱)
        si_raw = load_13f()
        cands = scan_candidates_13f() if si_raw else []
        si = build_superinvestor_block(si_raw, cands)
        si_txt = ""
        if si.get("available"):
            cash = si.get("cashization") or {}
            top = (si.get("sector_consensus") or [{}])[0]
            sigs = [c for c in cands if c.get("signal")]
            stretched = sorted([c for c in cands if not c.get("signal")],
                               key=lambda x: (x.get("pct_b") or 1.0))[:2]
            si_txt = (f"[13F {si.get('quarter')}] {si.get('reading_ko')}. "
                      f"{top.get('sector_ko')} 순매수 {top.get('score')} · 가치·주택 {next((s.get('score') for s in si.get('sector_laggards') or [] if s.get('sector') == 'VALUE_HOUSING_FIN'), '—')}. "
                      + ("감시종목 DIP 신호 " + ", ".join(f"{c['ticker']}({c.get('rsi2')}/{c.get('pct_b')})" for c in sigs) + ". "
                         if sigs else
                         "감시종목 DIP 신호 없음(" + ", ".join(f"{c['ticker']} %B {c.get('pct_b')}" for c in stretched) + "). ")
                      + "신규 종목 편입은 제안 전용.")

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
            "superinvestor_13f": si,
            "key_takeaway": (
                f"QQQ Put/Call OI {pc} · 거래량비 {qqq.get('put_call_volume')} → {bias}. "
                f"NVDA 감마월 ${nvda.get('gamma_wall_strike')} (OI {nvda.get('gamma_wall_oi')}), 맥스페인 ${nvda.get('max_pain_strike')}. "
                f"[신용] {credit_flow.get('reading_ko')} [듀레이션] {duration_flow.get('reading_ko')} [크립토] {crypto_flow.get('reading_ko')}"
                + (f" {si_txt}" if si_txt else "")
            ) if chains else ("옵션 체인 조회 실패 — 다음 주기 재시도" + (f" {si_txt}" if si_txt else "")),
        }
        self.confidence_score = 0.9 if chains else 0.4
        self.status = "COMPLETED"
        return self.output_data
