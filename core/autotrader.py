"""
Autonomous Trader — 장중 무인 매수·매도 집행 엔진 (2026-11-17 12:02 사용자 위임).

동훈님 지시: "매수매도를 자동으로 장중에 판단해서 하더록 해. 매번 물어보지 말고.
대신 손실은 줄이고 이익은 극대화할수 있는 전략을 계속 업데이트하고 ..."

한 사이클(10분)에서 하는 일
---------------------------
1. **조정(reconcile)** — 미확인 주문이 있으면 NH 잔고를 디스패치로 조회해
   체결/미체결을 확정하고 브래킷·실현손익·전략 원장을 갱신한다.
2. **청산(exits)** — `core/exit_rules`의 세 규칙(사다리 / v2 샹들리에 / DIP_SWING)을
   적용해 조건이 닿으면 확인 없이 즉시 매도 주문을 낸다. 손절·강제청산은
   가드레일과 무관하게 항상 집행된다(방어가 우선).
3. **진입(entries)** — 승인된 신호(DIP_SWING 일봉·장중, BEAR ALPHA, NVDA 갭 SOXS)만
   받고, 예산·포지션 수·손실 한도·쿨다운·중복 방지 게이트를 모두 통과할 때만 산다.
4. **전략 자기점검** — `core/strategy_evolver`가 실측 표본·기대값·PF·MDD 게이트를
   통과할 때만 파라미터를 안전 범위에서 조정하고 로그를 남긴다.

집행 채널은 `execution/order_bridge.OrderRouter` 하나뿐이다(로컬 NH API → 실패 시
GitHub Actions 디스패치). 단일 작성자 리스(lease)로 감시 스크립트와 동시에 주문하지
않는다.

CLI
---
    python3 scripts/autotrader.py --execute          # 실주문(디스패치)
    python3 scripts/autotrader.py --dry-run          # 판단만, 주문 없음
    python3 scripts/autotrader.py --status           # 상태 JSON 출력
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core import execution_policy, exit_rules, market_clock, session_time
from core import entry_signals
from execution.order_bridge import OrderRouter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
STATE_PATH = os.path.join(DATA, "autotrader_state.json")
STATUS_PATH = os.path.join(DATA, "autotrader_status.json")
LOG_PATH = os.path.join(DATA, "autotrader_log.jsonl")
LEASE_PATH = os.path.join(DATA, "autotrader_lease.json")
TRADE_LEDGER = os.path.join(DATA, "strategy_trade_ledger.json")

DAILY_ENTRY_WINDOW_MIN = 90      # 세션 시작 후 90분까지 '다음 세션 시가' 신호 유효
DIP_UNIVERSE = ["SOXL", "TQQQ"]
HEDGE_UNIVERSE = ["SOXS", "SQQQ"]
HEDGE_NOTIONAL_CAP = 60.0        # config hedge.max_notional_usd


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)


def _now_str() -> str:
    return _now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _parse_kst(ts: str) -> Optional[datetime]:
    """'2026-11-17 12:00:00' / '2026-11-18 06:20' 둘 다 받는다."""
    s = str(ts or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _fresh_state(session_date: str) -> Dict[str, Any]:
    return {
        "session_date": session_date,
        "entries": [],
        "orders": [],
        "realized": [],
        "realized_pnl_usd": 0.0,
        "halted": False,
        "halt_reason": None,
        "cooldowns": {},
        "pending": [],
        "cycles": 0,
        "last_cycle_kst": None,
    }


class AutonomousTrader:
    def __init__(self, policy: Optional[Dict[str, Any]] = None,
                 router: Optional[OrderRouter] = None, dry_run: bool = False,
                 market: Optional[Dict[str, Any]] = None,
                 account: Optional[Dict[str, Any]] = None,
                 base_dir: str = BASE_DIR):
        self.base_dir = base_dir
        self.policy = policy or execution_policy.load_policy()
        self.dry_run = bool(dry_run)
        self.router = router or OrderRouter(
            repo=(self.policy.get("execution_channel") or {}).get("repo"),
            workflow=(self.policy.get("execution_channel") or {}).get("workflow", "nh_trade_dispatch.yml"),
            dry_run=self.dry_run,
            timeout_s=int(((self.policy.get("guardrails") or {}).get("order_timeout_sec") or 300)),
            duplicate_window_min=float(((self.policy.get("guardrails") or {}).get("duplicate_order_window_min") or 10)),
        )
        self._market = market          # 테스트 주입용
        self._account = account        # 테스트 주입용
        self.events: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ state
    @property
    def guard(self) -> Dict[str, Any]:
        return execution_policy.guardrails(self.policy)

    def load_state(self, session_date: str) -> Dict[str, Any]:
        st = _load(STATE_PATH, {})
        if not st or st.get("session_date") != session_date:
            st = _fresh_state(session_date)
        st.setdefault("pending", [])
        st.setdefault("cooldowns", {})
        st.setdefault("realized", [])
        return st

    def save_state(self, st: Dict[str, Any]) -> None:
        _save(STATE_PATH, st)

    # ------------------------------------------------------------------ lease
    def acquire_lease(self, owner: str = "autotrader") -> bool:
        """단일 작성자 리스 — 감시 스크립트와 동시 집행을 막는다."""
        lease = _load(LEASE_PATH, {})
        stale_min = float(((self.policy.get("safety") or {}).get("lock_stale_min") or 12))
        if lease.get("owner") and lease["owner"] != owner:
            ts = _parse_kst(lease.get("heartbeat_kst", ""))
            if ts and (_now_kst() - ts) < timedelta(minutes=stale_min):
                return False
        _save(LEASE_PATH, {"owner": owner, "heartbeat_kst": _now_str(),
                           "pid": os.getpid(), "mode": "dry_run" if self.dry_run else "execute"})
        return True

    def release_lease(self, owner: str = "autotrader") -> None:
        lease = _load(LEASE_PATH, {})
        if lease.get("owner") in (owner, None):
            _save(LEASE_PATH, {"owner": None, "released_kst": _now_str()})

    @staticmethod
    def lease_owner_fresh(minutes: float = 15) -> Optional[str]:
        lease = _load(LEASE_PATH, {})
        owner = lease.get("owner")
        if not owner:
            return None
        ts = _parse_kst(lease.get("heartbeat_kst", ""))
        if ts and (_now_kst() - ts) < timedelta(minutes=minutes):
            return owner
        return None

    # ------------------------------------------------------------------ market
    def market(self) -> Dict[str, Any]:
        if self._market is not None:
            return self._market
        from core import market_snapshot, technical
        clock = market_clock.resolve()
        snap = market_snapshot.build_snapshot(quiet=True, max_age_min=2)
        hist = market_snapshot.fetch_history()
        # 장중에는 오늘 세션의 일봉이 '미완성' 상태로 들어온다. DIP_SWING 신호는 완료된
        # 세션의 봉으로만 계산해야 하므로, 진행 중인 세션 봉은 잘라낸다.
        session = str(clock.get("session_date") or "")
        if session:
            trimmed = {}
            for sym, df in hist.items():
                try:
                    if len(df) and str(df.index[-1].date()) == session:
                        df = df.iloc[:-1]
                except Exception:
                    pass
                trimmed[sym] = df
            hist = trimmed
        techs = {}
        for sym in set(DIP_UNIVERSE + HEDGE_UNIVERSE + ["NVDA", "SPY", "^VIX"]):
            if sym in hist and len(hist[sym]) > 30:
                techs[sym] = technical.analyze(sym, hist[sym], (snap.get("quotes") or {}).get(sym))
        quotes = snap.get("quotes") or {}
        # VIX는 스냅샷 macro 블록에도 있다(지수는 quotes에 없을 수 있음)
        vix = (snap.get("macro") or {}).get("vix") or {}
        return {"clock": clock, "snapshot": snap, "quotes": quotes, "techs": techs,
                "daily": hist, "vix": vix.get("last") if isinstance(vix, dict) else None}

    @staticmethod
    def price_of(quotes: Dict[str, Any], ticker: str) -> Optional[float]:
        q = quotes.get(ticker) or {}
        for key in ("intraday_price", "last", "close"):
            v = q.get(key)
            try:
                if v is not None and float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                continue
        return None

    # ------------------------------------------------------------------ helpers
    def _emit(self, kind: str, **kw) -> None:
        rec = {"ts_kst": _now_str(), "kind": kind}
        rec.update(kw)
        self.events.append(rec)

    def _append_log(self, rec: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _ledger(self) -> Dict[str, Any]:
        try:
            from core import ledger
            return ledger.summary()
        except Exception:
            return {}

    def _open_brackets(self) -> Dict[str, Any]:
        return _load(os.path.join(DATA, "active_brackets.json"), {})

    def _save_brackets(self, brackets: Dict[str, Any]) -> None:
        _save(os.path.join(DATA, "active_brackets.json"), brackets)

    def _limit_price(self, price: float, side: str) -> float:
        bps = float((self.policy.get("execution") or {}).get(
            "buy_limit_offset_bps" if side == "BUY" else "sell_limit_offset_bps", 20 if side == "BUY" else -30))
        rounded = round(float(price) * (1 + bps / 10000.0), int((self.policy.get("execution") or {}).get("price_round", 2)))
        return max(0.01, rounded)

    def _cooldown_ok(self, ticker: str, st: Dict[str, Any]) -> bool:
        ts = _parse_kst((st.get("cooldowns") or {}).get(ticker, ""))
        if not ts:
            return True
        return (_now_kst() - ts) >= timedelta(minutes=float(self.guard.get("ticker_cooldown_min", 20)))

    def _set_cooldown(self, ticker: str, st: Dict[str, Any]) -> None:
        st.setdefault("cooldowns", {})[ticker] = _now_str()

    def _has_pending(self, ticker: str, st: Dict[str, Any], side: Optional[str] = None) -> bool:
        for p in st.get("pending") or []:
            if p.get("ticker") == ticker and (side is None or p.get("side") == side):
                return True
        return False

    def position_qty(self, ticker: str, brackets: Dict[str, Any]) -> int:
        b = brackets.get(ticker) or {}
        return exit_rules.remaining_qty(b) if isinstance(b, dict) else 0

    # ------------------------------------------------------------------ reconcile
    def reconcile(self, st: Dict[str, Any], brackets: Dict[str, Any],
                  account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """미확인 주문을 NH 잔고·체결내역으로 확정한다."""
        pending = st.get("pending") or []
        if not pending:
            return {"checked": 0, "confirmed": 0}
        acct = account if account is not None else self._account
        if acct is None and not self.dry_run:
            acct = self.router.account_state(refresh_min=0)
        if not acct or not acct.get("found"):
            return {"checked": 0, "error": (acct or {}).get("reason") or "잔고 조회 실패 — 다음 주기 재확인"}

        positions = acct.get("positions") or {}
        execs = self._exec_index(acct)
        confirmed, remaining = 0, []
        for p in pending:
            tk, side = p.get("ticker"), p.get("side")
            qty = int(p.get("qty") or 0)
            orr = p.get("order_no")
            fill = execs.get(str(orr)) if orr is not None else None
            filled_qty = int((fill or {}).get("cns_qty") or 0)
            fill_price = float((fill or {}).get("cns_pr") or 0) or float(p.get("limit_price") or 0)
            held = int((positions.get(tk) or {}).get("qty") or 0)
            b = brackets.get(tk) or {}

            done = filled_qty >= qty
            if not done and side == "BUY" and held > 0:
                done = held >= int(b.get("bought_qty") or 0) + qty
            if not done and side == "SELL" and held <= int(b.get("bought_qty") or 0) - qty:
                done = held <= int(b.get("bought_qty") or 0) - qty

            if done:
                confirmed += 1
                self._apply_fill(p, brackets, fill_price=fill_price, filled_qty=max(qty, filled_qty),
                                 held=held, execs=execs, st=st)
                continue
            age = self._age_min(p.get("ts_kst"))
            if age is not None and age > float(self.guard.get("order_timeout_sec", 300)) / 60.0 + 5:
                p["status"] = "STALE_UNFILLED"
                self._emit("ORDER_STALE", ticker=tk, side=side, qty=qty, age_min=round(age, 1))
            remaining.append(p)
        st["pending"] = remaining
        return {"checked": len(pending), "confirmed": confirmed, "open": len(remaining),
                "account_equity": acct.get("equity_usd"), "account_cash": acct.get("cash_usd")}

    @staticmethod
    def _exec_index(account: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        recent = account.get("recent_executions") or {}
        for _day, block in recent.items():
            for row in ((block or {}).get("Output_0") or []):
                if isinstance(row, dict) and row.get("orr_no") is not None:
                    out[str(row.get("orr_no"))] = row
        return out

    @staticmethod
    def _age_min(ts: Optional[str]) -> Optional[float]:
        t = _parse_kst(ts or "")
        if not t:
            return None
        return (_now_kst() - t).total_seconds() / 60.0

    def _apply_fill(self, p: Dict[str, Any], brackets: Dict[str, Any], fill_price: float,
                    filled_qty: int, held: int, execs: Dict[str, Any], st: Dict[str, Any]) -> None:
        tk, side = p.get("ticker"), p.get("side")
        b = brackets.get(tk) or {}
        px = float(fill_price or p.get("limit_price") or 0)
        if side == "BUY":
            was_pending = bool(b.get("pending_entry"))
            b["entry_price"] = round(px, 2)
            b["planned_price"] = b.get("planned_price") or round(px, 2)
            b["bought_qty"] = int(b.get("bought_qty") or 0) + int(p.get("qty") or 0)
            if was_pending and px > 0:
                # 계획가가 아니라 '체결가' 기준으로 손절·본전선을 다시 잡는다
                sl_pct = float(b.get("initial_sl_pct") or 4.0)
                b["sl"] = round(px * (1 - sl_pct / 100), 2)
                b["breakeven_stop"] = round(px * 1.0034, 2)
                b["highest_high"] = round(max(float(b.get("highest_high") or 0), px), 2)
            b["filled_at_kst"] = _now_str()
            b["order_no"] = p.get("order_no")
            b["fill_evidence"] = f"NH 체결내역 orr_no {p.get('order_no')} (cns_qty {filled_qty}) · 잔고 보유 {held}주"
            b["pending_entry"] = False
            brackets[tk] = b
            st.setdefault("entries", []).append({
                "ticker": tk, "qty": p.get("qty"), "price": round(px, 2),
                "ts_kst": p.get("ts_kst"), "order_no": p.get("order_no"),
                "source": p.get("source"), "bracket": b.get("exit_mode") or b.get("bracket_version"),
            })
        else:
            post = p.get("post") or {}
            for k, v in post.items():
                b[k] = v
            b["last_exit"] = {"ts_kst": _now_str(), "qty": p.get("qty"), "price": round(px, 2),
                              "reason": p.get("reason"), "order_no": p.get("order_no")}
            # 부분익절은 tp1_filled/tp2_filled 플래그로 잔여수량을 계산하므로 bought_qty를 줄이지 않는다.
            if post.get("closed") or exit_rules.remaining_qty(b) <= 0:
                b["closed"] = True
            brackets[tk] = b
            entry = float(b.get("entry_price") or 0)
            pnl = round((px - entry) * int(p.get("qty") or 0), 2) if entry else 0.0
            st["realized_pnl_usd"] = round(float(st.get("realized_pnl_usd") or 0.0) + pnl, 2)
            st.setdefault("realized", []).append({
                "ticker": tk, "qty": p.get("qty"), "entry": entry, "exit": round(px, 2),
                "pnl_usd": pnl, "reason": p.get("reason"), "ts_kst": _now_str(),
                "source": p.get("source"),
            })
            self._record_trade(tk, entry, px, int(p.get("qty") or 0), pnl, p.get("reason") or "",
                               b.get("atr_pct_at_plan") or (b.get("ladder_params") or {}).get("atr_pct"),
                               p.get("source") or b.get("bracket_version"))
        self._emit("FILL_CONFIRMED", ticker=tk, side=side, qty=p.get("qty"), price=round(px, 2),
                   pnl_usd=(round((px - float(b.get("entry_price") or 0)) * int(p.get("qty") or 0), 2)
                            if side == "SELL" else None))

    def _record_trade(self, ticker: str, entry: float, exit_px: float, qty: int, pnl: float,
                      reason: str, atr_pct: Any, source: Any) -> None:
        ledger = _load(TRADE_LEDGER, {"trades": []})
        pnl_pct = round((exit_px / entry - 1) * 100, 3) if entry else 0.0
        ledger.setdefault("trades", []).append({
            "ts_kst": _now_str(), "ticker": ticker, "qty": qty, "entry": round(entry, 2),
            "exit": round(exit_px, 2), "pnl_usd": pnl, "pnl_pct": pnl_pct,
            "reason": reason, "atr_pct_at_entry": atr_pct, "source": source,
            "exit_kind": ("STOP" if "STOP" in reason or "SL" in reason else
                          "TRAIL" if "TRAIL" in reason else
                          "TIME" if "TIME" in reason or "강제" in reason else "TARGET"),
        })
        ledger["updated_at_kst"] = _now_str()
        _save(TRADE_LEDGER, ledger)

    # ------------------------------------------------------------------ exits
    def exits(self, st: Dict[str, Any], brackets: Dict[str, Any],
              quotes: Dict[str, Any], clock: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        for tk, b in list(brackets.items()):
            if not isinstance(b, dict):
                continue
            if not exit_rules.is_open(b):
                continue
            if self._has_pending(tk, st, side="SELL"):
                continue
            price = self.price_of(quotes, tk)
            if not price:
                continue
            b["highest_high"] = max(float(b.get("highest_high") or b.get("entry_price") or price), float(price))
            decision = exit_rules.decide(b, price, clock)
            if (not decision and not b.get("levels_locked") and self._cautious_force_exit_active()
                    and session_time.time_reached(clock, self.guard.get("force_exit_kst"))
                    and not b.get("force_exited")):
                decision = {"action": "SELL", "qty": exit_rules.remaining_qty(b), "stop": False, "close": True,
                            "reason": (f"신중 모드 강제청산 ({self.guard.get('force_exit_kst')} KST, "
                                       f"NVDA 실적 11-18 06:20 전 레버리지 정리)"),
                            "post": {"force_exited": True, "closed": True}}
            if not decision:
                continue
            qty = min(int(decision.get("qty") or 0), exit_rules.remaining_qty(b))
            if qty <= 0:
                continue
            limit = self._limit_price(price, "SELL")
            res = self.router.place(tk, "SELL", qty, limit, decision["reason"],
                                    session_date=str(clock.get("session_date") or ""),
                                    dry_run=self.dry_run,
                                    extra={"decided_at": round(float(price), 2), "rule": b.get("exit_mode") or
                                           ("swing_hold" if b.get("swing_hold") else b.get("bracket_version"))})
            status = res.get("status")
            rec = {"ticker": tk, "side": "SELL", "qty": qty, "price": limit, "reason": decision["reason"],
                   "status": status, "order_no": res.get("orr_no"), "run_id": res.get("run_id")}
            actions.append(rec)
            st.setdefault("orders", []).append({**rec, "ts_kst": _now_str()})
            self._emit("EXIT_ORDER", **rec)
            if status in ("SUBMITTED", "PENDING", "DISPATCHED") and not self.dry_run:
                st["pending"].append({
                    "ticker": tk, "side": "SELL", "qty": qty, "limit_price": limit,
                    "reason": decision["reason"], "post": decision.get("post") or {},
                    "order_no": res.get("orr_no"), "ts_kst": _now_str(),
                    "source": "exit", "dry_run": False,
                })
                self._set_cooldown(tk, st)
        return actions

    def _cautious_force_exit_active(self) -> bool:
        """신중 모드(11-16 23:43 승인 ~11-18 06:20) 동안에는 3배 레버리지를
        실적 전에 넘기지 않는다 — 신규 v2 포지션에도 05:40 강제청산을 적용한다.
        NVDA(`levels_locked`)와 SGOV 캐리는 제외."""
        until = str((self.policy.get("cautious_mode") or {}).get("until_kst") or "")
        t = _parse_kst(until)
        return bool(t and _now_kst() < t)

    # ------------------------------------------------------------------ entries
    def entry_candidates(self, market: Dict[str, Any], st: Dict[str, Any],
                         brackets: Dict[str, Any],
                         params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        clock = market["clock"]
        quotes = market.get("quotes") or {}
        params = params or self._strategy_params()
        filters = params.get("entry_filters") or {}
        minutes_into = session_time.session_minutes(session_time.kst_hhmm(clock)) - session_time.session_minutes("23:30")
        cands: List[Dict[str, Any]] = []

        # 1) DIP_SWING — 완료 세션 일봉 신호(세션 초반 90분만 유효: '다음 세션 시가' 진입 규칙)
        if 0 <= minutes_into <= DAILY_ENTRY_WINDOW_MIN:
            scan = entry_signals.dip_daily(market.get("daily") or {}, DIP_UNIVERSE, filters)
            for sig in scan.get("signals") or []:
                cands.append({"source": "DIP_SWING_DAILY", "ticker": sig.get("symbol"),
                              "signal": sig, "why": f"RSI2 {sig.get('rsi2')} · %B {sig.get('pct_b')} · RVOL {sig.get('rvol')}",
                              "atr_pct": sig.get("atr_pct")})
        # 2) DIP_SWING — 장중 등급 신호(신중 모드 규칙: 00:00 이후)
        if minutes_into >= 30:
            intraday = entry_signals.dip_intraday(DIP_UNIVERSE, thresholds=filters)
            for sig in intraday.get("signals") or []:
                cands.append({"source": "DIP_SWING_INTRADAY", "ticker": sig.get("symbol"),
                              "signal": sig, "why": f"5분봉 RSI2 {sig.get('rsi2')} · %B {sig.get('pct_b')} · RVOL {sig.get('rvol')}",
                              "atr_pct": sig.get("atr_pct")})
        # 3) BEAR ALPHA — VIX ≥ 24 + 확인 2개
        gate = entry_signals.bear_alpha_gate(market.get("snapshot") or {},
                                             entry_signals.news_sentiment_avg())
        if gate.get("armed"):
            for tk in HEDGE_UNIVERSE:
                cands.append({"source": "BEAR_ALPHA_HEDGE", "ticker": tk, "signal": gate,
                              "why": f"VIX {gate.get('vix')} + 확인 {len(gate.get('confirmations') or [])}개",
                              "atr_pct": ((market.get("techs") or {}).get(tk) or {}).get("atr_pct"),
                              "notional_cap": HEDGE_NOTIONAL_CAP})
        # 4) NVDA 실적 반응일 갭 → SOXS (11-18 23:30, 사용자 승인)
        gap = self._gap_candidate(market, quotes)
        if gap.get("candidate"):
            cands.append(gap["candidate"])
        return cands

    def _gap_candidate(self, market: Dict[str, Any], quotes: Dict[str, Any]) -> Dict[str, Any]:
        clock = market["clock"]
        today = _now_kst().strftime("%Y-%m-%d")
        if today != "2026-11-18":
            return {"armed": False, "reason": "승인된 반응일(2026-11-18) 아님"}
        minutes_into = session_time.session_minutes(session_time.kst_hhmm(clock)) - session_time.session_minutes("23:30")
        if minutes_into < 0 or minutes_into > 15:
            return {"armed": False, "reason": "개장 15분 밖"}
        q = quotes.get("NVDA") or {}
        prev_close = q.get("prev_close") or q.get("close")
        open_px = q.get("intraday_price")
        res = entry_signals.nvda_gap_soxs(prev_close, open_px)
        if not res.get("armed"):
            return {"armed": False, "detail": res}
        return {"armed": True, "detail": res, "candidate": {
            "source": "NVDA_EARNINGS_GAP_SOXS", "ticker": "SOXS", "signal": res,
            "why": res.get("reason"), "atr_pct": ((market.get("techs") or {}).get("SOXS") or {}).get("atr_pct"),
            "notional_cap": HEDGE_NOTIONAL_CAP, "time_exit_kst": res.get("exit_kst"),
            "disaster_stop_pct": res.get("disaster_stop_pct"), "allow_opening_entry": True,
            "min_qty": 1}}

    def entries(self, st: Dict[str, Any], brackets: Dict[str, Any], market: Dict[str, Any],
                led: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        clock = market["clock"]
        quotes = market.get("quotes") or {}
        g = self.guard
        params = params or self._strategy_params()
        actions: List[Dict[str, Any]] = []

        equity = float(led.get("equity_usd") or 0.0)
        cash = float(led.get("cash_usd") or 0.0)
        loss_limit = execution_policy.loss_limit_usd(equity, self.policy)
        realized = float(st.get("realized_pnl_usd") or 0.0)

        # ---- 전역 게이트
        global_block = None
        if not self.policy.get("enabled"):
            global_block = "위임 비활성(autonomous_execution.enabled=false)"
        elif execution_policy.halt_file_active(self.policy):
            global_block = "비상 정지 파일 존재"
        elif st.get("halted"):
            global_block = f"당일 손실 서킷브레이커 작동 ({st.get('halt_reason')})"
        elif realized <= -loss_limit:
            global_block = f"당일 실현손실 -${abs(realized):.2f} ≥ 한도 ${loss_limit:.2f}"
            st["halted"], st["halt_reason"] = True, global_block
        elif clock.get("phase") != "REGULAR":
            global_block = f"세션 아님({clock.get('phase')})"
        elif len(st.get("entries") or []) >= int(g.get("max_entries_per_session", 3)):
            global_block = f"세션 신규 진입 한도 {int(g.get('max_entries_per_session', 3))}건 도달"
        elif len(st.get("orders") or []) >= int(g.get("max_orders_per_session", 8)):
            global_block = "세션 주문 한도 도달"
        if global_block:
            self._emit("ENTRY_BLOCKED_GLOBAL", reason=global_block)
            return actions

        open_positions = [tk for tk, b in brackets.items()
                          if isinstance(b, dict) and exit_rules.is_open(b) and tk != "SGOV"]
        vix = market.get("vix")
        vix_scale = execution_policy.vix_scale(vix, self.policy)
        risk_scale = float(params.get("risk_scale") or 1.0)
        budget_info = execution_policy.entry_budget(equity, cash, self.policy, vix_scale * risk_scale)
        budget = float(budget_info["budget_usd"])

        for cand in self.entry_candidates(market, st, brackets, params):
            tk = (cand.get("ticker") or "").upper()
            src = cand.get("source")
            if not execution_policy.allowed_ticker(tk, self.policy):
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="유니버스 밖")
                continue
            if self.position_qty(tk, brackets) > 0 or self._has_pending(tk, st):
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="이미 보유/대기 주문(물타기 금지)")
                continue
            if not self._cooldown_ok(tk, st):
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="쿨다운")
                continue
            if len(open_positions) + len([a for a in actions if a.get("side") == "BUY"]) >= int(g.get("max_open_positions", 4)):
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="최대 보유 종목 수")
                continue
            if vix_scale <= 0:
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason=f"VIX {vix} 리스크오프")
                continue
            if not cand.get("allow_opening_entry") and session_time.in_window(clock, g.get("opening_blackout_kst")):
                self._emit("ENTRY_BLOCKED_OPENING", ticker=tk, source=src,
                           reason=f"개장 유예 {g.get('opening_blackout_kst')}")
                continue
            if session_time.time_reached(clock, g.get("no_new_entry_after_kst")):
                self._emit("ENTRY_SKIP", ticker=tk, source=src,
                           reason=f"신규 진입 마감 {g.get('no_new_entry_after_kst')} 이후")
                continue

            price = self.price_of(quotes, tk) or float((cand.get("signal") or {}).get("price") or 0)
            if not price or price < float(g.get("min_price_usd", 3.0)):
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="가격 확인 불가/최소가 미달")
                continue

            slot_budget = min(budget, float(cand.get("notional_cap") or budget))
            if slot_budget < float(g.get("entry_budget_floor_usd", 25.0)):
                self._emit("ENTRY_SKIP", ticker=tk, source=src,
                           reason=f"예산 ${slot_budget:.2f} < 하한 ${g.get('entry_budget_floor_usd')}")
                continue
            planned = sum(float(a.get("qty") or 0) * float(a.get("price") or 0)
                          for a in actions if a.get("side") == "BUY")
            qty, sizing = self._size(price, slot_budget, equity, max(0.0, cash - planned), g, cand, params)
            min_qty = int(cand.get("min_qty") or 0)
            if qty < min_qty and price <= float(cand.get("notional_cap") or price) \
                    and (cash - planned) - price >= float(g.get("cash_reserve_floor_usd", 150.0)):
                # 사용자가 승인한 고정 1주 헤지(11-18 SOXS)는 변동성 축소 계수에 걸리지 않는다.
                qty = min_qty
                sizing.update({"qty": qty, "notional_usd": round(qty * price, 2),
                               "override": f"min_qty {min_qty} — 승인된 고정 수량"})
            if qty <= 0:
                self._emit("ENTRY_SKIP", ticker=tk, source=src, reason="수량 0 (예산/리스크)")
                continue
            limit = self._limit_price(price, "BUY")
            res = self.router.place(tk, "BUY", qty, limit,
                                    f"AUTO BUY {src} — {cand.get('why')}",
                                    session_date=str(clock.get("session_date") or ""),
                                    dry_run=self.dry_run,
                                    extra={"signal": cand.get("signal"), "sizing": sizing})
            status = res.get("status")
            rec = {"ticker": tk, "side": "BUY", "qty": qty, "price": limit, "source": src,
                   "status": status, "order_no": res.get("orr_no"), "run_id": res.get("run_id"),
                   "sizing": sizing}
            actions.append(rec)
            st.setdefault("orders", []).append({**rec, "ts_kst": _now_str()})
            self._emit("ENTRY_ORDER", **rec)
            if status in ("SUBMITTED", "PENDING", "DISPATCHED") and not self.dry_run:
                b = self._new_bracket(cand, tk, price, qty, res.get("orr_no"), src, params)
                brackets[tk] = b
                st["pending"].append({
                    "ticker": tk, "side": "BUY", "qty": qty, "limit_price": limit,
                    "reason": rec.get("source"), "post": {},
                    "order_no": res.get("orr_no"), "ts_kst": _now_str(),
                    "source": src, "dry_run": False,
                })
                self._set_cooldown(tk, st)
        return actions

    def _size(self, price: float, budget: float, equity: float, cash: float,
              g: Dict[str, Any], cand: Dict[str, Any],
              params: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        qty = int(budget // price)
        reserve = float(g.get("cash_reserve_floor_usd", 150.0))
        while qty > 0 and qty * price > max(0.0, cash - reserve):
            qty -= 1
        sl_pct = self._sl_pct(cand, params)
        risk_cap = equity * float(g.get("risk_per_trade_pct", 1.5)) / 100.0
        per_share_risk = price * sl_pct / 100.0
        while qty > 0 and per_share_risk * qty > risk_cap:
            qty -= 1
        return qty, {"qty": qty, "notional_usd": round(qty * price, 2), "budget_usd": round(budget, 2),
                     "sl_pct": round(sl_pct, 2), "risk_usd": round(per_share_risk * qty, 2),
                     "risk_cap_usd": round(risk_cap, 2), "cash_after_reserve_usd": round(cash - reserve, 2)}

    def _sl_pct(self, cand: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> float:
        if cand.get("source") == "NVDA_EARNINGS_GAP_SOXS":
            return abs(float(cand.get("disaster_stop_pct") or -8.0))
        p = params or self._strategy_params()
        atr = float(cand.get("atr_pct") or 6.0)
        mult = float(p.get("initial_sl_atr_mult") or 0.75)
        lo = float(p.get("sl_min_pct") or 1.5)
        hi = float(p.get("sl_max_pct") or 8.0)
        return max(lo, min(hi, mult * atr))

    @staticmethod
    def _strategy_params() -> Dict[str, Any]:
        try:
            from core import strategy_evolver
            return strategy_evolver.load_params()
        except Exception:
            return {"trail_atr_mult": 0.9, "initial_sl_atr_mult": 0.75, "be_after_atr": 1.0,
                    "max_hold_sessions": 3, "risk_scale": 1.0,
                    "entry_filters": {"rsi2_max": 20.0, "pct_b_max": 0.15, "rvol_min": 1.0}}

    def _new_bracket(self, cand: Dict[str, Any], ticker: str, price: float, qty: int,
                     order_no: Any, source: str,
                     params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = params or self._strategy_params()
        sl_pct = self._sl_pct(cand, p)
        b: Dict[str, Any] = {
            "bought_qty": 0, "entry_price": round(price, 2), "planned_price": round(price, 2),
            "sl": round(price * (1 - sl_pct / 100), 2), "initial_sl_pct": round(sl_pct, 2),
            "breakeven_stop": round(price * 1.0034, 2), "sl_triggered": False, "closed": False,
            "atr_pct_at_plan": round(float(cand.get("atr_pct") or 6.0), 2),
            "sl_active_after_kst": "23:45",
            "highest_high": round(price, 2), "opened_at": _now_str(),
            "autonomous": True, "entry_source": source, "order_no": order_no,
            "pending_entry": True,
            "note": f"무인 집행 신호 {source} — {cand.get('why')}",
        }
        if cand.get("time_exit_kst"):
            b.update({"exit_mode": "time_exit", "force_exit_kst": cand["time_exit_kst"],
                      "tp1": None, "tp1_qty": 0, "tp2": None, "tp2_qty": 0, "runner_qty": 0,
                      "note": f"{source} — {cand.get('time_exit_kst')} 종가 청산, 재난손절 {sl_pct:.1f}%"})
        else:
            b.update({"exit_mode": "chandelier_v2", "bracket_version": "chandelier_v2",
                      "trail_atr_mult": float(p.get("trail_atr_mult") or 0.9),
                      "be_after_atr": float(p.get("be_after_atr") or 1.0),
                      "breakeven_pct": float(p.get("breakeven_pct") or 0.34),
                      "max_hold_sessions": int(p.get("max_hold_sessions") or 3),
                      "tp1": None, "tp1_qty": 0, "tp2": None,
                      "tp2_qty": 0, "runner_qty": 0})
        return b

    # ------------------------------------------------------------------ strategy
    def evolve(self, market: Dict[str, Any], st: Dict[str, Any], led: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from core import strategy_evolver
            market_ctx = {
                "vix": market.get("vix"),
                "atr": {tk: ((market.get("techs") or {}).get(tk) or {}).get("atr_pct")
                        for tk in set(DIP_UNIVERSE + HEDGE_UNIVERSE)},
                "equity": led.get("equity_usd"),
                "regime": ((market.get("snapshot") or {}).get("cycle") or {}).get("regime"),
            }
            return strategy_evolver.evaluate(market_ctx=market_ctx,
                                             realized_session=float(st.get("realized_pnl_usd") or 0.0))
        except Exception as e:
            return {"applied": False, "decision": "EVOLVER_ERROR", "error": str(e)}

    # ------------------------------------------------------------------ cycle
    def run(self, execute: bool = False) -> Dict[str, Any]:
        """한 사이클. execute=True면 실주문(라우터 dry_run=False)."""
        t0 = time.time()
        self.dry_run = not execute
        self.router.dry_run = not execute
        market = self.market()
        clock = market["clock"]
        session_date = str(clock.get("session_date") or _now_kst().strftime("%Y-%m-%d"))
        st = self.load_state(session_date)
        brackets = self._open_brackets()
        led = self._ledger()

        reconcile = self.reconcile(st, brackets)
        params = self._strategy_params()
        # 주문은 정규장에서만 낸다(시간외·휴장에 NH가 거부하거나 체결 품질이 나쁘다).
        # 조정(reconcile)은 읽기 전용이므로 세션 밖에서도 수행한다.
        if clock.get("phase") == "REGULAR":
            exits = self.exits(st, brackets, market.get("quotes") or {}, clock)
        else:
            exits = []
            self._emit("EXITS_SKIPPED", reason=f"phase={clock.get('phase')} — 주문 없음")
        entries = self.entries(st, brackets, market, led, params)
        evolution = self.evolve(market, st, led)

        st["cycles"] = int(st.get("cycles") or 0) + 1
        st["last_cycle_kst"] = _now_str()
        st["session_date"] = session_date
        st["pending"] = [p for p in (st.get("pending") or []) if p]
        if not self.dry_run:
            # DRY RUN은 판단만 한다 — 실전 브래킷·상태를 건드리지 않는다.
            self._save_brackets(brackets)
            self.save_state(st)

        equity = float(led.get("equity_usd") or 0.0)
        cash = float(led.get("cash_usd") or 0.0)
        desc = execution_policy.describe(self.policy, equity, cash, market.get("vix"),
                                         extra_scale=float(params.get("risk_scale") or 1.0))
        status = {
            "engine": "AUTONOMOUS_INTRADAY",
            "generated_at_kst": _now_str(),
            "phase": clock.get("phase"),
            "session_date": session_date,
            "mode": "EXECUTE" if execute else "DRY_RUN",
            "policy": desc,
            "loss_limit_usd": desc["daily_loss_limit_usd"],
            "realized_pnl_today_usd": round(float(st.get("realized_pnl_usd") or 0.0), 2),
            "halted": bool(st.get("halted")),
            "halt_reason": st.get("halt_reason"),
            "entries_today": len(st.get("entries") or []),
            "orders_today": len(st.get("orders") or []),
            "pending_orders": st.get("pending") or [],
            "reconcile": reconcile,
            "exits": exits,
            "entries": entries,
            "evolution": evolution,
            "strategy_params": {k: params.get(k) for k in ("trail_atr_mult", "initial_sl_atr_mult",
                                                          "risk_scale", "defensive_mode", "entry_filters")},
            "positions": {tk: {"qty": exit_rules.remaining_qty(b), "entry": b.get("entry_price"),
                               "sl": b.get("sl"), "exit_mode": b.get("exit_mode") or b.get("bracket_version"),
                               "entry_source": b.get("entry_source")}
                          for tk, b in brackets.items() if isinstance(b, dict) and exit_rules.is_open(b)},
            "ledger": {"equity_usd": equity, "cash_usd": cash},
            "next_gate": self._next_gate(clock, st),
            "elapsed_sec": round(time.time() - t0, 1),
            "events": self.events[-40:],
        }
        _save(STATUS_PATH, status)
        self._append_log({"ts_kst": _now_str(), "mode": status["mode"], "phase": status["phase"],
                          "reconcile": reconcile, "exits": exits, "entries": entries,
                          "evolution": evolution.get("decision"), "realized_today": status["realized_pnl_today_usd"],
                          "halted": status["halted"], "elapsed_sec": status["elapsed_sec"]})
        return status

    def _next_gate(self, clock: Dict[str, Any], st: Dict[str, Any]) -> str:
        if st.get("halted"):
            return "당일 신규 매수 중단 — 청산만 계속"
        if clock.get("phase") != "REGULAR":
            return f"다음 정규장 {clock.get('next_open_kst')}"
        if session_time.in_window(clock, self.guard.get("opening_blackout_kst")):
            return "개장 유예 — 00:00 KST부터 신규 진입"
        if not session_time.time_reached(clock, self.guard.get("no_new_entry_after_kst")):
            return f"신규 진입 마감 {self.guard.get('no_new_entry_after_kst')} 전"
        return "신호 대기"
