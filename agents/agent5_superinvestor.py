"""
Agent 5: Smart Money & Options Positioning Specialist

Replaces the hard-coded "Put/Call 0.68, 콜 감마 스퀴즈" copy with the real option
chain: open-interest and volume put/call ratios, the strike carrying the largest
call open interest (gamma-wall proxy) and an open-interest max-pain level for QQQ
and NVDA. Cached 30 minutes; on weekends it is explicitly labelled as the last
Friday snapshot.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from agents.base import BaseAgent

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data", "options_snapshot.json")


def _load_cache(max_age_min: float = 30) -> Dict[str, Any]:
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["fetched_at"])).total_seconds() / 60
        if age < max_age_min:
            return c
    except Exception:
        pass
    return {}


def _compute(symbol: str) -> Dict[str, Any]:
    import yfinance as yf
    t = yf.Ticker(symbol)
    exps = t.options
    if not exps:
        return {}
    expiry = exps[0]
    ch = t.option_chain(expiry)
    calls, puts = ch.calls, ch.puts
    call_oi = float(calls.openInterest.fillna(0).sum())
    put_oi = float(puts.openInterest.fillna(0).sum())
    call_vol = float(calls.volume.fillna(0).sum())
    put_vol = float(puts.volume.fillna(0).sum())
    wall = calls.loc[calls.openInterest.idxmax()] if len(calls) and calls.openInterest.notna().any() else None
    both = (calls.set_index("strike").openInterest.fillna(0) + puts.set_index("strike").openInterest.fillna(0))
    max_pain = float(both.idxmax()) if len(both) else None
    return {
        "symbol": symbol, "expiry": expiry,
        "call_oi": int(call_oi), "put_oi": int(put_oi),
        "put_call_oi": round(put_oi / call_oi, 2) if call_oi else None,
        "call_volume": int(call_vol), "put_volume": int(put_vol),
        "put_call_volume": round(put_vol / call_vol, 2) if call_vol else None,
        "gamma_wall_strike": float(wall["strike"]) if wall is not None else None,
        "gamma_wall_oi": int(wall["openInterest"]) if wall is not None else None,
        "max_pain_strike": max_pain,
        "strikes": int(len(calls)),
    }


class SuperinvestorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id=5,
            name="Agent 5: 스마트머니 & 기관 옵션 감마 분석가",
            code="SMART_MONEY_GAMMA_SPECIALIST",
            role="실제 옵션 체인 기반 Put/Call·감마월·맥스페인 산출",
            description="QQQ·NVDA 옵션 체인의 미결제약정과 거래량으로 기관 포지셔닝을 측정합니다. 소셜 버즈 데이터는 연동되어 있지 않아 사용하지 않습니다.",
        )

    def run(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        self.status = "RUNNING"
        self.last_run = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")
        clock = context.get("clock") or {}
        cached = _load_cache(30)
        chains = cached.get("chains") if cached else None
        if not chains:
            chains = {}
            errors = {}
            for sym in ("QQQ", "NVDA", "SOXL"):
                try:
                    d = _compute(sym)
                    if d:
                        chains[sym] = d
                except Exception as e:
                    errors[sym] = str(e)
            if chains:
                with open(CACHE, "w", encoding="utf-8") as f:
                    json.dump({"fetched_at": datetime.now().isoformat(), "chains": chains}, f,
                              ensure_ascii=False, indent=2)
        qqq = chains.get("QQQ", {})
        nvda = chains.get("NVDA", {})
        pc = qqq.get("put_call_oi")
        bias = ("PUT_HEAVY (하방 헤지 우위)" if pc and pc > 1.0 else
                "CALL_HEAVY (상방 베팅 우위)" if pc and pc < 0.8 else "BALANCED")

        self.output_data = {
            "phase": clock.get("phase"),
            "expiry_used": qqq.get("expiry"),
            "data_as_of": cached.get("fetched_at") if cached else self.last_run,
            "stale_note": "주말·휴장일에는 마지막 금요일 체인 값입니다." if clock.get("is_off_hours") else "정규장 실시간 체인",
            "chains": chains,
            "qqq_put_call_oi": pc,
            "qqq_put_call_volume": qqq.get("put_call_volume"),
            "nvda_gamma_wall": nvda.get("gamma_wall_strike"),
            "nvda_max_pain": nvda.get("max_pain_strike"),
            "institutional_bias": bias,
            "key_takeaway": (
                f"QQQ Put/Call OI {pc} · 거래량비 {qqq.get('put_call_volume')} → {bias}. "
                f"NVDA 감마월 ${nvda.get('gamma_wall_strike')} (OI {nvda.get('gamma_wall_oi')}), 맥스페인 ${nvda.get('max_pain_strike')}."
            ) if chains else "옵션 체인 조회 실패 — 다음 주기 재시도",
        }
        self.confidence_score = 0.9 if chains else 0.4
        self.status = "COMPLETED"
        return self.output_data
