"""
Agent 8: Cross-Market Alpha & Risk Evaluator

Job (2026-11-14 확장): 실측 백테스트 지표로 전략을 승인/폐기하는 기존 임무에 더해,
아래 세 가지 크로스마켓 리스크를 **측정값으로** 보고한다.

  1) 환노출 (FX exposure) — USD/KRW 변동이 원화 환산 계좌자산에 주는 영향.
     · 민감도: 평가금액 × 환율변동 (10원 / 1% / 일간 95% VaR)
     · 실측 환율 변동성: 일봉 USD/KRW 수익률 표준편차 × √252
  2) SGOV 단기채 vs 현금 기회비용 — 0-3M T-bill 캐리(실측 SGOV 수익률·^IRX)를
     현금 버퍼에 적용해 월·연 이자수익과 "현금 방치 비용"을 계산.
  3) 코인 주말 변동성 → 월요일 나스닥 갭 리스크 — BTC 주말(Sat+Sun) 수익률과
     그 다음 월요일 QQQ 시가 갭의 실측 회귀(β, R², n)로 이번 주말 리스크를 환산.

모든 값은 실측이며, 표본 부족·미확보는 그대로 표기한다(추정으로 채우지 않음).
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from agents.base import BaseAgent
from core import strategy_approval, market_snapshot


class AlphaEvaluatorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=8,
            name="Agent 8: 크로스마켓 알파·리스크 평가자",
            code="CROSS_MARKET_ALPHA_EVALUATOR",
            role="실측 백테스트 승인/폐기 + 환노출·단기채 캐리·코인 주말 갭 리스크 평가",
            description=("손익비(PF) 1.3 이상·샤프 0.8 이상·MDD 15% 이내·거래 20회 이상을 동시에 만족하는 전략만 승인합니다. "
                         "추가로 USD/KRW 환노출 민감도, SGOV 단기채 캐리 vs 현금 기회비용, BTC 주말 변동→월요일 나스닥 갭 "
                         "회귀를 실측값으로 산출해 Agent 9·10의 포지션 태세와 사이징에 넘깁니다."),
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        bt = context.get("backtest") or {}
        results = bt.get("backtest_results") or bt.get("results") or []
        phase = (context.get("clock") or {}).get("phase")
        snap = context.get("snapshot") or {}
        led = context.get("ledger") or {}
        leading = context.get("leading") or {}

        approved, rejected = [], []
        for r in results:
            m = r.get("metrics") or {}
            pf = m.get("profit_factor") or 0
            sh = m.get("sharpe_ratio") or 0
            mdd = abs(m.get("mdd_pct") or 0)
            n = m.get("total_trades") or 0
            ok = pf >= 1.3 and sh >= 0.8 and mdd <= 15 and n >= 20
            row = {"id": r.get("id"), "name": r.get("name"), "pf": pf, "sharpe": sh, "mdd_pct": -mdd,
                   "trades": n, "win_rate_pct": m.get("win_rate_pct"),
                   "avg_win_pct": m.get("avg_win_pct"), "avg_loss_pct": m.get("avg_loss_pct"),
                   "return_pct": m.get("total_return_pct")}
            if ok:
                row["alpha_source"] = self._alpha_source(r.get("id"))
                row["max_size_guidance"] = "리스크 2%/거래 · 종목 비중 상한 25%"
                approved.append(row)
            else:
                row["reject_reason"] = self._reason(pf, sh, mdd, n)
                rejected.append(row)

        approved.sort(key=lambda x: -(x["pf"] or 0))
        recommended = approved[0]["id"] if approved else "CASH_ONLY"
        overnight = strategy_approval.plan_block()

        # ---------------------------------------------------------- 1) FX exposure
        fx = (snap.get("fx_market") or {}).get("usd_krw") or {}
        fx_rate = led.get("fx_rate") or fx.get("last")
        equity_usd = float(led.get("equity_usd") or 0.0)
        cash_usd = float(led.get("cash_usd") or 0.0)
        fx_sens = {}
        if fx_rate:
            fx_sens = {
                "fx_rate": fx_rate,
                "equity_usd": round(equity_usd, 2),
                "equity_krw": int(round(equity_usd * float(fx_rate))),
                "per_10won_krw": int(round(equity_usd * 10)),
                "per_1pct_krw": int(round(equity_usd * float(fx_rate) * 0.01)),
                "per_10won_pct_of_equity": round(10.0 / float(fx_rate) * 100, 2),
            }
        fx_vol = self._fx_volatility(context)
        fx_regime = (leading.get("fx") or {}).get("krw_regime")
        fx_exposure = {
            "sensitivity": fx_sens,
            "realized_vol": fx_vol,
            "regime": fx_regime,
            "krw_20d_change_pct": fx.get("ret_20d"),
            "daily_var_95_krw": (int(round(equity_usd * float(fx_rate) * (fx_vol or {}).get("daily_sigma_pct", 0) / 100 * 1.65))
                                 if fx_rate and (fx_vol or {}).get("daily_sigma_pct") else None),
            "hedge_note": ("USD 자산을 보유하는 것 자체가 원화 약세 방어선입니다. 원/달러가 10원 오르면 "
                           f"평가금이 약 {fx_sens.get('per_10won_krw')}원 늘어납니다(계좌 기준)."
                           if fx_sens else "환율 데이터 미확보"),
            "measured_not_estimated": True,
        }

        # ---------------------------------------------------------- 2) SGOV vs cash
        carry = self._sgov_carry(snap, leading, cash_usd, fx_rate)
        sgov_vs_cash = carry

        # ---------------------------------------------------------- 3) crypto weekend gap
        gap_risk = self._crypto_weekend_gap_risk(context)

        self.output_data = {
            "phase": phase,
            "criteria": {"profit_factor_min": 1.3, "sharpe_min": 0.8, "max_dd_pct": 15, "min_trades": 20},
            "top_profitable_count": len(approved),
            "profitable_scenarios": approved,
            "rejected_scenarios": rejected,
            "recommended_execution_mode": recommended,
            "overnight_approval": overnight,
            "approval_note": (
                "승인 기준을 통과한 전략이 없으면 현금(SGOV) 보유가 최적입니다."
                if not approved else f"{len(approved)}개 전략 승인. 최우선 {recommended}."
            ) + (f" 오버나이트 보유(DIP_SWING) {overnight['status_ko']}." if recommended == "DIP_SWING" else ""),
            "fx_exposure": fx_exposure,
            "sgov_vs_cash": sgov_vs_cash,
            "crypto_weekend_gap_risk": gap_risk,
            "cross_market_summary": {
                "fx": fx_exposure.get("hedge_note"),
                "carry": sgov_vs_cash.get("reading_ko"),
                "crypto_gap": gap_risk.get("reading_ko"),
            },
            "key_takeaway": (
                f"승인 {len(approved)}개 / 폐기 {len(rejected)}개. 최우선 {recommended}"
                + (f" (PF {approved[0]['pf']}, MDD {approved[0]['mdd_pct']}%)" if approved else "")
                + f" | 환노출: 10원당 {fx_sens.get('per_10won_krw', '—')}원"
                + f" | SGOV 캐리: {sgov_vs_cash.get('annual_yield_pct')}%/년"
                + f" | 코인주말→월요일갭 β {gap_risk.get('beta')}"
            ),
        }
        self.confidence_score = 0.92
        self.status = "COMPLETED"
        return self.output_data

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _fx_volatility(context: Dict[str, Any]) -> Dict[str, Any]:
        """Realized USD/KRW volatility from the actual daily bars."""
        try:
            hist = context.get("history") or {}
            f = hist.get("KRW=X")
            if f is None or len(f) < 60:
                f = market_snapshot.fetch_history(["KRW=X"]).get("KRW=X")
            if f is None or len(f) < 60:
                return {}
            c = f["Close"].astype(float).dropna()
            r = c.pct_change().dropna()
            daily = float(r.std() * 100)
            return {
                "daily_sigma_pct": round(daily, 3),
                "annualized_pct": round(daily * np.sqrt(252), 2),
                "window_days": int(len(r)),
                "max_1d_move_pct": round(float(r.abs().max() * 100), 2),
                "bar_date": str(f.index[-1].date()),
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _sgov_carry(snap: Dict[str, Any], leading: Dict[str, Any],
                    cash_usd: float, fx_rate: Optional[float]) -> Dict[str, Any]:
        """Measured short-T-bill carry vs holding cash."""
        out: Dict[str, Any] = {"cash_usd": round(cash_usd, 2), "fx_rate": fx_rate}
        # 1) SGOV 거래소 종가 수익률(63거래일) — 주의: SGOV의 월분배금은 종가에 포함되지 않아
        #    가격만 보면 총수익이 과소평가된다. 그래서 캐리 계산은 T-bill 금리를 우선한다.
        try:
            hist = market_snapshot.fetch_history(["SGOV"])
            f = hist.get("SGOV")
            if f is not None and len(f) > 70:
                c = f["Close"].astype(float).dropna()
                r63 = float(c.iloc[-1] / c.iloc[-64] - 1)
                out["sgov_price_return_63d_pct"] = round(r63 * 100, 3)
                out["sgov_price_return_note"] = "종가 기준 가격수익률(월분배금 제외) — 총수익 아님"
        except Exception:
            pass
        # 2) 3M T-bill 금리(^IRX 할인율 → 쿠폰등가 환산) — 캐리의 기준
        irx = ((snap.get("quotes") or {}).get("^IRX") or {}).get("last")
        if irx is not None:
            out["t_bill_3m_discount_pct"] = irx
            out["t_bill_3m_coupon_equiv_pct"] = round(float(irx) / (1 - float(irx) * 91 / 36000), 3)
        # 3) SGOV 30일 SEC 수익률은 무료 소스에 없으므로 T-bill 쿠폰등가를 사용(가정 없이 근사만)
        yield_pct = out.get("t_bill_3m_coupon_equiv_pct")
        out["yield_basis"] = "^IRX 3개월 할인율 → 쿠폰등가 환산 (SGOV 총수익률 아님)"
        if yield_pct and cash_usd:
            monthly = cash_usd * (yield_pct / 100) / 12
            out.update({
                "annual_yield_pct": round(float(yield_pct), 2),
                "annual_interest_usd": round(cash_usd * float(yield_pct) / 100, 2),
                "monthly_interest_usd": round(monthly, 2),
                "daily_interest_usd": round(cash_usd * float(yield_pct) / 100 / 365, 3),
                "annual_interest_krw": (int(round(cash_usd * float(yield_pct) / 100 * float(fx_rate)))
                                        if fx_rate else None),
                "opportunity_cost_if_cash_sits_idle_krw_per_month": (int(round(monthly * float(fx_rate)))
                                                                     if fx_rate else None),
                "net_of_fee_note": "SGOV 운용보수 0.07%를 빼면 실질 캐리 ≈ "
                                   f"{round(float(yield_pct) - 0.07, 2)}%/년",
            })
            out["annual_yield_net_pct"] = round(float(yield_pct) - 0.07, 2)
        bond_etfs = ((leading.get("bonds") or {}).get("etfs") or {})
        sgov_etf = bond_etfs.get("SGOV") or {}
        out["sgov_etf_ret_20d_pct"] = sgov_etf.get("ret_20d")
        out["reading_ko"] = (f"3개월 T-bill 쿠폰등가 {out.get('annual_yield_pct')}%(보수 차감 {out.get('annual_yield_net_pct')}%) — "
                             f"현금 ${cash_usd:,.2f}를 SGOV에 파킹하면 월 ${out.get('monthly_interest_usd', 0):,.2f}"
                             f"({out.get('opportunity_cost_if_cash_sits_idle_krw_per_month', 0):,}원)/연 "
                             f"${out.get('annual_interest_usd', 0):,.2f}의 무위험 이자. 방치 시 그만큼 기회비용."
                             if yield_pct else "단기금리 미확보")
        return out

    @staticmethod
    def _crypto_weekend_gap_risk(context: Dict[str, Any]) -> Dict[str, Any]:
        """BTC weekend return -> next Monday QQQ open gap, measured regression."""
        out: Dict[str, Any] = {}
        try:
            hist = context.get("history") or {}
            btc = hist.get("BTC-USD")
            if btc is None:
                btc = market_snapshot.fetch_history(["BTC-USD"]).get("BTC-USD")
            qqq = hist.get("QQQ")
            if qqq is None:
                qqq = market_snapshot.fetch_history(["QQQ"]).get("QQQ")
            if btc is None or qqq is None or len(btc) < 60 or len(qqq) < 60:
                return {"available": False, "note": "BTC/QQQ 이력 미확보"}
            b = btc["Close"].astype(float).dropna()
            b.index = pd.to_datetime(b.index).normalize()
            b = b[~b.index.duplicated(keep="last")]
            q = qqq.copy()
            q.index = pd.to_datetime(q.index).normalize()
            q = q[~q.index.duplicated(keep="last")]

            rows = []
            for fri in [d for d in q.index if d.weekday() == 4]:
                mon = fri + pd.Timedelta(days=3)
                if mon not in q.index:
                    continue
                # Friday close -> last BTC print before Monday open
                fut = [d for d in b.index if fri < d < mon]
                if not fut:
                    continue
                fri_btc = b[b.index <= fri]
                if len(fri_btc) == 0:
                    continue
                wk_ret = (float(b.loc[fut[-1]]) / float(fri_btc.iloc[-1]) - 1) * 100
                prev_close = float(q.loc[fri, "Close"])
                mon_open = float(q.loc[mon, "Open"])
                gap = (mon_open / prev_close - 1) * 100
                rows.append({"weekend_btc_ret_pct": wk_ret, "monday_qqq_gap_pct": gap})
            if len(rows) < 10:
                return {"available": False, "note": f"표본 부족(n={len(rows)})"}
            x = np.array([r["weekend_btc_ret_pct"] for r in rows])
            y = np.array([r["monday_qqq_gap_pct"] for r in rows])
            beta, alpha = np.polyfit(x, y, 1)
            pred = alpha + beta * x
            ss_res = float(((y - pred) ** 2).sum())
            ss_tot = float(((y - y.mean()) ** 2).sum())
            r2 = 1 - ss_res / ss_tot if ss_tot else None
            corr = float(np.corrcoef(x, y)[0, 1])
            # this weekend's BTC move (last Friday close -> latest print)
            latest = b.index[-1]
            this_fri = [d for d in b.index if d.weekday() == 4 and d < latest]
            wk_now = ((float(b.iloc[-1]) / float(b.loc[this_fri[-1]]) - 1) * 100) if this_fri else None
            out = {
                "available": True,
                "sample_weeks": len(rows),
                "beta_monday_gap_per_1pct_weekend_btc": round(float(beta), 4),
                "alpha_pct": round(float(alpha), 3),
                "r_squared": round(float(r2), 3) if r2 is not None else None,
                "correlation": round(corr, 3),
                "weekend_btc_ret_now_pct": round(wk_now, 2) if wk_now is not None else None,
                "implied_monday_qqq_gap_pct": (round(float(alpha) + float(beta) * wk_now, 2)
                                               if wk_now is not None else None),
                "observed_max_gap_pct": round(float(np.max(np.abs(y))), 2),
                "note": ("BTC 주말 수익률 → 다음 월요일 QQQ 시가 갭의 단순 OLS 회귀(일봉 실측). "
                         "표본이 1년 내외이므로 통계적 신뢰도는 제한적이며, 방향성 경보용으로만 사용합니다."),
            }
            out["reading_ko"] = (
                f"BTC 주말 {out['weekend_btc_ret_now_pct']}% → 월요일 나스닥 갭 추정 {out['implied_monday_qqq_gap_pct']}% "
                f"(β {out['beta_monday_gap_per_1pct_weekend_btc']}, R² {out['r_squared']}, n={out['sample_weeks']}주)")
            return out
        except Exception as e:
            return {"available": False, "note": f"회귀 실패: {e}"}

    @staticmethod
    def _alpha_source(sid: str) -> str:
        return {
            "DIP_SWING": "과매도 패닉 매수 후 2~3세션 반등 포획 — 진입·청산 모두 ATR 대비 대칭 손익비",
            "INTRADAY_UPGRADED": "ATR 동적 브래킷과 체결가 재산정으로 손실 거래 크기를 축소",
            "INTRADAY_BASELINE": "고정 브래킷 장중 회전",
        }.get(sid, "실측 지표 통과")

    @staticmethod
    def _reason(pf: float, sh: float, mdd: float, n: int) -> str:
        reasons = []
        if pf < 1.3:
            reasons.append(f"손익비 {pf} < 1.3")
        if sh < 0.8:
            reasons.append(f"샤프 {sh} < 0.8")
        if mdd > 15:
            reasons.append(f"MDD {mdd:.1f}% > 15%")
        if n < 20:
            reasons.append(f"거래 {n}회 < 20회")
        return ", ".join(reasons)
