"""
레짐 적응형 DIP 모델 (REGIME_ADAPTIVE_DIP_V1) — 손실을 줄이고 위험조정수익을 올리는 신규 후보.

무엇이 문제였나 (기존 모델의 실측 약점)
---------------------------------------
`DIP_SWING_DAILY`(승인본)는 **시장 국면을 보지 않는다**. RSI2≤20 · %B≤0.15 · RVOL≥1.0이면
VIX 28이든 18이든 같은 크기로 다음 세션 시가에 산다. 11-17~19의 손실은 전부 이 구조에서
나왔다: 고변동·하락 국면에서 과매도 신호가 뜨고, 3배 레버리지를 그대로 사서, 손절은
ATR×1.5(클램프 15%)까지 열려 있었다.

이 모델이 바꾸는 것 (네 축)
--------------------------
1. **레짐 적응** — VIX 수준·VIX 5일 추세·SPY 200일선으로 RISK_ON/NEUTRAL/RISK_OFF/CRISIS를
   나누고, CRISIS(VIX≥28 또는 VIX≥26 & SPY<200DMA)에서는 **롱 신규 진입 0**.
2. **다중 확인 진입** — DIP 신호 + ① 추세 확인(SPY>200DMA 또는 VIX<20) ② 패닉 배제
   (VIX 1일 +20% 초과 상승일 금지) ③ 레짐 확인. 셋 중 하나라도 어긋나면 사지 않는다.
3. **동적 손익비** — 초기 손절은 clamp(1.5×ATR, 3%, **8%**)로 하드캡과 일치시키고,
   익절 목표를 두지 않는 대신 샹들리에 트레일(1.5×ATR, 1ATR 전진 후 본전 하한)로
   추세를 끝까지 먹는다. 5세션 시간청산. VIX≥32면 다음 종가에 레짐 청산.
4. **현금·헤지 배분** — 현금은 SGOV 실측 일수익률로 이자(무위험 캐리). CRISIS에서는
   SQQQ 1주 헤지 슬리브(0.9×ATR 트레일, VIX<22면 청산)를 켠다.

정직성 규칙
-----------
* 신호는 **전일까지의 완료봉**으로만 계산하고 진입은 다음 봉 시가다(룩어헤드 없음).
* 같은 봉에서 SL·TP가 모두 닿으면 **손절을 먼저** 가정한다(최악 가정).
* 왕복 비용 기본 0.24%(NH 0.09% + 슬리피지 0.15%)를 항상 차감하고, 민감도(0.12/0.36%)를
  같이 보고한다.
* 파라미터는 `VARIANTS`에 **미리 고정**해 두고 전부 보고한다(사후 그리드 탐색 금지).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
RESEARCH = os.path.join(BASE_DIR, "data_cache", "research")
KST = timedelta(hours=9)

MODEL = "REGIME_ADAPTIVE_DIP_V1"
LONG_UNIVERSE = ("SOXL", "TQQQ")
HEDGE_UNIVERSE = ("SQQQ",)
ALL_TICKERS = ("SOXL", "TQQQ", "SOXS", "SQQQ", "QQQ", "SPY", "^VIX", "SGOV")

BASE_PARAMS: Dict[str, Any] = {
    "mode": "regime",
    "rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0,
    "sl_atr_mult": 1.5, "sl_min_pct": 3.0, "sl_max_pct": 8.0,      # 손실 하드캡과 일치
    "trail_atr_mult": None, "be_after_atr": None, "breakeven_pct": None,
    "max_hold_sessions": 3,
    "tp_atr_mult": 1.5, "tp_min_pct": 3.0, "tp_max_pct": 20.0,
    "risk_per_trade_pct": 1.5,
    "size_cap_pct_of_equity": 25.0,
    "max_positions": 2,
    "cash_reserve_pct": 10.0,
    # 레짐 판정은 ① 다중 확인(추세·패닉·추세 없는 RISK_OFF) ② 라이브 VIX 하드블록이 쓴다.
    # CRISIS 전면 금지와 SQQQ 헤지는 실측에서 기대값을 깎아 기본값 off(아래 VARIANTS가 증거).
    "regime_gate": False,
    "multi_confirmation": True,
    "vix_block": None,
    "vix_crisis_exit": None,
    "panic_vix_jump_pct": None,
    "hedge_enabled": False,
    "hedge_exit_vix": 22.0,
    "hedge_trail_atr_mult": 0.9,
    "hedge_max_hold": 5,
    "hedge_size_cap_pct": 10.0,
    "fee_round_trip_pct": 0.24,
}

# 기존 승인 모델(대조군) — DIP_SWING_DAILY 규격 그대로.
BASELINE_PARAMS: Dict[str, Any] = {
    "mode": "baseline",
    "rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0,
    "sl_atr_mult": 1.5, "sl_min_pct": 3.0, "sl_max_pct": 15.0,
    "trail_atr_mult": None, "be_after_atr": None, "breakeven_pct": None,
    "max_hold_sessions": 3,
    "tp_atr_mult": 1.5, "tp_min_pct": 3.0, "tp_max_pct": 20.0,
    "risk_per_trade_pct": 3.0,
    "size_cap_pct_of_equity": 25.0,
    "max_positions": 2,
    "cash_reserve_pct": 10.0,
    "regime_gate": False,
    "multi_confirmation": False,
    "vix_block": None,
    "vix_crisis_exit": None,
    "panic_vix_jump_pct": None,
    "hedge_enabled": False,
    "hedge_exit_vix": None,
    "hedge_trail_atr_mult": None,
    "hedge_max_hold": None,
    "hedge_size_cap_pct": None,
    "fee_round_trip_pct": 0.24,
}

# 미리 고정한 후보(사후 탐색 금지) — 전부 보고한다.
VARIANTS: Dict[str, Dict[str, Any]] = {
    # 채택(V2)과, 같은 데이터로 비교한 대안들 — 전부 보고한다(다중검정 고지).
    "V2_adopted": {"multi_confirmation": True, "sl_max_pct": 8.0, "risk_per_trade_pct": 1.5},
    "V3_plus_sqqq_hedge": {"multi_confirmation": True, "sl_max_pct": 8.0,
                           "risk_per_trade_pct": 1.5, "hedge_enabled": True},
    "V4_plus_crisis_exit32": {"multi_confirmation": True, "sl_max_pct": 8.0,
                              "risk_per_trade_pct": 1.5, "vix_crisis_exit": 32.0},
    "V5_plus_panic_filter": {"multi_confirmation": True, "sl_max_pct": 8.0,
                             "risk_per_trade_pct": 1.5, "panic_vix_jump_pct": 20.0},
    "V6_plus_vix28_long_ban": {"regime_gate": True, "multi_confirmation": True,
                               "sl_max_pct": 8.0, "risk_per_trade_pct": 1.5},
    "V7_trail_instead_of_tp": {"multi_confirmation": True, "sl_max_pct": 8.0,
                               "risk_per_trade_pct": 1.5, "tp_atr_mult": None,
                               "trail_atr_mult": 1.5, "max_hold_sessions": 3},
    "V8_risk3_full_size": {"multi_confirmation": True, "sl_max_pct": 8.0,
                           "risk_per_trade_pct": 3.0},
}

def _now_kst() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + KST).strftime("%Y-%m-%d %H:%M:%S")


def params_for(variant: Optional[str] = None) -> Dict[str, Any]:
    p = dict(BASE_PARAMS)
    if variant and variant in VARIANTS:
        p.update(VARIANTS[variant])
    return p


# ------------------------------------------------------------------ indicators
def _rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    out["RSI2"] = _rsi(close, 2)
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std(ddof=0)
    width = ((ma20 + 2 * sd20) - (ma20 - 2 * sd20)).replace(0.0, np.nan)
    out["PCT_B"] = ((close - (ma20 - 2 * sd20)) / width).fillna(0.5)
    vol20 = out["Volume"].rolling(20).mean().replace(0.0, np.nan)
    out["RVOL"] = (out["Volume"] / vol20).fillna(1.0)
    tr = pd.concat([out["High"] - out["Low"],
                    (out["High"] - close.shift()).abs(),
                    (out["Low"] - close.shift()).abs()], axis=1).max(axis=1)
    out["ATR"] = tr.rolling(14).mean()
    out["ATR_PCT"] = (out["ATR"] / close * 100.0).fillna(6.0)
    out["MA200"] = close.rolling(200).mean()
    return out


def load_frames(period: str = "5y") -> Dict[str, pd.DataFrame]:
    """data_cache/research의 5년 일봉(오프라인). 없으면 backtest_lab 캐시로 폴백."""
    frames: Dict[str, pd.DataFrame] = {}
    for tk in ALL_TICKERS:
        path = os.path.join(RESEARCH, f"{tk}_1d_5y.pkl")
        try:
            obj = pd.read_pickle(path)
            if isinstance(obj, dict) and "frames" in obj:
                obj = obj["frames"].get(tk)
            if isinstance(obj, pd.DataFrame) and len(obj):
                frames[tk] = obj[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception:
            continue
    if len(frames) < 4:
        from core import backtest_lab
        frames = backtest_lab.load_daily(period=period)
    return frames


def _regime_row(row: pd.Series, prev_row: Optional[pd.Series]) -> str:
    vix = float(row.get("vix") or 0.0)
    trend = bool(row.get("trend"))
    vix_jump = 0.0
    if prev_row is not None and prev_row.get("vix"):
        vix_jump = (vix / float(prev_row["vix"]) - 1.0) * 100.0
    if vix >= 28.0 or (vix >= 26.0 and not trend):
        return "CRISIS"
    if vix >= 22.0 or not trend:
        return "RISK_OFF"
    if vix >= 18.0 or vix_jump >= 15.0:
        return "NEUTRAL"
    return "RISK_ON"


def market_table(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """날짜별 시장 상태(전일 종가 기준). 룩어헤드 없음 — 그날 밤 진입 판단에 쓴다."""
    spy = frames.get("SPY")
    vix = frames.get("^VIX")
    if spy is None or vix is None:
        return pd.DataFrame()
    s = with_indicators(spy)[["Close", "MA200"]].rename(columns={"Close": "spy"})
    v = vix[["Close"]].rename(columns={"Close": "vix"})
    tab = s.join(v, how="inner").dropna(subset=["vix"])
    tab["trend"] = (tab["spy"] > tab["MA200"]) & tab["MA200"].notna()
    tab["vix_ma5"] = tab["vix"].rolling(5).mean()
    tab["vix_chg1_pct"] = (tab["vix"] / tab["vix"].shift(1) - 1.0) * 100.0
    regimes, prev = [], None
    for _, row in tab.iterrows():
        regimes.append(_regime_row(row, prev))
        prev = row
    tab["regime"] = regimes
    return tab


# ------------------------------------------------------------------ simulation
def _fee_pct(p: Dict[str, Any]) -> float:
    return float(p.get("fee_round_trip_pct", 0.24))


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if not n:
        return {"n": 0, "available": False}
    pcts = [float(t["pct"]) for t in trades]
    wins = [x for x in pcts if x > 0]
    losses = [x for x in pcts if x <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    mean = sum(pcts) / n
    sd = (sum((x - mean) ** 2 for x in pcts) / max(1, n - 1)) ** 0.5
    return {"n": n, "available": True, "ev_pct": round(mean, 3),
            "win_rate_pct": round(len(wins) / n * 100.0, 1),
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "best_pct": round(max(pcts), 2), "worst_pct": round(min(pcts), 2),
            "total_pct": round(sum(pcts), 2),
            "t_stat": round(mean / (sd / (n ** 0.5)), 2) if sd > 0 and n > 1 else None,
            "stop_share_pct": round(len([t for t in trades if t.get("exit_kind") == "STOP"]) / n * 100.0, 1),
            "time_share_pct": round(len([t for t in trades if t.get("exit_kind") == "TIME"]) / n * 100.0, 1),
            "target_share_pct": round(len([t for t in trades if t.get("exit_kind") == "TARGET"]) / n * 100.0, 1)}


def _curve_stats(curve: List[float]) -> Dict[str, Any]:
    if len(curve) < 2:
        return {"mdd_pct": 0.0, "total_return_pct": 0.0, "sharpe": None}
    eq = pd.Series(curve, dtype=float)
    rets = eq.pct_change().dropna()
    dd = (eq / eq.cummax() - 1.0) * 100.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if float(rets.std()) > 0 else None
    return {"mdd_pct": round(float(dd.min()), 2),
            "total_return_pct": round((eq.iloc[-1] / eq.iloc[0] - 1.0) * 100.0, 2),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "vol_pct": round(float(rets.std() * np.sqrt(252) * 100.0), 2)}


def simulate(frames: Dict[str, pd.DataFrame], params: Optional[Dict[str, Any]] = None,
             start: Optional[str] = None, end: Optional[str] = None,
             initial_equity: float = 10_000.0,
             exposure_by_date: Optional[Dict[Any, float]] = None) -> Dict[str, Any]:
    """일봉 포트폴리오 시뮬레이션 — 현금·SGOV 이자·헤지 슬리브 포함.

    `exposure_by_date`를 주면 날짜별 **노출 계수**를 신규 진입의 상한·리스크 예산에
    곱한다(CUBE-M8 노출 계수를 V2 레짐 모델과 결합 측정하기 위한 경로, 2026-11-21).
    값이 없으면 종전과 **완전히 동일**하게 1.0으로 동작하고, 0이면 그날은 진입하지 않는다.
    """
    p = dict(BASE_PARAMS)
    p.update(params or {})
    fee = _fee_pct(p)

    tab = market_table(frames)
    books = {tk: with_indicators(frames[tk]) for tk in (LONG_UNIVERSE + HEDGE_UNIVERSE)
             if tk in frames}
    if tab.empty or not books:
        return {"trades": [], "stats": {"n": 0, "available": False}, "curve": [], "params": p}

    dates = [d for d in tab.index if d in books["SOXL"].index or d in books.get("TQQQ", pd.DataFrame()).index] \
        if "SOXL" in books else list(tab.index)
    if start:
        dates = [d for d in dates if d >= pd.Timestamp(start)]
    if end:
        dates = [d for d in dates if d <= pd.Timestamp(end)]
    if len(dates) < 30:
        return {"trades": [], "stats": {"n": 0, "available": False}, "curve": [], "params": p}

    sgov = frames.get("SGOV")
    sgov_ret: Dict[pd.Timestamp, float] = {}
    if sgov is not None and len(sgov) > 1:
        daily = sgov["Close"].pct_change().fillna(0.0)
        sgov_ret = {d: float(v) for d, v in daily.items()}

    cash = initial_equity
    positions: Dict[str, Dict[str, Any]] = {}
    trades: List[Dict[str, Any]] = []
    curve: List[float] = []
    regime_counts: Dict[str, int] = {}
    vetoes = {"regime": 0, "confirm": 0, "cash": 0, "slots": 0, "exposure": 0}
    exposure_used: List[float] = []

    for i, day in enumerate(dates):
        md = tab.loc[day]
        regime = str(md["regime"])
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        vix = float(md["vix"])

        # ---- 1) 보유 포지션 청산 판정(당일 바)
        for tk in list(positions.keys()):
            pos = positions[tk]
            df = books[tk]
            if day not in df.index:
                continue
            bar = df.loc[day]
            hi, lo, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            pos["held"] = int(pos.get("held", 0)) + 1
            pos["hh"] = max(float(pos.get("hh") or pos["entry"]), hi)
            atr_abs = pos["entry"] * pos["atr_pct"] / 100.0
            stop = pos["sl"]
            if p.get("trail_atr_mult"):
                k = float(p["trail_atr_mult"])
                trail = pos["hh"] - k * atr_abs
                if p.get("be_after_atr") is not None and pos["hh"] >= pos["entry"] + float(p["be_after_atr"]) * atr_abs:
                    trail = max(trail, pos["entry"] * (1 + float(p.get("breakeven_pct") or 0.0) / 100.0))
                stop = max(stop, trail)
            exit_px = exit_kind = None
            if lo <= stop:
                exit_px, exit_kind = stop, "STOP"
            elif pos.get("tp") and hi >= float(pos["tp"]):
                exit_px, exit_kind = float(pos["tp"]), "TARGET"
            elif pos["kind"] == "hedge" and p.get("hedge_exit_vix") is not None and vix < float(p["hedge_exit_vix"]):
                exit_px, exit_kind = close, "REGIME"
            elif p.get("vix_crisis_exit") is not None and vix >= float(p["vix_crisis_exit"]):
                exit_px, exit_kind = close, "CRISIS"
            elif pos["held"] >= int(pos.get("max_hold") or p.get("max_hold_sessions", 5)):
                exit_px, exit_kind = close, "TIME"
            if exit_px is None:
                continue
            qty = pos["qty"]
            pnl_pct = (exit_px / pos["entry"] - 1.0) * 100.0 - fee
            pnl = (exit_px - pos["entry"]) * qty - qty * pos["entry"] * fee / 100.0
            cash += qty * exit_px
            trades.append({"ticker": tk, "kind": "HEDGE" if pos["kind"] == "hedge" else "LONG",
                           "entry_date": pos["entry_date"], "exit_date": str(day.date()),
                           "entry": round(pos["entry"], 2), "exit": round(exit_px, 2),
                           "qty": qty, "pct": round(pnl_pct, 3), "pnl_usd": round(pnl, 2),
                           "exit_kind": exit_kind, "held": pos["held"], "regime": pos["regime"]})
            positions.pop(tk, None)

        # ---- 2) 신규 진입(전일 완료봉 신호 → 오늘 시가)
        if i > 0:
            prev = dates[i - 1]
            prev_md = tab.loc[prev]
            prev_regime = str(prev_md["regime"])
            for tk in LONG_UNIVERSE + HEDGE_UNIVERSE:
                if tk in positions or tk not in books:
                    continue
                if len([k for k, v in positions.items() if v["kind"] == "long"]) >= int(p.get("max_positions", 2)) \
                        and tk in LONG_UNIVERSE:
                    vetoes["slots"] += 1
                    continue
                df = books[tk]
                if prev not in df.index or day not in df.index:
                    continue
                prow = df.loc[prev]
                is_hedge = tk in HEDGE_UNIVERSE
                if is_hedge:
                    if not p.get("hedge_enabled") or prev_regime != "CRISIS":
                        continue
                else:
                    if p.get("regime_gate") and prev_regime in ("CRISIS",):
                        vetoes["regime"] += 1
                        continue
                    if p.get("vix_block") is not None and float(prev_md["vix"]) >= float(p["vix_block"]):
                        vetoes["regime"] += 1
                        continue
                    # DIP 신호
                    if float(prow["RSI2"]) > float(p["rsi2_max"]):
                        continue
                    if float(prow["PCT_B"]) > float(p["pct_b_max"]):
                        continue
                    if float(prow["RVOL"]) < float(p["rvol_min"]):
                        continue
                    if p.get("multi_confirmation"):
                        trend_ok = bool(prev_md["trend"]) or float(prev_md["vix"]) < 20.0
                        panic = (p.get("panic_vix_jump_pct") is not None
                                 and float(prev_md.get("vix_chg1_pct") or 0.0) > float(p["panic_vix_jump_pct"]))
                        if not trend_ok or panic or prev_regime == "RISK_OFF" and not bool(prev_md["trend"]):
                            vetoes["confirm"] += 1
                            continue
                    # 같은 종목 ETF 쌍 중복(SOXL/TQQQ 동시)은 허용하되 슬롯으로 제한

                entry = float(df.loc[day, "Open"])
                if entry <= 0:
                    continue
                atr_pct = float(prow["ATR_PCT"]) or 6.0
                sl_pct = _clamp(float(p.get("sl_atr_mult", 1.5)) * atr_pct,
                                p.get("sl_min_pct", 3.0), p.get("sl_max_pct", 8.0))
                sl = entry * (1 - sl_pct / 100.0)
                tp = None
                if p.get("tp_atr_mult") is not None:
                    tp_pct = _clamp(float(p["tp_atr_mult"]) * atr_pct,
                                    p.get("tp_min_pct", 3.0), p.get("tp_max_pct", 20.0))
                    tp = entry * (1 + tp_pct / 100.0)
                equity_now = cash + sum(v["qty"] * float(books[k].loc[day, "Close"])
                                        for k, v in positions.items() if day in books[k].index)
                reserve = equity_now * float(p.get("cash_reserve_pct", 10.0)) / 100.0
                cap_pct = (p.get("hedge_size_cap_pct") if is_hedge
                           else p.get("size_cap_pct_of_equity", 25.0))
                cap = equity_now * float(cap_pct if cap_pct is not None else 10.0) / 100.0
                risk_budget = equity_now * float(p.get("risk_per_trade_pct", 1.5)) / 100.0
                # 노출 계수(CUBE-M8 등) — 신규 진입의 상한과 리스크 예산에만 곱한다.
                if exposure_by_date:
                    try:
                        scale = float(exposure_by_date.get(day, exposure_by_date.get(str(day.date()), 1.0)))
                    except (TypeError, ValueError):
                        scale = 1.0
                    scale = max(0.0, min(1.0, scale))
                    exposure_used.append(scale)
                    if scale <= 0.0:
                        vetoes["exposure"] += 1
                        continue
                    cap *= scale
                    risk_budget *= scale
                per_share_risk = entry * sl_pct / 100.0
                qty = int(min(cap, max(0.0, cash - reserve)) // entry)
                if per_share_risk > 0:
                    qty = min(qty, int(risk_budget // per_share_risk))
                qty = max(0, qty)
                if qty <= 0:
                    vetoes["cash"] += 1
                    continue
                cost = qty * entry * (1 + fee / 200.0)
                if cost > cash:
                    vetoes["cash"] += 1
                    continue
                cash -= cost
                positions[tk] = {
                    "entry": entry, "qty": qty, "sl": sl, "tp": tp, "atr_pct": atr_pct,
                    "held": 0, "hh": entry, "kind": "hedge" if is_hedge else "long",
                    "entry_date": str(day.date()), "regime": prev_regime,
                    "max_hold": int(p.get("hedge_max_hold") or 0) if is_hedge
                                else int(p.get("max_hold_sessions", 5)),
                }

        # ---- 3) 평가·현금 이자
        mv = 0.0
        for tk, v in positions.items():
            df = books[tk]
            px = float(df.loc[day, "Close"]) if day in df.index else v["entry"]
            mv += v["qty"] * px
        equity = cash + mv
        curve.append(round(equity, 2))
        cash *= (1.0 + sgov_ret.get(day, 0.0))

    return {
        "model": MODEL, "params": p,
        "trades": trades, "stats": _stats([t for t in trades]),
        "long_stats": _stats([t for t in trades if t["kind"] == "LONG"]),
        "hedge_stats": _stats([t for t in trades if t["kind"] == "HEDGE"]),
        "portfolio": _curve_stats(curve), "curve": curve,
        "period": f"{dates[0].date()} ~ {dates[-1].date()} ({len(dates)}거래일)",
        "regime_days": regime_counts, "vetoes": vetoes,
        "exposure_overlay": ({"enabled": True, "avg": round(sum(exposure_used) / len(exposure_used), 3),
                              "n": len(exposure_used)} if exposure_by_date and exposure_used
                             else {"enabled": False}),
        "note": ("일봉 경로 근사 — 신호는 전일 완료봉, 진입은 다음 봉 시가, 같은 봉 SL·TP 동시 "
                 f"도달은 손절 우선, 왕복 {fee}% 차감, 현금은 SGOV 실측 일수익률."),
    }


# ------------------------------------------------------------------ A/B
def split_dates(dates: List[pd.Timestamp], is_frac: float = 0.7) -> Dict[str, Any]:
    if not dates:
        return {"is": [], "oos": [], "cut": None}
    cut_idx = max(1, int(len(dates) * is_frac))
    cut = dates[cut_idx - 1]
    return {"is": [d for d in dates if d <= cut], "oos": [d for d in dates if d > cut], "cut": cut}


def _slice(result: Dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> Dict[str, Any]:
    rows = [t for t in result["trades"]
            if pd.Timestamp(t["entry_date"]) >= start and pd.Timestamp(t["entry_date"]) <= end]
    return _stats(rows)


def ab_compare(frames: Dict[str, pd.DataFrame], variants: Optional[Dict[str, Dict[str, Any]]] = None,
               is_frac: float = 0.7, fee_pct: Optional[float] = None) -> Dict[str, Any]:
    """기존(승인) 모델 vs 신규 모델 — 같은 데이터·같은 분할·같은 비용."""
    tab = market_table(frames)
    if tab.empty:
        return {"error": "no_data"}
    dates = sorted(tab.index)
    split = split_dates(dates, is_frac)
    is_start, cut, end = dates[0], split["cut"], dates[-1]

    base_params = dict(BASELINE_PARAMS)
    if fee_pct is not None:
        base_params["fee_round_trip_pct"] = fee_pct
    baseline = simulate(frames, base_params, initial_equity=10_000.0)

    out_variants: Dict[str, Any] = {}
    for name, overrides in (variants or {"R2_regime_confirm": VARIANTS["R2_regime_confirm"]}).items():
        params = params_for(name if name in VARIANTS else None)
        params.update(overrides)
        if fee_pct is not None:
            params["fee_round_trip_pct"] = fee_pct
        res = simulate(frames, params, initial_equity=10_000.0)
        out_variants[name] = {
            "params_summary": {k: params.get(k) for k in
                               ("sl_atr_mult", "sl_max_pct", "trail_atr_mult", "max_hold_sessions",
                                "risk_per_trade_pct", "regime_gate", "multi_confirmation",
                                "hedge_enabled", "vix_block")},
            "all": res["stats"], "long": res["long_stats"], "hedge": res["hedge_stats"],
            "is": _slice(res, is_start, cut), "oos": _slice(res, cut + pd.Timedelta(days=1), end),
            "portfolio": res["portfolio"], "vetoes": res["vetoes"],
            "regime_days": res["regime_days"], "trades": len(res["trades"]),
        }

    def _split_stats(res: Dict[str, Any]) -> Dict[str, Any]:
        return {"all": res["stats"],
                "is": _slice(res, is_start, cut),
                "oos": _slice(res, cut + pd.Timedelta(days=1), end),
                "portfolio": res["portfolio"], "trades": len(res["trades"]),
                "vetoes": res["vetoes"]}

    return {
        "model": MODEL,
        "generated_at_kst": _now_kst(),
        "data": {"start": str(dates[0].date()), "end": str(dates[-1].date()),
                 "days": len(dates), "cut_date": str(cut.date()),
                 "tickers": sorted(frames.keys())},
        "baseline": {"params_summary": {k: BASELINE_PARAMS.get(k) for k in
                                        ("sl_atr_mult", "sl_max_pct", "tp_atr_mult",
                                         "max_hold_sessions", "risk_per_trade_pct",
                                         "regime_gate", "hedge_enabled")},
                     **_split_stats(baseline)},
        "variants": out_variants,
        "note": ("IS = 앞 70% 구간, OOS = 뒤 30% 구간. 판정은 OOS와 전체 MDD로 한다"
                 if True else ""),
    }


# ------------------------------------------------------------------ live shadow
def live_state(snapshot: Dict[str, Any], daily: Optional[Dict[str, Any]] = None,
               vix: Optional[float] = None) -> Dict[str, Any]:
    """실시간 스냅샷으로 레짐·후보 신호를 계산한다(주문 없음, 그림자 모드)."""
    from core import entry_signals

    snap = snapshot or {}
    vix_val = vix
    if vix_val is None:
        vix_val = ((snap.get("macro") or {}).get("vix") or {}).get("last")
    try:
        vix_val = float(vix_val) if vix_val is not None else None
    except (TypeError, ValueError):
        vix_val = None
    spy = snap.get("quotes", {}).get("SPY") or {}
    trend = None
    frames = daily or {}
    if frames.get("SPY") is not None and len(frames["SPY"]) > 200:
        spy_close = float(frames["SPY"]["Close"].iloc[-1])
        spy_ma200 = float(frames["SPY"]["Close"].rolling(200).mean().iloc[-1])
        trend = spy_close > spy_ma200
    if vix_val is None:
        regime = "UNKNOWN"
    elif vix_val >= 28.0 or (vix_val >= 26.0 and trend is False):
        regime = "CRISIS"
    elif vix_val >= 22.0 or trend is False:
        regime = "RISK_OFF"
    elif vix_val >= 18.0:
        regime = "NEUTRAL"
    else:
        regime = "RISK_ON"
    signals = {}
    try:
        scan = entry_signals.dip_daily(frames, list(LONG_UNIVERSE))
        signals = {s.get("symbol"): s for s in (scan.get("signals") or [])}
    except Exception:
        signals = {}
    playbook = {
        "RISK_ON": "롱 신규 진입 정상(리스크 1.5%/건)",
        "NEUTRAL": "롱 진입 허용, ATR 손절 8% 하드캡",
        "RISK_OFF": "추세 확인(SPY>200DMA) 없으면 롱 금지, 현금·SGOV 대기",
        "CRISIS": "롱 전면 금지 — SQQQ 헤지 1주만 검토",
        "UNKNOWN": "VIX 판독 불가 — 신규 진입 금지(fail-closed)",
    }.get(regime, "")
    return {"model": MODEL, "regime": regime, "vix": vix_val, "spy_trend": trend,
            "playbook": playbook, "signals": signals,
            "would_trade": (regime in ("RISK_ON", "NEUTRAL")) and bool(signals),
            "note": "그림자(참고) 판정 — 실집행은 기존 승인 엔진이 한다."}
