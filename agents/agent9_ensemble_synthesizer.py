"""
Agent 9: Ensemble Synthesizer — 5-axis stance

Target weights follow the evidence chain: approved strategies (Agent 8), sentiment
per ticker (Agent 1), the macro regime (Agent 4) and the valid brackets. When no
strategy passes validation the ensemble holds cash/SGOV rather than deploying
capital for the sake of activity.

2026-11-14 확장: 아래 5개 축을 각각 -100~+100 신호로 정규화해 최종 시장 포지션
태세를 결정한다. 축별 값은 모두 앞단 에이전트의 실측 보고에서 가져온다.

  equity : Agent 1 종목 평균 센티먼트 · Agent 2 진입 점수 · VIX 수준
  bond   : 10Y-3M 스프레드 · HY OAS · TLT 추세 (Agent 4)
  fx     : DXY 수준·변동 · 원/달러 (Agent 4)
  crypto : BTC/ETH 리스크온 프록시 · 주말 변동 (Agent 4)
  cycle  : CUBE-LEI z · Estrella & Mishkin 침체확률 · 신용등급 (Agent 4)

태세(stance) 4단:
  RISK_ON             : 5축 평균 ≥ +15
  CAUTIOUS_ROTATION   : -25 < 평균 < +15
  DEFENSIVE_TBILL     : 평균 ≤ -25 또는 CUBE-LEI 수축위험
  BEAR_HEDGE          : VIX ≥ 24 이고 확인 2개 이상(BEAR ALPHA 게이트) 또는 복합 위험 극단
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent


def _clamp(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


class EnsembleSynthesizerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=9,
            name="Agent 9: 5축 크로스마켓 앙상블 융합가",
            code="CROSS_MARKET_ENSEMBLE",
            role="주식·채권·외환·코인·경기선행지표 5축 신호 융합 → 시장 포지션 태세와 목표 비중 결정",
            description=("주식(센티먼트·모멘텀)·채권(금리차·크레딧)·외환(달러·원화)·코인(리스크온 프록시)·"
                         "경기선행지표(CUBE-LEI·침체확률)를 -100~+100 신호로 정규화해 RISK_ON / CAUTIOUS_ROTATION / "
                         "DEFENSIVE_TBILL / BEAR_HEDGE 중 하나를 결정하고, 국면 계수로 레버리지·현금 비중을 조정합니다."),
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or {}
        macro = context.get("macro") or {}
        alpha = context.get("alpha") or {}
        senti = (context.get("sentiment") or {}).get("tickers", {})
        ledger = context.get("ledger") or {}
        leading = context.get("leading") or {}
        technical_report = context.get("technical_report") or {}
        news_report = context.get("news_report") or {}

        vix = macro.get("vix_level") or 20.0
        regime = macro.get("macro_regime", "UNKNOWN")
        approved = [a["id"] for a in alpha.get("profitable_scenarios", [])]
        mode = alpha.get("recommended_execution_mode", "CASH_ONLY")

        s_soxl = senti.get("SOXL", {}).get("sentiment_score", 50)
        s_tqqq = senti.get("TQQQ", {}).get("sentiment_score", 50)
        s_nvda = senti.get("NVDA", {}).get("sentiment_score", 50)

        # ------------------------------------------------------------ 5 axes
        lei = leading.get("cube_lei") or {}
        probit = leading.get("recession_probit") or {}
        credit = leading.get("credit") or {}
        poi = leading.get("policy") or {}
        lei_z = lei.get("z")
        prob = probit.get("probability_used")
        lei_factor = float(poi.get("max_gross_exposure_factor", 1.0) or 1.0)

        # 1) equity axis
        avg_senti = news_report.get("average_sentiment_score")
        best_entry = (technical_report.get("best_setup") or {}).get("score")
        eq_score = 0.0
        eq_parts = {}
        if avg_senti is not None:
            eq_parts["sentiment"] = (avg_senti - 50) * 2.0          # 0-100 → -100..+100
        if best_entry is not None:
            eq_parts["entry_score"] = (best_entry - 45) * 2.0       # 45점 = 중립 진입점
        eq_parts["vix"] = _clamp((20.0 - float(vix)) * 8.0)          # VIX 20 중립, 25에서 -40
        if eq_parts:
            eq_score = sum(eq_parts.values()) / len(eq_parts)
        equity_axis = {"score": round(_clamp(eq_score), 1), "components": {k: round(v, 1) for k, v in eq_parts.items()},
                       "vix": vix, "sentiment_avg": avg_senti}

        # 2) bond axis
        bond = leading.get("bonds") or {}
        curve_bp = (bond.get("curve_bp") or {}).get("10y3m")
        hy_bp = credit.get("hy_oas_bp")
        tlt_ret20 = ((bond.get("etfs") or {}).get("TLT") or {}).get("ret_20d")
        bond_parts = {}
        if curve_bp is not None:
            bond_parts["curve_10y3m"] = _clamp(float(curve_bp) / 2.0)   # +200bp → +100
        if hy_bp is not None:
            bond_parts["credit_oas"] = _clamp((350.0 - float(hy_bp)) / 1.5)  # 350bp 중립
        if tlt_ret20 is not None:
            bond_parts["tlt_momentum"] = _clamp(float(tlt_ret20) * 10.0)
        bond_score = sum(bond_parts.values()) / len(bond_parts) if bond_parts else 0.0
        bond_axis = {"score": round(_clamp(bond_score), 1), "components": {k: round(v, 1) for k, v in bond_parts.items()},
                     "curve_10y3m_bp": curve_bp, "hy_oas_bp": hy_bp, "credit_level": credit.get("level")}

        # 3) fx axis — strong dollar / weak won is a global risk-off signal
        fx = leading.get("fx") or {}
        dxy_d = fx.get("dxy") or {}
        krw_d = fx.get("usd_krw") or {}
        fx_parts = {}
        if dxy_d.get("last"):
            fx_parts["dxy"] = _clamp((100.0 - float(dxy_d["last"])) * 6.0)  # DXY 100 중립
        if krw_d.get("ret_20d") is not None:
            # 원화 약세(USD/KRW 상승)는 신흥국 리스크오프 → 주식축에 부정
            fx_parts["usd_krw_20d"] = _clamp(-float(krw_d["ret_20d"]) * 20.0)
        fx_score = sum(fx_parts.values()) / len(fx_parts) if fx_parts else 0.0
        fx_axis = {"score": round(_clamp(fx_score), 1), "components": {k: round(v, 1) for k, v in fx_parts.items()},
                   "dxy": dxy_d.get("last"), "krw": krw_d.get("last"), "krw_regime": fx.get("krw_regime")}

        # 4) crypto axis
        crypto = leading.get("crypto") or {}
        c_score = crypto.get("risk_on_proxy_score")
        crypto_axis = {"score": round((float(c_score) - 50) * 2.0, 1) if c_score is not None else 0.0,
                       "risk_on_proxy": c_score, "weekend_return_pct": crypto.get("weekend_return_pct"),
                       "btc_7d_pct": (crypto.get("btc") or {}).get("ret_7d")}

        # 5) cycle axis
        cycle_parts = {}
        if lei_z is not None:
            cycle_parts["lei_z"] = _clamp(float(lei_z) * 50.0)              # 1σ = 50점
        if prob is not None:
            cycle_parts["recession_prob"] = _clamp(-(float(prob) - 20.0) * 2.5)  # 20% 중립
        cycle_score = sum(cycle_parts.values()) / len(cycle_parts) if cycle_parts else 0.0
        cycle_axis = {"score": round(_clamp(cycle_score), 1), "components": {k: round(v, 1) for k, v in cycle_parts.items()},
                      "cube_lei_index": lei.get("index"), "regime": lei.get("regime"),
                      "recession_prob_pct": prob}

        axes = {"equity": equity_axis, "bond": bond_axis, "fx": fx_axis, "crypto": crypto_axis, "cycle": cycle_axis}
        composite = sum(a["score"] for a in axes.values()) / len(axes)

        # BEAR ALPHA gate is preserved exactly: VIX >= 24 + 2 confirmations
        bear_confirmations: List[str] = []
        if vix >= 24:
            bear_confirmations.append(f"VIX {vix:.2f} ≥ 24")
        if (credit.get("hy_oas_bp") or 0) >= 500:
            bear_confirmations.append(f"HY OAS {credit.get('hy_oas_bp')}bp ≥ 500")
        if (curve_bp is not None) and float(curve_bp) < 0:
            bear_confirmations.append(f"10Y-3M {curve_bp}bp 역전")
        if (dxy_d.get("last") or 0) >= 104.5:
            bear_confirmations.append(f"DXY {dxy_d.get('last')} ≥ 104.5")
        if composite <= -50:
            bear_confirmations.append(f"5축 복합 {composite:.0f} ≤ -50")

        if vix >= 24 and len(bear_confirmations) >= 3:   # VIX + 확인 2개
            stance = "BEAR_HEDGE"
        elif composite <= -25 or lei.get("regime") == "CONTRACTION_RISK" or (prob or 0) >= 50:
            stance = "DEFENSIVE_TBILL"
        elif composite >= 15:
            stance = "RISK_ON"
        else:
            stance = "CAUTIOUS_ROTATION"

        stance_target = {
            "RISK_ON": {"equity_gross": 0.70, "bond_parking": 0.30},
            "CAUTIOUS_ROTATION": {"equity_gross": 0.40, "bond_parking": 0.60},
            "DEFENSIVE_TBILL": {"equity_gross": 0.15, "bond_parking": 0.85},
            "BEAR_HEDGE": {"equity_gross": 0.10, "bond_parking": 0.65},
        }[stance]

        # ------------------------------------------------------------ weights
        if vix >= 24 or "CASH_ONLY" in mode:
            weights = {"SGOV": 0.80, "SOXL": 0.10, "TQQQ": 0.10}
            note = "고변동성/검증 미통과 → 현금·SGOV 80% 방어"
        else:
            best_pf = max([a.get("pf") or 1.0 for a in alpha.get("profitable_scenarios", [])] or [1.0])
            gross = min(0.75, 0.35 + 0.15 * min(3.0, best_pf - 1.0))
            gross *= lei_factor                      # 국면 계수로 총노출 축소
            gross = min(gross, stance_target["equity_gross"])
            tilt_soxl = 0.5 + (s_soxl - 50) / 200.0
            tilt_tqqq = 0.5 + (s_tqqq - 50) / 200.0
            norm = max(0.2, tilt_soxl + tilt_tqqq)
            weights = {
                "SOXL": round(gross * tilt_soxl / norm, 3),
                "TQQQ": round(gross * tilt_tqqq / norm, 3),
                "NVDA": 0.0,
                "SGOV": round(1 - gross, 3),
            }
            note = (f"승인 전략 최고 PF {best_pf}·국면계수 {lei_factor}·태세 {stance} → 총노출 {gross:.0%}, "
                    f"센티먼트 틸트 적용")

        if s_nvda >= 65 and ledger.get("positions") and any(p["ticker"] == "NVDA" for p in ledger["positions"]):
            note += " · NVDA 보유분은 실적 전 카탈리스트 홀딩(전량 매도 지시 없음)"

        equity = ledger.get("equity_usd") or 0
        alloc = {k: round(v * equity, 2) for k, v in weights.items()}

        self.output_data = {
            "phase": (context.get("clock") or {}).get("phase"),
            "regime": regime, "vix": vix,
            "approved_strategies": approved,
            "execution_mode": mode,
            "five_axis": axes,
            "composite_score": round(composite, 1),
            "market_stance": stance,
            "stance_target": stance_target,
            "stance_rule": ("RISK_ON ≥ +15 / CAUTIOUS_ROTATION -25~+15 / DEFENSIVE_TBILL ≤ -25 또는 LEI 수축위험 / "
                            "BEAR_HEDGE = VIX ≥ 24 + 확인 2개 이상"),
            "bear_engine_gate": {"preserved": True, "vix_trigger": 24.0, "confirmations_required": 2,
                                 "confirmations_now": bear_confirmations,
                                 "armed": bool(vix >= 24 and len(bear_confirmations) >= 3)},
            "lei_leverage_factor": lei_factor,
            "target_portfolio_weights": weights,
            "target_alloc_usd": alloc,
            "fx_rate": ledger.get("fx_rate"),
            "target_alloc_krw": {k: (int(round(v * (ledger.get("fx_rate") or 1335))) if v else 0) for k, v in alloc.items()},
            "regime_note": note,
            "key_takeaway": (
                f"{note}. 태세 {stance} (5축 복합 {composite:+.0f}: 주식 {equity_axis['score']:+.0f} / 채권 {bond_axis['score']:+.0f} / "
                f"환율 {fx_axis['score']:+.0f} / 코인 {crypto_axis['score']:+.0f} / 사이클 {cycle_axis['score']:+.0f}). "
                f"목표 비중 SOXL {weights.get('SOXL', 0):.0%} / TQQQ {weights.get('TQQQ', 0):.0%} / "
                f"SGOV {weights.get('SGOV', 0):.0%} (총 {equity:.0f} USD 기준)."
            ),
        }
        self.confidence_score = 0.9
        self.status = "COMPLETED"
        return self.output_data
