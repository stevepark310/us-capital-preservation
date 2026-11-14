"""
Agent 2: Technical Levels, Momentum & Entry-Signal Specialist

All levels are computed from the fetched OHLCV history (core/technical). On
weekends and holidays the agent switches to a Monday-open plan: futures-implied
gap, weekly pivots and the ATR band around Friday's close, plus the validated
dip-swing watchlist.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent
from core import technical, swing_engine

UNIVERSE = ["SOXL", "TQQQ", "NVDA", "TSLA", "SOXS", "SQQQ"]
# 2026-11-14 지시: 채권·환율·코인도 기술적 감시 대상으로 편입
CROSS_ASSET_UNIVERSE = ["TLT", "IEF", "SHY", "HYG", "LQD",
                        "DX-Y.NYB", "KRW=X", "JPY=X", "EURUSD=X", "CNY=X",
                        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "IBIT"]


class TechnicalMomentumAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=2,
            name="Agent 2: 기술적 변동성 & 모멘텀 분석가 (주식·채권·환율·코인)",
            code="TECH_MOMENTUM_MULTIASSET",
            role="실측 OHLCV 기반 변동성 돌파·과매도 반등·갭 시나리오 산출 — 주식 외 채권(TLT·IEF)·환율(DXY·원/달러·엔/달러)·코인(BTC·ETH) 포함",
            description="일봉 실데이터로 ATR·RSI2·볼린저 %B·RVOL·EMA를 계산해 래리 윌리엄스 돌파 기준가와 과매도 반등 진입 점수를 산출하고, 채권·환율·코인 15개 심볼의 추세·변동성·지지저항을 별도 블록으로 보고합니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or {}
        clock = context.get("clock") or {}
        phase = clock.get("phase", "UNKNOWN")
        techs = context.get("technical") or {}
        history = market_data.get("history") or context.get("history") or {}
        quotes = snap.get("quotes", {})
        nq_chg = (snap.get("futures", {}).get("nq") or {}).get("change_pct") or 0.0
        sentiment = context.get("sentiment", {}).get("tickers", {})

        levels: Dict[str, Any] = {}
        for sym in UNIVERSE:
            t = techs.get(sym)
            if not t or not t.get("available"):
                continue
            br = technical.breakout_level(sym, t)
            entry = technical.composite_entry_score(
                t, sentiment.get(sym.replace("SOXS", "SOXL").replace("SQQQ", "TQQQ"), {}).get("sentiment_score", 50.0),
                regime_ok=(snap.get("macro", {}).get("vix", {}) or {}).get("last", 20) < 22,
            )
            levels[sym] = {
                "close": t["close"], "bar_date": t["bar_date"],
                "atr_pct": t["atr_pct"], "rsi14": t["rsi14"], "rsi2": t["rsi2"],
                "pct_b": t.get("bb_pct_b"), "rvol": t.get("rvol"),
                "ema20": t["ema20"], "above_ema20": t["above_ema20"],
                "breakout_trigger": br.get("trigger"), "breakout_pct": br.get("trigger_pct_from_close"),
                "breakdown_trigger": br.get("breakdown_trigger"),
                "entry_score": entry["score"], "entry_signal": entry["signal"],
                "entry_components": entry["components"],
                "pivots": t.get("weekly_pivots"),
                "gap_plan": technical.gap_scenarios(t, nq_chg) if phase in ("WEEKEND_PREP", "OFF_HOURS", "HOLIDAY_PREP", "PRE_MARKET") else None,
            }

        # validated dip-swing watchlist (research-backed; overnight approval: config/swing_approval.json)
        dip = {}
        try:
            daily = {k: history[k] for k in ("SOXL", "TQQQ") if k in history}
            if daily:
                dip = swing_engine.scan_latest(daily)
        except Exception as e:
            dip = {"error": str(e)}

        # ---- cross-asset technical read (bonds / FX / crypto)
        cross: Dict[str, Any] = {}
        for sym in CROSS_ASSET_UNIVERSE:
            t = techs.get(sym)
            if not t or not t.get("available"):
                continue
            trend = ("UPTREND" if t.get("above_ema20") and t.get("ema20_slope_pct", 0) > 0 else
                     "DOWNTREND" if not t.get("above_ema20") and t.get("ema20_slope_pct", 0) < 0 else "RANGE")
            cross[sym] = {
                "group": ("BOND" if sym in ("TLT", "IEF", "SHY", "HYG", "LQD") else
                          "FX" if sym in ("DX-Y.NYB", "KRW=X", "JPY=X", "EURUSD=X", "CNY=X") else "CRYPTO"),
                "close": t.get("close"), "bar_date": t.get("bar_date"),
                "atr_pct": t.get("atr_pct"), "realized_vol_20d_pct": t.get("realized_vol_20d_pct"),
                "rsi14": t.get("rsi14"), "rsi2": t.get("rsi2"),
                "above_ema20": t.get("above_ema20"), "ema20_slope_pct": t.get("ema20_slope_pct"),
                "trend": trend, "ret_5d_pct": t.get("ret_5d_pct"), "ret_20d_pct": t.get("ret_20d_pct"),
                "pct_b": t.get("bb_pct_b"), "rvol": t.get("rvol"),
                "support_pivot": (t.get("weekly_pivots") or {}).get("s1"),
                "resistance_pivot": (t.get("weekly_pivots") or {}).get("r1"),
                "atr_band": [round((t.get("close") or 0) * (1 - (t.get("atr_pct") or 0) / 100), 2),
                             round((t.get("close") or 0) * (1 + (t.get("atr_pct") or 0) / 100), 2)],
            }
        def _grp(g):
            return {s: v for s, v in cross.items() if v["group"] == g}
        bond_lv = _grp("BOND")
        fx_lv = _grp("FX")
        crypto_lv = _grp("CRYPTO")
        tlt_v = bond_lv.get("TLT", {})
        hyg_v, lqd_v = bond_lv.get("HYG", {}), bond_lv.get("LQD", {})
        credit_momentum_pct = (round((hyg_v.get("ret_20d_pct") or 0) - (lqd_v.get("ret_20d_pct") or 0), 2)
                               if hyg_v and lqd_v else None)
        btc_v = crypto_lv.get("BTC-USD", {})
        eth_v = crypto_lv.get("ETH-USD", {})
        krw_v = fx_lv.get("KRW=X", {})
        dxy_v = fx_lv.get("DX-Y.NYB", {})

        cross_summary = {
            "bonds": {
                "tlt": {k: tlt_v.get(k) for k in ("close", "trend", "rsi14", "atr_pct", "ret_20d_pct")},
                "ief": {k: bond_lv.get("IEF", {}).get(k) for k in ("close", "trend", "rsi14", "atr_pct")},
                "shy": {k: bond_lv.get("SHY", {}).get(k) for k in ("close", "trend", "atr_pct")},
                "hyg_lqd_credit_momentum_20d_pp": credit_momentum_pct,
                "reading_ko": (f"TLT {tlt_v.get('trend')} (RSI14 {tlt_v.get('rsi14')}, ATR {tlt_v.get('atr_pct')}%, "
                               f"20일 {tlt_v.get('ret_20d_pct')}%), HYG-LQD 20일 상대 {credit_momentum_pct}%p"
                               if tlt_v else "채권 기술적 데이터 미확보"),
            },
            "fx": {
                "dxy": {k: dxy_v.get(k) for k in ("close", "trend", "rsi14", "atr_pct")},
                "usd_krw": {k: krw_v.get(k) for k in ("close", "trend", "rsi14", "atr_pct", "ret_5d_pct")},
                "reading_ko": (f"DXY {dxy_v.get('trend')}, 원/달러 {krw_v.get('trend')} "
                               f"(RSI14 {krw_v.get('rsi14')}, 5일 {krw_v.get('ret_5d_pct')}%)" if krw_v else "FX 데이터 미확보"),
            },
            "crypto": {
                "btc": {k: btc_v.get(k) for k in ("close", "trend", "rsi14", "atr_pct", "realized_vol_20d_pct", "ret_20d_pct")},
                "eth": {k: eth_v.get(k) for k in ("close", "trend", "rsi14", "atr_pct")},
                "btc_eth_relative_20d_pp": (round((btc_v.get("ret_20d_pct") or 0) - (eth_v.get("ret_20d_pct") or 0), 2)
                                            if btc_v and eth_v else None),
                "reading_ko": (f"BTC {btc_v.get('trend')} (RSI14 {btc_v.get('rsi14')}, 실현변동성 {btc_v.get('realized_vol_20d_pct')}%, "
                               f"20일 {btc_v.get('ret_20d_pct')}%), ETH {eth_v.get('trend')}" if btc_v else "코인 데이터 미확보"),
            },
        }

        actionable = [s for s, v in levels.items() if v["entry_signal"] in ("STRONG_ENTRY", "WATCH")]
        best = max(levels.items(), key=lambda kv: kv[1]["entry_score"]) if levels else (None, {})
        weekend_note = ""
        if phase in ("WEEKEND_PREP", "HOLIDAY_PREP"):
            weekend_note = (f"주말 모드: {clock.get('next_open_kst')} 개장까지 {clock.get('hours_to_next_open')}시간. "
                            f"마지막 세션({clock.get('session_date')}) 종가·주간 피벗 기준 월요일 시나리오를 산출했습니다.")

        self.output_data = {
            "phase": phase,
            "levels": levels,
            "cross_asset_levels": cross,
            "cross_asset_summary": cross_summary,
            "actionable_symbols": actionable,
            "best_setup": {"symbol": best[0], "score": best[1].get("entry_score"),
                           "signal": best[1].get("entry_signal")} if best[0] else None,
            "dip_swing_watchlist": dip,
            "small_cap_surge_model": {
                "status": "DISABLED (사용자 2026-11-13 지시: 승률 낮은 스몰캡 급등주 실전 진입 배제)",
                "last_backtest": "Engine A 승률 42.1% / 손익비 1.39 — 유동성 부족으로 실전 제외",
            },
            "weekend_plan": weekend_note or None,
            "key_takeaway": (
                f"{', '.join(actionable) if actionable else '현재 진입 신호 없음'}. "
                f"최고 점수 {best[0]}({best[1].get('entry_score')}점, {best[1].get('entry_signal')}). "
                f"SOXL RSI2 {levels.get('SOXL', {}).get('rsi2')} · %B {levels.get('SOXL', {}).get('pct_b')} · "
                f"돌파 기준 ${levels.get('SOXL', {}).get('breakout_trigger')}. "
                f"[크로스애셋] {cross_summary['bonds'].get('reading_ko')} / {cross_summary['crypto'].get('reading_ko')}"
            ),
        }
        self.confidence_score = 0.9
        self.status = "COMPLETED"
        return self.output_data
