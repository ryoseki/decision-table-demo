# Issueから検証環境までの開発運用

Codexが案件別コピーで専用ブランチを作り、変更・検証・PR作成まで進めます。人はGitHub上で差分とCI結果を確認し、マージと配布を判断します。業務要件の根拠は[要件書](requirements.md)です。履歴は書かず、現行の運用だけを記します。AWSの作成・配布は、利用者本人がAWS CloudShellで1段階ずつ実行します（この文書の作業ではAWSを操作しません）。

## 着手と担当

人のIssue準備担当が、成果物、範囲、受入条件、依存関係、必要な権限を確認します。不足していれば質問と推奨案をIssueに残し、実行対象にしません。

| 担当 | 責任 |
| --- | --- |
| Issue準備担当（人） | 前提・受入条件を整え、実行対象を選ぶ |
| Symphony | Issueごとに分離した作業コピーを用意し、Codexを実行する |
| Codex | 専用ブランチで変更・検証し、コミット、push、PR作成まで行う。結果と未確認事項をworkpadに残す |
| マージ承認者（人） | PRの差分と最新コミットのCI結果を確認し、マージを判断する |
| 配布承認者（人） | 配布対象のSHA、検証先、復旧手順を確認して承認する（唯一の人が起動し承認する運用。後述） |

## Symphonyの準備と起動

標準の開発環境はWindowsのWSL 2のUbuntuです。コマンドは、クローンのルートをUbuntuのシェルで開いて実行します。

**環境**
- デスクトップの設定で`Agent environment`を`Windows Subsystem for Linux`に切り替え、アプリを再起動する。再起動後に`uname -sr`でLinuxであることを確認する。統合ターミナルのシェルは別の設定。（[Windowsの公式ガイド](https://learn.chatgpt.com/docs/app/windows)）
- Windows側でクローンした場合は、WSLから同じフォルダーを開く（`wslpath -a`でパスを変換できる）。Ubuntuに`bwrap`がなければ`sudo apt install bubblewrap`で入れる。

**準備確認**（Symphony v0.0.3、Codex 0.166.0のLinux x86_64版）

```powershell
pwsh -File scripts/Install-SymphonyRuntime.ps1   # PowerShell 7と tar が必要。配布物のSHA256を検証して .tools/ へ展開する
```

```bash
bash scripts/symphony.sh check   # git、sed、sha256sum、Codex CLIのログインが必要
```

ログインが必要なら、`chmod u+x .tools/codex/codex-x86_64-unknown-linux-musl`のあと、同じバイナリに`login`を渡してログインし、準備確認をやり直す。この確認は、配布バイナリ、Codexのログイン参照とLinuxサンドボックス、SymphonyのCLIの読み込みだけを見る。Linearへの実接続、Issueの実行、アプリの動作は別に確認する。Windows側のクローンでは`pwsh -File scripts/Start-Symphony.ps1 -Check`でも確認できる。

**LinearとSymphonyの認証**
- 対象のLinearプロジェクトを選び、実行するIssueの成果物・受入条件・依存関係を確認する。Linear操作は、まずプラグインを使う。
- Symphony用のLinear Personal API keyを用意する。チャットに貼らず、起動時の非表示入力を使う。プラグインの認証と、ローカルのSymphonyのAPIキーは別。
- 状態名が`Todo`・`In Progress`・`In Review`と違う場合は、`WORKFLOW.md`とチームの運用をそろえてから始める。
- 起動には、元リポジトリのコミット済みのHEADが必要。案件別コピーは`git clone`で作るため、元のフォルダーの未コミットの変更・ステージ済みの差分は引き継がれない。
- 実行条件を満たす他のIssueがないことを確認し、最初の統合検証のIssueにだけ`symphony-pilot`を付けて`Todo`にする。

**起動と停止**

```bash
SYMPHONY_LINEAR_PROJECT_SLUG='<slugId>' bash scripts/symphony.sh start
```

- `<slugId>`は、プロジェクトURL（`/<workspace>/project/<name>-<slugId>/overview`）の、名前の後ろの12桁の16進数。`LINEAR_API_KEY`が環境にないときは、非表示入力を求める（設定ファイルには保存しない）。CodexはWSL側の認証ファイル（既定は`~/.codex/auth.json`）を`.symphony/codex-home/`から参照する。
- Windows側のクローンでは`pwsh -File scripts/Start-Symphony.ps1 -ProjectUrl '<Linearプロジェクトの URL>'`でも起動できる（Windows側のCodex認証を参照する）。
- 起動スクリプトが`.symphony/WORKFLOW.md`に実行設定を作る（元は、ルートの[WORKFLOW.md](../WORKFLOW.md)）。ダッシュボードは`http://localhost:4090`。`symphony-pilot`付きで`Todo`か`In Progress`のIssueが1件ずつ実行される。
- 停止はCtrl+C。`max_turns: 5`は1回の実行内の連続ターンの上限で、総実行回数の上限ではない（実行対象の状態のままのIssueは再実行される）。

**成果物の場所とレビュー**

| 場所 | 内容 |
| --- | --- |
| `.symphony/workspaces/` | Issueごとの案件別コピー（変更した文書・コード） |
| `.symphony/log/` | Symphonyの実行ログ |
| Linearの`Symphony workpad`コメント | 計画、変更ファイルの相対パス、実施した検証、残る質問 |

レビューでは、Issue番号で案件別コピーを探し、workpadの相対パスから差分を開き、検証結果と残る質問を受入条件と照合する。`In Review`は自動実行の対象外で、再実行されない。案件別コピーの結果は、元の作業フォルダーへ自動では反映されない（次節のとおり、専用ブランチをpushしてPRで取り込む）。

## 実行とレビュー待ち

1. 人が前提を確認したIssueを、`symphony-pilot`付きの`Todo`にする。起動・停止は上の「起動と停止」に従う。
2. Codexは既存のworkpadを読んで`In Progress`にし、案件別コピー内で専用の`codex/`ブランチを使う。必要な検証後にpushし、PRを作る。
3. 同じworkpadに、PRのURL、変更ファイルの相対パス、基点と成果のコミット、検証結果と未実施の確認を記録する。認証情報、絶対パス、端末固有の情報は書かない。
4. GitHubに保存されたことを確認して`In Review`へ移す。必要な認証・権限がなければ、最後に成功した段階と再開条件を残して`In Review`へ渡す。修正が必要なら、人が指摘を記録して`Todo`へ戻す。

再試行では、既存のブランチとPRを確認し、同じPRに修正を追加する。force pushはしない。`Done`は、GitHubへの保存、人によるマージ、残る受入条件を確認してから進める（終了状態の案件別コピーは削除対象）。

## PRと必須チェック

PRには、目的・変更点、対象Issueとworkpad、基点／成果／PRのSHA、確認コマンド・結果、未確認事項を書きます。内部リンクはPRとLinearに置き、公開文書には書きません。

`main`は`protect-main`と`require-ci-main`のルールセットで保護され、必須チェックはReact + ViteのESLintとビルド、FastAPIのRuff lintと整形チェック、E2Eのスモークテストです。人が最新のPRコミットで全チェックの成功と差分を確認してからマージします。未実行・スキップ・保留は成功とみなしません。

失敗時は、PRのチェックから該当ジョブのログを開く。E2Eの失敗レポートとトレースは、そのrunのArtifacts（`e2e-report`、14日保持）から取れる。再実行はActionsの画面から行い、初回の失敗の記録を残す。`GITHUB_TOKEN`は読み取り専用で、アクションはコミットSHAで固定している。

`.github/workflows/ci.yml`は、PRの更新と`main`へのpushで、新規チェックアウトから依存を入れて次の3ジョブを実行します。

| ジョブ | 内容 |
| --- | --- |
| `frontend` | `npm ci`、ESLint、ビルド（Node.js 26.13.0） |
| `backend` | ハッシュ検証つきの依存導入、Ruffのlintと整形チェック（書き換えず合否だけ。Python 3.14.10） |
| `e2e` | ローカルと同じ`bash scripts/e2e.sh` |

失敗時の戻し先: 変更が原因なら人が`Todo`へ戻して同じPRを直す。テスト基盤・CI設定の問題なら子課題に記録して依存するPRを保留する（失敗チェックの無効化で通過扱いにしない）。一時的な基盤障害なら原因と再実行理由をPRに記録する。

## 配布（CD）

構成は、CloudFront + WAF + Lambda関数URL（`AWS_IAM`）+ OAC（公開URLはCloudFrontのみ。[公開経路の計画](../infra/cd/cloudfront-plan.md)）、実行はLambda（ZIP、Python 3.14、x86_64、Lambda Web Adapter v1.1.0のレイヤー）、DBはAurora DSQLです。初回構築（IAM、関数、エイリアス`demo`、関数URL、ログ保持7日、OIDCロール）は、配布とは別の人が承認する操作で、手順は[初回構築の手順書](../infra/cd/bootstrap.md)にあります。CDはその既存のエイリアスを更新するだけです。

### 配布の流れ

1. **起動**: 人が`workflow_dispatch`で、`main`上の完全な40桁のSHAを指定する。ブランチ名・タグ・短縮SHAは不可。`main`以外からの起動は失敗する。同時に動かせる配布は1つ。
2. **事前確認（AWSの資格情報なし）**: オフラインテスト、Environment`demo`の保護設定（必須レビュー担当者が`CD_APPROVER_LOGIN`の1人、デプロイブランチは`main`のみ）、対象SHAが`origin/main`の祖先であること、同じSHAの`ci.yml`の`frontend`・`backend`・`e2e`が成功していること。
3. **ビルドと保管**: `scripts/build-lambda.sh`でリリースモードのZIPとマニフェストを作り、ZIPの実測ハッシュ、CIのrun、ソースSHAをサマリーに出す。**承認者はこのサマリーを見てから承認する。**
4. **承認と再確認**: 承認後、AWSの資格情報の前に、設定・承認記録・必須変数と、成果物のハッシュを再確認する（改ざんがあれば止まる）。その後OIDCで一時資格情報を得る。
5. **変更前の検証（失敗したら何も変更しない）**: ZIPとマニフェスト（`releaseEligible=true`・`dirty=false`）、関数の設定（Python 3.14・x86_64・ハンドラ`run.sh`・固定レイヤー）、関数URL（`AWS_IAM`・BUFFERED）、CloudFront（有効・WAF関連付け・単一オリジン・OAC・`CachingDisabled`と`AllViewerExceptHostHeader`・許可メソッド）、OAC、Web ACL（us-east-1・必須の接続元IPごとの規則1件、ほかは既知の観測グループ（`Count`）0〜3件だけ）、予約済み同時実行数がちょうど3、関数のリソースポリシー、現在の版のスモーク確認。すべて読み取りのみ。
6. **更新と版の発行**: RevisionIdを条件にコードを更新し、CodeSha256を確認して完了を待つ。設定を読み直して合格したときだけ、版を発行して`Active`を待ち、エイリアスの`RevisionId`を条件に切り替え、承認したSHAでスモーク確認を行う。エイリアスが既に今回と同じソースSHA・ZIPを指し、現行版のスモーク確認が合格したときは、変更なしの成功（`no-change-success`）として終える。
7. **失敗時の戻し**: 切り替え後のスモーク確認が失敗したら、こちらが切り替えたエイリアスだけを直前の版へ戻し、直前のSHAでスモーク確認を行う（全体は失敗として終了）。エイリアス更新の結果が確認できないときは自動では戻さず、`alias=unknown`と`recoveryNote`を残して手動復旧とする。版・成果物・DBは削除しない。
8. **記録**: 結果JSON（ソースSHA、ZIPのハッシュ、CIのrun、段階、スモーク、ロールバック、版、エイリアスの状態、`mutationStarted`）を、別の成果物として常に保存する。秘密・絶対パスは含めない。

成果物（GitHub Actions）は90日で消えますが、Lambdaの版のdescriptionのZIPハッシュで取り戻せます（[手順書](../infra/cd/bootstrap.md)）。現在の版と直前の版は、より新しい版のスモーク確認が合格するまで削除しません。

### 配布の設定

| 項目 | 内容 |
| --- | --- |
| Lambda | 512MiB、10秒、ログ7日、予約済み同時実行数3（プロビジョニング済み同時実行は使わない）。環境変数`AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`・`PORT=8000` |
| 公開停止 | 予約済み同時実行数を0にする（人が行い、再開は人の承認後）。0の間は関数URLが429を返し、CDは変更の前に止まる |
| 同時実行数3の意味 | 関数URLの上限は予約済み同時実行数の10倍のRPS（最大30RPS）で、超過は429。1から3へ上げたのは、コールドスタートで画面のHTML・JS・CSSの並行読み込みが429になったため。月額の上限ではない（継続的なアクセスによるLambda・ログの料金は残る）。IPごとのレート制限は分散したIPには効かない。詳細は[計画](../infra/cd/cloudfront-plan.md) |
| 権限の分離 | 配布用ロールは、対象関数のコード更新・版の発行・エイリアスの参照と更新・設定の読み取り、固定レイヤーの読み取り、公開経路の検証のための読み取りに限る。IAM・CloudFront・WAFの書き込み、`iam:PassRole`、管理者権限は含めない（`infra/cd/deploy-policy.json`、`infra/cd/oidc-trust-policy.json`）。予約済み同時実行数の変更と公開停止は、人が行う別の操作 |
| 自己承認 | 唯一の人が配布を起動し、同じ人が承認する。Environmentの自己承認禁止は意識して無効にしている（承認の独立性はない）。代わりに、承認前のサマリー、承認後の再確認、`main`以外の拒否で補う |
| 費用 | 月額0円は目標で保証ではない。月額の許容上限は最大1,000円と回答済みだが、請求データの反映に遅れがあるため、厳密な上限は作れない。予算アラート（300・500・800円）は任意の監視案で、通知であって上限ではない。自動停止は実装しない |

### ZIPのビルドとスモーク確認

WSLのシェルでリポジトリルートから実行します。AWSへは接続しません。

```bash
# ビルド（クリーンなHEADだけ。dist/cd はGit無視）
bash scripts/build-lambda.sh <40桁のSHA> dist/cd/<40桁のSHA>
#   ローカル検証専用（releaseEligible=false。配布には使わない）
bash scripts/build-lambda.sh --allow-dirty <40桁のSHA> dist/cd/local

# 外部のURLに対するスモーク確認（httpsと releaseEligible=true が必須）
CD_BASE_URL=https://<公開URL> CD_EXPECTED_SHA=<40桁のSHA> bash scripts/cd-smoke.sh

# 配布ヘルパーと事前確認のオフラインテスト（偽のAWS・偽のGitHub APIを使う。-B を必ず付ける）
python3 -B -I -m unittest discover -s tests/cd
```

- オフラインテストは配布ロジックの確認で、実際のAWS・Lambda・GitHubの動作の検証ではない。CIの`cd-offline`ジョブが、固定したPython 3.14イメージで、通信なし・資格情報なしで同じテストを実行する。
- `manifest.json`にはZIPのSHA-256、ソースSHA、`dirty`、`releaseEligible`、ランタイム、アダプタの固定情報が入る。`releaseEligible`は、クリーンな作業ツリーでHEADが指定SHAと一致したことだけを示し、人の承認ではない。
- スモーク確認は、画面のHTTP 200・見出し・バンドルの読み込み、`/health`の完全一致、`/release`のSHAと配布可否を見る。`CD_BASE_URL`はオリジンのみで、httpは`CD_ALLOW_LOCAL=true`（ローカル専用。CDでは使えない）のときだけ許す。レポートは`e2e/cd/`配下（Git無視）に出る。`CD_REPORT_ID`で実行ごとに分けられる。
- ローカルでZIPを起動して確認するときは、ZIPを空の専用ディレクトリへ`python3 -I -m zipfile -e`で展開し、Python 3.14のコンテナで`sh run.sh`を起動する。`--tmpfs`を使うなら`exec`を付ける（付けないとネイティブ共有ライブラリを読み込めない）。
- FastAPIは、環境変数`STATIC_DIR`と`RELEASE_FILE`が設定されたときだけ、ビルド済み画面と`/release`を配信する。

## 公開時の書き込みと利用枠

公開する書き込みは、案件の受付POST、判定保存PUT、設問・判定ルールの提案POSTと決定POST、共有サンプル案件の作成POST（`POST /api/sample-cases`）です。CloudFrontで7メソッドを許可したときも、アプリはこの組み合わせだけを受け付けます（書き込みガード。拒否は405）。受付が同じインスタンスの内部HTTPモックを呼ぶときは、インスタンス内で生成したトークン（`X-Internal-Mock-Token`。エッジが利用者の送った同名のヘッダーを捨てる）で確認します。専用の接続元ヘッダー（`X-Demo-Viewer-Ip`）がある要求は、トークンが付いていても内部の呼び出しとして扱いません。

Bedrockを呼ぶ公開構成では、Aurora DSQLで、IP別5回と全体25回の枠を、依頼IDと同じトランザクションで確保します（日本時間の午前0時が日付の境界）。

- 接続元は、エッジ（CloudFront Functions、viewer-request）が`event.viewer.ip`から付ける専用ヘッダー`X-Demo-Viewer-Ip`（ポートなしのIP1つ）だけを使い、アプリは正規化して、DBには鍵付きHMAC（`AI_QUOTA_HMAC_KEY`、32バイト以上）を保存する。他のヘッダーへのフォールバックはない。ヘッダーがない・複数ある・IPの形でない要求は、枠の予約もBedrockの呼び出しもせず400で拒否する。IPv4射影のIPv6はIPv4として、IPv6は/64を同じ枠にまとめる。
- 日別カウンターは7日を過ぎたものを削除する。依頼IDは追記のみで保持し、再送は409で返す。予約が確定した後に推論を1回試み、応答が不明でも枠を返さない。承認やモック応答は枠を使わない。依頼文・生IP・生成出力は記録しない。

接続元IPの渡し方は、AWSのManagedポリシー`AllViewerExceptHostHeader`のまま、CloudFront Functionsが専用ヘッダーを書く案を採用しています（プロジェクト独自の判断）。利用者が送った同名のヘッダー（複数値を含む）と`x-internal-mock-token`は捨てます。接続元IPが取れないときは、エッジが400で止めます。専用のオリジンリクエストポリシー、関数URLの直接公開、アプリ側でのX-Forwarded-Forの解釈は採りません。CloudFront Functionsの費用・Freeプランでの利用可否は未確認です（確認できなければ書き込みを許可しません）。

### CDの検証（変更の前）

- 3メソッド（読み取り）: Managedポリシー、エッジ関数の関連付けなし。
- 7メソッド（書き込み）: `viewer-request`の関連付けがちょうど1件で、承認した関数（リポジトリ変数`CLOUDFRONT_VIEWER_FUNCTION_ARN`）と一致し、LIVEのコードのSHA-256が、ZIPの中のレビュー済みソースと一致すること。不一致や取得失敗は、AWSの変更の前に失敗で止まる。今回のZIPと戻し先の版の両方が、`guard=1`と`viewerip=1`の印（`manifest.json`の`capabilities`と版のdescription）を持つこと。
- 配布用ロールに足す権限は、承認した関数の`cloudfront:GetFunction`（読み取り）だけ。`UpdateFunction`・`PublishFunction`・`UpdateDistribution`は含めない。

### 共有サンプル案件の作成（`POST /api/sample-cases`）

動作と対象範囲は[要件書](requirements.md#共有サンプル案件の作成)、操作IDと案件IDの保存方式は[DBとAPIの設計](decision-support-schema.md)を参照します。画面は`Idempotency-Key`に操作IDのUUIDを付けます。

| ステータス | 条件 |
| --- | --- |
| 201 | 新しい案件を作成した |
| 200 | 同じ操作IDで作成済みの案件を返した |
| 422 | 操作IDの欠落・重複・形式違反 |
| 503 | DB未設定、保存時の混雑、採番の再試行上限 |

- ローカルは`db/init_dev.py`が既存データを保持して不足テーブルを追加する（既存テーブルの定義や初期データの更新は適用しない）。公開環境は、`sample_case_operations`のDDLと、アプリロールへの同表のSELECT・INSERT権限（`db/init_dsql.py`の対象テーブル）を適用してから配布する。AWS側の操作は利用者が行う。
- 既存のCloudFront・WAF・OAC・エッジ関数と予約済み同時実行数3を維持し、`Idempotency-Key`を既存のヘッダー転送で渡す。書き込みガードには固定経路`POST /api/sample-cases`を許可する。
- この経路専用のレート制限と自動削除はない（要否は未決）。保存量と処理費用は新しい操作の回数に応じて増える。AIを呼ばないため、AIの利用枠は消費しない。
- 確認状況: API契約・部品・E2E・ローカルPostgreSQLのDB統合テストで確認済み。公開環境と実Aurora DSQLでの追加DDL・権限・ON CONFLICT・OCC・費用は未確認。公開前に、実際の公開URL（CloudFront・OAC・エッジを通る経路）で、次を少数の操作で確認する。
  - 新しい操作IDのPOSTは201（`created: true`）、同じ操作IDの再送は200（`created: false`）で、同じ案件IDを返す。件数と保存済みの判定は変わらない。
  - `Idempotency-Key`と`x-amz-content-sha256`が付与されてオリジンへ転送される。別ブラウザーでも共有され、許可していないメソッドは拒否される。
  - WAFのレート制限の超過拒否は少数の操作では検証できない（設定値の確認と分ける。後者は未確認）。

### 運営者の手動切り替え（書き込み構成の公開）

AWSの作成・変更は、利用者本人が内容を確認し、1ブロックずつ実行します（実在のIDは出力・チャットに貼らない）。CloudFrontは`us-east-1`、Lambdaは`ap-northeast-1`です。

1. 読み取り構成のまま、Aurora DSQLに追加テーブルのDDL（共有設定・提案・利用枠・依頼ID・`sample_case_operations`）を1文ずつ適用し、アプリロールの権限を反映する。既存の案件・判定・ルールは保持する。
2. **第1段階（読み取り構成のまま、候補を配布する）**: Lambdaに`AI_PROVIDER=bedrock`、モデルとリージョン、`DSQL_ENDPOINT`とDB接続設定、シークレット`AI_QUOTA_HMAC_KEY`を設定する。実行ロールのBedrock権限は対象モデルの呼び出しに絞る。CDで配布し、エイリアスの版のdescriptionが`…;guard=1;viewerip=1`で終わることを読み取りで確認する。
3. **第2段階の準備（まだ公開しない）**: ZIPの中の`edge/viewer-ip.js`を新しい空のディレクトリへ取り出し、`manifest.json`の`edge.sha256Hex`と一致することを確認する。関数をDEVELOPMENTに作り（`aws cloudfront create-function`）、取り出したコードがバイト単位で一致することを確認する。`aws cloudfront test-function`で、偽の重複ヘッダー・偽のトークンを含む架空のイベントに対し、`x-demo-viewer-ip`が実IPの1件だけ（`multiValue`なし）で、`x-internal-mock-token`がないことを確認する。IPなしの入力では、関数が`statusCode` 400を返すことも確認する。外れたら公開しない。
4. **第2段階（公開と、関連付け・7メソッドを同時に反映）**: `publish-function`でLIVEにし、LIVEのコード（`get-function --stage LIVE`）が手順3のファイルと一致することを確認する。一致しなければ中止する。続けて、ディストリビューションの1回の更新で、`viewer-request`の関連付け1件と許可メソッド7種を設定する。`CachedMethods`と、Managedのオリジンリクエストポリシーは変えない。`Deployed`まで待ち、設定を読み戻して確認する。リポジトリ変数`CLOUDFRONT_VIEWER_FUNCTION_ARN`にエッジ関数のARNを設定する（未設定ではCDが止まる）。

   ```bash
   aws cloudfront get-distribution-config --id "$DIST_ID" --region us-east-1 > "$WORK/dist.json"
   ETAG="$(jq -r .ETag "$WORK/dist.json")"
   jq --arg arn "$EDGE_ARN" '.DistributionConfig
     | .DefaultCacheBehavior.AllowedMethods.Quantity = 7
     | .DefaultCacheBehavior.AllowedMethods.Items = ["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"]
     | .DefaultCacheBehavior.FunctionAssociations = {Quantity:1, Items:[{FunctionARN:$arn, EventType:"viewer-request"}]}' \
     "$WORK/dist.json" > "$WORK/dist-write.json"
   aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" \
     --distribution-config "file://$WORK/dist-write.json" --region us-east-1
   aws cloudfront wait distribution-deployed --id "$DIST_ID" --region us-east-1
   ```
5. `Deployed`の後、**確認できるまで利用者へ公開しない**。
   - 架空の案件の受付（201）・判定保存・開き直し、設問またはルールの提案とYes、別ブラウザーでの共有一覧。登録済みの再保存と、公開側からの内部モックPOST（`POST /mock/v1/auto-answers/A09`）が拒否されること（トークンなし・偽のトークンありのどちらも405）。依頼IDを固定した再送が409で、枠が1つだけ消費されること。
   - **偽の接続元ヘッダーの確認（必須）**: 自分の実際の接続元の枠を、検証日（日本時間）の上限（5）まで事前に埋め、全体の枠には空きを残す。偽のIPを付けた書き込み（偽の単一ヘッダー、偽の重複ヘッダー、偽のIPと偽のトークン）が、**429で、本文が接続元ごとの枠（「この接続元からの」）を示す**ことを確認する。201・400・403や全体の枠の429は失敗として扱い、直ちに手順6で戻す。429のあと、枠が増えていないこともDSQLの読み取りで確認する。接続元の枠のHMACは、秘密の鍵で計算する（鍵と計算結果は書き残さない）。
   - Lambda関数URLへの未署名の直接アクセスが403のままであること。
6. **戻し**: 問題があれば最初にCloudFrontを読み取り構成へ戻す。1回の更新で、許可メソッドを3種（GET・HEAD・OPTIONS）へ戻し、関連付けを外す（`FunctionAssociations = {Quantity:0}`）。`Deployed`の後に、`/`・`/health`・`/release`が200で、POSTが403か405になることを確認する（伝播が終わるまでは古い構成のエッジが残り得る）。この確認の後にだけ、必要ならLambdaの版を戻す。保存済みの共有データは変わらない。設定データを戻すときは、権限のある作業環境で`SharedConfigDb.restore(target_revision)`で過去版を新しい共有設定として追加する（過去版は変えない）。登録済みの判定は読み取り専用で、復元でも更新しない。
7. Lambdaの版を戻すとき、書き込み構成のままでは、`guard=1`と`viewerip=1`の印のない版へは戻せない。印のない古い版へ戻すときは、先に手順6でCloudFrontを読み取り構成へ戻してから、エイリアスを戻す（逆にすると、古い版が偽の接続元を信じ得る）。

### LIVEの公開とアクセスの限界

- エッジ関数はLIVEだけがディストリビューションに関連付けられる。`publish-function`は、その関数を使う全ディストリビューションの動作を即時に変えるため、公開の前に、DEVELOPMENTでのテストとソースの照合を終える。
- CDが確認できるのは、配布の実行時点のLIVEのコードだけ。公開後に別の人がLIVEを更新しても、次のCDまで検出しない。配布用ロールに`UpdateFunction`・`PublishFunction`を与えない。
- 実AWSでの動作（CloudFront Functionsの実行、`event.viewer.ip`の値、関連付けの反映、7メソッドの更新、費用・Freeプランでの利用可否）は確認していない。ローカルで確認したのは、エッジ関数のNodeでの実行、アプリの検証、偽のAWSによるCDの検査の論理だけ。
- 本文ハッシュは、ブラウザでシリアライズした書き込み本文のUTF-8バイト列から計算する。ローカル開発で共有設定をPostgreSQLに保存する場合は、初期化済みのDBに対して`SHARED_CONFIG_DB=1`を指定する。ローカル開発・E2Eではエッジがないため、テストが専用ヘッダーを直接付ける。

## 一連の流れの検証（未実施）

人が小さな変更1件と期待結果を合意したうえで、そのIssueから、workpad、専用ブランチと成果SHA、PR、CI、人がマージしたSHA、配布承認、配布結果、検証URLとスモーク結果まで順にたどる検証は、まだ実施していません。元の作業ツリーのブランチや未コミットの変更がそのままであることも確認します。

## 参照した公式資料

プロジェクト独自の判断が多く、AWSの料金・仕様とGitHubの設定は変わり得るため、実行前に再確認します。[Symphony v0.0.3のSPEC](https://github.com/openai/symphony/blob/v0.0.3/SPEC.md)、[Lambda関数URLの設定](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)、[GitHub ActionsのOIDC](https://docs.github.com/en/actions/reference/security/oidc)、[GitHubのデプロイと環境](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)、[Lambdaのバージョン](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html)、[UpdateFunctionCode](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateFunctionCode.html)、[PublishVersion](https://docs.aws.amazon.com/lambda/latest/api/API_PublishVersion.html)、[UpdateAlias](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateAlias.html)、[Lambda Web Adapter（固定コミット）](https://github.com/awslabs/aws-lambda-web-adapter/tree/596bdbcea78a89084fd0b522807d04cd2f1f3f7e)、[CloudFront Functionsのイベント構造](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html)、[Lambda関数URLへのOAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html)、[AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)。
