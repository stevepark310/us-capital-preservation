# --- CROSS-MARKET / LEADING INDICATOR PANEL (2026-11-14) ---------------------
# Built from the LIVE agent reports in data/agent_feed.json -> cross_market.
# Nothing here is a stored price: every number was fetched in this pipeline run.


def _cross_market_panel(feed_data: dict) -> str:
    cm = feed_data.get("cross_market") or {}
    lei = cm.get("cube_lei") or {}
    probit = cm.get("recession_probit") or {}
    comps = cm.get("components") or []
    refs = cm.get("references") or []
    bonds = cm.get("bonds") or {}
    fx = cm.get("fx") or {}
    crypto = cm.get("crypto") or {}
    credit = cm.get("credit") or {}
    a8 = cm.get("fx_exposure") or {}
    sgov = cm.get("sgov_vs_cash") or {}
    gap = cm.get("crypto_weekend_gap_risk") or {}
    axes = cm.get("five_axis") or {}
    adjustments = cm.get("agent_adjustments") or []

    def _f(x, d=2, suffix=""):
        try:
            if x is None:
                return "—"
            return f"{float(x):,.{d}f}{suffix}"
        except Exception:
            return "—"

    def _signed(x, d=2):
        try:
            return f"{float(x):+,.{d}f}"
        except Exception:
            return "—"

    regime = lei.get("regime") or "UNKNOWN"
    regime_color = {"EXPANSION": "text-emerald-400 border-emerald-500/30 bg-emerald-500/10",
                    "LATE_CYCLE_SLOWDOWN": "text-amber-300 border-amber-500/30 bg-amber-500/10",
                    "CONTRACTION_RISK": "text-rose-400 border-rose-500/30 bg-rose-500/10",
                    "RECOVERY": "text-cyan-300 border-cyan-500/30 bg-cyan-500/10"}.get(
        regime, "text-slate-300 border-slate-600/40 bg-slate-700/20")
    prob = probit.get("probability_used")
    prob_color = ("text-emerald-400" if (prob or 0) < 20 else
                  "text-amber-300" if (prob or 0) < 40 else "text-rose-400")

    # monthly LEI z polyline (measured history, 13 months)
    hist = lei.get("monthly_history") or []
    spark = ""
    if len(hist) >= 3:
        zs = [float(h.get("z") or 0.0) for h in hist]
        lo, hi = min(min(zs), -1.0), max(max(zs), 1.0)
        span = (hi - lo) or 1.0
        w, h = 260.0, 54.0
        pts = []
        for i, z in enumerate(zs):
            x = i / (len(zs) - 1) * w
            y = h - ((z - lo) / span) * h
            pts.append(f"{x:.1f},{y:.1f}")
        zero_y = h - ((0 - lo) / span) * h
        spark = (f'<svg viewBox="0 0 {w:.0f} {h:.0f}" class="w-full h-14" preserveAspectRatio="none">'
                 f'<line x1="0" y1="{zero_y:.1f}" x2="{w:.0f}" y2="{zero_y:.1f}" stroke="#334155" stroke-width="1" stroke-dasharray="3,3"/>'
                 f'<polyline points="{" ".join(pts)}" fill="none" stroke="#38bdf8" stroke-width="2"/></svg>')
        spark_months = f"{hist[0].get('month')} → {hist[-1].get('month')}"

    comp_rows = []
    for c in comps:
        ok = c.get("available")
        z = c.get("z")
        zcls = "text-emerald-400" if (z or 0) * (c.get("sign") or 1) > 0 else "text-rose-400"
        comp_rows.append(
            f'<tr class="border-t border-slate-800/70">'
            f'<td class="py-1.5 pr-2 text-slate-300">{c.get("name")}</td>'
            f'<td class="py-1.5 pr-2 font-mono text-slate-200 text-right">{_f(c.get("value"), 4)}</td>'
            f'<td class="py-1.5 pr-2 font-mono text-right {zcls}">{_signed(z) if z is not None else "—"}</td>'
            f'<td class="py-1.5 pr-2 font-mono text-slate-400 text-right">{c.get("weight")}</td>'
            f'<td class="py-1.5 pr-2 font-mono text-slate-300 text-right">{_signed(c.get("contribution"), 3)}</td>'
            f'<td class="py-1.5 text-[10px] text-slate-500">{c.get("source")}</td>'
            f'</tr>')
    ref_items = "".join(
        f'<li class="leading-relaxed"><span class="text-cyan-300 font-semibold">{r.get("key")}</span> — '
        f'{r.get("cite")} <span class="text-slate-500">→ {r.get("used_for")}</span></li>' for r in refs)

    bond_cells = []
    for t in ("TLT", "IEF", "SHY", "SGOV", "TIP", "HYG", "LQD"):
        e = (bonds.get("etfs") or {}).get(t) or {}
        if not e:
            continue
        chg = e.get("chg_pct") or 0
        bond_cells.append(
            f'<div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">'
            f'<div class="text-[10px] text-slate-400 font-sans">{t} · 듀레이션 {e.get("duration_years")}년</div>'
            f'<div class="text-xs md:text-sm font-bold text-slate-100 mt-0.5 font-mono">${_f(e.get("last"))}</div>'
            f'<div class="text-[10px] font-bold font-mono {"text-emerald-400" if chg >= 0 else "text-rose-400"}">{_signed(chg)}%</div>'
            f'<div class="text-[10px] text-slate-500 font-mono">20일 {_signed(e.get("ret_20d"))}% · 63일 {_signed(e.get("ret_63d"))}%</div>'
            f'</div>')
    fx_cells = []
    for name, key in (("DXY", "dxy"), ("USD/KRW", "usd_krw"), ("USD/JPY", "usd_jpy"),
                      ("EUR/USD", "eur_usd"), ("USD/CNY", "usd_cny")):
        v = fx.get(key) or {}
        if v.get("last") is None:
            continue
        chg = v.get("chg_pct") or 0
        fx_cells.append(
            f'<div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">'
            f'<div class="text-[10px] text-slate-400 font-sans">{name}</div>'
            f'<div class="text-xs md:text-sm font-bold text-slate-100 mt-0.5 font-mono">{_f(v.get("last"), 4)}</div>'
            f'<div class="text-[10px] font-bold font-mono {"text-emerald-400" if chg >= 0 else "text-rose-400"}">{_signed(chg)}%</div>'
            f'<div class="text-[10px] text-slate-500 font-mono">20일 {_signed(v.get("ret_20d"))}%</div>'
            f'</div>')
    crypto_cells = []
    for name, key, d in (("BTC", "btc", 0), ("ETH", "eth", 0), ("SOL", "sol", 2),
                         ("XRP", "xrp", 4), ("IBIT", "ibit", 2), ("COIN", "coin", 2)):
        v = crypto.get(key) or {}
        if v.get("last") is None:
            continue
        chg = v.get("chg_pct") or 0
        crypto_cells.append(
            f'<div class="bg-slate-900/90 border border-slate-800 p-2.5 rounded-xl">'
            f'<div class="text-[10px] text-slate-400 font-sans">{name}</div>'
            f'<div class="text-xs md:text-sm font-bold text-slate-100 mt-0.5 font-mono">{_f(v.get("last"), d)}</div>'
            f'<div class="text-[10px] font-bold font-mono {"text-emerald-400" if chg >= 0 else "text-rose-400"}">{_signed(chg)}%</div>'
            f'<div class="text-[10px] text-slate-500 font-mono">' +
            (f'7일 {_signed(v.get("ret_7d"))}%' if v.get("ret_7d") is not None else f'20일 {_signed(v.get("ret_20d"))}%') +
            f'</div></div>')
    adj_rows = "".join(
        f'<li><span class="text-cyan-300 font-semibold">Agent {a.get("agent")}</span> — {a.get("change")}</li>'
        for a in adjustments)
    axis_labels = {"equity": "주식", "bond": "채권", "fx": "외환", "crypto": "코인", "cycle": "사이클"}
    axis_chips = "".join(
        f'<span class="px-2 py-1 rounded-lg border border-slate-700 bg-slate-900/80 font-mono text-[11px]">'
        f'{axis_labels.get(k, k)} <span class="{"text-emerald-400" if (v.get("score") or 0) >= 0 else "text-rose-400"}">'
        f'{_signed(v.get("score"), 0)}</span></span>'
        for k, v in axes.items())

    return f"""
    <!-- Cross-Market & Leading Indicator Panel (채권·환율·코인 + 경기선행지표) -->
    <div class="bg-gradient-to-r from-slate-950 via-indigo-950/40 to-slate-950 border border-indigo-500/25 rounded-2xl p-4 md:p-5 space-y-4 shadow-xl">
      <div class="flex flex-col lg:flex-row justify-between lg:items-start gap-3 pb-3 border-b border-slate-800/80">
        <div class="flex items-start gap-3">
          <div class="w-11 h-11 rounded-xl bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center text-xl">🌐</div>
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-sm md:text-base font-bold text-white">채권 · 환율 · 코인 & 경기선행지표 (CUBE-LEI)</h2>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded-full border {regime_color}">{regime} · {lei.get("regime_ko")}</span>
              <span class="bg-slate-800/80 text-slate-300 text-[10px] font-mono px-2 py-0.5 rounded-full border border-slate-700">태세 {cm.get("market_stance")}</span>
            </div>
            <p class="text-[11px] text-slate-400 mt-1 leading-relaxed">
              Estrella &amp; Mishkin (1998) · Stock &amp; Watson (1989) · Gilchrist &amp; Zakrajšek (2012) · Burns &amp; Mitchell (1946)
              방법론으로 실측 산출한 종합 선행지수입니다. 기준 {cm.get("generated_at")} KST · 하드코딩 값 없음.
            </p>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-[11px] font-mono">{axis_chips}</div>
      </div>

      <!-- KPI row -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        <div class="bg-slate-900/90 border {regime_color.split(' ')[1] if len(regime_color.split(' ')) > 1 else 'border-slate-800'} rounded-xl p-3">
          <div class="text-[10px] text-slate-400 font-sans">CUBE-LEI 지수 <span class="text-slate-500">(100=추세, 1σ=10pt)</span></div>
          <div class="text-lg md:text-xl font-black text-white font-mono">{_f(lei.get("index"), 1)}</div>
          <div class="text-[10px] text-slate-400 font-mono">z {_signed(lei.get("z"), 2)} · 0~100 점수 {_f(lei.get("score"), 1)}</div>
          <div class="text-[10px] text-slate-500 font-mono">3개월 변화 {_signed(lei.get("trend_3m"), 2)}z · 동일가중 {_signed(lei.get("equal_weight_z"), 2)}z</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-3">
          <div class="text-[10px] text-slate-400 font-sans">12개월 침체확률 (Estrella–Mishkin)</div>
          <div class="text-lg md:text-xl font-black {prob_color} font-mono">{_f(prob, 1)}%</div>
          <div class="text-[10px] text-slate-400 font-mono">Φ(-0.6045 − 0.8180×{_f((probit.get("spread_10y3m_bp") or 0)/100, 2)})</div>
          <div class="text-[10px] text-slate-500 font-mono">역전 해소 후 {_f(probit.get("months_since_curve_uninverted"), 1)}개월</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-3">
          <div class="text-[10px] text-slate-400 font-sans">미 국채 커브 (10Y-3M / 10Y-2Y)</div>
          <div class="text-lg md:text-xl font-black text-cyan-300 font-mono">{_f((bonds.get("curve_bp") or {}).get("10y3m"), 1)}bp</div>
          <div class="text-[10px] text-slate-400 font-mono">10Y {_f((bonds.get("yields_pct") or {}).get("10y"))}% · 2Y {_f((bonds.get("yields_pct") or {}).get("2y"))}% · 30Y {_f((bonds.get("yields_pct") or {}).get("30y"))}%</div>
          <div class="text-[10px] text-slate-500 font-mono">{bonds.get("curve_shape")} · 10Y-2Y {_f((bonds.get("curve_bp") or {}).get("10y2y"), 1)}bp</div>
        </div>
        <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-3">
          <div class="text-[10px] text-slate-400 font-sans">크레딧 스트레스 (HY OAS)</div>
          <div class="text-lg md:text-xl font-black text-amber-300 font-mono">{_f(credit.get("hy_oas_bp"), 1)}bp</div>
          <div class="text-[10px] text-slate-400 font-mono">{credit.get("level")} · z {_signed(credit.get("hy_oas_z_5y"), 2)} · GZ 프록시 z {_signed(credit.get("gz_proxy_z"), 2)}</div>
          <div class="text-[10px] text-slate-500 font-mono">HYG-IEF 63일 {_signed(credit.get("hyg_ief_rel_63d_pct"), 2)}%p</div>
        </div>
      </div>

      <!-- LEI history sparkline + component table -->
      <div class="grid grid-cols-1 lg:grid-cols-5 gap-3">
        <div class="lg:col-span-2 self-start bg-slate-950/70 border border-slate-800 rounded-xl p-3">
          <div class="text-[11px] font-bold text-slate-300">CUBE-LEI z 추이 (월별 실측)</div>
          {spark if spark else '<div class="text-[10px] text-slate-500 py-6">이력 부족</div>'}
          <div class="text-[10px] text-slate-500 font-mono">{spark_months if spark else ""}</div>
          <div class="mt-2 text-[10px] text-slate-400 leading-relaxed">
            국면 규칙: z ≤ −1.0 또는 P ≥ 50% → 수축위험 / z &lt; 0 &amp; 3개월 변화 &gt; 0 → 회복 /
            z &lt; 0.3 또는 3개월 변화 ≤ −1.0 → 후기 둔화 / 그 외 확장.
          </div>
        </div>
        <div class="lg:col-span-3 bg-slate-950/70 border border-slate-800 rounded-xl p-3 overflow-x-auto">
          <div class="text-[11px] font-bold text-slate-300 mb-1">선행지표 성분 (가중 · 표준화 · 기여도)</div>
          <table class="w-full text-[11px] font-sans">
            <thead><tr class="text-slate-500 text-[10px]">
              <th class="text-left font-semibold pb-1">성분</th><th class="text-right font-semibold pb-1">값</th>
              <th class="text-right font-semibold pb-1">z</th><th class="text-right font-semibold pb-1">가중</th>
              <th class="text-right font-semibold pb-1">기여</th><th class="text-left font-semibold pb-1 pl-2">출처</th>
            </tr></thead>
            <tbody>{''.join(comp_rows) or '<tr><td class="text-slate-500 py-2" colspan="6">성분 데이터 없음</td></tr>'}</tbody>
          </table>
          <div class="mt-2 text-[10px] text-slate-500 leading-relaxed">
            가중치: {(cm.get("methodology") or {}).get("weight_note", "CUBE 설계값")}
          </div>
        </div>
      </div>

      <!-- Bonds / FX / Crypto -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="text-[11px] font-bold text-slate-300">🇺🇸 미국 국채·채권</div>
            <div class="text-[10px] text-slate-500 font-mono">{_signed((bonds.get("inflation_breakeven_proxy") or {}).get("tip_minus_ief_63d_pct"))}%p TIP-IEF</div>
          </div>
          <div class="grid grid-cols-2 gap-2">{''.join(bond_cells)}</div>
          <div class="text-[10px] text-slate-500 leading-relaxed">금리 10bp 상승 시 듀레이션 근사 가격영향: TLT {_f((bonds.get("duration_pnl_per_10bp_pct") or {}).get("TLT"))}% / IEF {_f((bonds.get("duration_pnl_per_10bp_pct") or {}).get("IEF"))}% / SHY {_f((bonds.get("duration_pnl_per_10bp_pct") or {}).get("SHY"))}%</div>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="text-[11px] font-bold text-slate-300">💱 외환 시장</div>
            <div class="text-[10px] text-slate-500 font-mono">{fx.get("dollar_regime")} · {fx.get("krw_regime")}</div>
          </div>
          <div class="grid grid-cols-2 gap-2">{''.join(fx_cells)}</div>
          <div class="text-[10px] text-slate-500 leading-relaxed">환노출: 원/달러 10원 변동 = 계좌 {_f((a8.get("sensitivity") or {}).get("per_10won_krw"), 0)}원 · 일간 95% VaR {_f(a8.get("daily_var_95_krw"), 0)}원</div>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <div class="text-[11px] font-bold text-slate-300">🪙 코인 시장 (24/7)</div>
            <div class="text-[10px] text-slate-500 font-mono">리스크온 {_f(crypto.get("risk_on_proxy_score"), 1)}/100</div>
          </div>
          <div class="grid grid-cols-2 gap-2">{''.join(crypto_cells)}</div>
          <div class="text-[10px] text-slate-500 leading-relaxed">주말 BTC {_signed(crypto.get("weekend_return_pct"))}% → 월요일 나스닥 갭 추정 {_signed(gap.get("implied_monday_qqq_gap_pct"))}% (β {_f(gap.get("beta_monday_gap_per_1pct_weekend_btc"), 3)}, R² {_f(gap.get("r_squared"), 2)})</div>
        </div>
      </div>

      <!-- Agent adjustments + references -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3">
          <div class="text-[11px] font-bold text-slate-300 mb-1">에이전트 담당 업무 반영 (앞단 보고 → 뒷단 조정)</div>
          <ul class="text-[10px] text-slate-400 space-y-1 list-disc list-inside">{adj_rows or '<li>기록 없음</li>'}</ul>
          <div class="mt-2 text-[10px] text-slate-500">단기채 파킹: {_f(sgov.get("annual_yield_pct"))}%/년 (보수 차감 {_f(sgov.get("annual_yield_net_pct"))}%) → 월 ${_f(sgov.get("monthly_interest_usd"))} · {_f(sgov.get("opportunity_cost_if_cash_sits_idle_krw_per_month"), 0)}원</div>
        </div>
        <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3">
          <div class="text-[11px] font-bold text-slate-300 mb-1">모델 근거 (논문·서적)</div>
          <ul class="text-[10px] text-slate-400 space-y-1.5 list-none">{ref_items or '<li>참조 없음</li>'}</ul>
        </div>
      </div>
    </div>
"""
