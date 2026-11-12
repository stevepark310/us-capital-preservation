"""
Inverse Hedging & Bear Market Short Strategy Engine (SOXS, SQQQ)
Enables continuous alpha generation during market downturns by automatically deploying
3X Bear Leveraged ETFs when macroeconomic, volatility (VIX), or sentiment indicators flash risk-off.
"""

from typing import Dict, Any, List

class InverseHedgingEngine:
    def __init__(self):
        self.inverse_universe = {
            "SOXS": {
                "name": "Direxion Daily Semiconductor Bear 3X Shares",
                "underlying": "Philadelphia Semiconductor Index (SOX)",
                "target_long_counterpart": "SOXL",
                "default_allocation_pct": 35.0,
                "tp1_pct": 3.5,
                "tp2_pct": 6.0,
                "sl_pct": -1.8,
                "role": "반도체 섹터 급락 및 악재 시 3배 역방향 수익 추구"
            },
            "SQQQ": {
                "name": "ProShares UltraPro Short QQQ 3X",
                "underlying": "NASDAQ-100 Index",
                "target_long_counterpart": "TQQQ",
                "default_allocation_pct": 25.0,
                "tp1_pct": 2.8,
                "tp2_pct": 5.0,
                "sl_pct": -1.5,
                "role": "나스닥 빅테크 전반 하락 장세 시 3배 인버스 헤지 & 알파 창출"
            }
        }

    def evaluate_downturn_triggers(
        self,
        vix: float,
        dxy: float,
        news_sentiment_score: float,
        tech_indicators: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates 4 core market breakdown indicators:
        1. VIX Spike Trigger: VIX > 22.0 (High Volatility Bear Regime)
        2. DXY Dollar Spike: DXY > 104.5 (Liquidity Drain / Tech Flight)
        3. Sentiment Crash Trigger: Sentiment < 40.0 (Bearish Panic)
        4. Tech Momentum Breakdown: Price < 20 EMA and MACD Bearish Divergence
        """
        triggers = []
        is_downturn = False

        if vix >= 22.0:
            triggers.append(f"VIX 경보 레벨 도달 ({vix:.2f} >= 22.0, 공포 국면 진입)")
        if dxy >= 104.5:
            triggers.append(f"달러 인덱스 급등 ({dxy:.2f} >= 104.5, 기술주 유동성 유출)")
        if news_sentiment_score < 45.0:
            triggers.append(f"뉴스·센티먼트 패닉 침체 (점수 {news_sentiment_score:.1f}/100, STRONG BEARISH)")

        # Technical breakdown check
        if tech_indicators.get("semi_death_cross", False) or tech_indicators.get("nasdaq_below_20ema", False):
            triggers.append("주요 지수 20일 이평선 하향 이탈 및 데드크로스 확인")

        # If 2 or more triggers fire, pivot to BEAR_HEDGE mode
        if len(triggers) >= 2 or vix >= 25.0:
            is_downturn = True

        return {
            "is_downturn": is_downturn,
            "mode": "BEAR_MARKET_INVERSE_ACTIVE (SOXS/SQQQ 숏 포지션 가동)" if is_downturn else "BULL_EXPANSION_LONG_ACTIVE (SOXL/TQQQ 롱 포지션 가동)",
            "triggers_fired": triggers,
            "recommended_inverse_tickers": ["SOXS", "SQQQ"] if is_downturn else []
        }

    def generate_inverse_orders(
        self,
        available_cash_usd: float,
        current_prices: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Generates tactical limit orders for SOXS and SQQQ with bracket exits.
        """
        orders = []
        p_soxs = current_prices.get("SOXS", 18.50)
        p_sqqq = current_prices.get("SQQQ", 9.80)

        # Budget allocation: 40% SOXS, 30% SQQQ, 30% SGOV/Cash buffer
        soxs_budget = available_cash_usd * 0.40
        sqqq_budget = available_cash_usd * 0.30

        qty_soxs = max(1, int(soxs_budget // p_soxs))
        qty_sqqq = max(1, int(sqqq_budget // p_sqqq))

        orders.append({
            "ticker": "SOXS",
            "action": "BUY_INVERSE_ON_BREAKDOWN",
            "qty": qty_soxs,
            "limit_price": p_soxs,
            "take_profit_stage1": f"${round(p_soxs * 1.035, 2)} (+3.5%)",
            "take_profit_stage2": f"${round(p_soxs * 1.060, 2)} (+6.0%)",
            "stop_loss_target": f"${round(p_soxs * 0.982, 2)} (-1.8%)",
            "reason": "반도체 섹터 하락 시그널 발생 시 3배 인버스(SOXS)로 하락장 수익 창출"
        })

        orders.append({
            "ticker": "SQQQ",
            "action": "BUY_INVERSE_ON_BREAKDOWN",
            "qty": qty_sqqq,
            "limit_price": p_sqqq,
            "take_profit_stage1": f"${round(p_sqqq * 1.028, 2)} (+2.8%)",
            "take_profit_stage2": f"${round(p_sqqq * 1.050, 2)} (+5.0%)",
            "stop_loss_target": f"${round(p_sqqq * 0.985, 2)} (-1.5%)",
            "reason": "나스닥 빅테크 전반 하방 변동성 확대 시 3배 인버스(SQQQ)로 포트폴리오 방어 및 알파 획득"
        })

        return orders
