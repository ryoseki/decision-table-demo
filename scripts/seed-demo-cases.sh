#!/usr/bin/env bash
# デモ用の架空の案件を、受付API（POST /api/intake/cases）で作る。画面の一覧に案件が出る。
#   bash scripts/seed-demo-cases.sh                  # http://localhost:8000 に作る
#   API_BASE=http://localhost:8000 bash scripts/seed-demo-cases.sh
# 前提: `docker compose up -d` で、backend と db が動いている（ホストから、curl だけで動く）。
# 作る案件（すべて架空）: 未審査 2件、取得エラー（A09が確認待ち）1件、登録済み 1件。
# 実行するたびに、案件が増える。作り直すときは `docker compose down -v` で、DBごと消す。
# 終了コード: 0=成功 / 0以外=失敗（APIに接続できない、受付や判定の保存が失敗した）
set -uo pipefail

api="${API_BASE:-http://localhost:8000}"

if ! curl -sf "$api/health" >/dev/null; then
  echo "デモ案件: $api に接続できません。先に 'docker compose up -d' を実行してください。" >&2
  exit 1
fi

# 受付: summary、user_id、（任意）取得を失敗させる設問。作った案件IDを標準出力へ返す。
intake() {
  local summary="$1" user_id="$2" fault="${3:-}" controls=""
  [ -n "$fault" ] && controls=",\"demo_controls\":{\"fault_questions\":[\"$fault\"]}"
  local body response
  body="{\"summary\":\"$summary\",\"user\":{\"user_id\":\"$user_id\"},\"inquiry\":{\"subject\":\"架空の問い合わせ\",\"body\":\"$summary（架空）\"}$controls}"
  if ! response="$(curl -sf -X POST "$api/api/intake/cases" -H 'Content-Type: application/json' -d "$body")"; then
    echo "デモ案件: 受付に失敗しました（$summary）。" >&2
    return 1
  fi
  printf '%s' "$response" | sed -n 's/.*"case_id":"\([^"]*\)".*/\1/p'
}

# user_id が偶数なら A09=Yes、奇数なら No（モックの契約）。
a="$(intake "身に覚えのないログインの通知が届いた" 456)" || exit 1
b="$(intake "メールアドレス変更の通知に心当たりがない" 457)" || exit 1
c="$(intake "APIキー発行の通知に心当たりがない（取得に失敗）" 458 A09)" || exit 1
d="$(intake "ログイン失敗の通知が夜間に何件も届いた" 460)" || exit 1

# 登録済みの案件: 結果「問題なし」（no_issue）を保存する。
if ! curl -sf -X PUT "$api/api/cases/$d/judgment" -H 'Content-Type: application/json' \
  -d '{"result_code":"no_issue"}' >/dev/null; then
  echo "デモ案件: 判定の保存に失敗しました（$d）。" >&2
  exit 1
fi

echo "デモ案件を作りました:"
echo "  未審査:       $a  $b"
echo "  取得エラー:   $c（A09が確認待ち）"
echo "  登録済み:     $d（結果: 問題なし）"
echo "画面: http://localhost:5173/ を開いて、再読み込みしてください。"
