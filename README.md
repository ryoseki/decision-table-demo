# 不正審査デモ

このデモで使用するシナリオや資料は架空です。

架空サービスの不正利用に関する事実確認を支援するWebアプリのデモです。無料枠・クレジットの不正利用と、アカウント・APIの不正利用を題材にしています。オペレータは各設問にYes／Noで回答します。資料が足りない場合やAPIの応答が得られない場合は確認待ちにします。

審査には、案件の主対象ユーザー（`review_cases.user_data`）と問い合わせ（`inquiry_data`）、設問単位で外部APIが返すYes／Noを使います。足りない情報を外部APIで補って表示する案もあります（設計案で、接続先や保存方法は未決です）。デモの外部APIは固定の規則で応答するモックを想定しています。管理者がAIチャットで設問や判定ルールの変更案を作成し、差分を確認する機能も検討します。

最終的な不正の認定と処置、実APIの仕様、管理者の運用は未決です。承認済みの設問、設計案、未決事項は[要件書](docs/requirements.md)に記載しています。

## 現在の段階

2026-11-07時点で、初版の要件と受入条件、主要な技術構成と初期化方針（技術計画）は合意済みで、アプリは未実装です。正確な版の固定、Composeの細目、保存設計、CI/CDなどは担当ごとに具体化します。主要構成はReact + Vite（TypeScript）、FastAPI、AWSのLambda、Aurora DSQL、ローカルのDocker ComposeとPostgreSQLです。詳細と未決事項は[要件書](docs/requirements.md)の「初版の技術構成」を参照してください。Symphonyの設定と起動スクリプトは用意しています。

準備時のローカル確認と、未確認の範囲は[確認記録](#確認記録)にまとめています。

## 文書とツールの役割

Linearで課題・受入条件・進捗を管理します。Symphonyは設定に合う課題を選び、案件別の作業コピーでCodexの実行を管理します。Codexの担当は、各課題の調査・計画・実装・検証です。業務上の未決事項やレビュー結果は、人が判断して次の作業へ反映します。

| 参照先 | 役割 |
| --- | --- |
| [README.md](README.md) | プロジェクトの目的、現在の段階、参加時の準備、成果確認の案内。 |
| [要件書](docs/requirements.md) | デモ要件の根拠。架空シナリオ、設問と回答条件、設計案、未決事項。 |
| [AGENTS.md](AGENTS.md) | エージェントが守る作業規約。担当範囲、未決事項の扱い、確認方法。 |
| [開発運用手順](docs/development-workflow.md) | 着手条件、成果の保存と取り込み、PR・CI・検証環境への配布の運用案と未決事項。 |
| [WORKFLOW.md](WORKFLOW.md) | Symphonyの実行対象・並列数・起動設定と、Codexへの作業・レビュー引き渡し指示。 |
| 利用者が選んだLinearプロジェクト | Issue番号・状態・受入条件・進捗・レビュー待ち事項の記録先。 |

要件整理では、要件案と質問を人のレビューへ提出します。続く実装計画、オペレータ向け試作、管理者向け試作は、前提がそろった課題から実行対象へ進めます。試作の具体的な範囲と順序は、合意した要件と計画で決めます。

## 準備

### Codexの実行環境をWSL 2に切り替える

このプロジェクトではWSL 2のUbuntuを標準の開発環境とします。デスクトップの設定で`Agent environment`を`Windows Subsystem for Linux`に切り替え、アプリを再起動します。再起動後はCodexに`uname -sr`を実行させ、Linuxで動いていることを確認してください。統合ターミナルのシェルは別の設定です。 [Windows版の公式手順](https://learn.chatgpt.com/docs/app/windows)

Ubuntuのターミナルで、クローンしたリポジトリのルートを開きます。Windows側でクローンした場合は、WSLから同じフォルダーを開きます。Windows側のパスはUbuntuの`wslpath -a`でWSLから見えるパスに変換できます。以降のコマンドは、特記がない限りこのルートで実行します。

Ubuntuに`bwrap`がない場合は、`sudo apt install bubblewrap`で導入します。 [OpenAI公式のサンドボックス準備手順](https://learn.chatgpt.com/docs/sandboxing#prerequisites)

### クローン先でローカル準備を確認する

Symphony v0.0.3とCodex 0.166.0のLinux x86_64版を使います。初回はPowerShell 7と`tar`を用意し、クローン先のルートで次を実行します。Windows側でクローンした場合はWindowsのPowerShell 7、Ubuntu側でクローンした場合はUbuntuにインストールしたPowerShell 7を使います。

```powershell
pwsh -File scripts/Install-SymphonyRuntime.ps1
```

インストーラーは公式配布物のSHA256を照合し、`.tools/`に展開します。Ubuntu側で`git`・`sed`・`sha256sum`とCodex CLIのログインを用意し、同じクローン先で次を実行します。

```bash
bash scripts/symphony.sh check
```

ログインが必要な場合は、Ubuntuで`chmod u+x .tools/codex/codex-x86_64-unknown-linux-musl`を実行し、同じバイナリに`login`を指定してログインします。その後に準備確認を再実行してください。準備確認では配布バイナリ、Codexのログイン参照とLinuxサンドボックス、SymphonyのCLI読み込みを確認します。Linearへの接続、Issueの実行、アプリの動作は別途確認します。

Windows側のクローンでは、PowerShell 7から`pwsh -File scripts/Start-Symphony.ps1 -Check`でも確認できます。

### Linearの課題とSymphonyの認証を準備する

Linearの操作にはプラグインを優先します。利用開始時に、接続先のチームと既存プロジェクトを確認してください。

1. 自分が利用できるLinearワークスペースで対象プロジェクトを選ぶ。必要ならプロジェクトを作成し、プロジェクトを開いてURLをコピーする。
2. Linearで実行候補のIssueを選び、成果物・受入条件・依存関係を確認する。
3. Symphony用のLinear Personal API keyを用意する。キーはチャットに貼り付けず、起動時の非表示入力に使う。

プラグインの認証と、ローカルSymphony用のAPI keyは別です。実際の状態名が`Todo`・`In Progress`・`In Review`と異なる場合は、起動前に[WORKFLOW.md](WORKFLOW.md)とチームの運用を合わせる必要があります。

起動には、元リポジトリにコミット済みのHEADが必要です。案件別コピーは、[WORKFLOW.md](WORKFLOW.md)の`git clone`で作ります。元フォルダーの未コミット変更やステージ済みの差分は引き継がれないため、実行対象にする版を確認してください。

コミットと認証の準備がそろったら、対象プロジェクトで実行条件を満たすほかのIssueがないことを確認します。最初の連携検証Issueだけに`symphony-pilot`ラベルを付け、`Todo`に移します。

## 起動・停止

Ubuntuのターミナルでクローン先のルートを開き、選んだLinearプロジェクトの`slugId`を指定します。コピーしたプロジェクトURLのパスが`/<workspace>/project/<name>-<slugId>/overview`の形式なら、`<slugId>`はプロジェクト名に続くハイフンの後ろにある12桁の16進文字列です。

```bash
SYMPHONY_LINEAR_PROJECT_SLUG='実際のslugId' bash scripts/symphony.sh start
```

`LINEAR_API_KEY`が環境変数にない場合は、ターミナルで非表示入力を求めます。キーは設定ファイルに保存しません。Codexは`.symphony/codex-home/`からWSL側の認証ファイルを参照します。既定は`~/.codex/auth.json`です。認証の更新時には元の認証ファイルも更新されます。

Windows側のクローンでは、`pwsh -File scripts/Start-Symphony.ps1 -ProjectUrl 'コピーしたLinearプロジェクトURL'`でも起動できます。このスクリプトは既定でWindows側のCodex認証を参照します。

起動スクリプトは、プロジェクトを指定した実行用設定を`.symphony/WORKFLOW.md`へ生成します。既定のダッシュボードは[http://localhost:4090](http://localhost:4090)です。対象プロジェクト内で`symphony-pilot`ラベルが付き、`Todo`または`In Progress`にあるIssueを、同時に1件ずつ実行します。

停止するときは、起動したターミナルでCtrl+Cを押してください。`max_turns: 5`は1回の実行内の連続ターン上限です。総実行回数の上限ではなく、Issueが実行対象の状態に残ると再実行されるため、初回はレビュー待ちへの状態変更まで確認します。

## E2E環境のスモークテスト

WSL 2のUbuntuで、クローン先のルートから次を実行します。必要なのはDocker Composeだけで、Node.jsやPythonをホストへ導入する必要はありません。

```bash
bash scripts/e2e.sh                  # 成功を期待する通常実行
bash scripts/e2e.sh --force-failure  # /health の期待値を変えて意図的に失敗させる
```

Composeが専用のプロジェクト名（`decision-table-demo-e2e`）でfrontend（Vite）とbackend（FastAPI）を起動し、healthcheckで待機してからPlaywright（`e2e/`）を実行します。終了後は、そのプロジェクトのコンテナとネットワークだけを停止・削除します。ホストのポートは公開しないため、通常の開発環境（`docker compose up`）を起動したままでも実行でき、その環境には影響しません。終了コードは成功が0、失敗が0以外です。起動や待機の失敗も0以外になります。失敗時は`e2e/playwright-report/index.html`のレポートと`e2e/test-results/`のトレースで理由を確認します。CIも同じ`bash scripts/e2e.sh`を呼びます。CIのワークフローとチェック名は別の課題で決めます。

対象は初期画面の表示と`GET /health`の応答だけです。業務E2Eのシナリオ・入力・期待結果は、初版要件の合意後に別に追加し、この環境のスモーク結果とは分けて記録します。

## CI（GitHub Actions）

`.github/workflows/ci.yml`が、PRの更新とmainへのpushで次の3ジョブを実行します。依存関係は新規チェックアウトから導入します。

| ジョブ | 内容 |
| --- | --- |
| `frontend` | `npm ci`、ESLint、ビルド（Node.js 26.13.0）。 |
| `backend` | ハッシュ検証付きの依存導入、Ruffのlintと整形チェック（Python 3.14.10）。整形は書き換えず合否だけを判定します。 |
| `e2e` | ローカルと同じ`bash scripts/e2e.sh`。 |

失敗時は、PRのチェックから該当ジョブのログを開きます。E2Eの失敗レポートとトレースは、実行結果のArtifacts（`e2e-report`、保持14日）から取得できます。再実行はActionsの画面から行い、初回の失敗記録は残します。`GITHUB_TOKEN`は読み取りだけで、アクションはコミットSHAで固定しています。

実際のActions上での実行は確認済みです。CI導入のPRでは`frontend`・`backend`・`e2e`の3ジョブが成功しました。失敗の確認には、意図的に`backend`と`e2e`を失敗させた検証用PRを使いました。両ジョブが失敗すること、失敗したジョブを再実行しても再び失敗することを確認しました。取得したArtifactsには、HTMLレポートとトレースが含まれていました。

取り込み前に必須とするチェックは、上の3ジョブ名（`frontend`・`backend`・`e2e`）を候補とします。必須とする対象の合意、ブランチ保護への登録、必須チェックとして実際にマージを止めることは、いずれも未完了です。GitHub側の権限確認と設定が必要で、人の判断と操作に残しています。PRのレビューとマージも、人の判断待ちです。

## 成果物とレビュー

| 場所 | 内容 |
| --- | --- |
| `.symphony/workspaces/` | Issueごとの作業コピー。変更した文書やコードなどの成果物。 |
| `.symphony/log/` | Symphonyの実行ログ。 |
| `.symphony/WORKFLOW.md` | 起動時に生成する実行用設定。編集元はルートの[WORKFLOW.md](WORKFLOW.md)。 |
| Linearの`Symphony workpad`コメント | 計画、変更ファイルの相対パス、実行した検証、残る質問。 |

レビューでは、Issue番号で案件別コピーを見つけ、workpadに記載された相対パスを使って該当ファイルと差分を開きます。Issueの受入条件に照らし、記録された確認結果と残る質問を確認してください。要件整理の課題では、確定事項と仮案の区別、判断が必要な項目を確認します。

Codexは着手時にIssueを`Todo`から`In Progress`へ移し、既存のworkpadがあれば更新します。成果と質問を同じworkpadに記録してから、Issueを`In Review`へ移します。`In Review`は自動実行の対象外です。修正が必要な場合は、レビュー担当者が修正内容を記録し、Issueを`Todo`へ戻します。

初回の連携検証では、ログまたはダッシュボードで対象Issueだけが起動したことを確認します。案件別コピーでCodexが確認したIssue番号、コミット、OpenAI公式資料を検索して本文を閲覧できたかをworkpadで確認します。`In Review`へ移った後は、次回以降のポーリングで再実行されないことを確認します。

案件別コピーの成果は、元の作業フォルダーへ自動反映されません。[開発運用手順](docs/development-workflow.md)では、Codexが案件別コピーから専用ブランチをpushしてGitHubにPRを作り、人がGitHub上で差分とCI結果を確認してマージする流れを提案しています。導入前の現行手順では、`Done`へ進める前に案件別コピーの成果を保存してください。

## Betterleaksでシークレットを検査する

Ubuntuのターミナルで、クローン先のルートから次のコマンドを実行します。初回は`curl`と`tar`を使い、Linux版v1.10.0のSHA256を照合して`.tools/betterleaks/`に配置します。 [公式配布ファイル](https://github.com/betterleaks/betterleaks/releases/expanded_assets/v1.10.0)

```bash
bash scripts/scan-secrets.sh
bash scripts/scan-secrets.sh staged
bash scripts/scan-secrets.sh history
```

既定の対象はGit管理下のファイルと、無視設定に該当しない未追跡ファイルです。`.tools/`と`.symphony/`は作業ツリーの検査対象から外します。`staged`はステージ済みの差分、`history`はGit履歴を検査します。

Go標準の正規表現エンジンを使い、検出した値は伏字にします。外部サービスへの有効性照会は無効です。終了コードは`0`が検出なし、`10`が検出ありです。`staged`と`history`はGitの検査用に一時ディレクトリを使います。Windowsから作業ツリーを検査する場合は`pwsh -NoProfile -File scripts/Scan-Secrets.ps1`も使えます。

## 確認記録

2026-11-05の準備時の記録では、配布ファイルのSHA256照合、WSL上のCodex起動とChatGPTログイン参照、SymphonyのCLI読み込みを確認しています。`pwsh -NoProfile -File scripts/Start-Symphony.ps1 -Check`は終了コード0でした。CLIの確認は、存在しないWorkflowに対するエラーを使った事前確認で、TrackerやAgentは起動していません。

同日のREADME見直しでは、Git差分、ローカル参照先、文書とスクリプトの説明の整合性を確認しました。準備・起動コマンドは再実行していません。Linearへの実接続、実Issueの実行、アプリの動作は未確認です。

2026-11-06には、Ubuntuで`bash scripts/symphony.sh check`を実行し、CodexのLinuxサンドボックスの起動まで確認しました。Betterleaksは、Linuxサンドボックス内で元の作業ツリーとステージ済み差分を検査し、検出は0件でした。元リポジトリの履歴には検査対象の内容がなかったため、履歴からの検出はダミーの値をコミットした一時リポジトリで確認しました。デスクトップのWSL設定での再起動、Linearへの実接続は未確認です。

## 参照した公式資料

- [Symphony v0.0.3の実装・設定](https://github.com/openai/symphony/tree/v0.0.3/elixir)
- [Symphonyの仕様](https://github.com/openai/symphony/blob/v0.0.3/SPEC.md)
- [CodexとLinearの公式連携](https://learn.chatgpt.com/docs/third-party/linear)
