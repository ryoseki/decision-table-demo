---
tracker:
  kind: linear
  provider:
    api_key: $LINEAR_API_KEY
    project_slug: __LINEAR_PROJECT_SLUG__
  required_labels:
    - symphony-pilot
  active_states:
    - Todo
    - In Progress
  terminal_states:
    - Done
    - Canceled
    - Cancelled
    - Duplicate
polling:
  interval_ms: 10000
workspace:
  root: $SYMPHONY_WORKSPACE_ROOT
hooks:
  after_create: |
    git clone --no-hardlinks "$SYMPHONY_SOURCE_REPO" .
agent:
  max_concurrent_agents: 1
  max_turns: 5
codex:
  command: '"$SYMPHONY_CODEX_BIN" app-server'
server:
  host: 127.0.0.1
---

あなたは不正審査デモのLinear Issue {{ issue.identifier }} を担当します。

タイトル: {{ issue.title }}
現在の状態: {{ issue.state }}
URL: {{ issue.url }}
説明:
{{ issue.description }}

{% if attempt %}
これは再開または再試行です（{{ attempt }}回目）。現在の作業コピーと進捗を確認し、完了済みの作業を繰り返さないでください。
{% endif %}

## 着手

作業コピーのAGENTS.mdとIssueに関係する文書を読みます。`linear_graphql`でこのIssueの現在の状態、チームの状態一覧、既存の進捗コメントを確認します。`Todo`なら`In Progress`へ移し、`In Progress`なら既存の進捗から再開します。すでに実行対象外の状態に移っていれば、作業を終了します。

既存の「Symphony workpad」があれば同じコメントを更新し、なければ1件だけ作成します。計画、変更したファイル、実行した検証、人の判断が必要な事項を記録します。Linearへの書き込みは担当Issueの進捗と状態更新に限ります。

## 変更前の公式資料確認

作業コピーのAGENTS.mdにある「OpenAI公式資料の確認」に従ってください。ファイルの最初の作成・編集前に、公式資料の検索・本文確認と根拠のチャット報告を済ませ、同じ根拠情報を担当Issueの「Symphony workpad」にも記録します。完了時も、参照資料・変更内容・実行した検証と未実行の確認を、チャットと同じworkpadに記録してください。

作業コピーに上記の節がない場合や、検索・本文閲覧ができず最新情報を確認できない場合は、ファイルを作成・編集しません。理由と未確認の範囲を報告し、workpad記録と状態変更は既存の「レビューへの引き渡し」に従ってください。Linearにもアクセスできない場合は、記録・状態変更が未実施であることをチャットに報告します。

## 作業の進め方

Issueの成果物と受入条件に沿って作業します。docs/requirements.mdの確定事項・仮案・未決事項を区別してください。要件整理、計画、実装はそれぞれの担当Issueの範囲で進めます。

成果物はこの案件別コピーに残してください。ローカルGitチェックポイントを作る場合は、依頼に関係する文書だけを対象にし、設定済みのGit名義を使います。名義がない場合はチェックポイントを作らず、差分と作業場所を進捗に残して構いません。

## レビューへの引き渡し

成果物と観測した確認結果、残る質問、作業コピーのパスを同じworkpadへ記録してから、Issueを`In Review`に移します。`In Review`は自動実行の対象外です。レビューで修正が必要な場合は、人が修正内容を記録してIssueを`Todo`へ戻します。`Done`への移動は人のレビューを経て行います。

実行に必要な認証・ツール・権限が不足した場合も、最後に通った段階と再開条件をworkpadに記録し、`In Review`へ移します。Linearにアクセスできない場合は、workpadの記録と状態変更のうち未実施の操作を報告します。
