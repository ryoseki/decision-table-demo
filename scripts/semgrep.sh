#!/usr/bin/env bash
# Semgrepの静的解析（Python・TypeScript）を、WSL（Linux）から繰り返し実行する。
#   bash scripts/semgrep.sh [scan]       リポジトリのソースを検査する（既定）
#   bash scripts/semgrep.sh fixtures     ルールを、架空の違反例（security/semgrep/fixtures）で検査する
#   bash scripts/semgrep.sh scan --root <dir>   検査する場所を変える（ルールは常にこのリポジトリのもの）
# 版・ルール・対象・除外・読み方は docs/semgrep.md を参照する。
# 終了コード:
#   0  = 問題なし（scan: 1件以上のファイルを検査し、検出なし。fixtures: 期待どおり）
#   10 = 検出あり（scan のみ）
#   20 = 実行できない（docker／python3 がない、Dockerデーモンに接続できない、固定したイメージを入手できない）
#   30 = 失敗（Semgrepの実行失敗、設定・ルールの誤り、構文エラー、検査されなかったファイル、対象が0件、
#        フィクスチャの期待との不一致）
#   2  = 引数の誤り
# Semgrepは、版とdigestを固定したコンテナで、ネットワークなし・ソースは読み取り専用で動かす。
# ルールはローカルのファイルだけを使う（Semgrepのレジストリは使わない）。メトリクスは送らない。
set -uo pipefail

# 固定する版。更新するときは、docs/semgrep.md の「版の固定」に従い、公式のリリース情報で確認して一緒に直す。
SEMGREP_VERSION=1.184.0
SEMGREP_IMAGE=semgrep/semgrep@sha256:cc1b68266f267878b39739373ea9985b9a78e5d64497dd875c8de100b22695ed

# 検査の対象（リポジトリのルートからの相対）。足すときは、docs/semgrep.md の対象の表も直す。
TARGET_DIRS=(backend db e2e frontend infra scripts tests)
TARGET_EXTENSIONS=(py ts tsx js jsx mjs cjs)
# 除外するディレクトリ名（依存・生成物・キャッシュ）。検査の対象に、フィクスチャは含まれない。
EXCLUDE_DIRS=(node_modules __pycache__ .venv venv dist build coverage playwright-report test-results .import_linter_cache .ruff_cache .mypy_cache)

usage() {
  awk 'NR>=2 && /^#/ {sub(/^# ?/, ""); print; next} NR>=2 {exit}' "$0"
}

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode=scan
root="$repo_root"
# ルールの置き場所。通常は変えない（テストが、壊れたルールや、検出しないルールで、失敗を確かめるときだけ使う）。
rules_dir="${SEMGREP_RULES_DIR:-$repo_root/security/semgrep/rules}"
if [ $# -gt 0 ] && { [ "$1" = scan ] || [ "$1" = fixtures ]; }; then mode="$1"; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      if [ "$mode" != scan ] || [ -z "${2:-}" ]; then usage >&2; exit 2; fi
      root="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Semgrep: 不明な引数です: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
if [ ! -d "$root" ]; then
  printf 'Semgrep: --root がディレクトリではありません。\n' >&2
  exit 2
fi
root="$(cd -- "$root" && pwd -P)"
if [ ! -d "$rules_dir" ]; then
  printf 'Semgrep: ルールの置き場所がありません。\n' >&2
  exit 2
fi
rules_dir="$(cd -- "$rules_dir" && pwd -P)"

unavailable() { printf 'Semgrep: 実行できません: %s\n（検査していません。問題なしとは扱わないでください。）\n' "$1" >&2; exit 20; }
failed() { printf 'Semgrep: 失敗: %s\n' "$1" >&2; exit 30; }

command -v docker >/dev/null 2>&1 || unavailable "docker コマンドが見つかりません。"
command -v python3 >/dev/null 2>&1 || unavailable "python3 コマンドが見つかりません。"
docker info >/dev/null 2>&1 || unavailable "Dockerデーモンに接続できません。"
if ! docker image inspect "$SEMGREP_IMAGE" >/dev/null 2>&1; then
  printf 'Semgrep: 固定したイメージを取得します（初回のみ。検査そのものはネットワークなしで動かします）。\n' >&2
  docker pull --quiet "$SEMGREP_IMAGE" >/dev/null || unavailable "固定したイメージ（$SEMGREP_IMAGE）を入手できません。"
fi

work="$(mktemp -d)" || failed "作業用ディレクトリを作れません。"
trap 'rm -rf -- "$work"' EXIT

# 検査するファイルを、ここで列挙して、Semgrepへ明示的に渡す（.gitignore や既定の除外に任せない）。
cd "$repo_root" || failed "リポジトリのルートへ移動できません。"
if [ "$mode" = fixtures ]; then
  root="$repo_root"
  find security/semgrep/fixtures -type f | LC_ALL=C sort >"$work/files" || failed "フィクスチャを列挙できません。"
else
  cd "$root" || failed "検査する場所へ移動できません。"
  existing=()
  for dir in "${TARGET_DIRS[@]}"; do [ -d "$dir" ] && [ ! -L "$dir" ] && existing+=("$dir"); done
  : >"$work/files"
  if [ ${#existing[@]} -gt 0 ]; then
    find_args=()
    for name in "${EXCLUDE_DIRS[@]}"; do find_args+=(-name "$name" -o); done
    ext_args=()
    for ext in "${TARGET_EXTENSIONS[@]}"; do ext_args+=(-name "*.$ext" -o); done
    # -type f はシンボリックリンクを含まない（ルートの外を読まない）。
    find "${existing[@]}" \( "${find_args[@]}" -false \) -prune -o -type f \( "${ext_args[@]}" -false \) -print |
      LC_ALL=C sort >"$work/files" || failed "対象のファイルを列挙できません。"
  fi
fi
count="$(wc -l <"$work/files")"
if [ "$count" -eq 0 ]; then
  # 0件は、検出ゼロの成功ではない。何も検査していないので、失敗として扱う。
  failed "検査する対象のファイルがありません（0件）。検査していません。--root と対象のディレクトリを確認してください。"
fi
mapfile -t files <"$work/files"

# 検査にGitは使わない。コンテナの中のSemgrepが `.git` を見てgitを呼び、外部を指すワークツリーの
# `.git` で失敗しないよう、コンテナの中では `.git` を空に見せる（ホスト側は変えない）。
mask_git=()
if [ -d "$root/.git" ]; then
  mask_git=(--tmpfs /src/.git:ro,size=4k)
elif [ -e "$root/.git" ]; then
  mask_git=(-v /dev/null:/src/.git:ro)
fi

container=(docker run --rm --pull never --network none --read-only --tmpfs /tmp
  --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)"
  -e HOME=/tmp -e SEMGREP_SEND_METRICS=off -e SEMGREP_ENABLE_VERSION_CHECK=0
  -v "$root:/src:ro" "${mask_git[@]}" -v "$rules_dir:/rules:ro" -w /src "$SEMGREP_IMAGE")

# 構文チェック: Semgrepは、ルールの候補になる語がないファイルを解析せず、構文エラーがあっても何も報告しない
# （検出0件に見える）。そこで、全ファイルを、Semgrepの構文解析（show dump-ast。構文エラーで終了コード3）に
# 通し、解析できないファイルがあれば、検査を失敗として扱う。.tsx も ts として解析できる。
parse_script='
check() {
  case "$1" in
    *.py) lang=python ;;
    *.ts|*.tsx) lang=ts ;;
    *.js|*.jsx|*.mjs|*.cjs) lang=js ;;
    *) echo "$1"; return 1 ;;
  esac
  semgrep show dump-ast "$lang" "$1" >/dev/null 2>&1 || { echo "$1"; return 1; }
}
rc=0
for f in "$@"; do check "$f" || rc=1; done
exit $rc'
parse_rc=0
"${container[@]}" sh -c "$parse_script" sh "${files[@]}" >"$work/parse.out" 2>"$work/parse.err" || parse_rc=$?
if [ "$parse_rc" -ne 0 ]; then
  if [ -s "$work/parse.out" ]; then
    printf 'Semgrep: 構文を解析できないファイルがあります（%s 件）。検査を完了できません。\n' "$(wc -l <"$work/parse.out")" >&2
    head -n 10 "$work/parse.out" | sed 's/^/  - /' >&2
    exit 30
  fi
  tail -n 10 "$work/parse.err" >&2
  failed "構文チェックの実行が異常終了しました（終了コード $parse_rc）。"
fi

docker_rc=0
"${container[@]}" semgrep scan --config /rules --metrics off --disable-version-check --strict --error \
  --no-git-ignore --json --quiet --timeout 30 \
  -- "${files[@]}" >"$work/out.json" 2>"$work/err.log" || docker_rc=$?

# 0=問題なし、1=検出あり（--error）。それ以外は、Semgrepまたはdockerの失敗。
if [ "$docker_rc" -ne 0 ] && [ "$docker_rc" -ne 1 ]; then
  tail -n 15 "$work/err.log" >&2
  failed "Semgrepの実行が異常終了しました（終了コード $docker_rc）。"
fi

cd "$repo_root" || failed "リポジトリのルートへ移動できません。"
report_rc=0
if [ "$mode" = fixtures ]; then
  python3 -B -I scripts/semgrep_report.py fixtures --json "$work/out.json" --version "$SEMGREP_VERSION" \
    --rules "$rules_dir" --fixtures security/semgrep/fixtures || report_rc=$?
else
  python3 -B -I scripts/semgrep_report.py scan --json "$work/out.json" --version "$SEMGREP_VERSION" \
    --expected "$work/files" --engine-rc "$docker_rc" || report_rc=$?
fi
if [ "$report_rc" -ne 0 ] && [ "$report_rc" -ne 10 ] && [ -s "$work/err.log" ]; then
  tail -n 10 "$work/err.log" >&2
fi
exit "$report_rc"
