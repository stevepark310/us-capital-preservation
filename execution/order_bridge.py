"""
Order bridge — GitHub Actions를 경유한 NH 실전 주문 라우터.

왜 필요한가
-----------
작업 환경은 `api.nhplug.com:8443` 아웃바운드가 막혀 있다(Errno 111). 반면
GitHub Actions 러너에서는 NH Open API가 정상 동작한다(2026-11-17 11:14 실행에서
`Token issued: True` + 잔고 4종목 조회 실측). 그래서 실주문은

    gh workflow run nh_trade_dispatch.yml -f mode=buy|sell -f ticker=.. -f qty=.. -f price=..

로 디스패치하고, 실행 로그의 응답 JSON(`rsp_cd`, `Output_0.orr_no`)을 파싱해
접수 여부를 확인한다. 잔고·체결 확인은 `mode=test_balance`를 디스패치해
`positions`(수량·평단·매도가능수량)를 읽어 대조한다.

워크플로우 스펙(.github/workflows/nh_trade_dispatch.yml, 수정 금지)
------------------------------------------------------------------
inputs: mode(test_balance|buy|sell|monitor), ticker, qty, price, order_type
- buy/sell: `NHBroker(dry_run=False).place_order(ticker, side, qty, price, order_type=...)`
  → 지정가 해외주식 주문. order_type 미지정이면 '00'(정규장 지정가)이다.
  '61'(프리마켓)·'62'(애프터마켓)는 `order_type` 입력이 워크플로에 있어야 디스패치된다 —
  없이 보내면 GitHub가 알 수 없는 입력으로 거부하므로, `order_type`이 기본값일 때는
  `-f order_type=`를 붙이지 않는다(기존 워크플로와 호환). 워크플로 개정본은
  `execution/nh_trade_dispatch.yml.template` 참조.
- monitor: `scripts/monitor_and_exit.py` 실행(러너에서 직접 주문 가능).
- test_balance: `broker.get_balance()` 결과 JSON 출력(비밀값은 ***로 마스킹).

안전장치
--------
- 멱등 키(세션·종목·방향·수량·가격)로 중복 디스패치를 차단한다
  (`duplicate_order_window_min`, 기본 10분).
- 주문 1건마다 `data/live_order_responses.jsonl`에 요청·응답·판정을 남긴다.
- `--dry-run`에서는 gh를 호출하지 않고 실행될 명령만 기록한다(단위 테스트 경로).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from execution.nh_trader import normalize_order_type

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS_LOG = os.path.join(BASE_DIR, "data", "live_order_responses.jsonl")
DISPATCH_STATE = os.path.join(BASE_DIR, "data", "order_dispatch_state.json")
ACCOUNT_SNAPSHOT = os.path.join(BASE_DIR, "data", "nh_account_snapshot.json")
PREMARKET_POLICY = os.path.join(BASE_DIR, "config", "premarket_policy.json")

ACCEPT_CODES = {"00171", "00166", "00000"}
REJECT_CODES = {"13578", "16156", "IGW40018"}
_LOG_PREFIX = re.compile(r"^\S+\t[^\t]*\t\d{4}-\d\d-\d\dT[\d:.]+Z\s?")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def now_kst_str() -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _iter_json_objects(text: str):
    dec = json.JSONDecoder()
    idx = 0
    while True:
        i = text.find("{", idx)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(text[i:])
        except ValueError:
            idx = i + 1
            continue
        yield obj
        idx = i + max(end, 1)


def clean_run_log(raw: str) -> str:
    """GitHub Actions 로그의 `job\tstep\t<timestamp>Z ` 접두사와 색상 코드 제거."""
    out = []
    for line in (raw or "").splitlines():
        m = _LOG_PREFIX.match(line)
        out.append(_ANSI.sub("", line[m.end():] if m else line))
    return "\n".join(out)


def parse_order_response(log_text: str) -> Dict[str, Any]:
    """로그에서 주문 응답 JSON을 찾아 판정까지 계산한다."""
    text = clean_run_log(log_text)
    found = None
    for obj in _iter_json_objects(text):
        if isinstance(obj, dict) and "rsp_cd" in obj and ("Output_0" in obj or "rsp_msg" in obj):
            found = obj
    if not found:
        return {"found": False, "status": "UNPARSED", "reason": "주문 응답 JSON을 로그에서 찾지 못했습니다."}
    code = str(found.get("rsp_cd") or "")
    out0 = found.get("Output_0") or {}
    orr_no = out0.get("orr_no") if isinstance(out0, dict) else None
    if code in ACCEPT_CODES:
        status = "SUBMITTED"
    elif code in REJECT_CODES:
        status = "REJECTED"
    else:
        status = "UNKNOWN"
    return {"found": True, "rsp_cd": code, "rsp_msg": found.get("rsp_msg"),
            "orr_no": orr_no, "status": status, "raw": found}


def parse_account_state(log_text: str) -> Dict[str, Any]:
    """`mode=test_balance` 로그에서 계좌 상태(equity/cash/positions)를 파싱."""
    text = clean_run_log(log_text)
    found = None
    for obj in _iter_json_objects(text):
        if isinstance(obj, dict) and "positions" in obj and "equity" in obj:
            found = obj
    if not found:
        return {"found": False, "reason": "잔고 JSON을 로그에서 찾지 못했습니다."}
    return {
        "found": True,
        "equity_usd": found.get("equity"),
        "cash_usd": found.get("cash_usd", found.get("cash")),
        "positions": found.get("positions") or {},
        "rsp_cd": found.get("rsp_cd"),
        "rsp_msg": found.get("rsp_msg"),
        "recent_executions": found.get("recent_executions") or {},
    }


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def premarket_gate() -> Dict[str, Any]:
    """프리마켓/애프터마켓 실주문 허용 여부 — `config/premarket_policy.json` 한 곳으로만 켜진다.

    2026-11-18 백테스트 결과(프리장 진입 기대값 음수)로 `enabled: false`가 기본이다.
    파일이 없거나 enabled가 참이 아니면 프리장·애프터마켓 주문은 디스패치 전에 막린다.
    """
    pol = _load(PREMARKET_POLICY)
    if not pol:
        return {"allowed": False, "reason": "config/premarket_policy.json 없음"}
    if not pol.get("enabled"):
        return {"allowed": False, "reason": pol.get("reason") or "정책 enabled=false"}
    return {"allowed": True, "reason": pol.get("reason", "")}


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class OrderRouter:
    """주문 라우터. `dry_run=True`면 네트워크를 타지 않고 판정만 기록한다."""

    def __init__(self, repo: Optional[str] = None, workflow: str = "nh_trade_dispatch.yml",
                 dry_run: bool = False, timeout_s: int = 300, run_cmd=None,
                 duplicate_window_min: float = 10.0, poll_first_s: float = 45.0,
                 poll_interval_s: float = 3.0, status_poll_s: float = 5.0):
        self.repo = repo or os.getenv("CUBE_REPO", "stevepark310/us-capital-preservation")
        self.workflow = workflow
        self.dry_run = dry_run
        self.timeout_s = int(timeout_s)
        self.duplicate_window_min = float(duplicate_window_min)
        self.poll_first_s = float(poll_first_s)
        self.poll_interval_s = float(poll_interval_s)
        self.status_poll_s = float(status_poll_s)
        self._runner = run_cmd or self._default_runner

    # ------------------------------------------------------------------ shell
    def _default_runner(self, args: List[str], timeout: int = 120) -> Dict[str, Any]:
        try:
            p = subprocess.run(args, cwd=BASE_DIR, text=True, capture_output=True, timeout=timeout)
            return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
        except subprocess.TimeoutExpired:
            return {"rc": -9, "out": "", "err": f"timeout after {timeout}s"}
        except FileNotFoundError as e:
            return {"rc": -1, "out": "", "err": f"command not found: {e}"}

    # ------------------------------------------------------------------ state
    def _recent(self) -> Dict[str, Any]:
        st = _load(DISPATCH_STATE)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        recent = {k: v for k, v in (st.get("recent") or {}).items() if str(v.get("ts_utc", "")) >= cutoff}
        return {"recent": recent, "runs": (st.get("runs") or [])[-50:]}

    def _record_dispatch(self, key: str, record: Dict[str, Any]) -> None:
        st = self._recent()
        st["recent"][key] = record
        st["runs"] = (st.get("runs") or []) + [record]
        st["runs"] = st["runs"][-50:]
        _save(DISPATCH_STATE, st)

    def _recent_duplicate(self, key: str) -> Optional[Dict[str, Any]]:
        rec = (self._recent().get("recent") or {}).get(key)
        if not rec:
            return None
        try:
            ts = datetime.fromisoformat(rec["ts_utc"])
        except Exception:
            return rec
        if datetime.now(timezone.utc) - ts <= timedelta(minutes=self.duplicate_window_min):
            return rec
        return None

    # ------------------------------------------------------------------ command
    def build_command(self, mode: str, ticker: str = "", qty: int = 0, price: float = 0.0,
                      order_type: str = "") -> List[str]:
        cmd = ["gh", "workflow", "run", self.workflow, "-R", self.repo, "-f", f"mode={mode}"]
        if ticker:
            cmd += ["-f", f"ticker={ticker.upper()}"]
        if qty:
            cmd += ["-f", f"qty={int(qty)}"]
        if price:
            cmd += ["-f", f"price={float(price):.2f}"]
        # 기본값 '00'·빈값에서는 입력을 보내지 않는다 — order_type 입력이 없는
        # 현행 워크플로에 알 수 없는 입력을 보내면 디스패치가 거부된다.
        code = normalize_order_type(order_type) if order_type not in ("", None) else "00"
        if code != "00":
            cmd += ["-f", f"order_type={code}"]
        return cmd

    def _list_runs(self, limit: int = 8) -> List[Dict[str, Any]]:
        r = self._runner(["gh", "run", "list", "-R", self.repo, "--workflow", self.workflow,
                          "-L", str(limit), "--json", "databaseId,createdAt,status,conclusion"], timeout=90)
        try:
            return json.loads(r.get("out") or "[]")
        except Exception:
            return []

    def _run_status(self, run_id: int) -> Dict[str, Any]:
        r = self._runner(["gh", "run", "view", str(run_id), "-R", self.repo,
                          "--json", "status,conclusion,createdAt,updatedAt"], timeout=90)
        try:
            return json.loads(r.get("out") or "{}")
        except Exception:
            return {"status": None, "conclusion": None}

    def _run_log(self, run_id: int) -> str:
        r = self._runner(["gh", "run", "view", str(run_id), "-R", self.repo, "--log"], timeout=180)
        return r.get("out") or ""

    def _dispatch_and_wait(self, cmd: List[str], baseline_id: int) -> Dict[str, Any]:
        d = self._runner(cmd, timeout=120)
        if d.get("rc") != 0:
            return {"status": "DISPATCH_FAILED", "error": (d.get("err") or d.get("out") or "").strip()[-800:]}
        run_id = None
        deadline = time.time() + self.poll_first_s
        while time.time() < deadline:
            for run in self._list_runs():
                try:
                    rid = int(run.get("databaseId"))
                except (TypeError, ValueError):
                    continue
                if rid > baseline_id:
                    run_id = rid
                    break
            if run_id:
                break
            time.sleep(self.poll_interval_s)
        if not run_id:
            return {"status": "RUN_NOT_FOUND", "error": "디스패치 후 실행을 찾지 못했습니다."}

        deadline = time.time() + self.timeout_s
        last = {}
        while time.time() < deadline:
            last = self._run_status(run_id)
            if last.get("status") == "completed":
                break
            time.sleep(self.status_poll_s)
        conclusion = last.get("conclusion")
        log = self._run_log(run_id)
        return {"status": "COMPLETED", "run_id": run_id, "conclusion": conclusion, "log": log}

    # ------------------------------------------------------------------ orders
    @staticmethod
    def idempotency_key(session_date: str, ticker: str, side: str, qty: int, price: float,
                        order_type: str = "00") -> str:
        key = f"{session_date}|{ticker.upper()}|{side.upper()}|{int(qty)}|{round(float(price), 2):.2f}"
        code = normalize_order_type(order_type)
        return key if code == "00" else f"{key}|{code}"

    def place(self, ticker: str, side: str, qty: int, price: float, reason: str,
              session_date: str = "", dry_run: Optional[bool] = None,
              extra: Optional[Dict[str, Any]] = None, order_type: str = "00") -> Dict[str, Any]:
        """매수/매도 1건. 반환: {status, run_id, orr_no, response, ...}

        order_type: '00' 정규장 지정가(기본) · '61' 프리마켓 · '62' 애프터마켓.
        """
        side = side.upper()
        ticker = ticker.upper()
        qty = int(qty)
        price = round(float(price), 2)
        code = normalize_order_type(order_type)
        dry = self.dry_run if dry_run is None else bool(dry_run)
        key = self.idempotency_key(session_date or now_kst_str()[:10], ticker, side, qty, price, code)
        record: Dict[str, Any] = {
            "ts": now_kst_str(), "ts_utc": datetime.now(timezone.utc).isoformat(),
            "side": side, "ticker": ticker, "qty": qty, "price": price,
            "order_type": code, "reason": reason, "idempotency_key": key,
            "channel": "github_actions" if not dry else "dry_run",
        }
        if extra:
            record["context"] = extra
        if qty <= 0:
            record.update({"status": "INVALID", "error": "qty must be positive"})
            self._append_log(record)
            return record

        if code != "00":
            gate = premarket_gate()
            if not gate["allowed"]:
                record.update({"status": "BLOCKED_POLICY", "error": gate["reason"],
                               "policy": "config/premarket_policy.json"})
                self._append_log(record)
                return record

        dup = self._recent_duplicate(key)
        if dup and dup.get("status") in ("SUBMITTED", "DISPATCHED", "PENDING"):
            record.update({"status": "DUPLICATE_BLOCKED", "duplicate_of": dup})
            self._append_log(record)
            return record

        mode = "buy" if side == "BUY" else "sell"
        cmd = self.build_command(mode, ticker, qty, price, code)
        record["command"] = " ".join(cmd)

        if dry:
            record["status"] = "DRY_RUN"
            self._append_log(record)
            return record

        # 1) 로컬에서 NH API가 열려 있으면 직접 발주(러너 안에서 돌 때)
        direct = self._try_direct(ticker, side, qty, price, code)
        if direct is not None:
            record.update(direct)
            record["channel"] = "direct_nh"
            self._append_log(record)
            self._record_dispatch(key, {"ts_utc": record["ts_utc"], "status": record["status"],
                                        "ticker": ticker, "side": side, "qty": qty, "price": price,
                                        "order_type": code,
                                        "channel": "direct_nh", "orr_no": record.get("orr_no")})
            return record

        # 2) GitHub Actions 디스패치
        baseline = max([int(r.get("databaseId") or 0) for r in self._list_runs()] or [0])
        result = self._dispatch_and_wait(cmd, baseline)
        record["channel"] = "github_actions"
        if result.get("status") != "COMPLETED":
            record.update({"status": result.get("status"), "error": result.get("error")})
            self._append_log(record)
            self._record_dispatch(key, {"ts_utc": record["ts_utc"], "status": record["status"],
                                        "ticker": ticker, "side": side, "qty": qty, "price": price, "order_type": code})
            return record

        record["run_id"] = result.get("run_id")
        record["run_conclusion"] = result.get("conclusion")
        parsed = parse_order_response(result.get("log") or "")
        record["response"] = parsed.get("raw") or {"rsp_cd": parsed.get("rsp_cd"), "rsp_msg": parsed.get("rsp_msg")}
        record["orr_no"] = parsed.get("orr_no")
        record["status"] = parsed.get("status") if parsed.get("found") else "UNPARSED"
        if result.get("conclusion") == "failure" and record["status"] != "SUBMITTED":
            record["status"] = "REJECTED"
            record["error"] = "워크플로우 실패 — 로그의 rsp_cd를 확인하세요."
        self._append_log(record)
        self._record_dispatch(key, {"ts_utc": record["ts_utc"], "status": record["status"],
                                    "ticker": ticker, "side": side, "qty": qty, "price": price,
                                    "order_type": code,
                                    "run_id": record.get("run_id"), "orr_no": record.get("orr_no")})
        return record

    def _try_direct(self, ticker: str, side: str, qty: int, price: float,
                    order_type: str = "00") -> Optional[Dict[str, Any]]:
        """로컬 NH API가 살아 있을 때만 직접 발주. 막혀 있으면 None(디스패치로 폴백)."""
        if os.getenv("CUBE_FORCE_ACTIONS", "0") == "1":
            return None
        if not (os.getenv("NH_APP_KEY") and os.getenv("NH_APP_SECRET")):
            return None
        try:
            from execution.nh_trader import NHBroker
            broker = NHBroker(dry_run=False)
            res = broker.place_order(ticker, side, qty, price, order_type=order_type)
            code = str((res or {}).get("rsp_cd") or "")
            return {"status": "SUBMITTED" if code in ACCEPT_CODES else "REJECTED",
                    "response": res, "orr_no": ((res or {}).get("Output_0") or {}).get("orr_no")}
        except Exception:
            return None

    @staticmethod
    def _append_log(record: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(ORDERS_LOG), exist_ok=True)
        with open(ORDERS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ account
    def account_state(self, dry_run: Optional[bool] = None, refresh_min: float = 0.0) -> Dict[str, Any]:
        """NH 잔고·보유수량 조회. `refresh_min` 안의 캐시가 있으면 그대로 쓴다."""
        dry = self.dry_run if dry_run is None else bool(dry_run)
        cached = _load(ACCOUNT_SNAPSHOT)
        if cached.get("found") and refresh_min > 0 and cached.get("fetched_ts_utc"):
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_ts_utc"])).total_seconds() / 60
                if age <= refresh_min:
                    return cached
            except Exception:
                pass
        if dry:
            return {"found": False, "dry_run": True, "reason": "dry-run"}
        cmd = self.build_command("test_balance")
        baseline = max([int(r.get("databaseId") or 0) for r in self._list_runs()] or [0])
        result = self._dispatch_and_wait(cmd, baseline)
        if result.get("status") != "COMPLETED":
            return {"found": False, "reason": result.get("error") or result.get("status")}
        state = parse_account_state(result.get("log") or "")
        state["run_id"] = result.get("run_id")
        state["fetched_at_kst"] = now_kst_str()
        state["fetched_ts_utc"] = datetime.now(timezone.utc).isoformat()
        if state.get("found"):
            _save(ACCOUNT_SNAPSHOT, state)
        return state
