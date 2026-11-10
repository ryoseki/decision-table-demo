#!/usr/bin/env bash
# 実Aurora DSQLで、スキーマ案のDDL・制約・一括保存・再表示を専用スキーマの中だけで検証する。
# CloudShellなど、AWSの資格情報があるシェルで実行する。認証トークンは表示・保存しない。
#   DSQL_ENDPOINT=<クラスタID>.dsql.ap-northeast-1.on.aws DSQL_REGION=ap-northeast-1 bash scripts/dsql-verify.sh
# 既存のテーブルには触れず、dsql_verify_<乱数> のスキーマだけを作って最後に削除する。
set -uo pipefail
cd "$(dirname "$0")/.."

: "${DSQL_ENDPOINT:?DSQL_ENDPOINTを指定してください}"
: "${DSQL_REGION:?DSQL_REGIONを指定してください}"

# psycopgを用意する。pipがなければ、まずensurepipで入れる。それでも無理なら手順を案内して止まる。
if ! python3 -c 'import psycopg' 2>/dev/null; then
  if ! python3 -m pip --version >/dev/null 2>&1; then
    python3 -m ensurepip --user >/dev/null 2>&1 || true
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "pipがありません。次を実行してから、もう一度このスクリプトを実行してください:" >&2
    echo "  sudo dnf install -y python3-pip" >&2
    exit 1
  fi
  # 仮想環境の中では--userが使えないので、環境に応じて切り替える。
  user_flag="--user"
  if python3 -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'; then user_flag=""; fi
  python3 -m pip install $user_flag --quiet --require-hashes -r db/requirements.txt || {
    echo "psycopgを用意できませんでした。" >&2; exit 1; }
fi

(
  DB_PASSWORD="$(aws dsql generate-db-connect-admin-auth-token --hostname "$DSQL_ENDPOINT" --region "$DSQL_REGION")" || exit 1
  export DB_PASSWORD
  export DB_HOST="$DSQL_ENDPOINT" DB_PORT=5432 DB_NAME=postgres DB_USER=admin DB_SSLMODE=require
  export DB_SCHEMA="dsql_verify_$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
  export DOCS_DIR="$PWD/docs"
  python3 db/verify_schema.py
)
status=$?
if [ "$status" -eq 0 ]; then echo "DSQL検証: 成功"; else echo "DSQL検証: 失敗または未完了（終了コード $status）" >&2; fi
exit "$status"
