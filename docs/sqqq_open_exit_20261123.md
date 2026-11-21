# 2026-11-23 SQQQ 1주 개장 청산 + 매도 전용 게이트

사용자 승인(2026-11-21 10:41 KST)
> "월요일 게이트 매도 전용 재개하고, SQQQ 1주는 11/23 개장 직후 시장가 익절로 준비해줘. 3주 확대는 보류할게."

## 1. 무엇을 바꿨나 (요약)

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| `config/live_execution_gate.json` | `enabled: false` (11-20 06:00 만료) | `enabled: true` + **`sell_only: true`**, 매수 한도 3종 모두 `0`, 세션 `2026-11-23`, 만료 `2026-11-24 06:00 KST` |
| 게이트 검증(`core/live_session_gate.py`) | `0 < max_buy_order_usd` — 0을 '잘못된 값'으로 보고 게이트를 닫아 **매도까지 차단** | `sell_only: true`이면 매수 한도 0을 정상값으로 인정(하나라도 0이 아니면 fail-closed) |
| 매수 차단 지점 | 없음(게이트만) | ① 게이트 판정 ② `OrderRouter._live_pretrade_check`(BUY 하드 블록) ③ `AutonomousTrader.entries()`(주문 생성 자체 차단) |
| SQQQ 1주 개장 청산 | 규칙 없음(샹들리에·SL $45.99·05:40 종가청산만) | `config/open_exit_plan.json` + `core/open_exit.py` — 11-23 개장(23:30 KST) 이후 첫 정규장 사이클에서 1주 청산 |
| 브로커 시세(직전 세션 종가) | 청산 판정가의 '보수적 = 낮은 쪽'으로 사용 → 11-21 05:57 `$49.53`(실제 `$57.66`) | 원장 `price_as_of`가 세션이 다르면 판정 기준가에서 제외 + 5분봉이 신선한데 괴리 > 1%면 브로커 값 무시 |
| 인버스 3주 확대 | 3주 제안 | **보류 유지** (`HEDGE_NOTIONAL_CAP` 등 손대지 않음) |

## 2. 게이트 설정값 (2026-11-23 세션 전용)

```json
{
  "enabled": true,
  "session_date": "2026-11-23",
  "expires_at_kst": "2026-11-24 06:00:00",
  "sell_only": true,
  "max_buy_order_usd": 0,
  "max_session_buy_usd": 0,
  "max_session_buy_orders": 0
}
```

* 세션 날짜와 만료가 **둘 다** 맞아야 열린다(`check_live_gate`). 만료 뒤에는 자동으로 DRY_RUN.
* `sell_only: true`인데 매수 한도가 1 이상이면 게이트 전체가 닫힌다(설정 실수로 매수가 열리는 경로 차단).

## 3. 개장 청산 지시 (`config/open_exit_plan.json`)

* `SQQQ 1주`, `mode: market`, 세션 `2026-11-23`, 개장 `23:30 KST`, 만료 `2026-11-24 06:00 KST`, 최대 3회 시도.
* `core/autotrader.exits()`가 **다른 청산 규칙보다 먼저(⓪)** 이 지시를 본다.
* NH 해외주식 API에는 시장가 코드가 없다(지정가 `00/61/62`만). 그래서 "시장가"는 **즉시 체결되는
  공격적 지정가**(판정가 × (1 − 300bp))로 낸다. 매도 지정가는 최우선 매수호가에서 체결되므로
  이 오프셋은 체결 확률만 올리고 체결가를 더 나쁘게 만들지 않는다.
* 접수되면 `data/open_exit_state.json`에 `done: true`로 남아 다시 나가지 않는다. 미체결로 남으면
  기존 **05:40 KST 종가청산**(브래킷 `force_exit_kst`)이 닫는다.

## 4. 월요일 집행 경로 (자동)

```
23:34 KST  GitHub Actions market_autotrade.yml (cron 4-59/10 13-21 UTC)
  → scripts/cloud_cycle.py market
      · 게이트 판정: enabled + sell_only + 세션 2026-11-23 + 만료 전 → 실주문 모드
      · 팀/계좌 preflight (매도 전용은 10인 하드락을 차단 사유로 쓰지 않음,
        NH 잔고 읽기 성공은 요구, 원장-잔고 수량 불일치는 경고만)
  → scripts/autotrader.py --execute (sell_only=True)
      · exits ⓪ 개장 청산 지시 → SQQQ 1주 SELL
      · entries: 매도 전용 게이트로 전면 차단
  → execution/order_bridge.OrderRouter
      · 주문 직전 NH 잔고·매도가능수량 재확인(SELL), BUY는 게이트에서 하드 블록
      · 러너에서는 NH API 직접 발주(`_try_direct`), 막히면 gh workflow run 디스패치
```

집행 명령(러너가 실제로 만드는 형태):

```
gh workflow run nh_trade_dispatch.yml -R stevepark310/us-capital-preservation \
  -f mode=sell -f ticker=SQQQ -f qty=1 -f price=<23:34 판정가 × 0.97>
```

## 5. 개장 점검 절차 (사람이 볼 때)

```bash
# 1) 게이트·지시가 살아 있는지
python3 - <<'PY'
import json
print(json.dumps(json.load(open("config/live_execution_gate.json")), ensure_ascii=False, indent=2))
print(json.dumps(json.load(open("config/open_exit_plan.json")), ensure_ascii=False, indent=2))
PY

# 2) 23:34 이후 사이클 결과 확인
python3 scripts/autotrader.py --status          # 보유/매도/개장 청산 상태
python3 -c "import json;d=json.load(open('data/cloud_status.json'));print(json.dumps(d['market'],ensure_ascii=False,indent=2))"
cat data/open_exit_state.json                   # SQQQ: done / order_no
tail -3 data/live_order_responses.jsonl         # 주문 응답 rsp_cd

# 3) 체결 확인 (NH 잔고·체결내역)
gh workflow run nh_trade_dispatch.yml -R stevepark310/us-capital-preservation -f mode=test_balance
```

* 23:34 사이클이 돌지 않았으면(크론 공백) 즉시 수동 1회:
  `gh workflow run market_autotrade.yml -R stevepark310/us-capital-preservation`
* 05:40 KST까지 미체결이면 브래킷 종가청산이 최종 방어선이다.
* 장 마감(06:00 KST) 뒤 게이트는 자동 만료된다 — 11-24 이후 세션은 다시 별도 승인이 필요하다.

## 6. 검증

* `python3 -m pytest -q` → **327 passed** (신규 `tests/test_sell_only_open_exit.py` 14건 포함).
* 모의 실행(2026-11-23 23:34 KST, 시세 $58.05): 매도 1건 `price=56.31`(=58.05×0.97), 매수 0건,
  `gh workflow run ... -f mode=sell -f ticker=SQQQ -f qty=1 -f price=56.31`, 사전점검 OK,
  `open_exit_state.json` → `done: true`.
* 11-21 05:57 결함 재현: 브로커 `$49.68`(11-19 종가) + 5분봉 `$58.055` → 종전 판정가 `$49.53`,
  수정 후 판정가 `$58.055`(브로커 값 무시, 신호 품질 경고 `trusted: false`는 유지).
