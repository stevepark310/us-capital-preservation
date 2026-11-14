"""
CUBE 10-Agent Orchestrator — 24/7 edition

One entry point builds the desk context from live data and runs all ten agents
in EVERY session phase (regular, pre-market, after-hours, weekend, holiday):

    clock/session  →  what the desk is doing right now
    snapshot       →  measured prices, futures, FX, crypto
    history        →  daily OHLCV for indicator math
    news           →  real headlines (weekend included)
    ledger         →  the one true account state
    backtest       →  measured strategy results

Each agent's full report is written into data/cube_report.json (the old file
carried empty `report: {}` objects and an 11-11 timestamp on 11-14), together
with the phase, the data provenance and the next session open.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.agent1_news_sentiment import NewsSentimentAgent
from agents.agent2_tech_momentum import TechnicalMomentumAgent
from agents.agent3_fundamentals import FundamentalsAgent
from agents.agent4_macro_rates import MacroRatesAgent
from agents.agent5_superinvestor import SuperinvestorAgent
from agents.agent6_quant_scenarios import QuantScenarioModelerAgent
from agents.agent7_backtester import BacktesterAgent
from agents.agent8_alpha_evaluator import AlphaEvaluatorAgent
from agents.agent9_ensemble_synthesizer import EnsembleSynthesizerAgent
from agents.agent10_daily_trader import DailyTraderAgent

from core import market_clock, market_snapshot, news_engine, technical, ledger as ledger_mod, leading_indicators

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(BASE_DIR, "data", "cube_report.json")
DOCS_REPORT_PATH = os.path.join(BASE_DIR, "docs", "cube_report.json")
NEWS_TTL_MIN = float(os.getenv("CUBE_NEWS_TTL_MIN", "20"))


class CubeOrchestrator:
    def __init__(self):
        self.agents = [
            NewsSentimentAgent(), TechnicalMomentumAgent(), FundamentalsAgent(), MacroRatesAgent(),
            SuperinvestorAgent(), QuantScenarioModelerAgent(), BacktesterAgent(),
            AlphaEvaluatorAgent(), EnsembleSynthesizerAgent(), DailyTraderAgent(),
        ]
        self.full_report: Dict[str, Any] = {}

    # ------------------------------------------------------------------ context
    def build_context(self, force_refresh: bool = False) -> Dict[str, Any]:
        clock = market_clock.resolve()
        snap = market_snapshot.build_snapshot(force_history=force_refresh)

        hist = market_snapshot.fetch_history(force=force_refresh)
        techs = {}
        scan = ["SOXL", "TQQQ", "NVDA", "TSLA", "SGOV", "SOXS", "SQQQ", "SPY", "QQQ", "^VIX",
                # 2026-11-14: 채권 · 외환 · 코인 · 경기순환 프록시도 기술적 분석 대상
                "TLT", "IEF", "SHY", "HYG", "LQD", "TIP",
                "DX-Y.NYB", "KRW=X", "JPY=X", "EURUSD=X", "CNY=X",
                "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "IBIT",
                "IWM", "XLY", "XLP", "HG=F", "GC=F"]
        for sym in scan:
            if sym in hist:
                techs[sym] = technical.analyze(sym, hist[sym], snap.get("quotes", {}).get(sym))

        # news cache with TTL (weekends still refresh: crypto/FX + wires keep moving)
        payload = news_engine.load_cached()
        stale = True
        if payload and payload.get("fetched_at_utc"):
            try:
                age_min = (datetime.utcnow() - datetime.fromisoformat(payload["fetched_at_utc"])).total_seconds() / 60
                stale = age_min > NEWS_TTL_MIN
            except Exception:
                stale = True
        if stale or force_refresh:
            payload = news_engine.fetch_all(quiet=True)
        senti = news_engine.aggregate(payload, snap)

        led = ledger_mod.summary(snapshot=snap)

        # Business-cycle leading index (core/leading_indicators) — computed once per
        # pipeline, consumed by Agents 4/5/6/7/8/9/10 through ctx["leading"].
        try:
            lei = leading_indicators.compute(snapshot=snap, force=force_refresh)
            leading_indicators.export(lei)
        except Exception as e:
            lei = {"error": str(e), "cube_lei": {}, "components": []}
            print(f"[orchestrator] leading indicator engine failed: {e}")

        bt = {}
        bt_path = os.path.join(BASE_DIR, "data", "1y_backtest_report.json")
        if os.path.exists(bt_path):
            try:
                with open(bt_path, "r", encoding="utf-8") as f:
                    bt = json.load(f)
            except Exception:
                bt = {}

        return {
            "clock": clock, "phase": clock["phase"], "snapshot": snap, "history": hist,
            "technical": techs, "news_payload": payload, "sentiment": senti,
            "ledger": led, "backtest_report": bt, "leading": lei,
        }

    # ------------------------------------------------------------------ pipeline
    def run_daily_pipeline(self, market_data: Dict[str, Any] = None, broker_type: str = "nh",
                           context: Dict[str, Any] = None, quiet: bool = False) -> Dict[str, Any]:
        t0 = time.time()
        ctx = context or self.build_context()
        clock = ctx["clock"]
        snap = ctx["snapshot"]
        market_data = market_data or {"snapshot": snap, "history": ctx["history"]}

        # derivative values the agents need
        ctx["vix"] = (snap.get("macro", {}).get("vix") or {}).get("last") or 20.0
        ctx["dxy"] = (snap.get("macro", {}).get("dxy") or {}).get("last") or 100.0
        ctx["us_10y_yield"] = snap.get("macro", {}).get("us_10y_yield")
        ctx.setdefault("leading", {})
        if not ctx.get("leading"):
            try:
                ctx["leading"] = leading_indicators.compute(snapshot=snap)
            except Exception as e:
                ctx["leading"] = {"error": str(e), "cube_lei": {}, "components": []}

        summaries: List[Dict[str, Any]] = []
        if not quiet:
            print("=" * 72)
            print(f"🚀 CUBE 10-Agent pipeline | {clock['now_kst']} KST | phase {clock['phase']} | next open {clock['next_open_kst']}")
            print("=" * 72)

        for agent in self.agents:
            try:
                result = agent.run(market_data, ctx)
            except Exception as e:
                agent.status = "FAILED"
                result = {"error": str(e), "key_takeaway": f"실행 실패: {e}"}
                agent.output_data = result
            # forward results down the chain
            aid = agent.agent_id
            if aid == 1:
                ctx["news_report"] = result
            elif aid == 2:
                ctx["technical_report"] = result
            elif aid == 3:
                ctx["catalyst_report"] = result
            elif aid == 4:
                ctx["macro"] = result
            elif aid == 5:
                ctx["options"] = result.get("chains", {}) if isinstance(result, dict) else {}
                ctx["smart_money_report"] = result
            elif aid == 6:
                ctx["scenarios"] = result.get("scenarios", [])
            elif aid == 7:
                ctx["backtest"] = result
            elif aid == 8:
                ctx["alpha"] = result
            elif aid == 9:
                ctx["ensemble"] = result
                ctx["target_portfolio_weights"] = result.get("target_portfolio_weights", {})
            summary = agent.get_summary()
            summary["report"] = result if isinstance(result, dict) else {}
            summaries.append(summary)
            if not quiet:
                print(f"  ✓ Agent {aid}: {str(result.get('key_takeaway', ''))[:110]}")

        failed = [s["agent_id"] for s in summaries if s["status"] == "FAILED"]
        led = ctx.get("ledger", {})
        self.full_report = {
            "title": "CUBE 10-Agent Quantitative Trading System (24/7)",
            "generated_at": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
            "phase": clock["phase"],
            "phase_label": clock["label_ko"],
            "next_open_kst": clock["next_open_kst"],
            "hours_to_next_open": clock["hours_to_next_open"],
            "system_status": "OPERATIONAL" if not failed else "DEGRADED",
            "agents_ok": len(summaries) - len(failed),
            "agents_failed": failed,
            "total_agents": len(self.agents),
            "data_provenance": {
                "market": "Yahoo Finance (yfinance) — daily + 5m intraday",
                "macro_official": "FRED fredgraph.csv — T10Y3M/T10Y2Y/DGS2/DGS10/BAMLH0A0HYM2/NFCI/ICSA/UMCSENT (API 키 불필요, 6시간 캐시)",
                "leading_index": f"CUBE-LEI {((ctx.get('leading') or {}).get('cube_lei') or {}).get('index')} "
                                 f"({((ctx.get('leading') or {}).get('cube_lei') or {}).get('regime')}) — Estrella & Mishkin (1998) · Stock & Watson (1989) · Gilchrist & Zakrajšek (2012) · Burns & Mitchell (1946) 기반 core/leading_indicators.py",
                "news": f"{ctx['news_payload'].get('feeds_ok')}/{ctx['news_payload'].get('feeds_total')} feeds, "
                        f"{ctx['news_payload'].get('item_count')} items, health {ctx['news_payload'].get('source_health')}",
                "ledger": "data/daily_trading_calendar.json + data/live_portfolio.json (single FX source)",
                "backtest": "data/1y_backtest_report.json (hourly-path simulation)",
                "hardcoded_market_values": False,
            },
            "account_status": {
                "act_no": led.get("account_no", "202-05-134849"),
                "act_no_formatted": "202-05-134849",
                "broker": "NH투자증권 (나무)",
                "equity_usd": led.get("equity_usd"),
                "equity_krw": led.get("equity_krw"),
                "cash_usd": led.get("cash_usd"),
                "cash_krw": led.get("cash_krw"),
                "cash_pct": led.get("cash_pct"),
                "fx_rate": led.get("fx_rate"),
                "realized_pnl_usd": led.get("realized_pnl_usd"),
                "unrealized_pnl_usd": led.get("unrealized_pnl_usd"),
                "last_inquiry_time": led.get("as_of"),
            },
            "agents": summaries,
            "executive_summary": {
                "macro_regime": (ctx.get("macro") or {}).get("macro_regime"),
                "cube_lei": (ctx.get("leading") or {}).get("cube_lei"),
                "recession_probability_pct": ((ctx.get("leading") or {}).get("recession_probit") or {}).get("probability_used"),
                "credit_level": ((ctx.get("leading") or {}).get("credit") or {}).get("level"),
                "market_stance": (ctx.get("ensemble") or {}).get("market_stance"),
                "sentiment_avg": (ctx.get("news_report") or {}).get("average_sentiment_score"),
                "market_implied_anchor": (ctx.get("news_report") or {}).get("market_implied_anchor", {}).get("score"),
                "approved_strategies": [a["id"] for a in (ctx.get("alpha") or {}).get("profitable_scenarios", [])],
                "execution_mode": (ctx.get("alpha") or {}).get("recommended_execution_mode"),
                "target_weights": ctx.get("target_portfolio_weights", {}),
                "next_open": clock["next_open_kst"],
                "simulation_disclaimer": "백테스트 수치는 시뮬레이션이며 실전 원장과 분리되어 있습니다.",
            },
            "pipeline_seconds": round(time.time() - t0, 1),
        }

        self._export_report()
        if not quiet:
            print(f"✅ pipeline done in {self.full_report['pipeline_seconds']}s | agents ok {self.full_report['agents_ok']}/10")
        return self.full_report

    def _export_report(self):
        payload = json.dumps(self.full_report, ensure_ascii=False, indent=2, default=str)
        for p in (REPORT_PATH, DOCS_REPORT_PATH):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(payload)


if __name__ == "__main__":
    CubeOrchestrator().run_daily_pipeline()
