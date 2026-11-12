#!/usr/bin/env bash
# 実装タスクの完了前に、変更に対するテストの不足（フロントエンド・バックエンド・E2E）を、
# 独立した新しい Claude Code セッションにレビューさせる。読み取り専用。
#   bash scripts/review-tests.sh --feature-file <機能の詳細のファイル>
#   bash scripts/review-tests.sh --feature-file <ファイル> --base <ref>   # 基準を指定（既定は origin/main）
#   bash scripts/review-tests.sh --feature-file <ファイル> --dry-run      # 渡すプロンプトを表示するだけ
# 機能の詳細（--feature-file、必須）: 何を実装したかを、Linear の課題のタイトル・説明・受入条件から、
#   書かれている通りに転記したMarkdown。作業メモ、テストが通ったという報告、検証結果は含めない。
#   リポジトリの外（mktemp や .symphony/ の下）に置く。コミットしない。
# レビュー担当に渡すのは、機能の詳細と、実装の内容（変更したファイルの一覧と差分の統計）だけ。
# プロンプトは docs/test-review-prompt.md。決まりは docs/testing-policy.md を参照する。
# 認証は Claude のサブスクリプション（ANTHROPIC_API_KEY は外して起動する）。
# 終了コード: 0=レビュー完了 / 0以外=失敗（引数の誤り、変更なし、起動失敗を含む）
set -uo pipefail

cd "$(dirname "$0")/.."

usage() {
  awk 'NR>=2 && /^#/ {sub(/^# ?/, ""); print; next} NR>=2 {exit}' "$0"
}

base=""
feature_file=""
dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base)
      base="${2:-}"
      if [ -z "$base" ]; then echo "レビュー: --base に基準の参照を指定してください。" >&2; exit 2; fi
      shift 2
      ;;
    --feature-file)
      feature_file="${2:-}"
      if [ -z "$feature_file" ]; then echo "レビュー: --feature-file にファイルを指定してください。" >&2; exit 2; fi
      shift 2
      ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "レビュー: 不明な引数です: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$feature_file" ]; then
  {
    echo "レビュー: --feature-file（実装した機能の詳細）が必要です。"
    echo "  Linear の課題のタイトル・説明・受入条件を、書かれている通りにMarkdownへ転記して渡してください。"
    echo "  作業メモ、テストが通ったという報告、検証結果は含めないでください。"
  } >&2
  exit 2
fi
if [ ! -s "$feature_file" ]; then
  echo "レビュー: 機能の詳細のファイルが無い、または空です: $feature_file" >&2
  exit 2
fi
feature="$(cat "$feature_file")"

if [ -z "$base" ]; then
  if git rev-parse --verify --quiet origin/main >/dev/null; then base="origin/main"; else base="main"; fi
fi
if ! merge_base="$(git merge-base HEAD "$base" 2>/dev/null)"; then
  echo "レビュー: 基準 $base との共通の祖先を求められませんでした。--base で指定してください。" >&2
  exit 1
fi

changed="$(git diff --name-status "$merge_base")"
untracked="$(git ls-files --others --exclude-standard | sed 's/^/?\t/')"
if [ -z "$changed" ] && [ -z "$untracked" ]; then
  echo "レビュー: 基準 $base からの変更がありません。" >&2
  exit 1
fi
stat="$(git diff --stat "$merge_base")"

prompt_file="docs/test-review-prompt.md"
if [ ! -f "$prompt_file" ]; then
  echo "レビュー: プロンプトがありません: $prompt_file" >&2
  exit 1
fi

prompt="$(cat "$prompt_file")

---

## 渡された機能の詳細（何を実装したか）

$feature

---

## 渡された実装の内容

- 比較の基準: \`$merge_base\`（\`$base\` との共通の祖先）。差分は \`git diff $merge_base\` で読める。
- 未追跡のファイル（\`?\`）は、差分に出ない。直接読むこと。

### 変更したファイル（状態: A=追加 M=変更 D=削除 R=改名 ?=未追跡）

\`\`\`
$changed
$untracked
\`\`\`

### 差分の統計

\`\`\`
$stat
\`\`\`"

if [ "$dry_run" -eq 1 ]; then
  printf '%s\n' "$prompt"
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "レビュー: claude コマンドが見つかりません。レビューは実行していません（未実行として報告してください）。" >&2
  exit 1
fi

# 読み取りと、決まった git・テストのコマンドだけを許可し、それ以外は確認なしで拒否する（dontAsk）。
# .env（秘密情報）は読めない。書き込みも拒否する。
printf '%s\n' "$prompt" | env -u ANTHROPIC_API_KEY claude -p \
  --permission-mode dontAsk \
  --no-session-persistence \
  --allowedTools \
    Read Grep Glob \
    "Bash(git diff *)" "Bash(git status *)" "Bash(git log *)" "Bash(git show *)" \
    "Bash(docker compose run --rm --no-deps --build backend *)" \
    "Bash(docker compose run --rm --no-deps --build frontend *)" \
    "Bash(docker compose config *)" \
  --disallowedTools \
    Edit Write NotebookEdit "Read(**/.env)" "Read(**/.env.*)"
