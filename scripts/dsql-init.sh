#!/usr/bin/env bash
# 実Aurora DSQLの専用スキーマに、テーブルと初期データ（設問・判定ルール）を作る。
# 管理者（admin）が、CloudShellなど、AWSの資格情報があるシェルで1回実行する。
# 何度実行しても壊れない。既存のテーブル・データは変更も削除もしない。認証トークンは表示・保存しない。
#   DSQL_ENDPOINT=<クラスタID>.dsql.ap-northeast-1.on.aws DSQL_REGION=ap-northeast-1 \
#   DB_SCHEMA=demo bash scripts/dsql-init.sh
# アプリ用のカスタムロールも作る場合は、次の2つを足す（両方、または両方なし）:
#   APP_DB_ROLE=app_role APP_IAM_ROLE_ARN=arn:aws:iam::<アカウントID>:role/<Lambda実行ロール名>
# 書き込む前に、対象を表示して確認を求める（yes と入力したときだけ進む）。
set -uo pipefail
cd "$(dirname "$0")/.."

: "${DSQL_ENDPOINT:?DSQL_ENDPOINTを指定してください}"
: "${DSQL_REGION:?DSQL_REGIONを指定してください}"
: "${DB_SCHEMA:?DB_SCHEMAを指定してください（専用スキーマの名前。例: demo）}"

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

echo "対象のクラスタ: $DSQL_ENDPOINT"
echo "専用スキーマ: $DB_SCHEMA"
if [ -n "${APP_DB_ROLE:-}" ]; then
  echo "アプリ用のDBロール: $APP_DB_ROLE（IAMロールと関連付け、最小限の権限を付ける）"
fi
read -r -p "このクラスタに書き込みます。続けるには yes と入力: " answer
if [ "$answer" != "yes" ]; then
  echo "中止しました。何も変更していません。" >&2
  exit 1
fi

(
  DB_PASSWORD="$(aws dsql generate-db-connect-admin-auth-token --hostname "$DSQL_ENDPOINT" --region "$DSQL_REGION")" || exit 1
  export DB_PASSWORD
  export DB_HOST="$DSQL_ENDPOINT" DB_PORT=5432 DB_NAME=postgres DB_USER=admin DB_SSLMODE=require
  export DOCS_DIR="$PWD/docs"
  python3 db/init_dsql.py
)
status=$?
if [ "$status" -eq 0 ]; then echo "DSQL初期化: 成功"; else echo "DSQL初期化: 失敗または未完了（終了コード $status）" >&2; fi
exit "$status"
