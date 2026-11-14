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


class TechnicalMomentumAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=2,
            name="Agent 2: 기술적 일중 변동성 & 모멘텀 분석가",
            code="TECH_MOMENTUM_SPECIALIST",
            role="실측 OHLCV 기반 변동성 돌파·과매도 반등·갭 시나리오 산출",
            description="일봉/시간봉 실데이터로 ATR·RSI2·볼린저 %B·RVOL·EMA를 계산해 래리 윌리엄스 돌파 기준가와 과매도 반등 진입 점수, 월요일 갭 대응 레벨을 산출합니다.",
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

        # validated dip-swing watchlist (research-backed, execution pending approval)
        dip = {}
        try:
            daily = {k: history[k] for k in ("SOXL", "TQQQ") if k in history}
            if daily:
                dip = swing_engine.scan_latest(daily)
        except Exception as e:
            dip = {"error": str(e)}

        actionable = [s for s, v in levels.items() if v["entry_signal"] in ("STRONG_ENTRY", "WATCH")]
        best = max(levels.items(), key=lambda kv: kv[1]["entry_score"]) if levels else (None, {})
        weekend_note = ""
        if phase in ("WEEKEND_PREP", "HOLIDAY_PREP"):
            weekend_note = (f"주말 모드: {clock.get('next_open_kst')} 개장까지 {clock.get('hours_to_next_open')}시간. "
                            f"마지막 세션({clock.get('session_date')}) 종가·주간 피벗 기준 월요일 시나리오를 산출했습니다.")

        self.output_data = {
            "phase": phase,
            "levels": levels,
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
                f"돌파 기준 ${levels.get('SOXL', {}).get('breakout_trigger')}."
            ),
        }
        self.confidence_score = 0.9
        self.status = "COMPLETED"
        return self.output_data
