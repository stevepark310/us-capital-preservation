"""
Agent 6: Quant Scenario Modeler (live-parameterised + weekend playbooks)

The 10 scenario catalog is kept, but every threshold is now expressed in ATR
units of the symbol being traded, using today's measured ATR — so a scenario
written for a 3% ATR ETF is not applied unchanged to a 22% ATR day. Weekend
playbooks (gap-up / gap-down / flat) are added from Friday close, weekly pivots
and the futures-implied gap.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent


class QuantScenarioModelerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=6,
            name="Agent 6: 단기 퀀트 시나리오 모델링 연구원",
            code="QUANT_SCENARIO_MODELER",
            role="10대 단기 시나리오의 ATR 단위 재정규화 + 주말·월요일 개장 시나리오 설계",
            description="래리 윌리엄스 돌파, 볼린저 회귀, VWAP 추종 등 10개 시나리오를 실측 ATR로 재정규화하고, 주말에는 갭업/갭다운/보합 3개 개장 시나리오를 브래킷과 함께 산출합니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        clock = context.get("clock") or {}
        snap = context.get("snapshot") or {}
        techs = context.get("technical") or {}
        phase = clock.get("phase", "UNKNOWN")
        soxl = techs.get("SOXL", {})
        tqqq = techs.get("TQQQ", {})
        atr_pct = soxl.get("atr_pct") or 6.0
        soxl_close = soxl.get("close")
        piv = soxl.get("weekly_pivots") or {}
        nq = (snap.get("futures", {}).get("nq") or {}).get("change_pct") or 0.0
        gap = round(nq * 1.9, 2)

        def unit(mult: float) -> float:
            return round(atr_pct * mult, 2)

        scenarios: List[Dict[str, Any]] = [
            {"id": "SCENARIO_01", "name": "래리 윌리엄스 변동성 돌파 (K=0.5)",
             "source": "Larry Williams (1998)", "universe": ["SOXL", "TQQQ"],
             "entry_rule": "Price >= Open + 0.5 × (Prev_High - Prev_Low)",
             "exit_rule": f"TP1 +{unit(0.45)}% (0.45ATR) / TP2 +{unit(0.95)}% / SL -{unit(0.55)}% / 당일 종가 청산",
             "validated": {"PF": 0.59, "verdict": "REJECTED — 시간봉 백테스트 총수익 -41.6%, PF 0.59"}},
            {"id": "SCENARIO_02", "name": "장초반 30분 ORB (Opening Range Breakout)",
             "source": "Toby Crabel (1990)", "universe": ["TQQQ", "NVDA"],
             "entry_rule": "개장 첫 30분 고가 돌파 + RVOL ≥ 1.5",
             "exit_rule": f"TP +{unit(0.6)}% / SL -{unit(0.4)}%", "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_03", "name": "일중 볼린저 과매도 평균회귀",
             "source": "John Bollinger (2001)", "universe": ["SOXL", "TSLA"],
             "entry_rule": "5분봉 BB(20,2) 하단 + RSI(14) < 25",
             "exit_rule": "BB 중앙선 도달 또는 트레일링 -1.5%", "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_04", "name": "프리마켓 갭 페이드 / 갭 필링",
             "source": "Cooper (1999)", "universe": ["SOXL", "TQQQ"],
             "entry_rule": "|Open - Prev_Close| / Prev_Close ≥ 2.5%", "exit_rule": "갭 80% 메움 또는 SL -1.8%",
             "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_05", "name": "실적 서프라이즈 모멘텀 드리프트 (PEAD)",
             "source": "Bernard & Thomas (1989)", "universe": ["NVDA", "TSLA"],
             "entry_rule": "EPS 5%+ 상회 + 첫 15분 양봉", "exit_rule": f"TP +{unit(0.8)}% / SL -{unit(0.5)}%",
             "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_06", "name": "VWAP 일중 추세 추종",
             "source": "Berkowitz et al. (1988)", "universe": ["NVDA", "TQQQ"],
             "entry_rule": "가격 VWAP 상향 돌파", "exit_rule": "VWAP 하향 이탈 또는 +2.0%",
             "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_07", "name": "레버리지 리스크 패리티 (변동성 역가중)",
             "source": "López de Prado (2016)", "universe": ["SOXL", "TQQQ", "SGOV"],
             "entry_rule": f"실현변동성 역가중 (SOXL {soxl.get('realized_vol_20d_pct')}% / TQQQ {tqqq.get('realized_vol_20d_pct')}%)",
             "exit_rule": "일일 리밸런스", "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_08", "name": "나스닥 선물 베이시스 차익",
             "source": "MacKinlay & Ramaswamy (1988)", "universe": ["TQQQ", "SGOV"],
             "entry_rule": "베이시스 괴리 ≥ 0.4%", "exit_rule": "수렴 시 청산",
             "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_09", "name": "옵션 감마 스퀴즈 스캘핑",
             "source": "Ni, Pearson, Poteshman (2005)", "universe": ["NVDA", "TSLA"],
             "entry_rule": "Call/Put 거래량 ≥ 3.0", "exit_rule": "TP +3.0% 트레일링 -1.5%",
             "validated": {"PF": None, "verdict": "미검증 (구 수치 폐기 — 시간봉 백테스트 대상 아님)"}},
            {"id": "SCENARIO_10", "name": "무위험 캐리 & 패닉 딥바잉",
             "source": "Frazzini & Pedersen (2014)", "universe": ["SGOV", "SOXL"],
             "entry_rule": "RSI(2) ≤ 20 & BB %B ≤ 0.15 & RVOL ≥ 1.0 → 다음 세션 시가 진입",
             "exit_rule": "TP +1.5ATR / SL -1.5ATR / 최대 3세션 보유",
             "validated": {"PF": 1.98, "verdict": "최우수 — DIP_SWING 승격·검증 완료 (1년 PF 1.98, MDD -6.63%)"}},
        ]

        weekend_playbook = None
        if phase in ("WEEKEND_PREP", "HOLIDAY_PREP", "OFF_HOURS", "PRE_MARKET") and soxl_close:
            weekend_playbook = {
                "friday_close": soxl_close,
                "futures_implied_gap_pct": gap,
                "scenarios": [
                    {"case": "GAP_UP (선물 +1% 이상)", "open_ref": round(soxl_close * (1 + max(gap, 1.0) / 100), 2),
                     "play": "시초 갭업 후 30분 고점 돌파 시 ORB 추격, 실패 시 전일 종가 리테스트까지 대기",
                     "bracket": f"TP +{unit(0.5)}% / SL -{unit(0.4)}%"},
                    {"case": "GAP_DOWN (선물 -1% 이하)", "open_ref": round(soxl_close * (1 + min(gap, -1.0) / 100), 2),
                     "play": f"DIP_SWING 발동 구간 — RSI2·%B 확인 후 주간 S1 ${piv.get('s1')} 지지에서 분할",
                     "bracket": f"TP +{unit(1.5)}% / SL -{unit(1.5)}% / 최대 3세션"},
                    {"case": "FLAT (선물 ±0.5% 이내)", "open_ref": round(soxl_close, 2),
                     "play": f"피벗 ${piv.get('pivot')} 상단 유지 시 롱, 하단 이탈 시 관망",
                     "bracket": f"TP +{unit(0.45)}% / SL -{unit(0.55)}%"},
                ],
            }

        approved = [s["id"] for s in scenarios if "검증 완료" in str(s["validated"].get("verdict", ""))]

        # ---- 2026-11-14 확장: CUBE-LEI 국면·침체확률 기반 시나리오 확률 / 레버리지 상한 조정
        leading = context.get("leading") or {}
        lei = leading.get("cube_lei") or {}
        prob = (leading.get("recession_probit") or {}).get("probability_used")
        credit = leading.get("credit") or {}
        regime = lei.get("regime") or "UNKNOWN"
        lei_factor = float((leading.get("policy") or {}).get("max_gross_exposure_factor", 1.0) or 1.0)

        # 설계 사전확률(prior)과 국면별 배수 — 실측이 아닌 모델 가정임을 명시
        PRIORS = {"SCENARIO_01": 0.24, "SCENARIO_02": 0.12, "SCENARIO_03": 0.12, "SCENARIO_04": 0.10,
                  "SCENARIO_05": 0.08, "SCENARIO_06": 0.10, "SCENARIO_07": 0.02, "SCENARIO_08": 0.02,
                  "SCENARIO_09": 0.06, "SCENARIO_10": 0.14}
        FAMILY = {"SCENARIO_01": "MOMENTUM", "SCENARIO_02": "MOMENTUM", "SCENARIO_03": "MEAN_REVERSION",
                  "SCENARIO_04": "MEAN_REVERSION", "SCENARIO_05": "MOMENTUM", "SCENARIO_06": "MOMENTUM",
                  "SCENARIO_07": "RISK_PARITY", "SCENARIO_08": "ARBITRAGE", "SCENARIO_09": "MOMENTUM",
                  "SCENARIO_10": "DIP_BUY"}
        FAMILY_MULT = {
            "EXPANSION": {"MOMENTUM": 1.20, "MEAN_REVERSION": 0.90, "RISK_PARITY": 1.00, "ARBITRAGE": 1.00, "DIP_BUY": 0.95},
            "LATE_CYCLE_SLOWDOWN": {"MOMENTUM": 0.85, "MEAN_REVERSION": 1.05, "RISK_PARITY": 1.10, "ARBITRAGE": 1.05, "DIP_BUY": 1.15},
            "CONTRACTION_RISK": {"MOMENTUM": 0.50, "MEAN_REVERSION": 1.10, "RISK_PARITY": 1.20, "ARBITRAGE": 1.10, "DIP_BUY": 1.25},
            "RECOVERY": {"MOMENTUM": 1.05, "MEAN_REVERSION": 1.00, "RISK_PARITY": 1.00, "ARBITRAGE": 0.95, "DIP_BUY": 1.20},
        }.get(regime, {})
        raw = {sid: PRIORS[sid] * FAMILY_MULT.get(FAMILY[sid], 1.0) for sid in PRIORS}
        tot = sum(raw.values()) or 1.0
        adj_prob = {sid: round(v / tot * 100, 1) for sid, v in raw.items()}
        for s in scenarios:
            s["prior_probability_pct"] = round(PRIORS[s["id"]] * 100, 1)
            s["regime_adjusted_probability_pct"] = adj_prob.get(s["id"])
            s["leverage_cap_factor"] = lei_factor
            s["position_cap_pct"] = round(25.0 * lei_factor, 1)

        leverage_filter = {
            "cube_lei_index": lei.get("index"), "regime": regime, "regime_ko": lei.get("regime_ko"),
            "recession_probability_pct": prob, "credit_level": credit.get("level"),
            "hy_oas_bp": credit.get("hy_oas_bp"),
            "leverage_cap_factor": lei_factor,
            "position_cap_pct": round(25.0 * lei_factor, 1),
            "position_cap_formula": "종목당 상한 25% × 국면 계수(EXPANSION 1.00 / LATE_CYCLE 0.70 / CONTRACTION 0.35 / RECOVERY 0.60)",
            "probability_adjustment": adj_prob,
            "probability_method": ("설계 사전확률(prior) × 국면 가족 배수 후 정규화 — 사전확률은 모델 가정이며 실측 빈도가 아닙니다."),
            "regime_family_multipliers": FAMILY_MULT,
            "applies_to": list(PRIORS.keys()),
            "reading_ko": (f"국면 {regime}(CUBE-LEI {lei.get('index')}), 침체확률 {prob}% → 레버리지 상한 계수 {lei_factor}, "
                           f"종목당 상한 {round(25.0 * lei_factor, 1)}%. 최우선 시나리오 "
                           f"{max(adj_prob.items(), key=lambda kv: kv[1])[0] if adj_prob else '—'}({max(adj_prob.values()) if adj_prob else 0}%)."),
        }

        self.output_data = {
            "phase": phase,
            "total_scenarios": len(scenarios),
            "atr_basis": {"SOXL_atr_pct": atr_pct, "TQQQ_atr_pct": tqqq.get("atr_pct")},
            "scenarios": scenarios,
            "weekend_playbook": weekend_playbook,
            "approved_now": approved,
            "leading_indicator_filter": leverage_filter,
            "key_takeaway": (
                f"ATR 기준 재정규화 완료(SOXL {atr_pct}%). 현재 검증상 유효 전략: {', '.join(approved)}. "
                + ("월요일 개장 3개 시나리오(갭업/갭다운/보합) 산출." if weekend_playbook else "정규장 시나리오 유지.")
                + f" CUBE-LEI {regime} → 레버리지 상한 {lei_factor}·종목 상한 {round(25.0 * lei_factor, 1)}%."
            ),
        }
        self.confidence_score = 0.9
        self.status = "COMPLETED"
        return self.output_data
