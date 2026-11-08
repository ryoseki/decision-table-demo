# 初回構築と運用手順（人が確認して実行する。未実行）

このファイルのコマンドは**一度も実行していません**。AWSでのリソース作成・設定・配布、GitHubの設定は、まだ許可されていません。実行前に人が内容を読み、承認してから行います。アカウントIDなどは `<…>` のプレースホルダーで、実在の値は書きません。

## 位置づけ

| 区分 | 内容 |
| --- | --- |
| 合意済みの設計 | 東京（ap-northeast-1）、既存のAWSアカウント、DBなし、公開は架空データのみ、常時公開を目指す。配布は人が起動し、同じ1人が承認する（Environmentの自己承認禁止は意図して無効）。費用アラート300・500・800円、500円で人が手動で公開停止。1,000円の厳密な上限は請求データの反映遅れのため作れない |
| 仮定 | 古いアカウントのため従量課金・無料利用枠の旧来アカウントと仮定する。実際の種別・無料枠は未確認 |
| GitHub側の設定（Codex設定、2026-11-08） | Environment `demo`（必須レビュー担当者1人、自己承認の禁止は無効、デプロイブランチは `main`（type=branch）のみ）とリポジトリ変数 `CD_APPROVER_LOGIN` を設定済み。管理者による迂回の無効化は要求したが、APIの応答にそのフィールドがなく**未確認**（無効と確認したわけではない）。AWS関連の変数（`AWS_ROLE_ARN`・`LAMBDA_FUNCTION_NAME`・`CD_BASE_URL`・`LAMBDA_LAYER_CODE_SHA256`）は**未設定** |
| 未実施・未確認 | 以下のすべてのAWSコマンド、AWS認証・実際のAWS操作、アダプタのレイヤーとPython 3.14の互換性、AWS上のスモーク確認、実際のCD（配布・ロールバック）の実行（動作は未検証）、予算アラートの宛先（本人が非公開で入力する。作り話の宛先は置かない） |
| 実行前に必要な承認（未取得） | AWSリソースの作成・課金を伴う操作の許可、支払い方法の状態確認。これらは内部の確認事項で、公開の主張ではない |

CDワークフロー（`.github/workflows/cd.yml`）は、**ここで作る既存の正常なエイリアス `demo` を更新するだけ**です。IAM・関数URL・同時実行数・ログの操作は含みません。

## 1. 準備

```bash
export AWS_REGION=ap-northeast-1
export ACCOUNT_ID=<ACCOUNT_ID>             # 対象アカウントを人が確認する
export FN=<FUNCTION_NAME>                  # 例: 自分で決めた関数名
export EXEC_ROLE=<FUNCTION_NAME>-exec
export LAYER_ARN=arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28
export SHA=<初回に承認する40桁のmainのSHA>    # クリーンなソースとCI成功・人の承認が必要
```

初回のZIPは、承認したSHAをクリーンにチェックアウトして作ります（`--allow-dirty` のZIPは使わない）。以前のローカルZIPのソースSHAのラベルは過去の検証用で、承認されたものではありません。

```bash
bash scripts/build-lambda.sh "$SHA" "dist/cd/$SHA"
jq '{sourceSHA, releaseEligible, dirty, bytes: .zip.bytes, hex: .zip.sha256Hex, code: .zip.codeSha256}' "dist/cd/$SHA/manifest.json"
```

`releaseEligible` が `true`、`dirty` が `false`、`bytes` が50MB（52,428,800）未満（直接アップロードできる）であることを確認します。

```bash
export ZIP="dist/cd/$SHA/lambda-$SHA.zip"
export ZIP_HEX="$(jq -r .zip.sha256Hex "dist/cd/$SHA/manifest.json")"
export ZIP_CODE="$(jq -r .zip.codeSha256 "dist/cd/$SHA/manifest.json")"
[ "$(sha256sum "$ZIP" | cut -d' ' -f1)" = "$ZIP_HEX" ] && echo OK
```

## 2. 実行ロールとログ（7日保持）

ログのグループを先に作り、実行ロールには作成権限を与えず、そのグループへの書き込みだけを許します。

```bash
aws logs create-log-group --log-group-name "/aws/lambda/$FN" --region "$AWS_REGION"
aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" --retention-in-days 7 --region "$AWS_REGION"

work="$(mktemp -d)"   # リポジトリの外の、自分専用の一時ディレクトリ
cat > "$work/exec-trust.json" <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
aws iam create-role --role-name "$EXEC_ROLE" --assume-role-policy-document "file://$work/exec-trust.json"

cat > "$work/exec-logs.json" <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:$AWS_REGION:$ACCOUNT_ID:log-group:/aws/lambda/$FN:*"}]}
JSON
aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name logs-only --policy-document "file://$work/exec-logs.json"
```

## 3. 関数（最初は公開しない）

```bash
aws lambda create-function --function-name "$FN" \
  --runtime python3.14 --architectures x86_64 --handler run.sh \
  --role "arn:aws:iam::$ACCOUNT_ID:role/$EXEC_ROLE" \
  --zip-file "fileb://$ZIP" \
  --memory-size 512 --timeout 10 \
  --layers "$LAYER_ARN" \
  --environment 'Variables={AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap,PORT=8000}' \
  --region "$AWS_REGION"
aws lambda wait function-active-v2 --function-name "$FN" --region "$AWS_REGION"

# 公開の許可を作る前に、予約済み同時実行数を0にする（0の間は呼び出せない）
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 0 --region "$AWS_REGION"
```

プロビジョニング済み同時実行、DB、NAT、ECR、S3、CloudFrontは作りません。ロールの作成直後は反映待ちで `create-function` が失敗することがあるため、その場合は少し待って再実行します。アカウントの同時実行数の上限が低く予約できない場合は、そこで止めて人が判断します。

### レイヤーのハッシュを記録する

```bash
aws lambda get-layer-version-by-arn --arn "$LAYER_ARN" --region "$AWS_REGION" --query Content.CodeSha256 --output text
```

この値が、配布時の期待値（リポジトリ変数 `LAMBDA_LAYER_CODE_SHA256`）になります。独立した情報源との照合ではなく、この時点の実体を記録する（最初の観測を信頼する）ものです。以後、レイヤーの実体が変わると配布は止まります。

### 最初の版とエイリアス

版番号は1とは限らない（作成の再試行などで変わる）ため、固定せず、発行の応答から取得して使います。

```bash
actual="$(aws lambda get-function-configuration --function-name "$FN" --region "$AWS_REGION" --query CodeSha256 --output text)"
if [ "$actual" = "$ZIP_CODE" ]; then echo "コードのハッシュ: 一致"; else echo "コードのハッシュが不一致。ここで止める" >&2; false; fi

FIRST_VERSION="$(aws lambda publish-version --function-name "$FN" --code-sha256 "$ZIP_CODE" \
  --description "source=$SHA;zip=$ZIP_HEX;run=0" --region "$AWS_REGION" --query Version --output text)"
case "$FIRST_VERSION" in ''|*[!0-9]*|0*) echo "版番号を取得できない: 止める" >&2; false;; esac
echo "最初の版: $FIRST_VERSION"
aws lambda create-alias --function-name "$FN" --name demo --function-version "$FIRST_VERSION" --region "$AWS_REGION"
```

description の形式は `source=<40桁のSHA>;zip=<64桁のSHA-256>;run=<数字>` で、配布ヘルパーが次回、直前の正常版の出所として読みます（初回は `run=0`）。

#### 発行済みの版が既にある場合（配布の再実行）

Lambdaは、コードも設定も最後の版から変わらないと新しい版を作らず、既存の版を返します（公式: PublishVersion API）。配布ヘルパーは次のとおり扱います。実際のAWSでは未確認です。

- エイリアスが既に今回と同じソースSHA・ZIPの版を指し、その版の現行スモーク確認が合格した場合: **変更なしの成功**（`overall=no-change-success`、`deployment=already-current`、新しい版は発行せず、新しい配布のスモーク確認もしない）。新しい配布の成功とは扱いません。
- 前回の試行が版を発行した後に止まり、エイリアスが旧版のまま残った場合: 再実行で返された既存の版を、**descriptionのソースSHA・ZIPのハッシュ、コードのハッシュ、検査対象の設定がすべて今回と一致すると証明できたときだけ**再利用します（`versions.reused=true`）。証明できなければ、エイリアスを動かさず止まります。設定を変えて版を作らせることはしません。
- 発行直後（または再利用）の版は、`aws lambda wait function-active-v2 --qualifier <版>`（AWS CLIの上限つき）で Active を待ってから確認し、その後にエイリアスを切り替えます。待機の失敗・タイムアウトはエイリアスを動かさず止まります（`stage=wait-published`）。Pending のまま確認する緩和はしません。更新後のRevisionId・CodeSha256・設定の確認も緩めておらず、更新後のRevisionIdの扱いは実際のAWSでの確認項目です（未確認）。
- 止まった場合の手動確認（読み取りのみ）: `aws lambda list-versions-by-function --function-name "$FN" --region "$AWS_REGION"` で版を見つけ、`get-function-configuration --qualifier <版>` の `Description`・`CodeSha256`・Layers・Runtime などが今回の想定と一致するか確認する。一致すると人が判断した場合に限り、`update-alias`（現在の `RevisionId` 指定）で切り替える。一致しない版は使わず、削除もしない。

### 関数URLと公開の許可

```bash
aws lambda create-function-url-config --function-name "$FN" --qualifier demo \
  --auth-type NONE --invoke-mode BUFFERED --region "$AWS_REGION"
aws lambda add-permission --function-name "$FN" --qualifier demo \
  --statement-id FunctionURLAllowPublicAccess --action lambda:InvokeFunctionUrl \
  --principal '*' --function-url-auth-type NONE --region "$AWS_REGION"
aws lambda add-permission --function-name "$FN" --qualifier demo \
  --statement-id FunctionURLAllowPublicInvoke --action lambda:InvokeFunction \
  --principal '*' --invoked-via-function-url --region "$AWS_REGION"
```

`InvokeFunctionUrl` と `InvokeFunction` の両方が要る現行の関数URLの仕様に基づく案です。`--invoked-via-function-url` が使えるかは、お使いのAWS CLIの `aws lambda add-permission help` で確認してください（未確認）。出力された `FunctionUrl` の末尾の `/` を除いたオリジンが、リポジトリ変数 `CD_BASE_URL` です。

## 4. 初回のスモーク確認と公開の開始

予約済み同時実行数を1にすると、公開URLが有効になります。**承認した初回デモを公開してよいと人が判断したときだけ**行います。この確認の間は公開されます（0のままではスモーク確認ができません）。

```bash
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 1 --region "$AWS_REGION"
CD_BASE_URL=<上のオリジン> CD_EXPECTED_SHA="$SHA" CD_REPORT_ID="bootstrap-$SHA" bash scripts/cd-smoke.sh
```

失敗したら**直ちに**手動停止します（初回は戻す先の正常版がない）。

```bash
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 0 --region "$AWS_REGION"
```

成功を確認するまで、エイリアスを正常とは扱いません。再開は原因を修正し、人が承認してからです。

## 5. 配布用のOIDCロール（IAM管理者の別操作）

GitHubのOIDCプロバイダーが既にあれば再利用します。

```bash
aws iam list-open-id-connect-providers   # token.actions.githubusercontent.com があるか確認
# なければ作成（--thumbprint-list の要否は `aws iam create-open-id-connect-provider help` で確認）
aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com
```

信頼ポリシー（`infra/cd/oidc-trust-policy.json`）は `aud` が `sts.amazonaws.com`、`sub` が `repo:<OWNER>/<REPO>:environment:demo` の完全一致だけです（ワイルドカードなし）。権限（`infra/cd/deploy-policy.json`）は、対象関数（修飾子なし）とその版・エイリアス（修飾子つき）に限った、設定・関数の参照（`GetFunction` は更新完了の待機 `aws lambda wait function-updated-v2` と、発行した版の有効化の待機 `aws lambda wait function-active-v2 --qualifier <版>` が使う。追加の権限は不要）、コード更新、版の発行、エイリアスの参照と更新と、固定のレイヤー版の読み取りだけです。`iam:PassRole`、管理者権限、実行時の権限、関数URL・同時実行数・ログ・IAMの操作は含めません。

```bash
work="$(mktemp -d)"
export REPO_SLUG=<OWNER>/<REPO>   # 対象のGitHubリポジトリ（人が入力）
sed -e "s|<ACCOUNT_ID>|$ACCOUNT_ID|g" -e "s|<OWNER>/<REPO>|$REPO_SLUG|g" infra/cd/oidc-trust-policy.json > "$work/trust.json"
sed -e "s|<ACCOUNT_ID>|$ACCOUNT_ID|g" -e "s|<FUNCTION_NAME>|$FN|g" infra/cd/deploy-policy.json > "$work/policy.json"
# 置換後の内容（subが repo:<実際のリポジトリ>:environment:demo の完全一致になっているか）を目で確認する
cat "$work/trust.json" "$work/policy.json"
aws iam create-role --role-name <FUNCTION_NAME>-deploy --assume-role-policy-document "file://$work/trust.json" --max-session-duration 3600
aws iam put-role-policy --role-name <FUNCTION_NAME>-deploy --policy-name deploy-demo-only --policy-document "file://$work/policy.json"
```

ロールのARNがリポジトリ変数 `AWS_ROLE_ARN` です。固定レイヤーの読み取りが実際のAWSで必要か・足りるかは未確認です（`AccessDenied` なら、固定ARNの読み取りだけを足りない操作に限って見直す）。

## 6. GitHubの設定（Environmentと `CD_APPROVER_LOGIN` は設定済み。AWS関連の変数は未設定）

リポジトリ変数（Settings → Secrets and variables → Actions → Variables）。いずれも秘密ではありません。人名は公開文書に書かず、ここへだけ入力します。

| 名前 | 値 | 状態 |
| --- | --- | --- |
| `CD_APPROVER_LOGIN` | 起動・承認する唯一の人のGitHubログイン。**リポジトリ変数**にする（buildジョブも参照する） | 設定済み |
| `AWS_ROLE_ARN` | 上のデプロイ用ロールのARN | 未設定（AWSの作成後） |
| `LAMBDA_FUNCTION_NAME` | `$FN` | 未設定 |
| `CD_BASE_URL` | 関数URLのオリジン（`https://<id>.lambda-url.ap-northeast-1.on.aws`、末尾の`/`なし） | 未設定 |
| `LAMBDA_LAYER_CODE_SHA256` | 上で記録したレイヤーのCodeSha256（base64） | 未設定 |

Environment `demo`（名前は運用上の選択）。設定済みですが、CDは自動作成に頼らず、既存のEnvironmentが次の設定であることを確認できないと、AWSの資格情報の前に止まります。

- 必須レビュー担当者: ちょうど1人のユーザー（`CD_APPROVER_LOGIN` と一致）
- 「レビュー担当者による自己承認の禁止」: **意図して無効**（唯一の人が起動し承認するため。合意済みで、承認の独立性は確保されない）
- 管理者による保護の迂回の許可: 無効にする設定を要求したが、APIの応答にフィールドがなく無効とは**確認できていない**（事前確認は「未確認」の注意を出す）
- デプロイブランチ: 「選択したブランチ」のカスタムポリシーで `main`（ブランチ）の1件だけ。ワイルドカード・タグは不可

設定は既に行われています。再現・修正が必要な場合の `gh` の案（`<USER_ID>` は承認者のGitHubの数値ID。`PUT` は既存のEnvironmentの設定を置き換えます。**現在の設定には実行不要**）:

```bash
gh api -X PUT repos/<OWNER>/<REPO>/environments/demo --input - <<'JSON'
{"reviewers":[{"type":"User","id":<USER_ID>}],"prevent_self_review":false,"can_admins_bypass":false,
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON
gh api -X POST repos/<OWNER>/<REPO>/environments/demo/deployment-branch-policies -f name=main -f type=branch
```

## 7. 配布（CD）の流れと、止まったときの確認

Actionsの「CD」を `main` から手動で起動し、`source_sha` に承認する完全なSHAを入れます。buildジョブのサマリー（CIのrun、ソースSHA、実測したZIPのSHA-256、成果物）を確認してから、Environment `demo` を承認します。結果は `cd-result-<run>-<attempt>` の `cd-result/result.json` とジョブのサマリーに残ります。

結果JSONの見方: `deployment`（配布処理）・`smoke`（新しい版）・`rollback` は別々に記録されます。`overall` が `success`（新しい版を配布し、スモーク確認に合格）または `no-change-success`（既に同じソース・ZIPの版が現行で、変更も新しい版の発行もしていない。新しい配布の成功ではない）以外なら失敗です。ロールバックが成功しても全体は失敗として終了します。`mutationStarted` が `true` なら、AWSの変更操作を始めており（成功を意味しない）、結果ファイルが無い場合もAWSの操作の有無は**未確認**で、人が確認します。`alias.state` が `unknown-outcome` / `conflict-not-switched` の場合、エイリアスの実際の版は不明（`unknown-outcome` の値は読み直しの観測値）で、自動の戻しはしていません。観測した版が今回の版でも、人が確認するまで上書きしません。`overall` が `in-progress` のまま残っている場合は、処理が強制終了した可能性があるため、`recoveryNote` に従って次を人が確認します。

```bash
aws lambda get-alias --function-name "$FN" --name demo --region "$AWS_REGION"   # 指す版と RevisionId
aws lambda get-function-configuration --function-name "$FN" --qualifier <版> --query '{d:Description,c:CodeSha256}' --region "$AWS_REGION"
```

直前の正常版へ戻す場合は、現在の `RevisionId` を指定して競合を避けます（版・成果物は削除しない）。

```bash
aws lambda update-alias --function-name "$FN" --name demo --function-version <直前の正常版> \
  --revision-id <get-aliasで確認したRevisionId> --routing-config '{"AdditionalVersionWeights":{}}' --region "$AWS_REGION"
```

公開を止めるとき（500円のアラート時、初回の失敗時、原因不明の異常時）は、予約済み同時実行数を0にします（関数URLは429を返す。データベースはないため削除するデータはない）。再開は原因を修正し、人が承認してからです。

公開停止中（予約済み同時実行数0）は、現行版のスモーク確認が失敗するため、配布ヘルパーは何も変更せずに止まります。これは意図した動作で、自動の迂回はありません。先に人が原因を確認し、公開してよいと判断してから予約済み同時実行数を戻します。新しい権限の要求や対応期限（SLA）はありません。

## 8. 費用の設定と通知（保証ではない）

メモリ512MiB、タイムアウト10秒、予約済み同時実行数1、ログ保持7日、プロビジョニング済み同時実行なし。これらは想定外の使用量を抑える目安で、**請求額の上限や月額ゼロ円の保証ではありません**。実際のアカウント種別・無料枠の対象・費用は未確認です。

予算アラート（300・500・800円の通知。設計のみ承認済みで、作成は未承認）の通知先（メールなど）は、利用者が自分でAWS側に非公開で入力します（リポジトリ・チャット・Linearには書かない。宛先を作り話で置かない）。

- 通貨: AWS Budgetsの予算額の単位は、公式のSpend APIの例ではUSDです（https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_budgets_Spend.html 。Codexが本文で確認）。**円建ての単位を使えるとは確認していません**。300・500・800円に相当するUSDの閾値は、為替レート・税の扱いを人が確認して決め、**作成前に人が確認します**（為替レートは作り話で置かない）。
- 費用: AWS Budgetsの監視と通知は無料です（https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/ 。Codexが確認）。有料のレポートやアクション（自動対応）は追加しません。
- 通知は請求データの反映が遅れるため、利用の上限ではありません。500円相当のアラートが届いたら、人が手動で上の公開停止を行います（自動停止は作りません）。

## 9. 成果物の保管と復元

GitHub Actionsの成果物の保管は最長90日で、期限後は消えます。Lambdaでは、現在の版と直前の版を、より新しい版がスモーク確認に合格するまで削除しません。成果物が期限切れでも、各版の description の `zip=<SHA-256>` と照合すれば正確なZIPを取り戻せます（IAM管理者などの権限で）。

```bash
aws lambda get-function --function-name "$FN" --qualifier <版> --query Code.Location --output text --region "$AWS_REGION"
# 出力されるURLは短時間だけ有効な署名つきなので、文書やチャットに貼らない。ダウンロード後に sha256sum で zip= と照合する
```

ソースの承認、CIとCDのrunの記録は、担当Issueのworkpadに残します。成果物が永久に残るとは扱いません。
