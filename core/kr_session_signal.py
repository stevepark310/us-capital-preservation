"""한국 세션 신호 모니터 — 낮에 끝난 한국장 정보를 미국 개장 전에 요약한다 (읽기 전용).

무엇을 하나
-----------
15:30 KST 한국장 마감 직후(또는 지정 시각)에 KOSPI·KOSDAQ·삼성전자·SK하이닉스·원/달러의
당일 움직임을 읽어 ①한국 반도체 고유 강도(전일 미국 세션 통제 잔차) ②z-score ③개장 갭
예상치를 계산해 `data/kr_session_signal.json`에 남긴다.

주문 능력은 없다
----------------
이 모듈은 `execution.order_bridge`·`execution.nh_trader`를 import하지 않는다. 신호 산출과
알림만 담당하며, 프리장 실주문은 `config/premarket_policy.json`(현재 enabled=false)으로
막려 있다. 산출물은 `core/autotrader.py`의 집행 루프에 연결되어 있지 않다.

계수 출처
---------
개장 갭 예상 계수는 5년 일봉(2021-11~2026-11, n=1130)으로 적합한
`config/kr_signal_calibration.json`을 쓴다(생성: scripts/research/kr_us_premarket.py).
근거·한계는 docs/kr_to_us_premarket_research_20261118.md 참조.

사용
----
    python3 -m core.kr_session_signal            # 사람이 읽는 요약
    python3 -m core.kr_session_signal --write    # data/kr_session_signal.json 저장
    python3 -m core.kr_session_signal --json     # JSON만 출력
    python3 -m core.kr_session_signal --offline  # 네트워크 없이 저장된 신호만 출력
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_PATH = os.path.join(BASE_DIR, "data", "kr_session_signal.json")
HISTORY_PATH = os.path.join(BASE_DIR, "data", "kr_session_history.json")
CALIB_PATH = os.path.join(BASE_DIR, "config", "kr_signal_calibration.json")

KR_TICKERS = {"^KS11": "kospi", "^KQ11": "kosdaq",
              "005930.KS": "samsung", "000660.KS": "hynix", "KRW=X": "usdkrw"}

# 알림 기준 — 연구(2026-11-18)에서 유의했던 구간
ALERT_TAIL_Z = -1.5     # 하위 약 7%: 5년 57일 갭 -1.89%(SOXX, t=-5.92)
WEAK_Z = -1.0


def now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


# ------------------------------------------------------------------ 데이터
def fetch_daily(ticker: str, days: int = 40) -> List[tuple]:
    """최근 일봉 (date, close). 네트워크 실패 시 빈 리스트."""
    try:
        import yfinance as yf
    except Exception:
        return []
    try:
        period = "3mo" if days <= 60 else "1y"
        df = yf.download(ticker, period=period, interval="1d", progress=False,
                         auto_adjust=False, threads=False)
    except Exception:
        return []
    if df is None or not len(df):
        return []
    if getattr(df.columns, "nlevels", 1) > 1:
        df.columns = [c[0] for c in df.columns]
    out = []
    for idx, row in df["Close"].items():
        try:
            out.append((str(idx)[:10], float(row)))
        except (TypeError, ValueError):
            continue
    return out


def load_history() -> List[Dict]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(rows: List[Dict]) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        if r.get("date") and r["date"] not in seen:
            seen.add(r["date"])
            out.append(r)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(out[-400:], f, ensure_ascii=False, indent=2)


def load_calibration() -> Dict:
    try:
        with open(CALIB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ------------------------------------------------------------------ 계산
def pct(a: float, b: float) -> float:
    return (a / b - 1.0) if b else float("nan")


def zscores(values: List[float], current: float) -> Optional[float]:
    """직전 표본(현재값 제외) 평균·표준편차로 현재값의 z-score."""
    hist = [v for v in values if v is not None and v == v]
    if len(hist) < 20:
        return None
    m = sum(hist) / len(hist)
    var = sum((v - m) ** 2 for v in hist) / (len(hist) - 1)
    sd = var ** 0.5
    if not sd:
        return None
    return (current - m) / sd


def compute_returns(closes: Dict[str, List[tuple]]) -> Dict[str, float]:
    """티커별 최근 두 종가로 당일 수익률."""
    out = {}
    for t, label in KR_TICKERS.items():
        rows = closes.get(t) or []
        if len(rows) >= 2:
            out[label] = pct(rows[-1][1], rows[-2][1])
    if "samsung" in out and "hynix" in out:
        out["semis"] = (out["samsung"] + out["hynix"]) / 2
    if "semis" in out and "kospi" in out:
        out["semis_excess"] = out["semis"] - out["kospi"]
    return out


def build_signal(offline: bool = False, as_of: Optional[str] = None) -> Dict:
    hist = load_history()
    cal = load_calibration()
    closes: Dict[str, List[tuple]] = {}
    if not offline:
        for t in KR_TICKERS:
            closes[t] = fetch_daily(t)
        # 전일 미국 세션 SOXL(잔차 계산용)
        soxl = fetch_daily("SOXL")
        if soxl:
            closes["SOXL"] = soxl

    rets = compute_returns(closes)
    date = as_of or (closes.get("^KS11")[-1][0] if closes.get("^KS11") else str(now_kst().date()))

    # 직전 이력 기반 z-score
    z = {}
    for key in ("kospi", "kosdaq", "semis", "semis_excess", "usdkrw"):
        if key in rets and hist:
            z[key] = zscores([h.get(key) for h in hist], rets[key])

    # 전일 미국 세션 통제 잔차
    resid = None
    coef = (cal.get("semis_resid_def") or {})
    soxl_rows = closes.get("SOXL") or []
    if "semis" in rets and len(soxl_rows) >= 2 and coef:
        soxl_prev = pct(soxl_rows[-1][1], soxl_rows[-2][1])
        resid = rets["semis"] - (coef.get("intercept", 0.0) + coef.get("beta_soxl_prev_ret", 0.0) * soxl_prev)
    z_resid = zscores([h.get("semis_resid") for h in hist], resid) if (resid is not None and hist) else None

    composite = None
    parts = [v for v in (z.get("kospi"), z.get("semis"), z.get("semis_excess")) if v is not None]
    if parts:
        composite = sum(parts) / len(parts)

    # 개장 갭 예상 (5년 적합 계수)
    gap = {}
    models = cal.get("models") or {}
    signal_for_gap = resid if resid is not None else rets.get("semis")
    for name in ("SOXX_gap", "SOXL_gap"):
        m = models.get(name)
        if m and signal_for_gap is not None:
            gap[name] = {
                "expected_pct": round(m["intercept_pct"] + m["beta_pct_per_pct_signal"] * signal_for_gap * 100, 2),
                "one_sd_pct": m.get("resid_sd_pct"),
                "r2": m.get("r2"),
            }

    level = "NEUTRAL"
    if z_resid is not None:
        if z_resid <= ALERT_TAIL_Z:
            level = "RISK_OFF_ALERT"
        elif z_resid <= WEAK_Z:
            level = "WEAK"
        elif z_resid >= 1.5:
            level = "STRONG"
    elif "semis" in rets:
        level = "WEAK" if rets["semis"] < -0.02 else ("STRONG" if rets["semis"] > 0.02 else "NEUTRAL")

    sig = {
        "as_of_date": date,
        "generated_at_kst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "offline" if offline else "live",
        "returns_pct": {k: round(v * 100, 3) for k, v in rets.items()},
        "z": {k: (round(v, 2) if v is not None else None) for k, v in z.items()},
        "semis_resid_pct": round(resid * 100, 3) if resid is not None else None,
        "semis_resid_z": round(z_resid, 2) if z_resid is not None else None,
        "composite_z": round(composite, 2) if composite is not None else None,
        "level": level,
        "expected_open_gap": gap,
        "note": "개장 갭 예상은 5년 일봉 적합(참고용). 개장 후 장중 예측력은 0에 가까움 — "
                "docs/kr_to_us_premarket_research_20261118.md",
        "orders": "none — 이 모듈은 주문 능력이 없다",
    }
    return sig


def append_history(sig: Dict) -> None:
    """당일 신호를 이력에 남겨 다음 날 z-score 표본으로 쓴다(주문 없음)."""
    hist = load_history()
    row = {"date": sig["as_of_date"], **sig["returns_pct"]}
    if sig.get("semis_resid_pct") is not None:
        row["semis_resid"] = sig["semis_resid_pct"] / 100.0
    hist = [h for h in hist if h.get("date") != row["date"]] + [row]
    save_history(hist)


def format_text(sig: Dict) -> str:
    r = sig["returns_pct"]
    lines = [f"🇰🇷 한국 세션 신호 ({sig['as_of_date']}, {sig['generated_at_kst']} KST)"]
    for key, label in [("kospi", "코스피"), ("kosdaq", "코스닥"), ("samsung", "삼성전자"),
                       ("hynix", "SK하이닉스"), ("semis", "반도체 2종 평균"),
                       ("semis_excess", "반도체 − 코스피"), ("usdkrw", "원/달러")]:
        if key in r:
            t = r[key]
            lines.append(f"  {label:<12} {t:+.2f}%" + (f"  (z {sig['z'].get(key)})" if sig["z"].get(key) is not None else ""))
    if sig.get("semis_resid_pct") is not None:
        lines.append(f"  반도체 고유(잔차) {sig['semis_resid_pct']:+.2f}%  z={sig['semis_resid_z']}")
    lines.append(f"  판정: {sig['level']}")
    for name, g in (sig.get("expected_open_gap") or {}).items():
        lines.append(f"  {name.replace('_gap','')} 개장 갭 예상 {g['expected_pct']:+.2f}% (±{g['one_sd_pct']}%p, R² {g['r2']})")
    if sig["level"] == "RISK_OFF_ALERT":
        lines.append("  ⚠️ 반도체 고유 급락 — 보유 반도체 포지션 손절·청산 판단 시 참고(자동 주문 없음)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="한국 세션 신호 모니터(읽기 전용)")
    ap.add_argument("--write", action="store_true", help="data/kr_session_signal.json 저장 + 이력 추가")
    ap.add_argument("--json", action="store_true", help="JSON만 출력")
    ap.add_argument("--offline", action="store_true", help="네트워크 없이 직전 저장 신호 출력")
    args = ap.parse_args(argv)

    if args.offline:
        try:
            with open(SIGNAL_PATH, "r", encoding="utf-8") as f:
                sig = json.load(f)
        except Exception:
            print("저장된 신호가 없습니다.")
            return 1
    else:
        sig = build_signal(offline=False)
        if sig["level"] == "NEUTRAL" and not sig["returns_pct"]:
            print("한국 신호를 가져오지 못했습니다(네트워크·데이터 확인).")
            return 1

    if args.write:
        os.makedirs(os.path.dirname(SIGNAL_PATH), exist_ok=True)
        with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
            json.dump(sig, f, ensure_ascii=False, indent=2)
        append_history(sig)

    print(json.dumps(sig, ensure_ascii=False, indent=2) if args.json else format_text(sig))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
