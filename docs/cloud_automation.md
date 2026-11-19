# 클라우드 상시 자동매매 · 10인 협업 빌드업 (2026-11-19)

피클 앱이 꺼지거나 모델 토큰이 소진돼도 **GitHub Actions만으로** 24/7 돌아가는 실행 구조.
거래 판단·주문·검증·기록이 모두 저장소(`data/`)에 남고, 러너가 그 파일을 직접 갱신한 뒤
`main`에 커밋·푸시한다.

## 워크플로 두 개

| 워크플로 | 파일 | 주기 (UTC 크론) | 하는 일 |
|---|---|---|---|
| CUBE Market Autotrade | `.github/workflows/market_autotrade.yml` | `*/10 13-21 * * 1-5` | 정규장이면 `scripts/cloud_cycle.py market` — 원장 동기화 → 유령 포지션 정리 → `autotrader --execute` → `monitor_and_exit --execute` → 원장 재동기화 → 평단 계획 재생성 → 상태 최신화 → `docs/` 발행 |
| CUBE Collab Pulse | `.github/workflows/cube_pulse.yml` | `*/30 * * * *` | `scripts/cloud_cycle.py pulse` — 원장·상태·가격 캐시 갱신 → 10인 업무보고 → 협업·빌드업 사이클 → 대시보드 렌더 |

두 워크플로는 `concurrency: group: cube-repo-writer`로 **직렬화**된다(동시에 `main`을 쓰지 않는다).
푸시는 `data`·`docs`(+ 펄스는 `index.html`·`assets`)만 스테이징하고, 실패하면 `pull --rebase` 후 3회까지 재시도한다.

### 크론 창이 서머타임을 덮는 방식
- EDT(3~11월): 09:30–16:00 ET = 13:30–20:00 UTC
- EST(11~3월): 09:30–16:00 ET = 14:30–21:00 UTC
- 크론은 UTC 13~21시를 넓게 잡고, **실제 세션 판정은 `core/market_clock`이 한다**(미국 동부 DST 규칙 계산).
  정규장이 아니면 `cloud_cycle.py market`이 `SKIPPED_OFF_HOURS`로 끝나 주문이 나가지 않는다.

## 필요한 Secrets

| 이름 | 값 |
|---|---|
| `NH_APP_KEY` / `NH_APP_SECRET` | NH투자증권 Open API 앱키·시크릿 |
| `NH_ACT_NO` | 계좌번호 `202-05-134849` |

`GITHUB_TOKEN`은 `permissions: contents: write`로 푸시에만 쓴다.
러너에서는 NH API가 직접 열려 있어 주문·잔고를 **중첩 디스패치 없이** 직접 호출한다
(`execution/order_bridge.py::_try_direct` / `_try_direct_account`).

## 안전장치 (코드에 고정)

- `config/swing_approval.json autonomous_execution.enabled` = true, `ask_before_trade` = false (2026-11-17 위임)
- `data/autotrader_halt.json` 파일이 있으면 신규 매수 중단(청산은 계속)
- 현금 마지노선 `$150` · 1회 예산 `$150~$220` · 일일 실현손실 `-$25`/-3% · 물타기 금지
- `NVDA` 잠금 `TP $173.0 / SL $150.0` — 자동 변경 없음
- 프리마켓 자동매매 `config/premarket_policy.json enabled=false` 유지
- 자동 개선은 **진입 필터 조이기**만, OOS 게이트(표본·기대값·PF·MDD) 통과 시

## 수동 실행·확인

```bash
gh workflow run market_autotrade.yml -R stevepark310/us-capital-preservation -f dry_run=true
gh workflow run cube_pulse.yml -R stevepark310/us-capital-preservation
gh run list -R stevepark310/us-capital-preservation --limit 5
```

로컬에서 상태만 보고 싶으면:

```bash
python3 scripts/cloud_cycle.py status      # data/cloud_status.json
python3 scripts/autotrader.py --status     # 마지막 집행 사이클
python3 -m core.buildup                    # 가설·개선 장부(요약 없음)
```

## 실패했을 때

- 시장이 열려 있는데 `cloud_status.json`의 `pulse.last_cycle_kst`가 75분보다 오래되면 대시보드가 `지연`으로 표시한다.
- Actions 로그에서 `::warning::push 실패`가 보이면 다음 사이클이 자동 재시도한다.
- 주문이 두 번 나가지 않는 근거: ① `concurrency` 직렬화 ② 주문 멱등키(`order_dispatch_state.json`)
  ③ `active_brackets.json`의 `closed`/`pending`, `autotrader_state.json`의 `pending` 확인.
