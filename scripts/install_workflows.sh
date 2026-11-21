#!/usr/bin/env bash
# 클라우드 워크플로 설치 — execution/*.yml.template → .github/workflows/
#
# 왜 필요한가: 이 저장소에 연결된 GitHub App에는 `workflows` 권한이 없어서
# `.github/workflows/*.yml` 푸시가 403으로 거절된다(2026-11-19 실측).
# 권한이 있는 곳(동훈님 로컬 clone 또는 GitHub 웹 UI)에서 한 번만 설치하면 된다.
#
# 사용법
#   bash scripts/install_workflows.sh          # 설치(이미 같으면 건너뜀)
#   bash scripts/install_workflows.sh --check  # 설치 상태만 확인
#   bash scripts/install_workflows.sh --only macro_leading_sync   # 한 개만 설치(기존 워크플로 보존)
#   git add .github/workflows && git commit -m "ci: cloud workflows" && git push
set -euo pipefail
cd "$(dirname "$0")/.."

PAIRS=(
  "execution/market_autotrade.yml.template:.github/workflows/market_autotrade.yml"
  "execution/cube_pulse.yml.template:.github/workflows/cube_pulse.yml"
  "execution/macro_leading_sync.yml.template:.github/workflows/macro_leading_sync.yml"
)

check_only=0
only=""
rc=0
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  case "${args[$i]}" in
    --check) check_only=1 ;;
    --only)  i=$((i+1)); only="${args[$i]:-}" ;;
  esac
  i=$((i+1))
done

for pair in "${PAIRS[@]}"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  if [ -n "$only" ] && [[ "$dst" != *"$only"* ]]; then
    continue
  fi
  if [ ! -f "$src" ]; then echo "✗ 템플릿 없음: $src"; rc=1; continue; fi
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    echo "✓ 설치됨: $dst"
    continue
  fi
  if [ "$check_only" = "1" ]; then
    echo "✗ 미설치/불일치: $dst  (bash scripts/install_workflows.sh 로 설치)"
    rc=1
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "＋ 설치: $dst"
done

if [ "$check_only" = "1" ]; then
  exit $rc
fi
echo
echo "다음 단계(workflows 권한이 있는 곳에서 1회):"
echo "  git add .github/workflows && git commit -m \"ci(cloud): 24/7 자율매매·협업 펄스·선행지표 워크플로\" && git push"
echo "설치 후 확인:"
echo "  gh run list -R stevepark310/us-capital-preservation --limit 5"
echo "  gh workflow run cube_pulse.yml -R stevepark310/us-capital-preservation   # 즉시 1회 시험"
