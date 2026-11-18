"""현행 핵심 알고리즘의 10분 단위 재현 시뮬레이터 (2026-11-18 연구용, 읽기 전용).

`core/autotrader.py`의 실제 진입·청산 판단을 10분 결정 그리드에서 재현한다.
프로덕션 모듈은 **읽기만** 한다(정책·파라미터·신호 함수를 그대로 import). 매매 로직
파일은 한 줄도 바꾸지 않는다 — 이 파일은 scripts/research/ 아래의 별도 시뮬레이터다.

재현하는 규칙(근거 = 실제 코드)
--------------------------------
진입
  · DIP_SWING_DAILY  : 완료 세션 일봉 RSI2≤20 · %B≤0.15 · RVOL≥1.0 → 다음 세션 진입
                       (세션 시작 후 90분 이내만 유효)
  · DIP_SWING_INTRADAY: 5분봉 RSI2≤20 · %B≤0.15 · RVOL≥1.0 (세션 시작 30분 이후)
  · BEAR_ALPHA_HEDGE : VIX ≥ 24.0 + 확인 2개(DXY ≥ 104.5 / 센티먼트 < 40) → SOXS·SQQQ
                       (notional cap $60)
  · NVDA_EARNINGS_GAP_SOXS: 승인 반영일이 2026-11-18 뿐 → 5거래일 창에는 후보 없음
게이트
  · 개장 유예 23:30~00:00 KST(09:30~10:00 ET) · 신규 진입 마감 04:30 KST(14:30 ET)
  · 1회 예산(execution_policy.entry_budget) · 현금보존 $150 · 위험 3%/건 · 정수주
  · 최대 보유 4종목 · 세션 신규 3건 · 티커 쿨다운 20분 · 물타기 금지
  · 당일 실현손실 -$25 → 신규 매수 중단
청산
  · chandelier_v2: 초기 SL = clamp(0.75×ATR%, 1.5%, 15%) · 트레일 = 최고가 − 0.9×ATR
    · 1.0×ATR 상승 후 본전 하한 · 09:45 ET까지 손절 유예 · 3세션 시간청산(15:45)
  · 신중 모드(2026-11-16 23:44 ~ 11-18 06:20) 강제청산 15:40 ET
  · 체결: 결정 봉 종가, 왕복 0.24% 차감

주의: 지표는 실제 엔진과 같이 **5분봉**에서 계산하고, 결정·체결은 10분 그리드에서 한다
(엔진이 10분마다 깨어나 5분봉 지표를 읽는 것과 같은 순서).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import i10_lib as L                                   # noqa: E402
from core import execution_policy                     # noqa: E402

BASE_DIR = L.BASE_DIR
DAILY_CACHE = os.path.join(L.CACHE, "__core_daily.pkl")

OPEN_MIN = 9 * 60 + 30            # 09:30 ET
BLACKOUT_END = 10 * 60            # 10:00 ET (23:30~00:00 KST 개장 유예)
SL_GRACE = 9 * 60 + 45            # 09:45 ET (23:45 KST 손절 유예)
NO_ENTRY_AFTER = 14 * 60 + 30     # 14:30 ET (04:30 KST 신규 진입 마감)
TIME_EXIT = 15 * 60 + 45          # 15:45 ET (05:45 KST 시간청산 판정)
FORCE_EXIT = 15 * 60 + 40         # 15:40 ET (05:40 KST 신중 모드 강제청산)

DIP_UNIVERSE = ["SOXL", "TQQQ"]
HEDGE_UNIVERSE = ["SOXS", "SQQQ"]
HEDGE_NOTIONAL_CAP = 60.0
MAX_ENTRIES_PER_SESSION = 3
MAX_OPEN = 4
COOLDOWN_MIN = 20.0
DAILY_LOSS_LIMIT = 25.0

LEGACY_BUDGET = {"default": 44.8, "vix24": 37.11}     # 2026-11-18 10:30 이전 실효 예산(문서값)


def hhmm_to_min(hm: str) -> int:
    h, m = str(hm).split(":")[:2]
    return int(h) * 60 + int(m)


# ------------------------------------------------------------------ daily bars
def daily_bars(symbols: List[str], period: str = "6mo") -> Dict[str, pd.DataFrame]:
    import pickle
    cached: Dict[str, pd.DataFrame] = {}
    if os.path.exists(DAILY_CACHE):
        try:
            with open(DAILY_CACHE, "rb") as f:
                cached = pickle.load(f)
        except Exception:
            cached = {}
    import yfinance as yf
    out: Dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = cached.get(s)
        if df is None or len(df) == 0:
            df = yf.download(s, period=period, interval="1d", progress=False, auto_adjust=False)
            if df is None or len(df) == 0:
                continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = [c[0] for c in df.columns]
            df = df.dropna(how="all")
            df.index = pd.to_datetime([str(x)[:10] for x in df.index])
            cached[s] = df
        out[s] = df
    try:
        with open(DAILY_CACHE, "wb") as f:
            pickle.dump(cached, f)
    except Exception:
        pass
    return out


def daily_metrics(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    lo, hi = mid - 2 * sd, mid + 2 * sd
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                    (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["rsi2"] = L.rsi(c, 2)
    out["pct_b"] = (c - lo) / (hi - lo).replace(0, np.nan)
    out["rvol"] = df["Volume"] / df["Volume"].rolling(20).mean().shift(1)
    out["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False).mean() / c * 100
    return out


# ------------------------------------------------------------------ 5m metrics
def five_min_metrics(df5: pd.DataFrame) -> pd.DataFrame:
    """엔진이 읽는 5분봉 지표(연속 시계열). 인덱스는 봉 '종료' 시각."""
    d = L.regular_session(df5)
    if not len(d):
        return pd.DataFrame()
    mid = d["Close"].rolling(20).mean()
    sd = d["Close"].rolling(20).std()
    lo, hi = mid - 2 * sd, mid + 2 * sd
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - d["Close"].shift()).abs(),
                    (d["Low"] - d["Close"].shift()).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=d.index)
    out["rsi2"] = L.rsi(d["Close"], 2)
    out["pct_b"] = (d["Close"] - lo) / (hi - lo).replace(0, np.nan)
    vol_ma = d["Volume"].rolling(20).mean().shift(1).replace(0, np.nan)
    out["rvol"] = d["Volume"] / vol_ma
    out["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False).mean() / d["Close"] * 100
    out.index = out.index + pd.Timedelta(minutes=5)
    return out


def metrics_at(m5: pd.DataFrame, ts: pd.Timestamp) -> Optional[Dict[str, float]]:
    if m5 is None or not len(m5):
        return None
    if ts not in m5.index:
        return None
    row = m5.loc[ts]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    if pd.isna(row.get("rsi2")):
        return None
    pb = row.get("pct_b")
    rv = row.get("rvol")
    at = row.get("atr_pct")
    return {"rsi2": float(row["rsi2"]),
            "pct_b": float(pb) if not pd.isna(pb) else 9.9,
            "rvol": float(rv) if not pd.isna(rv) else 0.0,
            "atr_pct": float(at) if not pd.isna(at) and at > 0 else 1.0}


# ------------------------------------------------------------------ simulator
def _sl_pct(atr_pct: float, params: Dict[str, Any]) -> float:
    lo = float(params.get("sl_min_pct") or 1.5)
    hi = float(params.get("sl_max_pct") or 15.0)
    return max(lo, min(hi, float(params.get("initial_sl_atr_mult") or 0.75) * float(atr_pct or 6.0)))


def sim_core(days: List[str], panel: Dict[str, pd.DataFrame], panel5: Dict[str, pd.DataFrame],
             daily_sig: Dict[str, Dict[str, Dict[str, Any]]], *,
             vix10: Optional[pd.Series] = None, dxy10: Optional[pd.Series] = None,
             sentiment: Optional[float] = None,
             budget_mode: str = "current", equity0: float = 883.5, cash0: float = 636.0,
             policy: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None,
             slippage_bps: float = 0.0, force_exit: bool = True, label: str = "") -> Dict[str, Any]:
    """현행 엔진 재현. 반환: trades, 일별 실현손익, 미체결·차단 사유 로그."""
    policy = policy or execution_policy.load_policy()
    g = execution_policy.guardrails(policy)
    if params is None:
        try:
            from core import strategy_evolver
            params = strategy_evolver.load_params()
        except Exception:
            params = {"trail_atr_mult": 0.9, "initial_sl_atr_mult": 0.75, "be_after_atr": 1.0,
                      "sl_min_pct": 1.5, "sl_max_pct": 15.0, "max_hold_sessions": 3,
                      "risk_scale": 0.7, "entry_filters": {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0}}
    ef = dict(params.get("entry_filters") or {})
    rsi2_max, pctb_max, rvol_min = (float(ef.get("rsi2_max", 20.0)), float(ef.get("pct_b_max", 0.15)),
                                    float(ef.get("rvol_min", 1.0)))
    risk_scale = float(params.get("risk_scale", 1.0) or 1.0)
    rt = L.RT_COST + 2 * float(slippage_bps) / 10000.0
    slip = float(slippage_bps) / 10000.0

    cash = float(cash0)
    equity = float(equity0)
    positions: Dict[str, Dict[str, Any]] = {}
    trades: List[Dict[str, Any]] = []
    blocks: Dict[str, int] = {}
    signal_log: List[Dict[str, Any]] = []
    last_exit_ts: Dict[str, pd.Timestamp] = {}
    entries_log: List[Dict[str, Any]] = []
    per_day: Dict[str, Dict[str, Any]] = {}
    equity_curve: List[Dict[str, Any]] = []

    def block(reason: str) -> None:
        blocks[reason] = blocks.get(reason, 0) + 1

    def mark(tk: str, ts: pd.Timestamp) -> Optional[float]:
        b = panel.get(tk)
        if b is None or not len(b):
            return None
        sub = b[b.index < ts]          # ts = 결정 시각(봉 종료) — 그 시점에 '완료된' 봉만
        if not len(sub):
            return None
        return float(sub["Close"].iloc[-1])

    for day in days:
        ref = panel["SOXL"]
        gday = ref[ref["session"] == day]
        if not len(gday):
            continue
        pd_day = {"entries": 0, "realized": 0.0, "halted": False, "trades": []}
        for hm, bar in gday.iterrows():
            ts = pd.Timestamp(hm) + pd.Timedelta(minutes=10)     # 결정 시각 = 봉 종료
            dec_min = hhmm_to_min(bar["hm_end"])                 # 결정·체결 시각(봉 종료)
            mins = dec_min
            price_now = float(bar["Close"])

            # ---- 평가 자산가치(보유 종목은 현재가)
            mtm = cash
            for tk, p in positions.items():
                px = mark(tk, ts) or p["entry"]
                mtm += px * p["qty"]
            equity = mtm

            # ---------------------------------------------------------- 1) 청산
            for tk in list(positions.keys()):
                p = positions[tk]
                px = mark(tk, ts)
                if px is None:
                    continue
                p["hh"] = max(float(p.get("hh") or p["entry"]), px)
                atr_abs = p["entry"] * p["atr_pct"] / 100.0
                trail = p["hh"] - float(params.get("trail_atr_mult") or 0.9) * atr_abs
                if p["hh"] >= p["entry"] + float(params.get("be_after_atr") or 1.0) * atr_abs:
                    trail = max(trail, p["entry"] * (1 + float(params.get("breakeven_pct") or 0.34) / 100.0))
                p["trail"] = trail
                act = None
                if force_exit and dec_min >= FORCE_EXIT:
                    act = ("FORCE_EXIT_05:40", px)
                elif dec_min >= TIME_EXIT and p["sessions_held"] >= int(params.get("max_hold_sessions") or 3):
                    act = ("TIME_EXIT", px)
                elif dec_min >= SL_GRACE and px <= p["sl"]:
                    act = ("STOP", px)
                elif dec_min >= SL_GRACE and px <= trail:
                    act = ("TRAIL", px)
                if act:
                    reason, base = act
                    px_fill = base * (1 - slip)
                    t = L.make_trade(day, tk, "LONG", p["entry_ts"], ts, p["entry"], px_fill,
                                     p["qty"], f"{reason} ({p['source']})", rt_cost=rt)
                    trades.append(t)
                    cash += px_fill * p["qty"] - p["entry"] * p["qty"] * rt
                    pd_day["realized"] += t["pnl_usd"]
                    pd_day["trades"].append(t)
                    last_exit_ts[tk] = ts
                    del positions[tk]
            if pd_day["realized"] <= -execution_policy.loss_limit_usd(equity, policy):
                pd_day["halted"] = True

            # ---------------------------------------------------------- 2) 진입
            if pd_day["halted"]:
                continue
            if dec_min <= BLACKOUT_END:
                block("개장 유예(09:30~10:00 ET)")
                continue
            if dec_min > NO_ENTRY_AFTER:
                block("신규 진입 마감(14:30 ET)")
                continue
            if pd_day["entries"] >= MAX_ENTRIES_PER_SESSION:
                block("세션 신규 진입 3건 한도")
                continue
            if len(positions) >= MAX_OPEN:
                block("최대 보유 4종목")
                continue

            vix = None
            if vix10 is not None and len(vix10):
                sub = vix10[vix10.index < ts]
                if len(sub):
                    vix = float(sub.iloc[-1])
            vscale = execution_policy.vix_scale(vix, policy)
            if vscale <= 0:
                block("VIX>=28 리스크오프(진입 금지)")
                continue

            cands: List[Dict[str, Any]] = []
            # DIP_SWING_DAILY — 90분 창, 완료 세션 일봉 신호
            if dec_min <= OPEN_MIN + 90:
                for tk in DIP_UNIVERSE:
                    sig = (daily_sig.get(day) or {}).get(tk)
                    if sig and sig.get("signal"):
                        cands.append({"source": "DIP_SWING_DAILY", "ticker": tk, "atr_pct": sig.get("atr_pct"),
                                      "why": f"일봉 RSI2 {sig.get('rsi2'):.1f} · %B {sig.get('pct_b'):.2f} · RVOL {sig.get('rvol'):.2f}"})
            # DIP_SWING_INTRADAY — 30분 이후, 5분봉 지표
            for tk in DIP_UNIVERSE:
                m = metrics_at(panel5.get(tk), ts)
                if not m:
                    continue
                ok = m["rsi2"] <= rsi2_max and m["pct_b"] <= pctb_max and m["rvol"] >= rvol_min
                signal_log.append({"day": day, "ts": str(ts), "hm": bar["hm"], "ticker": tk,
                                   "rsi2": round(m["rsi2"], 1), "pct_b": round(m["pct_b"], 3),
                                   "rvol": round(m["rvol"], 2), "atr_pct": round(m["atr_pct"], 2),
                                   "dip": bool(ok)})
                if ok:
                    cands.append({"source": "DIP_SWING_INTRADAY", "ticker": tk, "atr_pct": m["atr_pct"],
                                  "why": f"5분봉 RSI2 {m['rsi2']:.1f} · %B {m['pct_b']:.2f} · RVOL {m['rvol']:.2f}"})
            # BEAR_ALPHA — VIX ≥ 24 + 확인 2개
            confs = []
            if vix is not None and vix >= 24.0:
                if dxy10 is not None and len(dxy10):
                    sub = dxy10[dxy10.index < ts]
                    if len(sub) and float(sub.iloc[-1]) >= 104.5:
                        confs.append(f"DXY {float(sub.iloc[-1]):.2f}")
                if sentiment is not None and float(sentiment) < 40.0:
                    confs.append(f"센티먼트 {float(sentiment):.1f}")
                if len(confs) >= 2:
                    for tk in HEDGE_UNIVERSE:
                        cands.append({"source": "BEAR_ALPHA_HEDGE", "ticker": tk, "atr_pct": None,
                                      "notional_cap": HEDGE_NOTIONAL_CAP,
                                      "why": f"VIX {vix:.1f} + 확인 {len(confs)}개"})
                else:
                    block(f"BEAR_ALPHA 확인 {len(confs)}/2 (VIX {vix:.1f})")
            for c in cands:
                tk = c["ticker"]
                if tk in positions:
                    block(f"{tk} 이미 보유(물타기 금지)")
                    continue
                if tk in last_exit_ts:
                    age = (ts - last_exit_ts[tk]).total_seconds() / 60.0
                    if age < COOLDOWN_MIN:
                        block(f"{tk} 쿨다운 {COOLDOWN_MIN:.0f}분")
                        continue
                px = mark(tk, ts)
                if not px or px < float(g.get("min_price_usd", 3.0)):
                    block(f"{tk} 가격 확인 불가")
                    continue
                if budget_mode == "legacy":
                    budget = LEGACY_BUDGET["vix24"] if (vix is not None and vix >= 24.0) else LEGACY_BUDGET["default"]
                    eb = {"budget_usd": budget, "binding": "legacy_doc"}
                else:
                    eb = execution_policy.entry_budget(equity, cash, policy, vscale * risk_scale)
                    budget = float(eb["budget_usd"])
                if c.get("notional_cap"):
                    budget = min(budget, float(c["notional_cap"]))
                sl_pct = _sl_pct(float(c.get("atr_pct") or 0.0) if c.get("atr_pct") else 6.0, params)
                qty = int(budget // px)
                reserve = float(g.get("cash_reserve_floor_usd", 150.0))
                while qty > 0 and qty * px > max(0.0, cash - reserve):
                    qty -= 1
                risk_cap = equity * float(g.get("risk_per_trade_pct", 3.0)) / 100.0
                per_share_risk = px * sl_pct / 100.0
                while qty > 0 and per_share_risk * qty > risk_cap:
                    qty -= 1
                min_one = False
                if qty <= 0 and px <= max(0.0, cash - reserve) and per_share_risk <= risk_cap \
                        and (not c.get("notional_cap") or px <= float(c["notional_cap"])):
                    qty, min_one = 1, True
                if qty <= 0:
                    block(f"{tk} 수량 0 (예산 ${budget:.2f} < 1주 ${px:.2f})")
                    continue
                fill = px * (1 + slip)
                positions[tk] = {"entry": fill, "qty": qty, "entry_ts": str(ts), "sl": fill * (1 - sl_pct / 100.0),
                                 "sl_pct": sl_pct, "atr_pct": float(c.get("atr_pct") or 6.0),
                                 "hh": fill, "sessions_held": 1, "source": c["source"],
                                 "budget_usd": round(budget, 2), "min_one": min_one, "why": c.get("why")}
                cash -= fill * qty
                pd_day["entries"] += 1
                entries_log.append({"day": day, "ts": str(ts), "hm": bar["hm_end"], "ticker": tk,
                                    "qty": qty, "price": round(fill, 2), "source": c["source"],
                                    "sl_pct": round(sl_pct, 2), "budget_usd": round(budget, 2),
                                    "binding": (eb or {}).get("binding"), "min_one": min_one,
                                    "why": c.get("why")})
            equity_curve.append({"ts": str(ts), "equity": round(equity, 2), "cash": round(cash, 2)})
        # 세션 종료: 남은 포지션 세션 카운트 +1 (다음 세션에도 보유 시)
        for p in positions.values():
            p["sessions_held"] = int(p.get("sessions_held", 1)) + 1
        per_day[day] = {"realized_usd": round(pd_day["realized"], 2), "entries": pd_day["entries"],
                        "trades": len(pd_day["trades"]), "halted": pd_day["halted"],
                        "open_at_close": list(positions.keys())}
    open_value = 0.0
    for tk, p in positions.items():
        px = mark(tk, pd.Timestamp(days[-1]) + pd.Timedelta(hours=16)) or p["entry"]
        open_value += px * p["qty"]
        trades.append(L.make_trade(days[-1], tk, "LONG", p["entry_ts"], str(pd.Timestamp(days[-1]) + pd.Timedelta(hours=16)),
                                   p["entry"], px, p["qty"], "OPEN_AT_WINDOW_END(평가)", rt_cost=rt))
    st = L.trade_stats([t for t in trades if "OPEN_AT_WINDOW_END" not in t["reason"]], 150.0)
    return {"label": label, "budget_mode": budget_mode, "trades": trades, "stats": st,
            "entries": entries_log,
            "per_day": per_day, "blocks": dict(sorted(blocks.items(), key=lambda x: -x[1])),
            "signal_log": signal_log, "final_cash": round(cash, 2), "open_value": round(open_value, 2),
            "final_equity": round(cash + open_value, 2), "equity_curve": equity_curve}
