#!/usr/bin/env bash
# ローカルPostgreSQLでDDL案・制約・一括保存・ロールバック・再表示を検証する。
# Aurora DSQLへの適合の証拠にはならない。実行ごとに一意のプロジェクト名で隔離し、
# 終了時（成功・失敗・INT・TERM）にそのプロジェクトだけを削除する。SIGKILLでは後片付けできない。
#   bash scripts/db-verify.sh
# 終了コード: 0=成功 / 0以外=失敗（検証失敗、起動失敗、後片付け失敗を含む）
set -uo pipefail

cd "$(dirname "$0")/.."

# 乱数が取得できなければ、何も起動せず停止する。
suffix="$(python3 -c 'import secrets; print(secrets.token_hex(6))' 2>/dev/null)"
DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))' 2>/dev/null)"
if ! [[ "$suffix" =~ ^[0-9a-f]{12}$ ]] || ! [[ "$DB_PASSWORD" =~ ^[0-9a-f]{32}$ ]]; then
  echo "DB検証: 乱数を取得できなかったため、何も起動せず停止しました。" >&2
  exit 1
fi

project="dtd-db-verify-${suffix}"
export DB_PASSWORD
export DB_NAME="dtd_verify_db"
export DB_USER="dtd_verify_user"

compose=(docker compose -p "$project" -f compose.yaml -f compose.db.yaml --profile verify)
started=0

on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$started" -eq 1 ]; then
    "${compose[@]}" down -v
    local down_code=$?
    if [ "$down_code" -ne 0 ]; then
      echo "DB検証: 後片付けに失敗（終了コード $down_code）。プロジェクト $project の資源が残っている可能性があります。" >&2
      [ "$rc" -eq 0 ] && rc=$down_code
    fi
  fi
  if [ "$rc" -ne 0 ]; then
    echo "DB検証: 失敗（終了コード $rc）" >&2
  else
    echo "DB検証: 成功（ローカルPostgreSQLのみ）"
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 起動の途中で失敗しても資源が残りうるため、upの前に後片付け対象とする。
started=1
"${compose[@]}" up --build --abort-on-container-exit --exit-code-from db-verify db-verify
