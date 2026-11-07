#!/usr/bin/env bash
# E2E環境のスモークテストを実行する。ローカルとCIで同じコマンドを使う。
#   bash scripts/e2e.sh                      # 成功を期待する通常実行
#   bash scripts/e2e.sh --force-failure      # /health の期待値を変え、意図的に失敗させる
# 終了コード: 0=成功 / 0以外=失敗（テスト失敗、起動・待機の失敗を含む）
# 失敗時のレポート: e2e/playwright-report/index.html、トレース: e2e/test-results/
set -uo pipefail

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--force-failure" ]; then
  export E2E_EXPECTED_HEALTH_STATUS="intentional-failure"
  echo "意図的な失敗モード: /health の期待値を変更して実行します。"
fi

mkdir -p e2e/test-results e2e/playwright-report

# frontend・backend の起動と healthcheck 待機は compose が担当し、
# e2e の終了コードをそのまま返す。終了後はE2E専用プロジェクトだけを停止・削除する。
# 通常の開発環境を止めないよう、E2E専用のプロジェクト名とポート無公開の設定で分離する。
compose=(docker compose -p decision-table-demo-e2e -f compose.yaml -f compose.e2e.yaml)
"${compose[@]}" up --build --abort-on-container-exit --exit-code-from e2e e2e
up_code=$?

# 後片付けはテスト結果に関わらず必ず実行し、その終了コードも記録する。
"${compose[@]}" down --remove-orphans
down_code=$?

# 終了コードの決定:
#   - up（起動・テスト）が失敗していれば、down の成否に関わらずその終了コードを保持する。
#   - up が成功し down だけ失敗した場合は、down の終了コードで失敗扱いにする。
if [ "$up_code" -ne 0 ]; then
  exit_code=$up_code
elif [ "$down_code" -ne 0 ]; then
  exit_code=$down_code
else
  exit_code=0
fi

if [ "$up_code" -ne 0 ]; then
  echo "E2Eスモークテスト: 失敗（テストまたは起動の終了コード $up_code）。e2e/playwright-report と e2e/test-results を確認してください。" >&2
fi
if [ "$down_code" -ne 0 ]; then
  echo "E2Eスモークテスト: 後片付け（docker compose down）に失敗（終了コード $down_code）。プロジェクト decision-table-demo-e2e のコンテナやネットワークが残っている可能性があります。" >&2
fi
if [ "$exit_code" -eq 0 ]; then
  echo "E2Eスモークテスト: 成功"
else
  echo "E2Eスモークテスト: 失敗（最終終了コード $exit_code）" >&2
fi
exit "$exit_code"
