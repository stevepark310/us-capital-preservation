#!/usr/bin/env python3
"""
Real-time Scanner for Undervalued Surge Candidates (UV-SURGE-ALPHA)
Scans US small-to-mid cap stocks for deep oversold bounce and volatility squeeze signals.
"""

import os
import sys
import json
import pandas as pd
import numpy as np
import yfinance as yf

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

def scan_candidates():
    tickers = [
        'ASTS', 'RKLB', 'SOUN', 'SYM', 'SMCI', 'LUNR', 'MARA', 'CLSK', 'CIFR', 'SMR', 
        'OKLO', 'VRT', 'ALAB', 'APP', 'BE', 'HIMS', 'IONQ', 'ACHR', 'CVNA', 'UPST', 
        'ROOT', 'PLTR', 'PLUG', 'TEM', 'POWI', 'BBAI', 'JOBY', 'RGTI', 'QBTS', 'ENVX',
        'QS', 'BLNK', 'CHPT', 'WOLF', 'LCID', 'RIVN', 'DKNG', 'COIN', 'HOOD', 'AFRM',
        'AI', 'RDDT', 'CELH', 'DUOL', 'CAVA', 'IBRX', 'CRSP', 'NTLA', 'BEAM'
    ]

    print(f"[Scanner] Scanning {len(tickers)} US small/mid-cap high-surge candidates...")
    raw = yf.download(tickers, period='3mo', interval='1d', progress=False)
    closes = raw['Close']
    highs = raw['High']
    lows = raw['Low']
    vols = raw['Volume']
    opens = raw['Open']

    candidates = []
    for t in tickers:
        try:
            c = closes[t].dropna()
            h = highs[t].dropna()
            l = lows[t].dropna()
            v = vols[t].dropna()
            o = opens[t].dropna()
            if len(c) < 30: continue
            
            vol_sma = v.rolling(20).mean()
            rvol = v / vol_sma
            
            delta = c.diff()
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            rs = up.ewm(com=13, adjust=False).mean() / (down.ewm(com=13, adjust=False).mean() + 1e-8)
            rsi = 100 - (100 / (1 + rs))
            ret_20d = c / c.shift(20) - 1.0
            
            curr_c = float(c.iloc[-1])
            curr_rsi = float(rsi.iloc[-1])
            curr_ret20 = float(ret_20d.iloc[-1])
            curr_rvol = float(rvol.iloc[-1])
            
            sma = c.rolling(20).mean()
            std = c.rolling(20).std()
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(20).mean()
            bb_u = sma + 2.0 * std
            bb_l = sma - 2.0 * std
            kc_u = sma + 1.5 * atr
            kc_l = sma - 1.5 * atr
            is_sqz = bool((bb_l.iloc[-1] > kc_l.iloc[-1]) and (bb_u.iloc[-1] < kc_u.iloc[-1]))
            
            score = 0.0
            reasons = []
            
            # Deep value / oversold
            if curr_ret20 <= -0.15:
                score += 25
                reasons.append(f"20일 낙폭과대 ({curr_ret20*100:+.1f}%)")
            if curr_rsi <= 35:
                score += 25
                reasons.append(f"RSI 극단 과매도 ({curr_rsi:.1f})")
            elif curr_rsi <= 45 and curr_rsi > 35:
                score += 15
                reasons.append(f"RSI 저점 반등권 ({curr_rsi:.1f})")
                
            # Squeeze
            if is_sqz:
                score += 20
                reasons.append("볼린저-켈트너 변동성 스퀴즈(에너지 응축)")
                
            # Volume buildup
            if curr_rvol >= 1.5:
                score += 25
                reasons.append(f"거래량 급증 (RVOL {curr_rvol:.1f}배)")
            elif curr_rvol >= 1.2:
                score += 15
                reasons.append(f"거래량 유입 (RVOL {curr_rvol:.1f}배)")
                
            if score >= 40:
                candidates.append({
                    "ticker": t,
                    "price": round(curr_c, 2),
                    "score": score,
                    "rvol": round(curr_rvol, 2),
                    "rsi": round(curr_rsi, 1),
                    "ret_20d_pct": round(curr_ret20 * 100, 1),
                    "is_squeeze": is_sqz,
                    "reasons": reasons
                })
        except Exception:
            continue

    candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
    out_file = os.path.join(base_dir, "data", "surge_candidates_live.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)
        
    print(f"\n[Scanner] Found {len(candidates)} candidates matching UV-SURGE criteria:")
    for s in candidates[:6]:
        print(f"• {s['ticker']}: ${s['price']} (Score: {s['score']}) -> {', '.join(s['reasons'])}")
    return candidates

if __name__ == "__main__":
    scan_candidates()
