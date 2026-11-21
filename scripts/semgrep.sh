#!/usr/bin/env bash
# Semgrepの標準ルール（p/default）で、Python・TypeScriptのソースを検査する薄い入口。
#   bash scripts/semgrep.sh        引数なし。終了コードはSemgrepのまま返す。
# 終了コード: 0 = 検出なし、1 = 検出あり（--error）、その他 = 失敗。
#   dockerがない（127）・Dockerに接続できない／起動できない（125）も、成功ではない。
# 出力の概要（検査したファイル数、エラー、解析できなかったファイル）を読んでから、結果を報告する。
# 版・対象・除外・限界は docs/semgrep.md を参照する。
set -uo pipefail

if [ $# -gt 0 ]; then
  printf 'usage: bash scripts/semgrep.sh（引数なし）\n' >&2
  exit 2
fi

# 固定する版（Semgrep 1.184.0）。ルール（p/default）は、公式のレジストリから、実行のたびに取得する。
SEMGREP_IMAGE=semgrep/semgrep@sha256:cc1b68266f267878b39739373ea9985b9a78e5d64497dd875c8de100b22695ed

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Dockerに接続できないときも、dockerは終了コード1を返し、「検出あり」と区別できない。先に確かめて、125を返す。
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  printf 'Semgrep: Dockerデーモンに接続できません。検査していません。\n' >&2
  exit 125
fi

# ソースは読み取り専用。ルールの取得のため、コンテナのネットワークは有効のまま（Dockerの既定。通信先の制限はしていない）。
# --config auto は、プロジェクトのURLを送るので使わない。
exec docker run --rm --pull missing --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -v "$repo_root:/src:ro" -w /src "$SEMGREP_IMAGE" \
  semgrep scan --config p/default --metrics off --disable-version-check --error --strict \
  --no-git-ignore --include '*.py' --include '*.ts' --include '*.tsx' \
  --exclude node_modules --exclude __pycache__ --exclude .venv --exclude dist --exclude build \
  backend db e2e frontend infra scripts tests
