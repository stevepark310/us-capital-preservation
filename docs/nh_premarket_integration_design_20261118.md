# NH Open API 프리마켓(61) 연동 설계 및 코드 준비

- 작성: 2026-11-18 (KST)
- 상태: **코드 준비 완료 · 실주문 차단(정책 `enabled: false`)·워크플로 1회 반영 대기**
- 근거: `docs/kr_to_us_premarket_research_20261118.md` (프리장 자동매매 도입 기각 권고)

## 1. 요약

| 항목 | 상태 |
|---|---|
| `execution/nh_trader.NHBroker.place_order()` 호가유형 확장 | ✅ `order_type`/`ahi_nmn_pr_tp_cd` 지원, 기본값 `"00"`(기존 동작 동일) |
| 지원 코드 | `"00"` 정규장 지정가 · `"61"` 프리마켓 지정가 · `"62"` 애프터마켓 지정가 |
| `execution/order_bridge.OrderRouter` | ✅ `place(..., order_type=...)` 전달, 디스패치 명령에 조건부 반영 |
| 안전장치 | ✅ `config/premarket_policy.json` `enabled: false` → 61/62 주문은 `BLOCKED_POLICY`로 차단 |
| `.github/workflows/nh_trade_dispatch.yml` | ⚠️ **동훈님 1회 반영 필요** (GitHub App에 `workflows` 권한 없음) — 개정본은 `execution/nh_trade_dispatch.yml.template` |
| 낮 한국 증시 모니터 | ✅ `core/kr_session_signal.py` (읽기 전용, 주문 능력 없음) |
| 자동매매 루프 연결 | ❌ 연결하지 않음(백테스트 결과에 따라 보류) — `tests/test_nh_order_type.py`가 회귀를 감시 |
| 단위 테스트 | ✅ 신규 24건 + 전체 139건 통과 |

## 2. 왜 확장이 필요했나

기존 `place_order()`는 payload에 `ahi_nmn_pr_tp_cd="00"`을 하드코딩해, NH 해외주식 주문 중 **정규장 지정가만** 낼 수 있었다. 프리마켓(18:00~23:30 KST) 주문은 코드 `61`, 애프터마켓(06:00~07:50 KST)은 `62`이며 셋 다 지정가만 지원한다(시장가 코드 없음). 그래서 `order_type` 파라미터를 추가하되 다음 원칙을 지켰다.

1. **기본값은 `"00"`** — 기존 호출부(자동매매 루프·워크플로·스크립트)는 수정 없이 그대로 동작한다.
2. **모르는 값은 조용히 폴백하지 않는다** — 실주문에서 `"61"` 오타가 `"00"`으로 떨어지면 의도하지 않은 정규장 주문이 나간다. `ValueError`로 막는다.
3. **세 유형 모두 `price > 0` 필수** — 지정가 전용이므로 가격 없는 주문은 거부한다.
4. **정책 게이트** — 코드가 준비되어 있어도 `config/premarket_policy.json`이 `enabled: false`면 `OrderRouter`가 디스패치 전에 막는다.

## 3. 구현 상세

### 3.1 `execution/nh_trader.py`

```python
ORDER_TYPE_CODES = {
    "00": ("regular_limit", "정규장 지정가"),
    "61": ("premarket_limit", "프리마켓 지정가"),
    "62": ("aftermarket_limit", "애프터마켓 지정가"),
}
normalize_order_type(order_type=None, ahi_nmn_pr_tp_cd=None) -> "00" | "61" | "62"
```

- 별칭 허용: `"premarket"`, `"PRE-MARKET"`, `"pre_market"`, `"프리마켓"`, `"프리"`, `"after_hours"`, `"시간외"` 등 (공백·하이픈·밑줄 제거 후 매핑).
- `ahi_nmn_pr_tp_cd`가 함께 주어지면 원본 API 필드명 쪽을 우선한다(기존 호출부 호환).
- `place_order(ticker, side, qty, price=0.0, order_type="00", ahi_nmn_pr_tp_cd=None)`:
  - 드라이런 반환에 `order_type`(이름)과 `ahi_nmn_pr_tp_cd`(코드)를 남긴다 — 사후 감사용.
  - 실주문 payload의 `ahi_nmn_pr_tp_cd`에 정규화된 코드를 싣는다.
  - `execute_rebalance(..., order_type="00")`도 같은 정규화를 쓴다.

### 3.2 `execution/order_bridge.py`

- `place(..., order_type="00")` → 기록(`record["order_type"]`)·디스패치 상태(`order_dispatch_state.json`)·주문 로그(`live_order_responses.jsonl`)에 유형을 남긴다.
- **워크플로 호환**: `order_type`이 기본값이면 `-f order_type=`를 **붙이지 않는다**. 현재 워크플로에는 그 입력이 없고, GitHub은 선언되지 않은 입력을 받으면 디스패치를 거부하기 때문이다.
- **정책 게이트**:
  ```python
  if code != "00":
      gate = premarket_gate()      # config/premarket_policy.json
      if not gate["allowed"]:
          return {"status": "BLOCKED_POLICY", "error": gate["reason"], ...}
  ```
  게이트는 드라이런에도 적용된다(프리장 주문 경로 자체를 열지 않는다).
- 멱등 키: 기본 유형은 기존 키 그대로(`2026-11-18|SOXL|BUY|1|50.60`), 비기본 유형만 `|61`을 덧붙여 프리장 주문과 정규장 주문이 중복으로 오인되지 않게 한다.

### 3.3 `config/premarket_policy.json`

```json
{
  "enabled": false,
  "reason": "2026-11-18 백테스트에서 프리장 진입 기대값 음수 … 모니터링·알림 전용",
  "supported_order_types": {"00": "정규장 지정가", "61": "프리마켓 지정가", "62": "애프터마켓 지정가"},
  "enable_requirements": [
    "동훈님 명시 승인",
    "표본 외 t >= 2.0 및 왕복 0.44% 차감 후 건당 기대값 > 0",
    "1주 가격 <= 1회 진입 실효 예산(현재 약 44.80달러)",
    "일일 손실 서킷브레이커·진입 마감 유지"
  ]
}
```

켜는 방법은 이 파일의 `enabled`를 `true`로 바꾸는 하나뿐이다(코드 수정 불필요). 끄는 것도 같은 한 줄이다.

## 4. 워크플로 (동훈님 1회 반영 필요)

`.github/workflows/nh_trade_dispatch.yml` 수정은 **GitHub App 권한 제약으로 실패**한다(2026-11-18 실제 확인):

```
! [remote rejected] … (refusing to allow a GitHub App to create or update workflow
  `.github/workflows/nh_trade_dispatch.yml` without `workflows` permission)
```

그래서 개정본을 `execution/nh_trade_dispatch.yml.template`에 반영해 두었다. 반영 내용은 세 줄이다.

1. `workflow_dispatch.inputs`에 `order_type`(choice: `00`/`61`/`62`, 기본 `00`) 추가
2. 히어독에서 `order_type = "${...}"` 읽기 + `print(f"order_type={order_type}")`
3. `broker.place_order(ticker, side, qty, price, order_type=order_type)`

반영 전에도 **아무것도 깨지지 않는다**: 현행 워크플로는 `place_order(ticker, side, qty, price)`로 호출하고 기본값이 `"00"`이기 때문이다. 다만 반영 전에는 61/62 디스패치가 GitHub에서 거부되므로, `order_bridge`는 기본값일 때 입력을 보내지 않도록 해 두었다.

## 5. 낮 한국 증시 모니터링 설계

### 5.1 모듈

`core/kr_session_signal.py` — **주문 능력이 없다**(`execution.*` import 금지, 테스트가 소스 수준에서 검사).

```bash
python3 -m core.kr_session_signal            # 15:30 KST 직후 요약
python3 -m core.kr_session_signal --write    # data/kr_session_signal.json 저장 + 이력 추가
python3 -m core.kr_session_signal --json     # JSON만
python3 -m core.kr_session_signal --offline  # 네트워크 없이 직전 신호
```

산출 예(2026-11-18 16:09 KST 실행):

```
🇰🇷 한국 세션 신호 (2026-11-18, 2026-11-18 16:09:22 KST)
  코스피 +1.99% / 코스닥 +1.96% / 삼성전자 +2.31% / SK하이닉스 +4.19%
  반도체 2종 평균 +3.25% / 반도체 − 코스피 +1.26% / 원/달러 −0.23%
  반도체 고유(잔차) +2.99%  판정: STRONG
  SOXX 개장 갭 예상 +0.84% (±1.429%p, R² 0.197)
  SOXL 개장 갭 예상 +2.47% (±4.248%p, R² 0.196)
```

### 5.2 판정 체계

| 판정 | 조건(반도체 고유 잔차 z) | 용도 |
|---|---|---|
| `RISK_OFF_ALERT` | ≤ −1.5 (5년 57일, 갭 −1.89%/SOXX·−5.70%/SOXL) | 반도체 갭 하락 경보 — 알림만 |
| `WEAK` | −1.5 < z ≤ −1.0 | 보유 반도체 포지션 점검 |
| `NEUTRAL` | −1.0 < z < +1.5 | — |
| `STRONG` | ≥ +1.5 | 참고(강세 신호의 예측력은 유의하지 않음) |

갭 예상치는 5년 일봉 적합 계수(`config/kr_signal_calibration.json`)를 쓰며, **R² 0.20과 ±1.43·±4.25%p 잔차**를 함께 표시한다 — 단일 숫자로 오해하지 않도록 하기 위함이다.

### 5.3 연결 계획(보류 중)

향후 승인이 나면 다음 두 지점만 추가하면 된다.

1. **스케줄**: 15:40 KST 액션이 `--write` 실행 → 알림 발송. (현재 미등록)
2. **집행(승인 시)**: `core/autotrader.py`의 주문 호출에 `order_type=("61" if 세션 == PREMARKET else "00")` 한 줄. `config/premarket_policy.json`이 `enabled: false`인 동안에는 `BLOCKED_POLICY`로 막히므로 안전하게 미리 넣을 수도 있지만, **백테스트가 지지하지 않는 상태에서 집행 루프를 건드리지 않는다**는 원칙으로 지금은 넣지 않았다(`tests/test_nh_order_type.py::test_autotrader_does_not_place_premarket_orders`가 감시).

## 6. 테스트

| 파일 | 검증 |
|---|---|
| `tests/test_nh_order_type.py` (16건) | 코드 정규화·별칭·모호한 값 거부, 드라이런 반환, payload 코드(00/61/62), 매도 payload, 정책 게이트 차단, 정책 파일 `enabled: false` 고정, 워크플로 명령에 기본값 미전송, 멱등 키 분리, 자동매매 미연결 |
| `tests/test_kr_session_signal.py` (8건) | 수익률·잔차 계산, 갭 예상 계수 적용, `RISK_OFF_ALERT` 판정, 저장·이력, 주문 능력 부재 |

전체 `pytest`: **139 passed**.

## 7. 롤백

| 되돌릴 것 | 방법 |
|---|---|
| 프리장 주문만 차단 | `config/premarket_policy.json` `enabled: false` (이미 기본) |
| 코드 확장 전체 | `execution/nh_trader.py`·`execution/order_bridge.py`를 이전 커밋으로 되돌림 — 기존 호출부는 기본값 `"00"`만 쓰므로 함께 되돌릴 필요 없음 |
| 모니터 | `core/kr_session_signal.py` 미실행(스케줄 미등록 상태이므로 실행 중지 외 작업 없음) |
| 워크플로 | `order_type` 입력이 추가되어도 기본값이 `"00"`이라 동작 동일 |

## 8. 남은 일

1. **동훈님**: `.github/workflows/nh_trade_dispatch.yml`에 템플릿 변경 반영(1회). 그전까지 61/62 디스패치는 GitHub에서 거부된다.
2. **승인 시에만**: `premarket_policy.json` `enabled: true` + `autotrader` 한 줄 연결. 현재 근거 없음.
3. **15:40 KST 알림 스케줄** 등록 여부는 동훈님 판단(제안: 알림 전용으로 등록).
