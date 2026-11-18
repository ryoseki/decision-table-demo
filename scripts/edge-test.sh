#!/usr/bin/env bash
# エッジ（CloudFront Functions）のテストを、固定したNodeのイメージで実行する。AWSへは接続しない。
#   bash scripts/edge-test.sh
# 設定は compose.edge.yaml（読み取り専用マウント・ネットワークなし）。プロジェクト名は
# EDGE_TEST_PROJECT で変えられる（他の作業と名前が衝突しないようにするため）。
set -euo pipefail
cd "$(dirname "$0")/.."
exec docker compose -p "${EDGE_TEST_PROJECT:-dtd-edge-test}" -f compose.edge.yaml \
  run --rm --no-deps edge-test
