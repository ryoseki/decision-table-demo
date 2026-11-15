#!/usr/bin/env bash
# アプリ用カスタムロールの権限を、管理者の接続で、カタログから読み取って確認する（読み取りのみ）。
# CloudShellなど、AWSの資格情報があるシェルで実行する。認証トークンは表示・保存しない。
#   DSQL_ENDPOINT=<クラスタID>.dsql.ap-northeast-1.on.aws DSQL_REGION=ap-northeast-1 \
#   DB_SCHEMA=demo bash scripts/dsql-check-role.sh
# ロール名は既定で app_role。違う場合は APP_DB_ROLE で指定する。
set -uo pipefail
cd "$(dirname "$0")/.."

: "${DSQL_ENDPOINT:?DSQL_ENDPOINTを指定してください}"
: "${DSQL_REGION:?DSQL_REGIONを指定してください}"
: "${DB_SCHEMA:?DB_SCHEMAを指定してください（例: demo）}"

if ! python3 -c 'import psycopg' 2>/dev/null; then
  if ! python3 -m pip --version >/dev/null 2>&1; then
    python3 -m ensurepip --user >/dev/null 2>&1 || true
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "pipがありません。次を実行してから、もう一度このスクリプトを実行してください:" >&2
    echo "  sudo dnf install -y python3-pip" >&2
    exit 1
  fi
  user_flag="--user"
  if python3 -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'; then user_flag=""; fi
  python3 -m pip install $user_flag --quiet --require-hashes -r db/requirements.txt || {
    echo "psycopgを用意できませんでした。" >&2; exit 1; }
fi

(
  DB_PASSWORD="$(aws dsql generate-db-connect-admin-auth-token --hostname "$DSQL_ENDPOINT" --region "$DSQL_REGION")" || exit 1
  export DB_PASSWORD
  export DB_HOST="$DSQL_ENDPOINT" DB_PORT=5432 DB_NAME=postgres DB_USER=admin DB_SSLMODE=require
  python3 db/check_app_role.py
)
status=$?
if [ "$status" -eq 0 ]; then echo "権限の確認: 期待どおり"; else echo "権限の確認: 期待と違う項目あり、または未完了（終了コード $status）" >&2; fi
exit "$status"
