#!/usr/bin/env bash
# Lambda用ZIPとmanifest.jsonをDockerでビルドする（AWSへは接続しない）。
#   bash scripts/build-lambda.sh <40桁のコミットSHA> <出力ディレクトリ>
#   bash scripts/build-lambda.sh --allow-dirty <40桁のSHA> <出力ディレクトリ>   # ローカル検証専用
# 通常（リリース）モード: 作業ツリーが未コミット変更・未追跡ファイルなしで、HEADが指定SHAと
#   一致するときだけビルドし、releaseEligible=true にする。
# --allow-dirty: releaseEligible=false 固定。配布には使えない。
# 出力ディレクトリは存在しないか空であること（例: dist/cd/<SHA>）。dist/cd はGit無視済み。
# 指定SHAはこのリポジトリに存在するコミットであること（リリースモードはさらにHEAD一致が必要）。
set -euo pipefail

allow_dirty=false
if [ "${1:-}" = "--allow-dirty" ]; then
  allow_dirty=true
  shift
fi
if [ "$#" -ne 2 ]; then
  echo "使い方: bash scripts/build-lambda.sh [--allow-dirty] <40桁のコミットSHA> <出力ディレクトリ>" >&2
  exit 2
fi
sha="$1"
out="$2"

if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "エラー: SHAは40桁の小文字16進数で指定してください（ブランチ名・タグ・短縮SHAは不可）。" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

if [ -e "$out" ] && { [ ! -d "$out" ] || [ -n "$(ls -A "$out")" ]; }; then
  echo "エラー: 出力先 '$out' は存在しないか空のディレクトリにしてください（古い成果物の混在を防ぐため）。" >&2
  exit 2
fi

if ! head_sha="$(git rev-parse --verify HEAD 2>/dev/null)"; then
  echo "エラー: Gitのリポジトリ内で、コミットのあるHEADから実行してください。" >&2
  exit 1
fi
# 存在しないSHAはラベルとしても受け付けない（出所不明の成果物を作らない）。
if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
  echo "エラー: 指定SHA($sha) はこのリポジトリのコミットとして存在しません。" >&2
  exit 1
fi
status="$(git status --porcelain --untracked-files=normal)"
dirty=false
[ -n "$status" ] && dirty=true

if [ "$allow_dirty" = true ]; then
  release_eligible=false
  echo "警告: --allow-dirty。releaseEligible=false の成果物です（配布不可）。" >&2
  [ "$head_sha" = "$sha" ] || echo "警告: HEAD($head_sha) と指定SHAが一致しません。ラベルとしてのみ使います。" >&2
else
  if [ "$dirty" = true ]; then
    echo "エラー: 未コミットの変更または未追跡ファイルがあるためリリースビルドを拒否します。" >&2
    exit 1
  fi
  if [ "$head_sha" != "$sha" ]; then
    echo "エラー: HEAD($head_sha) が指定SHAと一致しません。" >&2
    exit 1
  fi
  release_eligible=true
fi

# --platform で全ステージをlinux/amd64に固定する（ホストがarm64でも宣言どおりのx86_64成果物にする。
# arm64ホストではQEMUなどのエミュレーションが必要）。依存のwheelはDockerfile側でも明示している。
docker buildx build \
  --platform linux/amd64 \
  --file infra/cd/Dockerfile \
  --target artifact \
  --build-arg "SOURCE_SHA=$sha" \
  --build-arg "RELEASE_ELIGIBLE=$release_eligible" \
  --build-arg "DIRTY=$dirty" \
  --output "type=local,dest=$out" \
  .

echo "完了: $out（releaseEligible=$release_eligible）。manifest.json のZIPハッシュを確認してください。"
