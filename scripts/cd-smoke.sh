#!/usr/bin/env bash
# 配布先（または展開したZIPのローカルコンテナ）へのスモーク確認。AWSのAPIは呼ばない。
#   CD_BASE_URL=https://... CD_EXPECTED_SHA=<40桁> bash scripts/cd-smoke.sh
#   CD_ALLOW_LOCAL=true CD_BASE_URL=http://<コンテナ名>:8000 CD_EXPECTED_SHA=<40桁> \
#     bash scripts/cd-smoke.sh --network <Dockerネットワーク名>     # ローカル統合確認専用
# --network は、ローカルのコンテナへ到達するためのDockerネットワーク名（任意）。
# 終了コード: 0=成功 / 0以外=失敗（SHA不一致・接続不能・設定不足を含む）
# レポート: e2e/cd/playwright-report/<ID>、トレース等: e2e/cd/test-results/<ID>（Git無視）。
#   既存E2E（scripts/e2e.sh）の出力先とは分離しており、そちらの実行では消えない。
# CD_REPORT_ID=<識別子>（任意。英数字で始まる64文字以内の英数字・_・.・-）を指定すると、
#   上記の下の <識別子>/ に出力し、過去の記録を上書きしない。
#   未指定なら _default/ だけに出力する（再実行で上書きされるのは _default のみ。
#   ルートや名前付きの記録は消さない）。正式な確認には一意な CD_REPORT_ID を使う。
set -uo pipefail

network=""
if [ "${1:-}" = "--network" ]; then
  network="${2:-}"
  if ! [[ "$network" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    echo "エラー: --network にはDockerネットワーク名を指定してください。" >&2
    exit 2
  fi
  shift 2
fi
if [ "$#" -ne 0 ]; then
  echo "使い方: CD_BASE_URL=... CD_EXPECTED_SHA=... bash scripts/cd-smoke.sh [--network <名前>]" >&2
  exit 2
fi
for name in CD_BASE_URL CD_EXPECTED_SHA; do
  if [ -z "${!name:-}" ]; then
    echo "エラー: 環境変数 $name が必要です（暗黙の既定値は使いません）。" >&2
    exit 2
  fi
done

case "${CD_ALLOW_LOCAL:-}" in
  ""|true|false) ;;
  *) echo "エラー: CD_ALLOW_LOCAL は true または false（未設定は false）です。" >&2; exit 2 ;;
esac

report_id="${CD_REPORT_ID:-}"
if [ -n "$report_id" ] && ! [[ "$report_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]]; then
  echo "エラー: CD_REPORT_ID は英数字で始まる64文字以内の英数字・_・.・- で指定してください。" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
mkdir -p e2e/cd/test-results e2e/cd/playwright-report
report_path="e2e/cd/playwright-report/${report_id:-_default}"

image="decision-table-demo-cd-smoke"
docker build -q -t "$image" e2e >/dev/null || { echo "エラー: e2eイメージのビルドに失敗しました。" >&2; exit 1; }

# 設定ファイルは e2e/cd のものに差し替え、値は -e で名前だけ渡して環境から引き継ぐ。
net_args=()
[ -n "$network" ] && net_args=(--network "$network")
docker run --rm "${net_args[@]}" \
  -e CD_BASE_URL -e CD_EXPECTED_SHA -e CD_ALLOW_LOCAL -e CD_REPORT_ID -e CI=true \
  -v "$PWD/e2e/cd/test-results:/e2e/cd/test-results" \
  -v "$PWD/e2e/cd/playwright-report:/e2e/cd/playwright-report" \
  "$image" npx playwright test --config cd/playwright.config.ts
code=$?

if [ "$code" -eq 0 ]; then
  echo "CDスモーク確認: 成功"
else
  echo "CDスモーク確認: 失敗（終了コード $code）。$report_path を確認してください。" >&2
fi
exit "$code"
