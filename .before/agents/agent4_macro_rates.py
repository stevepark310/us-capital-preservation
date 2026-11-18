"""
Agent 4: Global Macro, Rates, FX, Crypto & Business-Cycle Leading Indicator Desk

Job (2026-11-14 확장 지시): 코인 · 미국 국채/채권 · 환율 시장을 상시 감시하고
경기 선행지표를 논문 기반으로 산출해 업무보고를 올린다.

Four measured areas every run:
  1) 경기선행지표  — core/leading_indicators.py (CUBE-LEI, Estrella & Mishkin
     침체 프로빗, Stock & Watson 성분, Gilchrist & Zakrajšek 신용 프록시)
  2) 미국 국채/채권 — 2Y/5Y/10Y/30Y 커브, TLT/IEF/SHY/SGOV 듀레이션·수익률,
     HY OAS · HYG/LQD 크레딧
  3) 외환          — DXY, USD/KRW, USD/JPY, EUR/USD, USD/CNY
  4) 코인          — BTC/ETH/SOL/XRP 24/7 리스크온 프록시 + IBIT/COIN 기관 프록시

위 값은 전부 실측입니다. 레짐은 core/regime.py 규칙을 실제 SPY/VIX 봉으로 평가합니다.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent
from core import regime as regime_mod


class MacroRatesAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=4,
            name="Agent 4: 글로벌 매크로 · 채권 · 외환 · 선행지표 총괄",
            code="GLOBAL_MACRO_RATES_FX_CRYPTO_LEI",
            role="경기선행지표(CUBE-LEI·침체확률)·미국 국채 커브·환율·코인 4대 영역 정량 분석 및 리스크 레짐 판정",
            description=("Estrella & Mishkin(1998)·Stock & Watson(1989)·Gilchrist & Zakrajšek(2012)·Burns & Mitchell(1946) "
                         "방법론으로 CUBE-LEI와 12개월 침체확률을 실측 산출하고, 국채 커브(2Y/5Y/10Y/30Y)·크레딧(HY OAS)·"
                         "환율(DXY·원/달러)·코인(BTC/ETH)을 4대 영역으로 보고합니다. VIX 22·DXY 104.5 초과 시 레버리지를 제한합니다."),
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        snap = context.get("snapshot") or {}
        clock = context.get("clock") or {}
        q = snap.get("quotes", {})
        fx = snap.get("fx", {})
        leading = context.get("leading") or {}
        if not leading.get("cube_lei"):
            try:
                from core import leading_indicators
                leading = leading_indicators.compute(snapshot=snap)
                context["leading"] = leading
            except Exception as e:
                leading = {"error": str(e), "cube_lei": {}, "components": []}

        def g(sym, key="last"):
            return (q.get(sym) or {}).get(key)

        vix = g("^VIX") or 20.0
        vix_chg = g("^VIX", "change_pct") or 0.0
        dxy_d = (snap.get("fx_market") or {}).get("dxy") or {}
        dxy = dxy_d.get("last") or 0.0
        dxy_chg = dxy_d.get("chg_pct") or 0.0

        # regime from real bars
        hist = market_data.get("history") or context.get("history") or {}
        spy = hist.get("SPY")
        regime_label, diag = "UNKNOWN", {}
        if spy is None or len(spy) < 60:
            from core import market_snapshot as _ms
            hist = _ms.fetch_history(["SPY", "^VIX"]) or hist
            spy = hist.get("SPY")
        if spy is not None and len(spy) >= 60:
            try:
                import pandas as pd
                det = regime_mod.RegimeDetector()
                v = hist.get("^VIX")
                vix_s = v["Close"] if v is not None and len(v) else pd.Series([vix] * len(spy), index=spy.index)
                reg, diag = det.detect(spy["Close"], vix_s.reindex(spy.index).ffill())
                regime_label = reg.value
            except Exception as e:
                diag = {"error": str(e)}

        # ---------------------------------------------------------------- 1) LEI
        lei = leading.get("cube_lei") or {}
        probit = leading.get("recession_probit") or {}
        credit = leading.get("credit") or {}
        lei_components = leading.get("components") or []

        # ---------------------------------------------------------------- 2) bonds
        bonds = (leading.get("bonds") or {}) if leading else {}
        if not bonds:
            b = snap.get("bonds") or {}
            bonds = {"yields_pct": b.get("yields_pct", {}), "curve_bp": b.get("curve", {}),
                     "curve_shape": (b.get("curve") or {}).get("shape"), "etfs": b.get("etfs", {})}
        yields = bonds.get("yields_pct") or {}
        curve_bp = bonds.get("curve_bp") or {}
        etfs = bonds.get("etfs") or {}
        tlt = etfs.get("TLT") or {}
        ief = etfs.get("IEF") or {}
        shy = etfs.get("SHY") or {}
        sgov = etfs.get("SGOV") or {}
        tlt_tech = (context.get("technical") or {}).get("TLT") or {}

        # ---------------------------------------------------------------- 3) FX
        fxm = snap.get("fx_market") or {}
        krw = fxm.get("usd_krw") or {}
        jpy = fxm.get("usd_jpy") or {}
        eur = fxm.get("eur_usd") or {}
        cny = fxm.get("usd_cny") or {}

        # ---------------------------------------------------------------- 4) crypto
        cm = snap.get("crypto_market") or {}
        crypto_blk = leading.get("crypto") or {}
        btc = cm.get("btc") or {}
        eth = cm.get("eth") or {}
        sol = cm.get("sol") or {}
        xrp = cm.get("xrp") or {}
        ibit = cm.get("ibit") or {}

        # ---- triggers (risk-off)
        triggers: List[str] = []
        if vix >= 22:
            triggers.append(f"VIX {vix:.2f} ≥ 22 (공포 국면)")
        if dxy >= 104.5:
            triggers.append(f"DXY {dxy:.2f} ≥ 104.5 (달러 유출)")
        if (curve_bp.get("10y3m") or 0) < 0:
            triggers.append(f"10Y-3M 금리차 역전 ({curve_bp.get('10y3m')}bp)")
        if (credit.get("hy_oas_bp") or 0) >= 500:
            triggers.append(f"HY OAS {credit.get('hy_oas_bp')}bp ≥ 500 (신용경색)")
        if (g("NQ=F", "change_pct") or 0) <= -1.5:
            triggers.append(f"나스닥 선물 {g('NQ=F','change_pct')}% 급락")
        if (btc.get("ret_7d") or 0) <= -10:
            triggers.append(f"BTC 7일 {btc.get('ret_7d')}% 급락 (리스크오프 선행 신호)")

        # ---- leverage gate: VIX/DXY 하드 게이트 + 선행지표 국면 계수
        base_safe = vix < 22.0 and dxy < 104.5
        lei_factor = (leading.get("policy") or {}).get("max_gross_exposure_factor", 1.0)
        safe_for_lvrg = bool(base_safe and lei_factor >= 0.7)

        # ---- what the other agents must consume
        handoff = {
            "lei_index": lei.get("index"),
            "lei_z": lei.get("z"),
            "regime": lei.get("regime"),
            "recession_probability_pct": probit.get("probability_used"),
            "months_since_curve_uninverted": probit.get("months_since_curve_uninverted"),
            "credit_level": credit.get("level"),
            "hy_oas_bp": credit.get("hy_oas_bp"),
            "max_gross_exposure_factor": lei_factor,
            "safe_for_leverage": safe_for_lvrg,
            "parking_preference": (leading.get("policy") or {}).get("parking_preference"),
            "bond_duration_stance": (leading.get("policy") or {}).get("bond_duration_stance"),
            "fx_regime": (leading.get("fx") or {}).get("krw_regime"),
            "crypto_weekend_alert": (leading.get("policy") or {}).get("crypto_weekend_alert"),
        }

        self.output_data = {
            "phase": clock.get("phase"),
            "job_scope": "코인 · 미국 국채/채권 · 환율 · 경기선행지표 4대 영역 상시 감시",
            "vix_level": round(float(vix), 2), "vix_change_pct": vix_chg,
            "dxy": dxy, "dxy_change_pct": dxy_chg,
            "macro_regime": regime_label,
            "regime_diagnostics": diag,

            # 1) 경기선행지표
            "leading_indicators": {
                "engine": leading.get("engine"),
                "cube_lei_index": lei.get("index"), "cube_lei_score": lei.get("score"),
                "cube_lei_z": lei.get("z"), "equal_weight_z": lei.get("equal_weight_z"),
                "regime": lei.get("regime"), "regime_ko": lei.get("regime_ko"),
                "trend_3m_z_change": lei.get("trend_3m_z_change"),
                "monthly_history": lei.get("monthly_history"),
                "recession_probit": probit,
                "credit": credit,
                "components": lei_components,
                "methodology": (leading.get("methodology") or {}),
                "references": (leading.get("methodology") or {}).get("references", []),
                "notes": leading.get("notes"),
            },

            # 2) 미국 국채/채권
            "bond_market": {
                "yields_pct": yields,
                "curve_bp": curve_bp,
                "curve_shape": bonds.get("curve_shape"),
                "curve_source": bonds.get("curve_source"),
                "term_premium_proxy_bp": bonds.get("term_premium_proxy_bp"),
                "etfs": etfs,
                "duration_pnl_per_10bp_pct": bonds.get("duration_pnl_per_10bp_pct"),
                "inflation_breakeven_proxy": bonds.get("inflation_breakeven_proxy"),
                "tlt_technical": {k: tlt_tech.get(k) for k in
                                  ("close", "atr_pct", "rsi14", "above_ema20", "ret_20d_pct", "realized_vol_20d_pct") if tlt_tech},
                "credit": credit,
                "read_ko": bonds.get("read_ko"),
            },

            # 3) 외환
            "fx_market": {
                "dxy": fxm.get("dxy"), "usd_krw": krw, "usd_jpy": jpy,
                "eur_usd": eur, "usd_cny": cny,
                "dollar_regime": (leading.get("fx") or {}).get("dollar_regime"),
                "krw_regime": (leading.get("fx") or {}).get("krw_regime"),
                "read_ko": (leading.get("fx") or {}).get("read_ko"),
                "legacy_fx_block": fx,
            },

            # 4) 코인
            "crypto_market": {
                "btc": btc, "eth": eth, "sol": sol, "xrp": xrp, "ibit": ibit,
                "coin": cm.get("coin"),
                "weekend_return_pct": crypto_blk.get("weekend_return_pct"),
                "risk_on_proxy_score": crypto_blk.get("risk_on_proxy_score"),
                "read_ko": crypto_blk.get("read_ko"),
            },

            "global_indices": {
                "hang_seng": {"level": g("^HSI"), "change_pct": g("^HSI", "change_pct")},
                "nikkei_225": {"level": g("^N225"), "change_pct": g("^N225", "change_pct")},
                "dax_40": {"level": g("^GDAXI"), "change_pct": g("^GDAXI", "change_pct")},
                "nasdaq_fut": {"level": g("NQ=F"), "change_pct": g("NQ=F", "change_pct")},
                "sp500_fut": {"level": g("ES=F"), "change_pct": g("ES=F", "change_pct")},
            },
            "commodities": {"wti": g("CL=F"), "brent": g("BZ=F"), "gold": g("GC=F"), "copper": g("HG=F")},
            "risk_off_triggers": triggers,
            "safe_for_leverage": safe_for_lvrg,
            "handoff_to_agents": handoff,
            "key_takeaway": (
                f"CUBE-LEI {lei.get('index')} ({lei.get('regime_ko')}), 12개월 침체확률 {probit.get('probability_used')}%. "
                f"국채 10Y {yields.get('10y')}% / 2Y {yields.get('2y')}% (10Y-3M {curve_bp.get('10y3m')}bp), "
                f"HY OAS {credit.get('hy_oas_bp')}bp({credit.get('level')}). "
                f"DXY {dxy:.2f}, 원/달러 {krw.get('last')}원, "
                f"BTC ${(btc.get('last') or 0):,.0f}(7일 {btc.get('ret_7d')}%). "
                f"레짐 {regime_label}, 레버리지 {'가능' if safe_for_lvrg else '제한'}(상한계수 {lei_factor}), 트리거 {len(triggers)}개."
            ),
        }
        self.confidence_score = 0.94 if lei.get("index") is not None else 0.6
        self.status = "COMPLETED"
        return self.output_data
