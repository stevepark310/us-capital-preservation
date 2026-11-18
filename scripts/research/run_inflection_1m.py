#!/usr/bin/env python3
"""1분봉 변곡점 포착 — 10m · 5m · 1m 직접 비교 백테스트 (2026-11-18).

동훈님 지시 (2026-11-18 12:16 KST): "포착 시간을 1분단위로 바꿔보고 다시 백테스트 돌려봐"

직전 연구(`run_inflection_100pct.py`, `inflection_100pct_research_20261118.md`)의 결론은
"포착률을 올리면 돈은 더 잃는다"(60일 corr -0.768, 1%p당 -$2.02)였고, 5분봉 판정은
같은 포착률에서 거래 1.4~1.6배 · 손실 1.4~1.7배로 악화됐다. 이 스크립트는 같은 규약을
**1분봉 판정**으로 확장해 두 가지를 분리 실측한다.

  ① **빠른 진입 이득** — 같은 c에서 1분봉은 피벗 확인이 얼마나 빨라지는가(lag 분),
     그 대가로 오라클 대비 얼마를 더 보존하는가.
  ② **노이즈 비용** — 1분봉 판정이 만들어내는 추가 신호가 정밀도·거래수·수수료를
     얼마나 악화시키는가.

연구 전용 스크립트다.
  · `core/` · `config/` · 실전 매매 로직은 한 줄도 바꾸지 않는다(읽기 전용 import만).
  · git commit/push 없음 — `run_10m_pulse.py`의 커밋과 충돌을 피한다. 산출물은 로컬 파일뿐.

실행
  python3 scripts/research/run_inflection_1m.py
출력
  data/research_inflection_1m_20261118.json

데이터 (실측으로 확정한 가용 범위)
  · yfinance 1분봉은 **최근 30일 이내**만 조회된다(7~8일 단위 청크 필수, 초과 시
    "The requested range must be within the last 30 days").
  · 실제 반환 범위: **2026-10-19 ~ 2026-11-17, 22개 세션, 전 세션 390봉**(결측 0).
  · 60일(2026-08-25~) 1분봉은 **조회 불가** — 추정하지 않고 비교 창을 22일로 통일했다.
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import date, time as dtime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import i10_lib as L                       # noqa: E402
import i10_core_engine as CE              # noqa: E402
import run_inflection_100pct as R1        # noqa: E402  (정의 재사용 — 이벤트·시뮬레이터·채점)

BASE = L.BASE_DIR
DATA = os.path.join(BASE, "data")
CACHE = L.CACHE
OUT = os.path.join(DATA, "research_inflection_1m_20261118.json")

PAIRS = R1.PAIRS                                   # {"SEMI": (SOXL, SOXS, 1.5), "NDX": (TQQQ, SQQQ, 1.0)}
SYMS = ["SOXL", "TQQQ", "SOXS", "SQQQ"]
BUDGET = R1.BUDGET                                 # $150/건
RT = R1.RT                                         # 왕복 0.24%
RT_SLIP = RT + 0.0024                              # 편도 +12bp 민감도
C_SPECTRUM = (3.0, 2.5, 2.0, 1.5, 1.0, 0.5)         # 지시된 c 스펙트럼
TFS: Tuple[Tuple[str, int], ...] = (("10m", 10), ("5m", 5), ("1m", 1))
DELAY_KS = (0, 1, 2, 3, 5, 10, 15, 20, 30, 60)     # 오라클 지연(분)
hm_min = R1.hm_min


# ============================================================== 1. 1분봉 확보·검증
def _fetch_meta_path(sym: str) -> str:
    return os.path.join(CACHE, f"__1m_{sym}_30d.pkl")


def fetch_1m(sym: str, refresh: bool = False) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """7일 단위 청크로 1분봉을 모아 하나의 프레임으로. 청크 응답을 그대로 기록한다."""
    now_et = pd.Timestamp.now(tz="America/New_York")
    end_d = (now_et + pd.Timedelta(days=1)).date()          # end 는 배타 → 오늘 세션 포함
    first_d = (now_et - pd.Timedelta(days=29)).date()       # 30일 제한의 안쪽 경계
    wins: List[Tuple[date, date]] = []
    s = first_d
    while s < end_d:
        e = s + pd.Timedelta(days=7)
        wins.append((s, min(e, end_d)))
        s = e

    path = _fetch_meta_path(sym)
    if os.path.exists(path) and not refresh:
        try:
            with open(path, "rb") as f:
                blob = pickle.load(f)
            if (blob.get("meta", {}).get("anchor_end") == str(end_d)
                    and blob.get("meta", {}).get("first") == str(first_d) and len(blob.get("df", []))):
                return blob["df"], blob["meta"]
        except Exception:
            pass

    import yfinance as yf
    frames: List[pd.DataFrame] = []
    log: List[Dict[str, Any]] = []
    for s_d, e_d in wins:
        got = None
        for bump in (0, 1, 2):
            s_try = s_d + pd.Timedelta(days=bump)
            if s_try >= e_d:
                break
            df = yf.download(sym, interval="1m", start=str(s_try), end=str(e_d),
                             progress=False, auto_adjust=False)
            n = 0 if df is None else len(df)
            log.append({"window": f"{s_try}~{e_d}", "rows": int(n),
                        "first": None if not n else str(df.index[0]),
                        "last": None if not n else str(df.index[-1])})
            if n:
                got = df
                break
            log[-1]["note"] = "empty (30일 제한 또는 휴장)"
        if got is None:
            continue
        if getattr(got.columns, "nlevels", 1) > 1:
            got.columns = [c[0] for c in got.columns]
        frames.append(got)

    if not frames:
        return pd.DataFrame(), {"anchor_end": str(end_d), "first": str(first_d),
                                "chunks": log, "error": "1m 데이터 없음"}
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("America/New_York")
    sessions = sorted({str(ts.date()) for ts in df.index})
    meta = {"anchor_end": str(end_d), "first": str(first_d), "chunks": log,
            "rows_raw": int(len(df)), "first_ts": str(df.index[0]), "last_ts": str(df.index[-1]),
            "sessions_raw": sessions}
    try:
        with open(path, "wb") as f:
            pickle.dump({"df": df, "meta": meta}, f)
    except Exception:
        pass
    return df, meta


def bars_1m(df: pd.DataFrame) -> pd.DataFrame:
    """정규장 09:30~16:00 ET만. 라벨은 봉 시작, hm_end 는 봉 종료(=결정·체결) 시각."""
    if df is None or not len(df):
        return pd.DataFrame()
    d = df.copy()
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_convert("America/New_York")
    t = d.index.time
    d = d[(t >= dtime(9, 30)) & (t < dtime(16, 0))]
    d.index = d.index.tz_localize(None)
    d = d[~d.index.duplicated(keep="first")]
    d["session"] = [str(ts.date()) for ts in d.index]
    d["hm_end"] = [(ts + pd.Timedelta(minutes=1)).strftime("%H:%M") for ts in d.index]
    return d


def bars_5m(sym: str) -> pd.DataFrame:
    d = L.regular_session(L.load5m(sym, "60d"))
    if not len(d):
        return d
    d = d.copy()
    d["session"] = [str(ts.date()) for ts in d.index]
    d["hm_end"] = [(ts + pd.Timedelta(minutes=5)).strftime("%H:%M") for ts in d.index]
    return d


def validate_data(b1m: Dict[str, pd.DataFrame], panel10: Dict[str, pd.DataFrame],
                  panel5: Dict[str, pd.DataFrame], days: List[str]) -> Dict[str, Any]:
    """1분봉 품질 + 1m→10m 집계가 기존 5m→10m 봉과 일치하는지 대조."""
    out: Dict[str, Any] = {"per_symbol": {}, "bars_per_session": {}, "ten_min_equivalence": {}}
    for sym, d in b1m.items():
        cnt = d.groupby("session").size()
        per_day = {k: int(v) for k, v in cnt.items()}
        missing = {}
        for day in days:
            g = d[d["session"] == day]
            have = set(g["hm_end"])
            exp = {(pd.Timestamp(f"{day} 09:30") + pd.Timedelta(minutes=i + 1)).strftime("%H:%M")
                   for i in range(390)}
            miss = sorted(exp - have)
            if miss:
                missing[day] = {"n": len(miss), "sample": miss[:8]}
        out["per_symbol"][sym] = {
            "sessions": len(cnt), "first": str(d["session"].iloc[0]),
            "last": str(d["session"].iloc[-1]),
            "bars_total": int(len(d)), "bars_min": int(cnt.min()), "bars_max": int(cnt.max()),
            "nan_close": int(d["Close"].isna().sum()),
            "zero_volume_bars": int((d["Volume"].fillna(0) <= 0).sum()),
            "sessions_missing_minutes": missing,
        }
        out["bars_per_session"][sym] = per_day

    # 1m → 10m 집계 vs 5m → 10m (기존 연구 데이터) 대조
    for sym in SYMS:
        d1 = b1m.get(sym)
        p10 = panel10.get(sym)
        if d1 is None or p10 is None or not len(d1) or not len(p10):
            continue
        diffs, vols, nbars = [], [], 0
        for day in days:
            g = d1[d1["session"] == day]
            if len(g) < 100:
                continue
            agg = g.resample("10min", label="left", closed="left").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            ).dropna(subset=["Close"])
            ref = p10[p10["session"] == day]
            common = agg.index.intersection(ref.index)
            if not len(common):
                continue
            diffs.append((agg.loc[common, "Close"].to_numpy(float) - ref.loc[common, "Close"].to_numpy(float)))
            vols.append((agg.loc[common, "Volume"].fillna(0).to_numpy(float)
                         - ref.loc[common, "Volume"].fillna(0).to_numpy(float)))
            nbars += len(common)
        if not diffs:
            continue
        dd = np.concatenate(diffs)
        vv = np.concatenate(vols)
        out["ten_min_equivalence"][sym] = {
            "bars_compared": int(nbars),
            "close_max_abs_diff": round(float(np.nanmax(np.abs(dd))), 4),
            "close_bars_differing_over_0p01pct": int(np.nansum(np.abs(dd) > 0.0001 * 100)),
            "close_max_abs_diff_pct": round(float(np.nanmax(np.abs(dd)) / max(1e-9, np.nanmean(np.abs(dd) + 1))), 4),
            "volume_bars_differing": int(np.nansum(np.abs(vv) > 1)),
            "volume_identical_pct": round(100.0 * float(np.nansum(np.abs(vv) <= 1)) / len(vv), 1),
        }
    return out


# ============================================================== 2. 지표·격자 준비
def enrich_bars(d: pd.DataFrame) -> pd.DataFrame:
    """R1.prep_pair 와 같은 지표 세트를 임의 봉 주기에서 계산."""
    d = d.copy()
    d["rsi2"] = L.rsi(d["Close"], 2)
    d["rsi14"] = L.rsi(d["Close"], 14)
    _m, _u, _l, pctb = L.bollinger(d["Close"], 20, 2.0)
    d["pct_b"] = pctb
    d["atr"] = L.atr(d, 14)
    d["atr_pct"] = d["atr"] / d["Close"] * 100
    d["vol_ma20"] = d["Volume"].rolling(20).mean().shift(1)
    d["rvol"] = d["Volume"] / d["vol_ma20"].replace(0, np.nan)
    d["ema9"] = L.ema(d["Close"], 9)
    d["ema21"] = L.ema(d["Close"], 21)
    rows = []
    for _day, g in d.groupby("session"):
        g = g.copy()
        tp = (g["High"] + g["Low"] + g["Close"]) / 3.0
        v = g["Volume"].fillna(0).astype(float)
        g["vwap"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
        dev = g["Close"] - g["vwap"]
        g["vwap_sd"] = dev.expanding(min_periods=6).std()
        g["vwap_z"] = dev / g["vwap_sd"].replace(0, np.nan)
        rows.append(g)
    return pd.concat(rows).sort_index()


def prep_from_bars(bars: Dict[str, pd.DataFrame], days: List[str], bar_min: int) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for pname, (lead, inv, _thr) in PAIRS.items():
        if lead not in bars or inv not in bars:
            continue
        dl = enrich_bars(bars[lead])
        di = enrich_bars(bars[inv])
        for day in days:
            g = dl[dl["session"] == day]
            gi = di[di["session"] == day]
            if len(g) < 5 or len(gi) < 5:
                continue
            gi = gi.reindex(g.index)
            gi = gi.ffill().bfill()
            out[f"{pname}|{day}"] = {
                "day": day, "pair": pname, "bar_min": bar_min,
                "hm": list(g["hm_end"]),
                "L_close": g["Close"].to_numpy(float), "L_open": g["Open"].to_numpy(float),
                "L_high": g["High"].to_numpy(float), "L_low": g["Low"].to_numpy(float),
                "rsi2": g["rsi2"].to_numpy(float), "rsi14": g["rsi14"].to_numpy(float),
                "pct_b": g["pct_b"].to_numpy(float), "rvol": g["rvol"].to_numpy(float),
                "atr_pct": g["atr_pct"].to_numpy(float), "vwap": g["vwap"].to_numpy(float),
                "vwap_z": g["vwap_z"].to_numpy(float), "ema9": g["ema9"].to_numpy(float),
                "ema21": g["ema21"].to_numpy(float),
                "I_close": gi["Close"].to_numpy(float), "I_open": gi["Open"].to_numpy(float),
                "I_high": gi["High"].to_numpy(float), "I_low": gi["Low"].to_numpy(float),
                "I_atr_pct": gi["atr_pct"].to_numpy(float),
            }
    return out


def retime_atr(prep_src: Dict[str, Dict[str, Any]], panel10: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
    """1분봉 판정 배열에 **10분봉 스케일 ATR%** 를 주입 — 청산 규격만 10분으로 고정한 절제 실험.

    각 1분봉 종료시각 기준 **직전에 완료된 10분봉**의 ATR%를 쓴다(룩어헤드 없음 —
    09:31 봉은 전일 16:00 봉의 ATR% 값을 갖는다). 손절은 어차피 1.5% 하한으로 묶이므로
    실제로 달라지는 것은 추적청산 폭(1.5×ATR) 하나다.
    """
    atr10: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for pname, (lead, inv, _t) in PAIRS.items():
        for sym in (lead, inv):
            if sym in atr10 or sym not in panel10:
                continue
            e = enrich_bars(panel10[sym])
            atr10[sym] = (e.index.to_numpy(), e["atr_pct"].to_numpy(float))
    out: Dict[str, Dict[str, Any]] = {}
    for key, s in prep_src.items():
        pname = s["pair"]
        lead, inv, _t = PAIRS[pname]
        if lead not in atr10 or inv not in atr10:
            continue
        ts = np.array([np.datetime64(pd.Timestamp(f"{s['day']} {hm}")) for hm in s["hm"]])
        arrs = {}
        for tag, sym, field in (("L", lead, "atr_pct"), ("I", inv, "I_atr_pct")):
            t10, a10 = atr10[sym]
            idx = np.searchsorted(t10, ts, side="right") - 1
            idx = np.clip(idx, 0, len(a10) - 1)
            arrs[field] = a10[idx]
        t = dict(s)
        t.update(arrs)
        t["bar_min"] = s["bar_min"]
        t["exit_atr_tf"] = "10m"
        out[key] = t
    return out


def pivot_trigger_times(prep: Dict[str, Dict[str, Any]], days: List[str],
                        c: float) -> List[Dict[str, Any]]:
    """포지션과 무관한 피벗 신호 시각 전부(노이즈 계측용)."""
    rows: List[Dict[str, Any]] = []
    tp = {"c": c, "c_exit": c}
    for day in days:
        for pname in PAIRS:
            s = prep.get(f"{pname}|{day}")
            if not s:
                continue
            st: Dict[str, Any] = {}
            for i, hm in enumerate(s["hm"]):
                side = R1.TRIGGERS["pivot"](s, i, st, tp)
                if side:
                    rows.append({"day": day, "pair": pname, "hm": hm, "side": side})
    return rows


def event_signal_stats(prep: Dict[str, Dict[str, Any]], days: List[str], ev: List[Dict[str, Any]],
                       c: float, tf: str, trigs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """같은 c에서 각 TF가 이벤트 레그를 언제(몇 분 뒤) 잡는지 + 진입가 이탈·레그 잔여 수익."""
    trigs = trigs if trigs is not None else pivot_trigger_times(prep, days, c)
    lags, departs, rets, usd = [], [], [], []
    captured = 0
    for e in ev:
        need = 1 if e["dir"] == "UP" else -1
        key = "L_close" if need > 0 else "I_close"
        cand = [t for t in trigs if t["day"] == e["day"] and t["pair"] == e["pair"]
                and t["side"] == need and e["t0"] <= t["hm"] <= e["t1"]]
        if not cand:
            continue
        s = prep.get(f"{e['pair']}|{e['day']}")
        if not s:
            continue
        hm0 = min((t["hm"] for t in cand), key=hm_min)
        try:
            i0 = s["hm"].index(hm0)
            i1 = s["hm"].index(e["t1"])
        except ValueError:
            continue
        px = float(s[key][i0])
        px1 = float(s[key][i1])
        if px <= 0:
            continue
        captured += 1
        lag = hm_min(hm0) - hm_min(e["t0"])
        dep = (px / e["instr_px0"] - 1) * 100
        r = (px1 / px - 1) * 100 - RT * 100
        lags.append(lag)
        departs.append(dep)
        rets.append(r)
        usd.append(r / 100 * BUDGET)
    n_sessions = max(1, len(days) * len(PAIRS))
    lag_arr = np.array(lags, float) if lags else np.array([np.nan])
    return {
        "tf": tf, "c": c, "n_events": len(ev), "n_captured": captured,
        "recall_signal_pct": round(100.0 * captured / len(ev), 1) if ev else None,
        "lag_median_min": float(np.median(lag_arr)) if lags else None,
        "lag_mean_min": round(float(np.mean(lag_arr)), 1) if lags else None,
        "lag_p90_min": float(np.percentile(lag_arr, 90)) if lags else None,
        "departure_mean_pct": round(float(np.mean([abs(x) for x in departs])), 3) if departs else None,
        "leg_net_mean_pct": round(float(np.mean(rets)), 3) if rets else None,
        "leg_net_usd_total": round(float(sum(usd)), 2) if usd else None,
        "triggers_total": len(trigs), "triggers_per_session": round(len(trigs) / n_sessions, 2),
    }


def trigger_noise(trigs: List[Dict[str, Any]], ev: List[Dict[str, Any]], days: List[str]) -> Dict[str, Any]:
    """이벤트 레그 밖에서 발생한 신호(거짓신호 후보) 비중."""
    n_sess = max(1, len(days) * len(PAIRS))
    inside = 0
    for t in trigs:
        for e in ev:
            if (e["day"] == t["day"] and e["pair"] == t["pair"]
                    and e["t0"] <= t["hm"] <= e["t1"]
                    and ((t["side"] > 0) == (e["dir"] == "UP"))):
                inside += 1
                break
    return {"triggers": len(trigs), "inside_event_legs": inside,
            "outside_pct": round(100.0 * (len(trigs) - inside) / len(trigs), 1) if trigs else None,
            "per_session": round(len(trigs) / n_sess, 2)}


# ============================================================== 3. 오라클 지연 (분 단위)
def delay_minutes(b1m: Dict[str, pd.DataFrame], ev: List[Dict[str, Any]]) -> Dict[str, Any]:
    """오라클(피벗 봉 확정 즉시 진입, 레그 끝 청산)에서 k분 늦을 때의 잔여 수익."""
    px_map: Dict[Tuple[str, str], Dict[str, float]] = {}
    for sym, d in b1m.items():
        lut: Dict[Tuple[str, str], Dict[str, float]] = {}
        for day, g in d.groupby("session"):
            lut[(sym, day)] = dict(zip(g["hm_end"], g["Close"].to_numpy(float)))
        px_map.update(lut)

    def price(sym: str, day: str, minute: int) -> Optional[float]:
        m = px_map.get((sym, day))
        if not m:
            return None
        for bump in (0, 1, 2, 3):
            hm = f"{(minute + bump) // 60:02d}:{(minute + bump) % 60:02d}"
            if hm in m:
                return float(m[hm])
        return None

    out: Dict[str, Any] = {}
    for k in DELAY_KS:
        rows = []
        for e in ev:
            t0, t1 = hm_min(e["t0"]), hm_min(e["t1"])
            px_in = price(e["instrument"], e["day"], t0 + k)
            px_out = price(e["instrument"], e["day"], t1)
            if px_in is None or px_out is None or px_in <= 0:
                continue
            ret = (px_out / px_in - 1) * 100 - RT * 100
            oracle = e["instr_ret_pct"] - RT * 100
            rows.append({"departure_pct": (px_in / e["instr_px0"] - 1) * 100,
                         "ret_pct": ret, "usd": ret / 100 * BUDGET,
                         "retained_frac": ret / oracle if oracle else None,
                         "minutes_lost": t1 - (t0 + k)})
        if not rows:
            continue
        ret_a = np.array([r["ret_pct"] for r in rows], float)
        out[f"delay_{k}m"] = {
            "n": len(rows),
            "mean_abs_departure_pct": round(float(np.mean([abs(r["departure_pct"]) for r in rows])), 3),
            "mean_retained_frac": round(float(np.nanmean([r["retained_frac"] for r in rows
                                                          if r["retained_frac"] is not None])), 3),
            "mean_ret_pct": round(float(ret_a.mean()), 3),
            "usd_total": round(float(sum(r["usd"] for r in rows)), 2),
            "neg_ret_share_pct": round(100.0 * float((ret_a < 0).mean()), 1),
            "mean_minutes_left": round(float(np.mean([r["minutes_lost"] for r in rows])), 1),
        }
    orc = out.get("delay_0m", {}).get("usd_total")
    for k, v in out.items():
        if orc:
            v["usd_vs_oracle_pct"] = round(100.0 * v["usd_total"] / orc, 1)
    return out


# ============================================================== 4. 격자 실행
def run_grid(prep: Dict[str, Dict[str, Any]], dt: Dict[str, Dict[str, bool]], days: List[str],
             ev: List[Dict[str, Any]], tf: str, bar_min: int) -> List[Dict[str, Any]]:
    cfgs: List[Dict[str, Any]] = [
        {"label": "DIP(현행 신호계열)", "trigger": "dip",
         "params": {"rsi_max": 20.0, "pctb_max": 0.15, "rvol_min": 1.0},
         "filters": (), "blackout": True, "exit": "trail", "tm": 1.5, "c": None, "fname": "-"},
    ]
    for c in C_SPECTRUM:
        for fname, flt in (("무필터", ()), ("+거래량", ("vol",))):
            for exit_mode in ("trail", "flip"):
                cfgs.append({"label": f"피벗 c={c}% {fname}", "trigger": "pivot",
                             "params": {"c": c, "c_exit": c}, "filters": flt,
                             "blackout": False, "exit": exit_mode, "tm": 1.5,
                             "c": c, "fname": fname})
    rows: List[Dict[str, Any]] = []
    for cfg in cfgs:
        diag: Dict[str, Any] = {}
        tr = R1.simulate(prep, dt, days, cfg["trigger"], cfg["params"], cfg["exit"],
                         filters=cfg["filters"], blackout=cfg["blackout"],
                         trail_mult=cfg["tm"], diag=diag)
        sc = R1.score(tr, ev)
        pf = R1.perf(tr)
        row = {"tf": tf, "bar_min": bar_min, "config": cfg["label"], "c": cfg["c"],
               "filter": cfg["fname"], "exit": cfg["exit"],
               "recall_pct": sc.get("recall_pct"), "recall_overlap_pct": sc.get("recall_overlap_pct"),
               "precision_pct": sc.get("precision_pct"), "n_false": sc.get("n_false"),
               "n_trades": pf["n"], "fee_usd": pf["fee_usd"], "pnl_usd": pf["pnl_usd"],
               "pf": pf["pf"], "mdd_usd": pf["mdd_usd"], "win_pct": pf["win_pct"],
               "avg_pct": pf["avg_pct"], "t_stat": pf["t_stat"],
               "fee_share_pct": pf["fee_share_pct"], "in_market_pct": pf["in_market_pct"],
               "avg_hold_min": pf["avg_hold_min"],
               "capture_median": sc.get("capture_ratio_median"),
               "trades_per_session": round(pf["n"] / (len(days) * len(PAIRS)), 2),
               "signals": diag.get("signals", 0),
               "skip_in_position": diag.get("skip_in_position", 0),
               "skip_blackout": diag.get("skip_blackout", 0),
               "skip_late": diag.get("skip_late", 0),
               "per_day": pf["per_day"]}
        rows.append(row)
        print(f"  [{tf}] {cfg['label']} / {cfg['exit']}: R={row['recall_pct']}% "
              f"P={row['precision_pct']}% n={row['n_trades']} pnl=${row['pnl_usd']} "
              f"fee=${row['fee_usd']} PF={row['pf']} MDD=${row['mdd_usd']}", flush=True)
    return rows


def compare_table(grid: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """(c, 필터, 청산) 셀마다 10m·5m·1m 을 나란히."""
    out: List[Dict[str, Any]] = []
    for c in C_SPECTRUM:
        for fname in ("무필터", "+거래량"):
            for exit_mode in ("trail", "flip"):
                cell: Dict[str, Any] = {"c": c, "filter": fname, "exit": exit_mode}
                for tf, _b in TFS:
                    m = [r for r in grid[tf] if r["c"] == c and r["filter"] == fname
                         and r["exit"] == exit_mode]
                    if m:
                        r = m[0]
                        cell[tf] = {k: r[k] for k in ("recall_pct", "precision_pct", "n_false",
                                                      "n_trades", "fee_usd", "pnl_usd", "pf",
                                                      "mdd_usd", "capture_median",
                                                      "in_market_pct", "avg_hold_min",
                                                      "trades_per_session")}
                out.append(cell)
    return out


def ladder(grid: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """포착률 사다리 — 각 TF·청산별로 50% / 80% / 95~100% 구간의 최선 설정."""
    bands = [("50%", 45.0, 55.0), ("80%", 70.0, 85.0), ("95~100%", 93.0, 100.1)]
    out: List[Dict[str, Any]] = []
    for tf, _b in TFS:
        for exit_mode in ("trail", "flip"):
            for bname, lo, hi in bands:
                sel = [r for r in grid[tf] if r["exit"] == exit_mode and r["c"] is not None
                       and r["recall_pct"] is not None and lo <= r["recall_pct"] < hi]
                if not sel:
                    continue
                best = max(sel, key=lambda r: r["pnl_usd"])
                out.append({"tf": tf, "band": bname, "exit": exit_mode,
                            "config": f"c={best['c']}% {best['filter']}",
                            "recall_pct": best["recall_pct"], "precision_pct": best["precision_pct"],
                            "n_false": best["n_false"], "n_trades": best["n_trades"],
                            "fee_usd": best["fee_usd"], "pnl_usd": best["pnl_usd"],
                            "pf": best["pf"], "mdd_usd": best["mdd_usd"],
                            "n_configs_in_band": len(sel)})
    for tf, _b in TFS:
        for r in grid[tf]:
            if r["config"].startswith("DIP"):
                out.append({"tf": tf, "band": "현행신호계열", "exit": r["exit"],
                            "config": "DIP(RSI2≤20·%B≤0.15·RVOL≥1.0) · 추적청산",
                            "recall_pct": r["recall_pct"], "precision_pct": r["precision_pct"],
                            "n_false": r["n_false"], "n_trades": r["n_trades"],
                            "fee_usd": r["fee_usd"], "pnl_usd": r["pnl_usd"], "pf": r["pf"],
                            "mdd_usd": r["mdd_usd"], "n_configs_in_band": 1})
    return out


def bridge(cfg: Dict[str, Any], prep_10: Dict[str, Dict[str, Any]], prep_1: Dict[str, Dict[str, Any]],
           dt: Dict[str, Dict[str, bool]], days: List[str], ev: List[Dict[str, Any]]) -> Dict[str, Any]:
    """PnL(1m) − PnL(10m) 를 '공유 레그 이득 / 1m 추가 포착 / 거짓신호'로 분해."""
    def run(prep):
        tr = R1.simulate(prep, dt, days, "pivot", {"c": cfg["c"], "c_exit": cfg["c"]},
                         cfg["exit"], filters=cfg["filters"], blackout=False, trail_mult=1.5)
        return tr

    def match(trs: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        m: Dict[int, Dict[str, Any]] = {}
        for k, e in enumerate(ev):
            for t in trs:
                if (t["day"] == e["day"] and t["ticker"] == e["instrument"]
                        and e["t0"] <= t["entry_hm"] <= e["t1"]):
                    m.setdefault(k, t)
        return m

    t10, t1 = run(prep_10), run(prep_1)
    m10, m1 = match(t10), match(t1)
    shared = sorted(set(m10) & set(m1))
    only1 = sorted(set(m1) - set(m10))
    only10 = sorted(set(m10) - set(m1))
    idx_used = {"10m": {id(t) for t in m10.values()}, "1m": {id(t) for t in m1.values()}}
    junk10 = [t for t in t10 if id(t) not in idx_used["10m"]]
    junk1 = [t for t in t1 if id(t) not in idx_used["1m"]]

    shared_delta = sum(m1[k]["pnl_usd"] - m10[k]["pnl_usd"] for k in shared)
    only1_pnl = sum(m1[k]["pnl_usd"] for k in only1)
    only10_pnl = -sum(m10[k]["pnl_usd"] for k in only10)
    junk_delta = sum(t["pnl_usd"] for t in junk1) - sum(t["pnl_usd"] for t in junk10)
    total_delta = sum(t["pnl_usd"] for t in t1) - sum(t["pnl_usd"] for t in t10)
    return {
        "c": cfg["c"], "exit": cfg["exit"], "filters": list(cfg["filters"]),
        "pnl_10m": round(sum(t["pnl_usd"] for t in t10), 2),
        "pnl_1m": round(sum(t["pnl_usd"] for t in t1), 2),
        "delta_usd": round(total_delta, 2),
        "shared_events": len(shared),
        "shared_leg_delta_usd": round(shared_delta, 2),
        "only_1m_events": len(only1), "only_1m_pnl_usd": round(only1_pnl, 2),
        "only_10m_events": len(only10), "only_10m_lost_pnl_usd": round(only10_pnl, 2),
        "false_trade_delta_usd": round(junk_delta, 2),
        "n_false_10m": len(junk10), "n_false_1m": len(junk1),
        "fee_10m": round(sum(t["fee_usd"] for t in t10), 2),
        "fee_1m": round(sum(t["fee_usd"] for t in t1), 2),
        "residual_usd": round(total_delta - (shared_delta + only1_pnl + only10_pnl + junk_delta), 2),
        "mean_shared_delta_usd": round(shared_delta / len(shared), 3) if shared else None,
    }


def trigger_quality(prep: Dict[str, Dict[str, Any]], days: List[str], c: float,
                    horizon_min: int, bar_min: int) -> Dict[str, Any]:
    """피벗 트리거 직후 horizon_min 분의 MFE/MAE(비용 전)."""
    horizon = max(1, horizon_min // bar_min)
    mfe, mae = [], []
    tp = {"c": c, "c_exit": c}
    for day in days:
        for pname in PAIRS:
            s = prep.get(f"{pname}|{day}")
            if not s:
                continue
            st: Dict[str, Any] = {}
            for i in range(len(s["hm"])):
                side = R1.TRIGGERS["pivot"](s, i, st, tp)
                if not side:
                    continue
                key = "L_close" if side > 0 else "I_close"
                px = s[key][i]
                fut = s[key][i + 1:i + 1 + horizon]
                if not len(fut) or px <= 0:
                    continue
                if side > 0:
                    mfe.append((fut.max() / px - 1) * 100)
                    mae.append((fut.min() / px - 1) * 100)
                else:
                    mfe.append((px / fut.min() - 1) * 100)
                    mae.append((px / fut.max() - 1) * 100)
    if not mfe:
        return {}
    m, n = np.array(mfe), np.array(mae)
    return {"c": c, "n": len(m), "horizon_min": horizon_min,
            "mfe_mean_pct": round(float(m.mean()), 2), "mae_mean_pct": round(float(n.mean()), 2),
            "mfe_over_mae": round(float(m.mean() / abs(n.mean())), 2) if n.mean() else None,
            "p_mfe_ge_cost_pct": round(100.0 * float((m >= RT * 100 + 0.1).mean()), 1)}


def engine_trades(days: List[str]) -> List[Dict[str, Any]]:
    """현행 엔진(L0) 10분봉 재현 — R1.main 과 같은 준비."""
    panel = L.panel(period="60d")
    panel5 = {s: CE.five_min_metrics(L.load5m(s, "60d")) for s in L.SYMBOLS}
    vix10 = panel["^VIX"]["Close"] if "^VIX" in panel else None
    dxy10 = L.to10m(L.load5m("DX-Y.NYB", "60d"))["Close"]
    sentiment = None
    try:
        sentiment = float(json.load(open(os.path.join(DATA, "news_sentiment_report.json")))
                          .get("average_sentiment_score"))
    except Exception:
        pass
    from core import execution_policy
    policy = execution_policy.load_policy()
    params = json.load(open(os.path.join(DATA, "strategy_params.json")))
    return R1.run_engine(panel, panel5, days, vix10, dxy10, sentiment, policy, params)


# ============================================================== 5. main
def main() -> int:
    t_start = pd.Timestamp.now(tz="Asia/Seoul")
    print("1) 1분봉 확보", flush=True)
    b1m: Dict[str, pd.DataFrame] = {}
    metas: Dict[str, Any] = {}
    for sym in SYMS:
        raw, meta = fetch_1m(sym)
        b1m[sym] = bars_1m(raw)
        metas[sym] = meta
        print(f"   {sym}: {len(b1m[sym])}봉, {b1m[sym]['session'].nunique()}세션 "
              f"({b1m[sym]['session'].iloc[0]}~{b1m[sym]['session'].iloc[-1]})", flush=True)

    days_1m = sorted(set.intersection(*[{d for d in b1m[s]["session"].unique()} for s in SYMS]))
    print(f"   공통 세션 {len(days_1m)}개: {days_1m[0]} ~ {days_1m[-1]}", flush=True)

    print("2) 10m/5m 패널", flush=True)
    panel10 = L.panel(period="60d")
    panel5 = {s: bars_5m(s) for s in L.SYMBOLS if s in L.SYMBOLS}
    all_days_60 = sorted(panel10["SOXL"]["session"].unique())
    days = [d for d in all_days_60 if d in set(days_1m)]
    print(f"   비교 창 {len(days)}세션: {days[0]} ~ {days[-1]}", flush=True)

    print("3) 데이터 검증", flush=True)
    val = validate_data(b1m, panel10, panel5, days)
    for sym in SYMS:
        v = val["per_symbol"][sym]
        print(f"   {sym}: {v['sessions']}세션 {v['bars_min']}~{v['bars_max']}봉/세션 "
              f"NaN={v['nan_close']} 결측세션={len(v['sessions_missing_minutes'])}", flush=True)
    for sym, v in val["ten_min_equivalence"].items():
        print(f"   1m→10m vs 5m→10m {sym}: {v['bars_compared']}봉 "
              f"최대괴리={v['close_max_abs_diff']} ({v['close_bars_differing_over_0p01pct']}봉)", flush=True)

    print("4) 이벤트 집합(10m 정의 그대로)", flush=True)
    dt_all = R1.daily_trend(panel10, all_days_60)
    ev_all = R1.build_events(panel10, all_days_60)
    ev = [e for e in ev_all if e["day"] in set(days)]
    ev_5d = [e for e in ev_all if e["day"] in set(L.SESSION_DAYS)]
    print(f"   비교 창 이벤트 {len(ev)}개 / 5일 {len(ev_5d)}개 / 60일 {len(ev_all)}개", flush=True)

    print("5) 격자 준비(10m·5m·1m)", flush=True)
    prep_tf: Dict[str, Dict[str, Dict[str, Any]]] = {}
    prep_tf["10m"] = R1.prep_pair(panel10, days)
    prep_tf["5m"] = prep_from_bars(panel5, days, 5)
    prep_tf["1m"] = prep_from_bars(b1m, days, 1)
    for tf, _b in TFS:
        print(f"   {tf}: {len(prep_tf[tf])} 세션×페어 준비", flush=True)

    result: Dict[str, Any] = {
        "generated_at_kst": t_start.strftime("%Y-%m-%d %H:%M:%S"),
        "directive": "2026-11-18 12:16 KST — 포착 시간을 1분단위로 바꿔보고 다시 백테스트",
        "cost_round_trip_pct": RT * 100, "cost_slippage_round_trip_pct": RT_SLIP * 100,
        "budget_usd": BUDGET,
        "window": {"compare": {"start": days[0], "end": days[-1], "sessions": len(days)},
                   "five_day": L.SESSION_DAYS,
                   "sixty_day": {"start": all_days_60[0], "end": all_days_60[-1],
                                 "sessions": len(all_days_60)}},
        "data_availability": {
            "1m_period": {"start": days_1m[0], "end": days_1m[-1], "sessions": len(days_1m),
                          "bounded_by": "Yahoo 1m 30일 제한(7일 청크)"},
            "1m_not_available": {"60d_window": f"{all_days_60[0]}~{all_days_60[-1]}",
                                 "reason": "1m 봉은 최근 30일만 조회 가능 — 미확보 기간은 추정하지 않음"},
            "fetch_chunks": {s: metas[s].get("chunks") for s in SYMS},
            "raw_rows": {s: metas[s].get("rows_raw") for s in SYMS},
            "five_day_window_covered": all(d in days_1m for d in L.SESSION_DAYS),
        },
        "validation": val,
        "event_set": {"n_compare": len(ev), "n_5d": len(ev_5d), "n_60d": len(ev_all),
                      "matches_prior_24": [True if len(ev_5d) == 24 else False][0],
                      "sum_abs_instr_ret_compare": round(sum(abs(e["instr_ret_pct"]) for e in ev), 1),
                      "oracle_usd_compare": round(sum(abs(e["instr_ret_pct"]) for e in ev) * BUDGET / 100
                                                  - len(ev) * BUDGET * RT, 2),
                      "median_abs_instr_ret_compare": round(float(np.median(
                          [abs(e["instr_ret_pct"]) for e in ev])), 2),
                      "per_session": round(len(ev) / len(days), 2)},
        "events": ev,
    }
    print(f"   창 이벤트 {len(ev)}개 · 오라클 ${result['event_set']['oracle_usd_compare']}", flush=True)

    print("6) 오라클 지연(분)", flush=True)
    result["delay_minutes"] = {"compare": delay_minutes(b1m, ev), "five_day": delay_minutes(b1m, ev_5d)}
    for k, v in result["delay_minutes"]["compare"].items():
        print(f"   {k}: 이탈 {v['mean_abs_departure_pct']}% 보존 {v['mean_retained_frac']} "
              f"${v['usd_total']} ({v.get('usd_vs_oracle_pct')}%)", flush=True)

    print("7) 격자 실행", flush=True)
    grid: Dict[str, List[Dict[str, Any]]] = {}
    for tf, _b in TFS:
        print(f" --- {tf}", flush=True)
        grid[tf] = run_grid(prep_tf[tf], dt_all, days, ev, tf, dict(TFS)[tf])
    result["grid"] = grid
    result["compare_10m_5m_1m"] = compare_table(grid)
    result["recall_ladder"] = ladder(grid)

    # ---- 절제: 1분봉 판정 + 10분봉 스케일 청산 (진입 시점만 1분으로 줄인 효과)
    print("7b) 절제 — 1m 판정 + 10m 청산 규격", flush=True)
    prep_1m_atr10 = retime_atr(prep_tf["1m"], panel10)
    grid_abl = run_grid(prep_1m_atr10, dt_all, days, ev, "1m+10mexit", 1)
    result["grid_1m_exit10m"] = grid_abl

    # ---- 동일 신호빈도 대조: 1m 신호/세션을 10m(c=2.5) 수준으로 맞출 수 있는가
    print("7c) 동일 신호빈도 대조", flush=True)
    mf = []
    base_rate = [r for r in result["signal_timing"] if r["tf"] == "10m" and r["c"] == 2.5][0]["triggers_per_session"]
    for c_hi in (3.5, 4.0, 5.0):
        trigs = pivot_trigger_times(prep_tf["1m"], days, c_hi)
        st = event_signal_stats(prep_tf["1m"], days, ev, c_hi, "1m", trigs)
        tp = {"c": c_hi, "c_exit": c_hi}
        for exit_mode in ("trail", "flip"):
            tr = R1.simulate(prep_tf["1m"], dt_all, days, "pivot", tp, exit_mode, blackout=False)
            tr10 = R1.simulate(prep_1m_atr10, dt_all, days, "pivot", tp, exit_mode, blackout=False)
            mf.append({"tf": "1m", "c": c_hi, "exit": exit_mode,
                       "signals_per_session": st["triggers_per_session"],
                       "ref_10m_c2.5_signals_per_session": base_rate,
                       "recall_pct": R1.score(tr, ev).get("recall_pct"),
                       "precision_pct": R1.score(tr, ev).get("precision_pct"),
                       "n_trades": R1.perf(tr)["n"], "pnl_usd": R1.perf(tr)["pnl_usd"],
                       "pnl_usd_exit10m": R1.perf(tr10)["pnl_usd"],
                       "fee_usd": R1.perf(tr)["fee_usd"],
                       "lag_median_min": st["lag_median_min"],
                       "departure_mean_pct": st["departure_mean_pct"]})
            print(f"   1m c={c_hi} {exit_mode}: 신호/세션 {st['triggers_per_session']} "
                  f"n={mf[-1]['n_trades']} pnl=${mf[-1]['pnl_usd']} "
                  f"(10m청산 ${mf[-1]['pnl_usd_exit10m']})", flush=True)
    result["matched_frequency"] = mf

    print("8) 신호 시점 분해(빠른 진입 vs 노이즈)", flush=True)
    sig_rows = []
    noise = {}
    for tf, _b in TFS:
        noise[tf] = {}
        for c in C_SPECTRUM:
            trigs = pivot_trigger_times(prep_tf[tf], days, c)
            st = event_signal_stats(prep_tf[tf], days, ev, c, tf, trigs)
            st.update({f"noise_{k}": v for k, v in trigger_noise(trigs, ev, days).items()})
            sig_rows.append(st)
            noise[tf][f"c{c}"] = st["triggers_per_session"]
            print(f"   [{tf}] c={c}: 포착 {st['n_captured']}/{len(ev)} lag중앙 {st['lag_median_min']}분 "
                  f"이탈 {st['departure_mean_pct']}% 레그순수익 {st['leg_net_mean_pct']}% "
                  f"신호/세션 {st['triggers_per_session']}", flush=True)
    result["signal_timing"] = sig_rows

    print("9) 손익 분해 브리지", flush=True)
    bridges = []
    for c in (2.5, 1.5, 1.0, 0.5):
        for exit_mode in ("trail", "flip"):
            for flt, fname in (((), "무필터"), (("vol",), "+거래량")):
                b = bridge({"c": c, "exit": exit_mode, "filters": flt},
                           prep_tf["10m"], prep_tf["1m"], dt_all, days, ev)
                b["filter"] = fname
                bridges.append(b)
                print(f"   c={c} {exit_mode} {fname}: 10m ${b['pnl_10m']} → 1m ${b['pnl_1m']} "
                      f"(공유레그 {b['shared_leg_delta_usd']}, 1m추가 {b['only_1m_pnl_usd']}, "
                      f"거짓 {b['false_trade_delta_usd']}, 잔차 {b['residual_usd']})", flush=True)
    result["pnl_bridge_10m_to_1m"] = bridges

    print("10) 5m 브리지(참조) 및 트리거 품질", flush=True)
    bridges5 = []
    for c in (2.5, 1.5, 1.0):
        for exit_mode in ("trail", "flip"):
            b = bridge({"c": c, "exit": exit_mode, "filters": ()},
                       prep_tf["10m"], prep_tf["5m"], dt_all, days, ev)
            b["filter"] = "무필터"
            bridges5.append(b)
    result["pnl_bridge_10m_to_5m"] = bridges5
    result["trigger_quality"] = {}
    for tf, _b in TFS:
        result["trigger_quality"][tf] = {
            f"c{c}": trigger_quality(prep_tf[tf], days, c, 60, dict(TFS)[tf]) for c in C_SPECTRUM}

    print("11) 현행 엔진 L0 (22일 창)", flush=True)
    tr_engine = engine_trades(days)
    result["engine_L0"] = {"n": len(tr_engine), **R1.perf(tr_engine),
                           **{f"score_{k}": v for k, v in R1.score(tr_engine, ev).items()}}
    print(f"   L0: n={len(tr_engine)} pnl=${result['engine_L0']['pnl_usd']} "
          f"R={result['engine_L0']['score_recall_pct']}%", flush=True)
    result["engine_L0"]["per_day_pnl"] = result["engine_L0"].pop("per_day", {})

    print("12) 강건성(11/11 분할 + 슬리피지)", flush=True)
    h = len(days) // 2
    d_a, d_b = days[:h], days[h:]
    ev_a = [e for e in ev if e["day"] in set(d_a)]
    ev_b = [e for e in ev if e["day"] in set(d_b)]
    robust = []
    for tf, _b in TFS:
        for c in (3.0, 2.5, 1.5, 1.0):
            for exit_mode in ("trail", "flip"):
                tp = {"c": c, "c_exit": c}
                ta = R1.simulate(prep_tf[tf], dt_all, d_a, "pivot", tp, exit_mode, blackout=False)
                tb = R1.simulate(prep_tf[tf], dt_all, d_b, "pivot", tp, exit_mode, blackout=False)
                ts = R1.simulate(prep_tf[tf], dt_all, days, "pivot", tp, exit_mode, blackout=False,
                                 rt=RT_SLIP)
                t_all = R1.simulate(prep_tf[tf], dt_all, days, "pivot", tp, exit_mode, blackout=False)
                robust.append({"tf": tf, "c": c, "exit": exit_mode,
                               "first11": {**{k: R1.perf(ta)[k] for k in ("n", "pnl_usd")},
                                           "recall_pct": R1.score(ta, ev_a).get("recall_pct")},
                               "last11": {**{k: R1.perf(tb)[k] for k in ("n", "pnl_usd")},
                                          "recall_pct": R1.score(tb, ev_b).get("recall_pct")},
                               "full_pnl_usd": R1.perf(t_all)["pnl_usd"],
                               "slippage_pnl_usd": R1.perf(ts)["pnl_usd"]})
                print(f"   [{tf}] c={c} {exit_mode}: {robust[-1]['first11']['pnl_usd']} / "
                      f"{robust[-1]['last11']['pnl_usd']} | 전 {robust[-1]['full_pnl_usd']} "
                      f"슬립 {robust[-1]['slippage_pnl_usd']}", flush=True)
    result["robustness"] = robust

    # ---------------------------------------------------------- 요약 판정
    def best(tf: str, key: str = "pnl_usd") -> Dict[str, Any]:
        return max(grid[tf], key=lambda r: r[key])

    summary = {}
    for tf in ("10m", "5m", "1m", "1m+10mexit"):
        b = best(tf)
        summary[tf] = {"best_config": b["config"], "exit": b["exit"], "c": b["c"],
                       "recall_pct": b["recall_pct"], "precision_pct": b["precision_pct"],
                       "n_trades": b["n_trades"], "fee_usd": b["fee_usd"],
                       "pnl_usd": b["pnl_usd"], "pf": b["pf"], "mdd_usd": b["mdd_usd"]}
    result["best_by_tf"] = summary
    result["verdict_numbers"] = {
        "oracle_usd_compare": result["event_set"]["oracle_usd_compare"],
        "delay_gain_1m_vs_10m": {
            "usd_at_1m_delay": result["delay_minutes"]["compare"].get("delay_1m", {}).get("usd_total"),
            "usd_at_10m_delay": result["delay_minutes"]["compare"].get("delay_10m", {}).get("usd_total"),
        },
        "signal_timing": {f"{r['tf']}_c{r['c']}": {"lag_median_min": r["lag_median_min"],
                                                   "departure_mean_pct": r["departure_mean_pct"],
                                                   "leg_net_mean_pct": r["leg_net_mean_pct"],
                                                   "triggers_per_session": r["triggers_per_session"]}
                          for r in sig_rows},
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("saved", OUT)
    print("elapsed", (pd.Timestamp.now(tz="Asia/Seoul") - t_start).total_seconds(), "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
