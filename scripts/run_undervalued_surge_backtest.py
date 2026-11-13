#!/usr/bin/env python3
"""
Undervalued Surge & Short-Squeeze Predictor (UV-SURGE-ALPHA) 1-Year Backtest Engine
Developed for Agent 2 (Technical Momentum Specialist) & Agent 6 (Quant Scenario Model)
Target: Predicting explosive breakouts (+10%~+20%+ in 5 days) in undervalued / high-potential US small-to-mid caps.

Period: 2025-11-12 ~ 2026-11-12 (251 trading days)
Friction: NH Securities 0.09% commission + 0.15% slippage = 0.24% round-trip deducted.
Hit Definition: Price reaches >= +10.0% (Hit) and >= +15.0% (Strong Surge) within 5 trading days post-signal.
Exit Framework: TP1 +10.0% (50% scale out + SL to breakeven), TP2 +20.0% (remaining 50%), SL -5.0% (hard stop), Max hold 5 days.
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import yfinance as yf

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

def run_undervalued_surge_backtest():
    print("[Agent 2 & 7] Running UV-SURGE-ALPHA 1-Year Backtest on US Small/Mid-Cap Candidates...")
    
    tickers = [
        'ASTS', 'RKLB', 'SOUN', 'SYM', 'SMCI', 'LUNR', 'MARA', 'CLSK', 'CIFR', 'SMR', 
        'OKLO', 'VRT', 'ALAB', 'APP', 'BE', 'HIMS', 'IONQ', 'ACHR', 'CVNA', 'UPST', 
        'ROOT', 'PLTR', 'PLUG', 'TEM', 'POWI', 'BBAI', 'JOBY', 'RGTI', 'QBTS', 'ENVX',
        'QS', 'BLNK', 'CHPT', 'WOLF', 'LCID', 'RIVN', 'DKNG', 'COIN', 'HOOD', 'AFRM',
        'AI', 'RDDT', 'CELH', 'DUOL', 'CAVA', 'IBRX', 'CRSP', 'NTLA', 'BEAM'
    ]
    
    print(f"Downloading historical daily data for {len(tickers)} tickers...")
    raw = yf.download(tickers, period="1y", interval="1d", progress=False)
    
    closes = raw["Close"].ffill().bfill()
    highs = raw["High"].ffill().bfill()
    lows = raw["Low"].ffill().bfill()
    opens = raw["Open"].ffill().bfill()
    volumes = raw["Volume"].ffill().bfill()
    
    dates = closes.index
    cost_per_trade = 0.0024  # 0.24% round-trip NH fee + slippage
    
    signals = []
    
    for t in tickers:
        try:
            c = closes[t].dropna()
            h = highs[t].dropna()
            l = lows[t].dropna()
            v = volumes[t].dropna()
            o = opens[t].dropna()
            if len(c) < 60: continue
            
            vol_sma = v.rolling(20).mean()
            rvol = v / vol_sma
            
            delta = c.diff()
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            rs = up.ewm(com=13, adjust=False).mean() / (down.ewm(com=13, adjust=False).mean() + 1e-8)
            rsi = 100 - (100 / (1 + rs))
            ret_20d = c / c.shift(20) - 1.0
            
            sma = c.rolling(20).mean()
            std = c.rolling(20).std()
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(20).mean()
            bb_u = sma + 2.0 * std
            bb_l = sma - 2.0 * std
            kc_u = sma + 1.5 * atr
            kc_l = sma - 1.5 * atr
            squeeze_on = (bb_l > kc_l) & (bb_u < kc_u)
            high_10 = h.rolling(10).max().shift(1)
            
            for i in range(30, len(c) - 6):
                # Dual-Engine Quant Logic:
                # Engine A: Deep Value Volume Shock (Oversold Dip + 2x RVOL Burst + Bullish Reversal Candle)
                cond_a = ((ret_20d.iloc[i] <= -0.10) or (rsi.iloc[i-5:i].min() <= 35)) and \
                         (rvol.iloc[i] >= 2.0) and (c.iloc[i] >= o.iloc[i] * 1.03) and \
                         (c.iloc[i] > h.iloc[i-3:i].max())
                
                # Engine B: Volatility Squeeze Compression & Expansion Breakout
                cond_b = squeeze_on.iloc[i-5:i].any() and (rvol.iloc[i] >= 1.8) and \
                         (c.iloc[i] > high_10.iloc[i]) and (c.iloc[i] > o.iloc[i])
                
                if cond_a or cond_b:
                    engine_type = "DEEP_VALUE_SHOCK" if cond_a else "SQUEEZE_BREAKOUT"
                    entry = float(c.iloc[i])
                    signal_date = c.index[i].strftime("%Y-%m-%d")
                    
                    tp1_target = entry * 1.10  # +10%
                    tp2_target = entry * 1.20  # +20%
                    sl_price = entry * 0.95    # -5%
                    
                    tp1_hit = False
                    tp2_hit = False
                    sl_hit = False
                    pnl = 0.0
                    max_reach = 0.0
                    exit_reason = "TIME_EXIT_DAY5"
                    
                    # Path-dependent 5-day execution
                    for d in range(1, 6):
                        dh = float(h.iloc[i+d])
                        dl = float(l.iloc[i+d])
                        dc = float(c.iloc[i+d])
                        
                        day_reach = (dh / entry) - 1.0
                        if day_reach > max_reach:
                            max_reach = day_reach
                            
                        # Stop-loss check
                        if dl <= sl_price:
                            sl_hit = True
                            if tp1_hit:
                                pnl = (0.50 * 0.10) + (0.50 * 0.0) - cost_per_trade
                                exit_reason = "TP1_THEN_BREAKEVEN_STOP"
                            else:
                                pnl = -0.05 - cost_per_trade
                                exit_reason = "STOP_LOSS (-5%)"
                            break
                            
                        # TP2 hit
                        if dh >= tp2_target:
                            tp2_hit = True
                            pnl = (0.50 * 0.10) + (0.50 * 0.20) - cost_per_trade
                            exit_reason = "FULL_TP2_HIT (+20%)"
                            break
                            
                        # TP1 hit
                        if dh >= tp1_target and not tp1_hit:
                            tp1_hit = True
                            sl_price = entry * 1.00  # Ratchet stop to breakeven!
                            
                    if not tp2_hit and not sl_hit and pnl == 0.0:
                        final_c = float(c.iloc[i+5])
                        c_gain = (final_c / entry) - 1.0
                        if tp1_hit:
                            pnl = (0.50 * 0.10) + (0.50 * max(0.0, c_gain)) - cost_per_trade
                            exit_reason = f"TP1_THEN_DAY5_CLOSE ({c_gain*100:+.1f}%)"
                        else:
                            pnl = c_gain - cost_per_trade
                            exit_reason = f"DAY5_CLOSE ({c_gain*100:+.1f}%)"
                            
                    signals.append({
                        "ticker": t,
                        "date": signal_date,
                        "engine": engine_type,
                        "entry": round(entry, 2),
                        "max_reach_pct": round(max_reach * 100, 2),
                        "is_hit_10pct": bool(max_reach >= 0.10),
                        "is_hit_15pct": bool(max_reach >= 0.15),
                        "is_hit_20pct": bool(max_reach >= 0.20),
                        "realized_pnl_pct": round(pnl * 100, 2),
                        "exit_reason": exit_reason
                    })
        except Exception as e:
            continue

    total_signals = len(signals)
    hits_10 = sum(1 for s in signals if s["is_hit_10pct"])
    hits_15 = sum(1 for s in signals if s["is_hit_15pct"])
    hits_20 = sum(1 for s in signals if s["is_hit_20pct"])
    
    wins = sum(1 for s in signals if s["realized_pnl_pct"] > 0)
    losses = sum(1 for s in signals if s["realized_pnl_pct"] <= 0)
    
    gross_gains = sum(s["realized_pnl_pct"] for s in signals if s["realized_pnl_pct"] > 0)
    gross_losses = sum(abs(s["realized_pnl_pct"]) for s in signals if s["realized_pnl_pct"] <= 0)
    profit_factor = round(gross_gains / max(1e-8, gross_losses), 2)
    
    hit_ratio_10_pct = round((hits_10 / max(1, total_signals)) * 100, 1)
    hit_ratio_15_pct = round((hits_15 / max(1, total_signals)) * 100, 1)
    win_rate_pct = round((wins / max(1, total_signals)) * 100, 1)
    avg_pnl_pct = round(float(np.mean([s["realized_pnl_pct"] for s in signals])), 2)
    avg_win_pct = round(gross_gains / max(1, wins), 2)
    avg_loss_pct = round(-gross_losses / max(1, losses), 2)
    
    # Portfolio simulation: 5% allocation per surge signal
    sim_equity = 1000000.0  # 100만 원 시작
    sim_curve = [sim_equity]
    for s in signals:
        trade_ret = s["realized_pnl_pct"] / 100.0
        sim_equity *= (1.0 + (trade_ret * 0.08))  # 8% allocation per signal
        sim_curve.append(sim_equity)
        
    cum_ret_pct = round(((sim_equity / 1000000.0) - 1.0) * 100, 2)
    peaks = pd.Series(sim_curve).cummax()
    mdd_pct = round(((pd.Series(sim_curve) - peaks) / peaks).min() * 100, 2)
    
    top_hits = sorted(signals, key=lambda x: x["max_reach_pct"], reverse=True)[:8]

    report = {
        "model_name": "UV-SURGE-ALPHA (Undervalued Squeeze & Deep-Value Volume Shock Predictor)",
        "period": f"{dates[0].strftime('%Y-%m-%d')} ~ {dates[-1].strftime('%Y-%m-%d')} (최근 1년, 251거래일)",
        "monitored_universe_size": len(tickers),
        "total_signals_fired": total_signals,
        "hits_10pct_count": hits_10,
        "hits_15pct_count": hits_15,
        "hits_20pct_count": hits_20,
        "hit_ratio_10pct": hit_ratio_10_pct,
        "hit_ratio_15pct": hit_ratio_15_pct,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "avg_trade_pnl_pct": avg_pnl_pct,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "simulated_strategy_cum_return_pct": cum_ret_pct,
        "strategy_mdd_pct": mdd_pct,
        "friction_deducted": "왕복 0.24% 전액 차감 (NH 수수료 0.09% + 슬리피지 0.15%)",
        "top_surge_hits_sample": top_hits
    }
    
    out_path = os.path.join(base_dir, "data", "undervalued_surge_backtest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    print("\n=================== [Agent 2 & 7] UV-SURGE-ALPHA 1-YEAR BACKTEST OFFICIAL RESULTS ===================")
    print(f"• Total Signals Generated: {total_signals}건")
    print(f"• 5일 내 +10% 이상 급등 적중(Hits): {hits_10}건 ({hit_ratio_10_pct}%)")
    print(f"• 5일 내 +15% 이상 폭등 적중(Strong Surge): {hits_15}건 ({hit_ratio_15_pct}%)")
    print(f"• 매매 승률 (Win Rate): {win_rate_pct}% ({wins}승 {losses}패)")
    print(f"• 손익비 (Profit Factor): {profit_factor} : 1")
    print(f"• 평균 이익 / 평균 손실: +{avg_win_pct}% / {avg_loss_pct}%")
    print(f"• 8% 소액 분산 포트폴리오 누적 수익률: +{cum_ret_pct}% | MDD: {mdd_pct}%")
    print("• 대표 급등 적중 사례:")
    for h in top_hits[:5]:
        print(f"  - [{h['date']}] {h['ticker']} ({h['engine']}): 5일 내 최고 +{h['max_reach_pct']}% 급등 (실현익 +{h['realized_pnl_pct']}%, {h['exit_reason']})")
    print("===================================================================================================\n")
    return report

if __name__ == "__main__":
    run_undervalued_surge_backtest()
