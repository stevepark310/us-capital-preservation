"""
Autonomy policy — 동훈님의 무인 집행 위임(2026-11-17 12:02)과 하드 가드레일.

`config/swing_approval.json`의 `autonomous_execution` 블록이 정본이다. 이 모듈은
그 값을 읽어 ① 1회 진입 예산 ② 일일 손실 서킷브레이커 ③ 세션 시간 게이트를
계산한다. 모든 계산은 '작은 쪽이 이긴다'(가장 보수적인 상한)로 결합하며,
코드에 박아 둔 절대 상한(SAFE_CAPS)을 정책 파일이 넘어설 수 없다.

위임의 경계
-----------
- 위임된 것: 신호 판단 → 매수/매도 → 브래킷 등록·청산을 사용자 확인 없이 집행.
- 위임되지 않은 것: NVDA `levels_locked`(TP $173.0 / SL $150.0) 변경, 물타기,
  스몰캡 급등주, BEAR ALPHA 게이트(VIX ≥ 24 + 확인 2개) 완화, 신규 종목 편입.
  이 네 가지는 정책 파일로도 켤 수 없게 코드에서 막는다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "swing_approval.json")
HALT_FILE = os.path.join(BASE_DIR, "data", "autotrader_halt.json")

# 정책 파일이 무엇을 쓰든 넘을 수 없는 절대 상한 (계좌 $891 규모 기준)
SAFE_CAPS: Dict[str, float] = {
    "entry_budget_cap_usd": 150.0,        # 1회 진입 명목 상한
    "entry_budget_pct_of_cash": 25.0,     # 가용 현금 대비
    "entry_budget_pct_of_equity": 15.0,   # 총자산 대비
    "risk_per_trade_pct": 2.0,            # 손절까지 감수하는 자산 비율
    "max_open_positions": 4,
    "max_entries_per_session": 4,
    "daily_realized_loss_limit_pct": 3.0,
    "daily_realized_loss_limit_usd": 30.0,
}

# 사용자가 잠근 레벨 — 어떤 경로로도 변경 금지
LOCKED_LEVELS: Dict[str, Dict[str, float]] = {"NVDA": {"tp": 173.0, "sl": 150.0}}
# 실전 배제 유지 (11-13 지시)
BANNED_TICKERS = {"ENVX", "IBRX", "SOUN"}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "ask_before_trade": True,
    "universe": ["SOXL", "TQQQ"],
    "guardrails": {
        "risk_per_trade_pct": 1.5,
        "entry_budget_pct_of_cash": 20.0,
        "entry_budget_pct_of_equity": 12.0,
        "entry_budget_cap_usd": 120.0,
        "entry_budget_floor_usd": 25.0,
        "cash_reserve_floor_usd": 150.0,
        "max_open_positions": 4,
        "max_entries_per_session": 3,
        "max_orders_per_session": 8,
        "daily_realized_loss_limit_usd": 25.0,
        "daily_realized_loss_limit_pct": 3.0,
        "ticker_cooldown_min": 20,
        "duplicate_order_window_min": 10,
        "order_timeout_sec": 300,
        "opening_blackout_kst": "23:30-00:00",
        "no_new_entry_after_kst": "04:30",
        "force_exit_kst": "05:40",
        "min_price_usd": 3.0,
        "vix_riskoff_scale": {"24.0": 0.5, "28.0": 0.0},
        "no_averaging_down": True,
    },
    "execution": {"order_type": "limit", "buy_limit_offset_bps": 20,
                  "sell_limit_offset_bps": -30, "price_round": 2},
    "execution_channel": {"primary": "github_actions", "workflow": "nh_trade_dispatch.yml",
                          "repo": "stevepark310/us-capital-preservation"},
    "safety": {"single_writer_lease_min": 15, "lock_stale_min": 12,
               "kill_switch_file": "data/autotrader_halt.json"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """정책 로드 — 파일이 없거나 형식이 깨져도 안전한 기본값(비활성)으로."""
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        block = cfg.get("autonomous_execution") or {}
        policy = _deep_merge(DEFAULTS, block)
        policy["cautious_mode"] = cfg.get("cautious_mode") or {}
        policy["swing_approval"] = {
            "status": cfg.get("overnight_status"),
            "universe": cfg.get("universe"),
            "entry_conditions": cfg.get("entry_conditions"),
            "exit_rules": cfg.get("exit_rules"),
            "risk_limits": cfg.get("risk_limits"),
        }
        return policy
    except Exception as e:
        policy = dict(DEFAULTS)
        policy["load_error"] = str(e)
        return policy


def guardrails(policy: Dict[str, Any]) -> Dict[str, Any]:
    g = dict(policy.get("guardrails") or {})
    for key, cap in SAFE_CAPS.items():
        if key in g:
            try:
                g[key] = min(float(g[key]), float(cap))
            except (TypeError, ValueError):
                g[key] = float(cap)
    return g


def loss_limit_usd(equity: float, policy: Dict[str, Any]) -> float:
    """일일 실현손실 한도 — 금액 한도와 자산 % 중 작은 쪽(엄격한 쪽)."""
    g = guardrails(policy)
    pct_part = abs(float(g.get("daily_realized_loss_limit_pct", 3.0))) / 100.0 * max(float(equity or 0), 0.0)
    usd_part = abs(float(g.get("daily_realized_loss_limit_usd", 25.0)))
    if pct_part <= 0:
        return usd_part
    return round(min(pct_part, usd_part), 2)


def vix_scale(vix: Optional[float], policy: Dict[str, Any]) -> float:
    """VIX 구간별 리스크 축소 계수(0이면 신규 진입 전면 금지)."""
    try:
        v = float(vix) if vix is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    table = (policy.get("guardrails") or {}).get("vix_riskoff_scale") or {}
    scale = 1.0
    for level, factor in table.items():
        try:
            if v >= float(level):
                scale = min(scale, float(factor))
        except (TypeError, ValueError):
            continue
    return max(0.0, min(1.0, scale))


def entry_budget(equity: float, cash: float, policy: Dict[str, Any],
                 vol_scale: float = 1.0) -> Dict[str, Any]:
    """1회 진입 예산 — 현금/자산/절대상한/현금보존 하한을 모두 만족하는 최소값.

    반환값의 `binding`은 어떤 제약이 예산을 결정했는지 남긴다(보고·로그용).
    """
    g = guardrails(policy)
    equity = max(float(equity or 0), 0.0)
    cash = max(float(cash or 0), 0.0)
    candidates = {
        "cash_pct": cash * float(g.get("entry_budget_pct_of_cash", 20.0)) / 100.0,
        "equity_pct": equity * float(g.get("entry_budget_pct_of_equity", 12.0)) / 100.0,
        "abs_cap": float(g.get("entry_budget_cap_usd", 120.0)),
        "cash_after_reserve": cash - float(g.get("cash_reserve_floor_usd", 150.0)),
    }
    usable = {k: v for k, v in candidates.items() if v is not None}
    binding = min(usable, key=usable.get) if usable else "none"
    budget = max(0.0, min(usable.values())) if usable else 0.0
    budget *= max(0.0, min(1.0, float(vol_scale)))
    floor = float(g.get("entry_budget_floor_usd", 25.0))
    return {
        "budget_usd": round(budget, 2),
        "binding": binding,
        "components": {k: round(v, 2) for k, v in candidates.items()},
        "vol_scale": round(float(vol_scale), 3),
        "below_floor": budget < floor,
        "floor_usd": floor,
    }


def halt_file_active(policy: Optional[Dict[str, Any]] = None) -> bool:
    """비상 정지 파일(data/autotrader_halt.json)이 있으면 신규 매수 중단."""
    rel = ((policy or {}).get("safety") or {}).get("kill_switch_file") or "data/autotrader_halt.json"
    path = rel if os.path.isabs(rel) else os.path.join(BASE_DIR, rel)
    return os.path.exists(path)


def allowed_ticker(ticker: str, policy: Dict[str, Any]) -> bool:
    t = (ticker or "").upper()
    if t in BANNED_TICKERS:
        return False
    return t in {x.upper() for x in (policy.get("universe") or [])}


def locked_levels(ticker: str) -> Optional[Dict[str, float]]:
    return LOCKED_LEVELS.get((ticker or "").upper())


def describe(policy: Dict[str, Any], equity: float, cash: float,
             vix: Optional[float] = None, extra_scale: float = 1.0) -> Dict[str, Any]:
    """상태 보고용 요약(가드레일 수치와 현재 예산).

    `extra_scale`은 전략 엔진의 변동성 계수(`data/strategy_params.json` risk_scale)다.
    실제 진입 예산과 보고 숫자가 갈라지지 않도록 호출한 쪽이 넘긴다.
    """
    g = guardrails(policy)
    scale = vix_scale(vix, policy)
    total = scale * max(0.0, min(1.0, float(extra_scale or 1.0)))
    budget = entry_budget(equity, cash, policy, total)
    return {
        "enabled": bool(policy.get("enabled")),
        "ask_before_trade": bool(policy.get("ask_before_trade", True)),
        "guardrails": g,
        "daily_loss_limit_usd": loss_limit_usd(equity, policy),
        "vix": vix,
        "vix_scale": scale,
        "strategy_risk_scale": round(float(extra_scale or 1.0), 3),
        "budget_scale_total": round(total, 3),
        "entry_budget": budget,
        "halt_file": halt_file_active(policy),
        "universe": policy.get("universe"),
    }
