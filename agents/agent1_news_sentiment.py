"""
Agent 1: Real-time News, Wall Street Action & Sentiment Specialist

Before: a hard-coded insight table (TSMC +29.2% 월 매출, 씨티 PT $175, VIX 18.42 ...)
replayed every hour regardless of the date, and NVDA scores that contradicted the
following line of the same report.

Now: every score is derived from headlines actually fetched at run time
(core/news_engine), blended with a market-implied anchor computed from live
prices (futures / VIX / SOXS-SOXL spread / BTC). Each headline keeps its source,
timestamp and link. Weekends are handled explicitly: crypto and FX trade, so the
engine has real weekend signal and reports it as such.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent
from core import news_engine, market_snapshot, technical
from core.exit_planner import AdaptiveExitPlanner

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = {
    "SOXL": "디렉시온 반도체 3배 레버리지 (SOXL)",
    "TQQQ": "프로셰어즈 나스닥100 3배 레버리지 (TQQQ)",
    "NVDA": "엔비디아 (NVIDIA)",
    "TSLA": "테슬라 (Tesla)",
    "SGOV": "아이셰어즈 0-3개월 초단기 국채 (SGOV)",
}


class NewsSentimentAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=1,
            name="Agent 1: 뉴스 & 증권사 인사이트 센티먼트 분석가",
            code="NEWS_SENTIMENT_SPECIALIST",
            role="RSS·뉴스 API 실시간 수집, 종목별 센티먼트 점수화, 매수·매도 브래킷 동적 보정",
            description="Yahoo/CNBC/MarketWatch/Nasdaq/CoinDesk/Google News RSS와 yfinance 뉴스 API를 1시간마다 수집해 종목 관련도·감성·최신성으로 점수화하고, 시장 내재 리스크 점수와 블렌딩해 종목별 익절·손절 폭을 산출합니다.",
        )
        self.planner = AdaptiveExitPlanner()

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or market_data.get("snapshot") or market_snapshot.load_snapshot()
        clock = context.get("clock") or snap.get("clock") or {}
        phase = clock.get("phase", snap.get("phase", "UNKNOWN"))

        payload = context.get("news_payload") or news_engine.fetch_all(quiet=True)
        agg = context.get("sentiment") or news_engine.aggregate(payload, snap)
        senti = agg["tickers"]
        anchor = agg["anchor"]
        items = payload.get("items", [])
        techs = context.get("technical", {})

        ticker_reports: Dict[str, Any] = {}
        for ticker in ["SOXL", "TQQQ", "NVDA", "TSLA", "SGOV"]:
            s = senti.get(ticker, {})
            score = s.get("sentiment_score", 50.0)
            t = techs.get(ticker, {})
            atr_pct = t.get("atr_pct") or 4.0
            plan = self.planner.plan(
                symbol=ticker, planned_price=t.get("close", 100.0), fill_price=t.get("close", 100.0),
                qty=3, atr_pct=atr_pct, sentiment_score=score, regime_ok=anchor["score"] >= 40,
            )
            if score >= 75:
                action = "RUNNER_EXPAND (목표 상향·러너 확대)"
            elif score >= 60:
                action = "STANDARD_STAGED (표준 3단계)"
            elif score >= 35:
                action = "DEFENSIVE (목표 축소·손절 타이트)"
            else:
                action = "EARLY_DEFENSIVE (조기 익절·손절 축소)"

            headlines = s.get("top_headlines", [])[:3]
            ib = []
            for note in s.get("analyst_notes", [])[:3]:
                ib.append({"firm": note.get("firm", "Wall Street"),
                           "view": self._action_word(note.get("headline", "")),
                           "comment": note.get("headline", "")})
            # real options positioning stands in for the SNS feeds we do not have
            opt = (context.get("options") or {}).get("QQQ", {})
            pc = opt.get("put_call_oi")
            ticker_reports[ticker] = {
                "name": NAMES.get(ticker, ticker),
                "sentiment_score": score,
                "sentiment_grade": news_engine.grade_for(score),
                "breaking_news": [f"[{h['source']}] {h['title']}" for h in headlines] or ["최근 24시간 내 해당 종목 직접 헤드라인 없음"],
                "ib_insights": ib,
                "community_sentiment": {
                    "source": "SNS(레딧·X) 미연동 → 기관 옵션 포지셔닝으로 대체",
                    "call_put_ratio": f"QQQ Put/Call OI {pc:.2f}" if pc else "옵션 데이터 수집 전",
                    "summary": "소셜 버즈는 수집하지 않습니다(연동 없음). 대신 실제 옵션 미결제약정과 뉴스 플로우로 대체합니다.",
                },
                "news_flow": {
                    "headline_count": s.get("headline_count", 0),
                    "weighted_sentiment": s.get("weighted_sentiment", 0.0),
                    "news_only_score": s.get("news_score", 50.0),
                    "market_implied_score": anchor["score"],
                },
                "dynamic_exit_adjustment": {
                    "action": action,
                    "tp1": f"+{plan.tp1_pct}% (${plan.tp1_price}) · ATR {atr_pct}% 기준",
                    "tp2": f"+{plan.tp2_pct}% (${plan.tp2_price})",
                    "runner_weight": f"{int(plan.runner_tip_share*100) if hasattr(plan,'runner_tip_share') else int(plan.runner_qty/max(1,plan.qty)*100)}% (샹들리에+EMA 트레일)",
                    "sl": f"-{plan.sl_pct}% (${plan.hard_sl_price}) · 본전스탑 ${plan.breakeven_price}",
                    "rationale": self._rationale(ticker, s, anchor),
                },
            }

        # macro / weekend flow
        def by_topic(topic: str, limit: int = 4) -> List[Dict[str, Any]]:
            rows = [it for it in items if topic in it.get("topics", [])]
            return [{"title": it["title"], "source": it["source"], "published": it["published"],
                     "link": it["link"], "sentiment": it["sentiment"]} for it in rows[:limit]]

        macro_flow = {t: by_topic(t) for t in ("FED_RATES", "TARIFF_TRADE", "OIL_ENERGY", "CRYPTO", "AI_CAPEX")}
        fresh_24h = sum(1 for it in items if (it.get("age_hours") or 99) <= 24)
        weekend = self._weekend_block(snap, clock)
        sector_sentiment = self._sector_sentiment(items)

        avg = round(sum(ticker_reports[t]["sentiment_score"] for t in ["SOXL", "TQQQ", "NVDA", "TSLA"]) / 4.0, 1)
        mood = ("RISK_OFF (매크로 악화)" if avg < 40 else
                "NEUTRAL_MIXED" if avg < 55 else
                "RISK_ON (반도체·기술주 우호)" if avg < 75 else "STRONG_RISK_ON")

        self.output_data = {
            "scan_timestamp": self.last_run,
            "phase": phase,
            "phase_label": clock.get("label_ko", ""),
            "hourly_cadence": "EVERY_1_HOUR",
            "data_sources": {
                "news_feeds_ok": f"{payload.get('feeds_ok')}/{payload.get('feeds_total')}",
                "news_items": payload.get("item_count", 0),
                "items_last_24h": fresh_24h,
                "source_health": payload.get("source_health"),
                "market_data": "Yahoo Finance 일봉·분봉 (core/market_snapshot)",
                "hardcoded_values": False,
            },
            "average_sentiment_score": avg,
            "overall_market_mood": mood,
            "market_implied_anchor": anchor,
            "monitored_tickers": list(ticker_reports.keys()),
            "ticker_reports": ticker_reports,
            "macro_news_flow": macro_flow,
            "sector_sentiment": sector_sentiment,
            "sector_coverage": {
                "note": "업무 범위 2026-11-14 확장: 주식/매크로 외에 채권·연준, 외환(FX), 크립토를 섹터별로 분리 보고합니다.",
                "sectors": list(sector_sentiment.keys()),
                "method": "헤드라인 키워드 매칭 → 가중 센티먼트 합산 → 0~100 점수(50=중립). 시장 내재 앵커와 별도 산출.",
            },
            "weekend_block": weekend,
            "top_headlines": [{"title": it["title"], "source": it["source"], "published": it["published"],
                               "link": it["link"], "tickers": it.get("tickers", []), "sentiment": it["sentiment"]}
                              for it in items[:12]],
            "key_takeaway": (
                f"뉴스 {payload.get('item_count', 0)}건(24시간 내 {fresh_24h}건) 수집. "
                f"시장 내재 리스크 점수 {anchor['score']}/100 ({anchor['label']}), "
                f"종목 평균 센티먼트 {avg}/100 → {mood}. "
                f"NVDA {ticker_reports['NVDA']['sentiment_score']}점, SOXL {ticker_reports['SOXL']['sentiment_score']}점."
            ),
        }

        for p in (os.path.join(BASE_DIR, "data", "news_sentiment_report.json"),
                  os.path.join(BASE_DIR, "docs", "news_sentiment_report.json")):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.output_data, f, ensure_ascii=False, indent=2)

        self.confidence_score = 0.8 if payload.get("source_health") == "OK" else 0.6
        self.status = "COMPLETED"
        return self.output_data

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _action_word(headline: str) -> str:
        low = headline.lower()
        for kw, label in (("upgrade", "상향"), ("downgrade", "하향"), ("raises", "목표가 상향"),
                          ("cuts", "목표가 하향"), ("top pick", "Top Pick"), ("initiates", "신규 커버리지"),
                          ("overweight", "비중확대"), ("underweight", "비중축소")):
            if kw in low:
                return label
        return "리서치"

    @staticmethod
    def _rationale(ticker: str, s: Dict[str, Any], anchor: Dict[str, Any]) -> str:
        flow = (s.get("top_headlines") or [{}])[0].get("title", "")
        return (f"뉴스 점수 {s.get('news_score', 50)} + 시장 내재 {anchor['score']} (NQ {anchor['components'].get('nasdaq_fut_pct')}%, "
                f"VIX {anchor['components'].get('vix')}) → 종합 {s.get('sentiment_score', 50)}. 최근 헤드라인: {flow[:70]}")

    @staticmethod
    def _sector_sentiment(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Bond/FX/crypto headline sentiment, separated from the equity scores.

        Same lexicon as the ticker scores (core/news_engine weighted_sentiment),
        but the relevance filter is sector-level instead of ticker-level. A sector
        with no fresh headline reports score 50.0 with `headline_count: 0` — it is
        never given a made-up value.
        """
        SECTORS = {
            "BONDS_FED": {"label": "채권·연준 (국채/금리/FOMC)",
                          "keywords": ["treasury", "yield", "bond", "fomc", "fed ", "federal reserve",
                                       "rate cut", "rate hike", "powell", "duration", "auction", "oas",
                                       "high yield", "credit spread"],
                          "proxy": "TLT / IEF / SHY · 10Y-3M 스프레드"},
            "FX": {"label": "외환 (달러/원/엔/유로)",
                   "keywords": ["dollar", "yen", "euro", "yuan", "currency", "forex", "dxy",
                                "exchange rate", "greenback", "won"],
                   "proxy": "DXY / USD-KRW / USD-JPY"},
            "CRYPTO": {"label": "크립토 (비트코인/이더/알트)",
                       "keywords": ["bitcoin", "btc", "ethereum", "ether", "crypto", "solana", "xrp",
                                    "stablecoin", "coinbase", "spot etf", "binance"],
                       "proxy": "BTC-USD / ETH-USD · IBIT"},
        }
        out: Dict[str, Any] = {}
        for key, cfg in SECTORS.items():
            rel = []
            for it in items:
                low = f"{it.get('title','')} {it.get('summary','')}".lower()
                if any(k in low for k in cfg["keywords"]):
                    rel.append(it)
            # de-duplicate by title, keep the strongest |sentiment|
            seen, uniq = set(), []
            for it in sorted(rel, key=lambda x: -abs(x.get("weighted_sentiment", 0.0))):
                if it.get("title") in seen:
                    continue
                seen.add(it.get("title"))
                uniq.append(it)
            w = sum(it.get("weighted_sentiment", 0.0) for it in uniq[:40])
            score = round(50.0 + 50.0 * (w / (abs(w) + 6.0)), 1) if uniq else 50.0
            bullish = sum(1 for it in uniq if it.get("sentiment") == "POSITIVE")
            bearish = sum(1 for it in uniq if it.get("sentiment") == "NEGATIVE")
            out[key] = {
                "label": cfg["label"], "proxy": cfg["proxy"],
                "sentiment_score": score,
                "grade": news_engine.grade_for(score),
                "headline_count": len(uniq),
                "positive": bullish, "negative": bearish,
                "weighted_sentiment": round(w, 2),
                "top_headlines": [{"title": it["title"], "source": it["source"],
                                   "published": it["published"], "link": it["link"],
                                   "sentiment": it["sentiment"]} for it in uniq[:4]],
            }
        return out

    @staticmethod
    def _weekend_block(snap: Dict[str, Any], clock: Dict[str, Any]) -> Dict[str, Any]:
        q = snap.get("quotes", {})
        btc = q.get("BTC-USD", {})
        eth = q.get("ETH-USD", {})
        return {
            "is_weekend": clock.get("is_weekend", False),
            "crypto_24_7": {"btc": btc.get("last"), "btc_change_pct": btc.get("change_pct"),
                            "eth": eth.get("last"), "eth_change_pct": eth.get("change_pct"),
                            "note": "주말에도 실제 거래되는 유일한 위험자산 프록시"},
            "fx_open": {"usd_krw": (snap.get("fx") or {}).get("usd_krw"),
                        "usd_jpy": (snap.get("fx") or {}).get("usd_jpy")},
            "next_open": clock.get("next_open_kst"),
            "hours_to_open": clock.get("hours_to_next_open"),
            "source_note": "주말에는 지수·주식 선물이 휴장이므로 마지막 금요일 종가와 크립토/외환만 신규 데이터입니다.",
        }
