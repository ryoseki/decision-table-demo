#!/bin/sh
# Lambda Web Adapter（Handler=run.sh、AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap）または
# 展開したZIPのローカル確認から起動する。ポートはアダプタの PORT=8000 と揃える。
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export STATIC_DIR="$ROOT/static"
export RELEASE_FILE="$ROOT/release.json"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
