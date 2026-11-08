# 初回構築と運用手順（再現用。主要なAWSリソースは準備済み。公開経路・CD・復旧は未検証）

**最初に読む: この文書は、新しい環境で一から再現するための初回手順です。既に作成済みのリソース（関数・実行ロール・ロググループ・OIDCプロバイダー・配布ロール・エイリアス `demo`・関数URL・予約済み同時実行数）を再作成・上書きしません。** 既にあるものは読み取りで確認するだけで、「2.」「3.」「5.」の作成コマンドは実行しません（利用者の現在の環境では、下の現況のとおり準備済み）。

**現況（2026-11-09。Codexの実測・報告の転記。アカウントID・URLの実値・秘密は書かない）**

- 準備済み: Lambda関数、ログ保持7日のロググループ、実行ロール、配布ロール、OIDCプロバイダー、エイリアス `demo`（版1）、予約済み同時実行数1、関数URL（`AWS_IAM`・`BUFFERED`）。どのコマンドで作成したかは、この文書では確認していない。
- 確認済み（Codex実測）: デプロイ済みのZIP本体が `main` の `526db5bb` のビルドと照合して一致。非公開の呼び出しで `health` が200・本文 `ok`、`release` が期待SHAと一致（真）、ルートのHTMLとそのJS参照が成功。未署名の匿名GETは関数URLで403。
- 未検証・未作成: ブラウザでの表示、CloudFrontを通した実際のHTTP、CD（配布）、復旧（ロールバック・公開停止）。CloudFrontとWAFは未作成。OACは作成コマンドを利用者に案内済みだが結果は未受領で、作成済みかは未確認。
- GitHub: Environment `demo`、`CD_APPROVER_LOGIN`、`LAMBDA_FUNCTION_NAME`、`LAMBDA_LAYER_CODE_SHA256` は設定・読み戻し済み。`AWS_ROLE_ARN` とCloudFront関連の変数は未設定。

**過去の経緯（2026-11-08時点。現況ではない）**: 当時は構築・配布が未実施で、利用者本人がCloudShellで読み取り専用の関数一覧（`list-functions`）を実行して成功し、同じアカウントに既存のAmplify用の関数が4件あることを確認したと報告を受けた（読み取りのみで、この文書のコマンドではない。既存の関数名は公開文書に書かない）。

Lambda関連の構築は、本人がAWS CloudShellで、ここに提示したコマンドを1ブロックずつ実行する方式です。月額の目安（0円・1,000円など）を理由に手順を止めることはしませんが、支払い方法・認証・既存のリソース・データへの影響は、実行する時点で人が確認します（「8.」参照）。GitHubの設定は、下の表のとおり記録済みです。実行前に人が内容を読み、承認してから行います。アカウントIDなどは `REPLACE_*` の引用符つきの目印で、実在の値は書きません。実在のアカウントID・認証情報・セッショントークンは、この文書にも出力の貼り付けにも書きません。

## 位置づけ

| 区分 | 内容 |
| --- | --- |
| 合意済みの設計 | 東京（ap-northeast-1）、既存のAWSアカウント、公開は架空データのみ、常時公開を目指す。初期のCDはDB接続を必要とせず、この手順ではDBを作らない（Aurora DSQLの作成・接続は別作業担当）。配布は人が起動し、同じ1人が承認する（Environmentの自己承認禁止は意図して無効）。月0円・月1,000円は停止の条件にしない。過去に合意した費用通知の閾値（300・500・800円）は参考・任意の監視案で、必須の停止条件ではない。1,000円の厳密な上限は請求データの反映遅れのため作れない |
| 既存のリソース・DBとの関係 | Lambda関数・実行ロール・配布ロール・ロググループ・OIDCプロバイダーは、作成の前に読み取りで既存の有無を確認し、既存のものを自動で上書き・削除しない（人が判断する）。同じアカウントに既存のAmplify用の関数があるため、`FN` はそれらと重ならない新しい名前にし、下の `probe` で衝突を確認する。DBとの連携時は、別作業担当の状況（既存のクラスタや設定）を先に確認し、存在しない・未作成と推測しない |
| 仮定 | 古いアカウントのため従量課金・無料利用枠の旧来アカウントと仮定する。実際の種別・無料枠は未確認 |
| GitHub側の設定（Codex設定、2026-11-08） | Environment `demo`（必須レビュー担当者1人、自己承認の禁止は無効、デプロイブランチは `main`（type=branch）のみ）とリポジトリ変数 `CD_APPROVER_LOGIN` を設定済み。管理者による迂回の無効化は要求したが、APIの応答にそのフィールドがなく**未確認**（無効と確認したわけではない）。その後、Codexが `LAMBDA_FUNCTION_NAME` と `LAMBDA_LAYER_CODE_SHA256` を設定し、読み戻しで確認した（報告の転記。値は書かない）。ほかのAWS関連の変数（`AWS_ROLE_ARN`・`CD_BASE_URL`・`CLOUDFRONT_DISTRIBUTION_ID`・`CLOUDFRONT_OAC_ID`・`WAF_WEB_ACL_ARN`）は**未設定** |
| 現況の確認済み（2026-11-09、Codex実測） | 準備済みのAWSリソース（上の現況）、デプロイ済みZIP本体と `main` の `526db5bb` のビルドの照合一致、非公開の呼び出しでの `health`（200・本文 `ok`）・`release`（期待SHAと一致）・ルートHTMLとJS参照の成功、関数URLへの未署名の匿名GETが403。アダプタのレイヤーとPython 3.14の組み合わせは、この版・この構成の非公開の呼び出しが成功した範囲で動作を確認した（ほかの構成・負荷での動作は未確認）。文書の最終版の `bash -n` による構文確認は、この文書の24ブロックと[計画](cloudfront-plan.md)の6ブロックで成功し、`git diff --check` も成功した（Codex実測。構文のみで、AWSでの実行ではない。この文書の最終の文書修正は説明文だけで、ブロックは変えていない。最後の再確認はCodexが行う） |
| 未実施・未確認 | ブラウザでの表示、CloudFrontを通した実際のHTTP、実際のCD（配布・ロールバック）の実行と復旧（動作は未検証）、CloudFront・WAFの作成（未作成）、OACの作成状況（作成コマンドは案内済みで結果は未受領）、CloudFrontのFreeプランの契約・適用可否（Freeを選べるという報告は、契約・適用の成功ではない）、予算アラートの宛先（任意の監視案を採る場合。本人が非公開で入力し、作り話の宛先は置かない）、CloudShell以外の別環境や、将来のコミットでの `scripts/build-lambda.sh`（Dockerを使う）によるZIPビルドの一般的な再現（Dockerの可否・実行時間・ディスク容量はその環境ごとに未確認）。なお、利用者の報告では、CloudShellで `main` の `526db5bb` からDockerのZIPをビルドし、ZIPのSHA-256 `7610e36b9cc46a82f2496ee49c72b8310d06f817c7d8922cac656100ff075a62` と `verify-artifact-only` の成功まで確認済み（利用者の報告で、Codex・レビュー担当は再実行していない）。上の構文確認はAWSでの実行と同じではない |
| 実行時に人が確認する事項 | 本人がCloudShellで各ブロックを実行する時点での、支払い方法の状態・認証・既存リソースの有無・データへの影響の確認。内部の確認事項で、公開の主張ではない。月額の目安を理由に止めることはしない |

**公開経路（2026-11-09の更新）**: 関数URLを匿名で直接公開する旧手順は外しました。公開は、CloudFront + WAF + Lambda関数URL（`AWS_IAM`）+ OACだけで、手順と設定は[公開経路の計画](cloudfront-plan.md)にあります。利用者の環境では、関数URL（`AWS_IAM`・`BUFFERED`）、エイリアス `demo`・予約済み同時実行数1・ログ・OIDCプロバイダー・実行ロール・配布ロールが準備済みです（**再作成しない**。既存のものは読み取りで確認する）。CloudFrontとWAFは**未作成**です。OACは利用者に作成コマンドを案内済みですが、結果は未受領で**作成済みかは未確認**です（未作成とも断定しません。計画の手順0の2で読み取り確認）。匿名のGETが403になることはCodexが実測し、非公開の呼び出しでの `health`・`release`・ルートHTMLの成功も確認済みです。ブラウザでの表示とCloudFront経由の実際のHTTPは未検証です。CloudFrontのFreeプランを選べるという報告は契約・適用の成功ではなく、適用可否は未確認で、適合しない場合も自動では有料にしません。

CDワークフロー（`.github/workflows/cd.yml`）は、**ここで作る既存の正常なエイリアス `demo` を更新するだけ**です。IAM・関数URL・同時実行数・ログ・CloudFront・WAFの操作は含みません（CloudFront・OAC・WAF・関数のリソースポリシーは読み取りで検証するだけ）。

## 1. 準備

### 実行のルール（最初に読む）

- 以下の手順は、AWS CloudShellの新しいセッションで、**1ブロックずつ**貼り付けます。出力を人が確認してから次のブロックへ進みます。複数のブロックをまとめて貼らないでください。
- 専用のセッションは `exec bash --noprofile --norc` で始めます（現在のシェルを置き換えるため、エラーでセッションが終了しても、貼り付けの残りが元のシェルで実行されません）。ここで `set -euo pipefail` を有効にするので、**コマンドが1つでも失敗するとセッションが終了します**。
- セッションが終了したら、**以降のブロックを貼り付けません**。端末を閉じ、人が読み取り専用のコマンドで現状（作成済みのリソース）を確認してから、再開するかを判断します。既存の名前などで作成が失敗した場合も同様で、既存のリソースの再利用・上書き・削除は自動で行いません（人が判断します）。
- 変数は引用符つきの `REPLACE_*` を実際の値に書き換えて使います（`<…>` はシェルでリダイレクトと解釈されるため使いません）。書き換え忘れは次の検証で止まります。
- AWSの認証は、CloudShellにログイン中の利用者の資格情報だけで行います（アクセスキーを発行・貼り付けしない）。`aws configure` などで共通のプロファイルや設定ファイルを変更しません。実在のアカウントIDや認証情報を、出力・チャット・文書に貼りません。

```bash
exec bash --noprofile --norc
```

新しいセッションで、次のブロックを貼ります（値の書き換えだけを人が行う）。

```bash
set -euo pipefail
die() { echo "停止: $*" >&2; exit 1; }

export AWS_REGION=ap-northeast-1
export AWS_PAGER=""                                  # このセッションだけ。ページャで止まらないため
ACCOUNT_ID='REPLACE_ACCOUNT_ID'                      # 対象の12桁のアカウントID。人が確認する
FN='REPLACE_FUNCTION_NAME'                           # 自分で決めた関数名
SHA='REPLACE_SHA40'                                  # 初回に承認する40桁のmainのSHA（小文字16進）
REPO_SLUG='REPLACE_OWNER/REPLACE_REPO'               # 対象のGitHubリポジトリ（人が入力）
LAYER_ARN='arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28'

# AWSの変更の前に、すべて検証する（どれかに失敗すればセッションが終了する）
[[ "$AWS_REGION" == ap-northeast-1 ]]           || die "リージョンは ap-northeast-1 固定"
[[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]]              || die "ACCOUNT_ID は12桁の数字（REPLACE_ACCOUNT_ID のままではないか）"
[[ "$FN" != REPLACE_* ]]                        || die "FN が目印のまま"
[[ "$FN" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]{0,56}$ ]] || die "FN は英数字・-・_ の1〜57文字で先頭は - 不可（-deploy を付けてもロール名が64文字以内）"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]]                  || die "SHA は小文字16進の40桁"
[[ "$REPO_SLUG" != *REPLACE_* ]]                || die "REPO_SLUG が目印のまま"
[[ "$REPO_SLUG" =~ ^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$ ]] || die "REPO_SLUG は owner/repo の形式"
[[ "${REPO_SLUG#*/}" != . && "${REPO_SLUG#*/}" != .. ]]          || die "REPO_SLUG のリポジトリ名が不正"

EXEC_ROLE="${FN}-exec"          # 検証済みの FN から作る
DEPLOY_ROLE="${FN}-deploy"
[[ ${#EXEC_ROLE} -le 64 && ${#DEPLOY_ROLE} -le 64 ]] || die "ロール名が64文字を超える"
echo "入力の検証: 合格"
```

AWSへの最初の書き込みの前に、呼び出し元のアカウントが宣言した `ACCOUNT_ID` と一致することを確認します（値は表示しない。読み取りのみ）。

```bash
CALLER_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
[[ "$CALLER_ACCOUNT" == "$ACCOUNT_ID" ]] || die "認証中のアカウントが宣言した ACCOUNT_ID と異なる。AWSの変更をしない"
unset CALLER_ACCOUNT
echo "呼び出し元アカウント: 一致"
```

作成の前に、既存のリソースを読み取りだけで確認します。`probe` は失敗してもセッションを終了させません。取得できなかった理由（未作成・権限なし・通信エラー）は、表示されるエラーを人が読んで区別し、存在しないと推測しません。1つでも「既存あり」なら、「2.」以降へ進まず、人が再利用・中止を判断します（自動で上書き・削除しない）。出力には実在のアカウントIDが含まれ得るため、チャット・文書に貼りません。

```bash
probe() { local label="$1"; shift; if "$@"; then echo "既存あり: $label。作成の手順へ進まず、人が判断する"; else echo "取得できず: $label（エラーが『存在しない』か権限・通信の問題かを人が読んで判断する）"; fi; }
probe "関数 $FN" aws lambda get-function --function-name "$FN" --region "$AWS_REGION" --query Configuration.FunctionName --output text
probe "実行ロール $EXEC_ROLE" aws iam get-role --role-name "$EXEC_ROLE" --query Role.RoleName --output text
probe "配布ロール $DEPLOY_ROLE" aws iam get-role --role-name "$DEPLOY_ROLE" --query Role.RoleName --output text
# 次の2件は一覧の取得で、成功しても空なら「無い」。失敗してもセッションを終了させない（出力を人が読む）
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$FN" --region "$AWS_REGION" --query 'logGroups[].logGroupName' --output text || echo "取得できず: ロググループの一覧"
aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text || echo "取得できず: OIDCプロバイダーの一覧"   # 「5.」で再利用する既存の有無
```

初回のZIPは、承認したSHAをクリーンにチェックアウトして作ります（`--allow-dirty` のZIPは使わない）。以前のローカルZIPのソースSHAのラベルは過去の検証用で、承認されたものではありません。このビルドはDockerを使います。利用者の報告では、CloudShellで `main` の `526db5bb` からのDockerビルドと `verify-artifact-only` の成功まで確認済みです（Codex・レビュー担当は再実行していない。別環境での再現は未確認）。使えない・失敗する場合は先へ進まず、成功とは扱わず、代替（別の環境でのビルドとハッシュの照合など）を人が判断します。

```bash
bash scripts/build-lambda.sh "$SHA" "dist/cd/$SHA"
MANIFEST="dist/cd/$SHA/manifest.json"
jq '{sourceSHA, releaseEligible, dirty, bytes: .zip.bytes, hex: .zip.sha256Hex, code: .zip.codeSha256}' "$MANIFEST"
```

`sourceSHA` が承認したSHAと一致、`releaseEligible` が `true`、`dirty` が `false`、`bytes` が50MB（52,428,800）未満（直接アップロードできる）であることを、次で検査します。1つでも満たさなければ止まります。

```bash
jq -e --arg sha "$SHA" '.sourceSHA == $sha and .releaseEligible == true and .dirty == false and .zip.bytes < 52428800' "$MANIFEST" >/dev/null \
  || die "マニフェストが条件を満たさない"

ZIP="dist/cd/$SHA/lambda-$SHA.zip"
ZIP_HEX="$(jq -r .zip.sha256Hex "$MANIFEST")"       # export と同時に代入しない（失敗が隠れるため）
ZIP_CODE="$(jq -r .zip.codeSha256 "$MANIFEST")"
[[ "$ZIP_HEX" =~ ^[0-9a-f]{64}$ ]]                  || die "ZIPのSHA-256（16進）が不正"
[[ "$ZIP_CODE" =~ ^[A-Za-z0-9+/]{43}=$ ]]           || die "CodeSha256（base64）が不正"
[[ -f "$ZIP" ]]                                     || die "ZIPがない"
ZIP_ACTUAL="$(sha256sum "$ZIP" | cut -d' ' -f1)"
[[ "$ZIP_ACTUAL" == "$ZIP_HEX" ]]                   || die "ZIPの実体のSHA-256がマニフェストと不一致"
echo "ZIPのハッシュ: 一致"
```

## 2. 実行ロールとログ（7日保持）

ログのグループを先に作り、実行ロールには作成権限を与えず、そのグループへの書き込みだけを許します。

同名のロールやログのグループが既にあると `create-*` が失敗してセッションが終了します。その場合は**止まり**、既存のものを再利用・上書きしません（特に、既存のロールに `put-role-policy` を書き込まないため、`create-role` が成功したときだけ次へ進む構造にしています）。作成済みの分は削除せず、人が読み取りで確認して判断します。

```bash
aws logs create-log-group --log-group-name "/aws/lambda/$FN" --region "$AWS_REGION"
aws logs put-retention-policy --log-group-name "/aws/lambda/$FN" --retention-in-days 7 --region "$AWS_REGION"

work="$(mktemp -d)"   # リポジトリの外の、自分専用の一時ディレクトリ
cat > "$work/exec-trust.json" <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
aws iam create-role --role-name "$EXEC_ROLE" --assume-role-policy-document "file://$work/exec-trust.json"

cat > "$work/exec-logs.json" <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/${FN}:*"}]}
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

プロビジョニング済み同時実行、DB（作成・接続は別作業担当）、NAT、ECR、S3、独自ドメインは、この手順では作りません（CloudFront・OAC・WAFは「4.」と[計画](cloudfront-plan.md)で別に作ります）。ロールの作成直後は反映待ちで `create-function` が失敗することがあります。その場合もセッションが終了して止まります。人が `aws lambda get-function --function-name "$FN" --region "$AWS_REGION"` で関数が未作成であることを読み取りで確認し、少し待ってから、新しいセッションで「1. 準備」の設定・検証・呼び出し元アカウントの確認をやり直し（ビルドは再実行せず、`MANIFEST="dist/cd/$SHA/manifest.json"` を設定してマニフェスト・ZIPの検査のブロックだけ実行）、`create-function` 以降だけを実行します（ロールとログは再作成しない）。関数が既にある場合は再利用・上書きせず、人が判断します。アカウントの同時実行数の上限が低く予約できない場合も、そこで止めて人が判断します。

### レイヤーのハッシュを記録する

```bash
aws lambda get-layer-version-by-arn --arn "$LAYER_ARN" --region "$AWS_REGION" --query Content.CodeSha256 --output text
```

この値が、配布時の期待値（リポジトリ変数 `LAMBDA_LAYER_CODE_SHA256`）になります。独立した情報源との照合ではなく、この時点の実体を記録する（最初の観測を信頼する）ものです。以後、レイヤーの実体が変わると配布は止まります。

### 最初の版とエイリアス

版番号は1とは限らない（作成の再試行などで変わる）ため、固定せず、発行の応答から取得して使います。

次のブロックは、ハッシュの不一致・版番号の取得失敗・Activeにならない場合に、エイリアスを作る前に止まります（`die` と `set -e` による。版が発行済みでも削除せず、人が確認する）。

```bash
actual="$(aws lambda get-function-configuration --function-name "$FN" --region "$AWS_REGION" --query CodeSha256 --output text)"
[[ "$actual" == "$ZIP_CODE" ]] || die "コードのハッシュが不一致。エイリアスを作らない"
echo "コードのハッシュ: 一致"

FIRST_VERSION="$(aws lambda publish-version --function-name "$FN" --code-sha256 "$ZIP_CODE" \
  --description "source=$SHA;zip=$ZIP_HEX;run=0" --region "$AWS_REGION" --query Version --output text)"
[[ "$FIRST_VERSION" =~ ^[1-9][0-9]*$ ]] || die "版番号を取得できない。エイリアスを作らない"
echo "最初の版: $FIRST_VERSION"

# 発行した版が Active になるまで待つ（AWS CLIの上限つきのwaiter。タイムアウトは失敗で止まる）
aws lambda wait function-active-v2 --function-name "$FN" --qualifier "$FIRST_VERSION" --region "$AWS_REGION"

aws lambda create-alias --function-name "$FN" --name demo --function-version "$FIRST_VERSION" --region "$AWS_REGION"
```

description の形式は `source=<40桁のSHA>;zip=<64桁のSHA-256>;run=<数字>` で、配布ヘルパーが次回、直前の正常版の出所として読みます（初回は `run=0`）。

#### 発行済みの版が既にある場合（配布の再実行）

Lambdaは、コードも設定も最後の版から変わらないと新しい版を作らず、既存の版を返します（公式: PublishVersion API）。配布ヘルパーは次のとおり扱います。実際のAWSでは未確認です。

- エイリアスが既に今回と同じソースSHA・ZIPの版を指し、その版の現行スモーク確認が合格した場合: **変更なしの成功**（`overall=no-change-success`、`deployment=already-current`、新しい版は発行せず、新しい配布のスモーク確認もしない）。新しい配布の成功とは扱いません。
- 前回の試行が版を発行した後に止まり、エイリアスが旧版のまま残った場合: 再実行で返された既存の版を、**descriptionのソースSHA・ZIPのハッシュ、コードのハッシュ、検査対象の設定がすべて今回と一致すると証明できたときだけ**再利用します（`versions.reused=true`）。証明できなければ、エイリアスを動かさず止まります。設定を変えて版を作らせることはしません。
- 発行直後（または再利用）の版は、`aws lambda wait function-active-v2 --qualifier <版>`（AWS CLIの上限つき）で Active を待ってから確認し、その後にエイリアスを切り替えます。待機の失敗・タイムアウトはエイリアスを動かさず止まります（`stage=wait-published`）。Pending のまま確認する緩和はしません。更新後のRevisionId・CodeSha256・設定の確認も緩めておらず、更新後のRevisionIdの扱いは実際のAWSでの確認項目です（未確認）。
- 止まった場合の手動確認（読み取りのみ）: `aws lambda list-versions-by-function --function-name "$FN" --region "$AWS_REGION"` で版を見つけ、`get-function-configuration --qualifier "$VERSION"`（「7.」の `VERSION` の書き方）の `Description`・`CodeSha256`・Layers・Runtime などが今回の想定と一致するか確認する。一致すると人が判断した場合に限り、`update-alias`（現在の `RevisionId` 指定）で切り替える。一致しない版は使わず、削除もしない。

### 関数URL（非公開の `AWS_IAM`。匿名の公開許可は作らない）

関数URLは `AWS_IAM` で作ります。**匿名で直接公開する旧手順（`--auth-type NONE` と `Principal: *` の許可）は、現行の手順から外しました**（botへの露出を避けるため）。公開は、CloudFront + OAC + WAFの経由だけで、[公開経路の計画](cloudfront-plan.md)に従います。

利用者の環境では、関数URL（`AWS_IAM`・`BUFFERED`・エイリアス `demo`）は**準備済み**です。**再作成しません**。まず読み取りで現状を確認します。

```bash
aws lambda get-function-url-config --function-name "$FN" --qualifier demo --region "$AWS_REGION" \
  --query '{auth:AuthType,mode:InvokeMode}' --output json
```

`AWS_IAM`・`BUFFERED` であれば次へ進みます（何も作らない）。**取得できなかった理由が「存在しない」と人が確認できた場合だけ**、次を実行します。既に存在する関数URLがあれば、実行せず、人が判断します（`create-function-url-config` は既存の関数URLを置き換えない）。

```bash
aws lambda create-function-url-config --function-name "$FN" --qualifier demo \
  --auth-type AWS_IAM --invoke-mode BUFFERED --region "$AWS_REGION"
```

出力の `FunctionUrl` は、CloudFrontのオリジンのドメインに使うだけで、公開URL（`CD_BASE_URL`）にもリポジトリ変数にもしません。リソースポリシーへの許可は、CloudFrontのディストリビューションを作って設定を確認した後の**最後の手順**（[計画の手順5](cloudfront-plan.md)）で、特定のディストリビューションにだけ与えます。

## 4. 公開経路の作成・確認と、初回のスモーク確認

CloudFrontとWAFは**未作成**で、OACは作成状況が**未確認**です（作成済みならそれを再利用し、重複して作りません）。[計画](cloudfront-plan.md)の手順0の2（OACの読み取り確認）と手順1〜5（Freeプランのコンソールでのディストリビューション・Web ACLの作成と、Lambdaの許可を付けないこと、専用Web ACLの確認、OAC・キャッシュなどの全設定の確認、設定の読み取り確認、最後の許可）を、その順序で行います。許可（手順5）の前に、公開経路の迂回がないこと（関数URLが匿名のGETを403で拒否し、許可が特定のディストリビューションだけであること）を確認します。Freeプランに適合しない場合は、自動で有料にせず、そこで止まって人が判断します（Web ACLを単独で作る有料の代替は計画の末尾）。

予約済み同時実行数は、作成後も1のままです（公開後の異常時は人が0にして止める）。0にしてある場合（初回構築の途中）は、**承認した初回デモを公開してよいと人が判断したときだけ**1にします。許可を作った時点から、公開されます。

```bash
CD_BASE_URL_VALUE='REPLACE_CLOUDFRONT_ORIGIN'   # https://<id>.cloudfront.net（末尾の / なし）
[[ "$CD_BASE_URL_VALUE" =~ ^https://[a-z0-9]{8,64}\.cloudfront\.net$ ]] || die "CloudFrontのオリジンの形式が不正"
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 1 --region "$AWS_REGION"

# スモーク確認の失敗ではセッションを終了させない（直後に公開停止を実行できるようにするため）
smoke_rc=0
CD_BASE_URL="$CD_BASE_URL_VALUE" CD_EXPECTED_SHA="$SHA" CD_REPORT_ID="bootstrap-$SHA" bash scripts/cd-smoke.sh || smoke_rc=$?
if [ "$smoke_rc" -ne 0 ]; then echo "スモーク確認が失敗。直ちに下の公開停止のブロックを実行する" >&2; fi
```

失敗したら**直ちに**手動停止します（初回は戻す先の正常版がない）。これが唯一、失敗後に続けて貼ってよいブロックです。セッションが終了していた場合は、「1. 準備」の設定と検証だけをやり直してから実行します。

```bash
aws lambda put-function-concurrency --function-name "$FN" --reserved-concurrent-executions 0 --region "$AWS_REGION"
```

成功を確認するまで、エイリアスを正常とは扱いません。再開は原因を修正し、人が承認してからです。

## 5. 配布用のOIDCロール（IAM管理者の別操作）

この節も、「1. 準備」の設定・検証・呼び出し元アカウントの確認を済ませたセッションで行います（`DEPLOY_ROLE`・`REPO_SLUG` などを使うため）。

GitHubのOIDCプロバイダーが既にあれば再利用します。まず読み取りで確認します。

```bash
aws iam list-open-id-connect-providers   # ARNの末尾が token.actions.githubusercontent.com のものがあるか、人が目で確認する
```

一覧に**無いと人が確認したときだけ**、次の作成を実行します（`--thumbprint-list` の要否は `aws iam create-open-id-connect-provider help` で確認）。既にある場合は実行せず、既存のものを再利用します。

```bash
aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com
```

信頼ポリシー（`infra/cd/oidc-trust-policy.json`）は `aud` が `sts.amazonaws.com`、`sub` が `repo:<OWNER>/<REPO>:environment:demo` の完全一致だけです（ワイルドカードなし）。権限（`infra/cd/deploy-policy.json`）は、CloudFront・WAFの読み取り（対象のディストリビューションとWeb ACLの `Get` だけ。OACの読み取りはリソース指定ができないため `Resource: *`）、エイリアス `demo` のリソースポリシーの読み取り（`lambda:GetPolicy`）、対象関数（修飾子なし）とその版・エイリアス（修飾子つき）に限った、設定・関数の参照（`GetFunction` は更新完了の待機 `aws lambda wait function-updated-v2` と、発行した版の有効化の待機 `aws lambda wait function-active-v2 --qualifier <版>` が使う。追加の権限は不要）、コード更新、版の発行、エイリアスの参照と更新と、固定のレイヤー版の読み取りだけです。`iam:PassRole`、管理者権限、実行時の権限、関数URL・同時実行数・ログ・IAMの操作は含めません。

このロールは、CloudFront・WAFを作って（「4.」）ディストリビューションIDとWeb ACLのARNが決まった**後**に作ります（権限が対象を指定するため）。`CF_DIST_ID`・`WAF_ARN` は、[計画](cloudfront-plan.md)で作成した実値を、引用符つきの目印を書き換えて設定します（CDのコードの形式検証と同じ正規表現で検証。作成後に実際に取得できることも確認する）。

```bash
CF_DIST_ID='REPLACE_DISTRIBUTION_ID'
WAF_ARN='REPLACE_WEB_ACL_ARN'      # arn:aws:wafv2:us-east-1:<アカウント>:global/webacl/<名前>/<ID>
[[ "$CF_DIST_ID" =~ ^[A-Z0-9]{8,32}$ ]] || die "CF_DIST_ID の形式が不正（REPLACE_ のままではないか）"
[[ "$WAF_ARN" =~ ^arn:aws:wafv2:us-east-1:${ACCOUNT_ID}:global/webacl/[A-Za-z0-9_-]{1,128}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
  || die "WAF_ARN が us-east-1・global scope・宣言したアカウントのWeb ACLの形式ではない"
aws cloudfront get-distribution --id "$CF_DIST_ID" --region us-east-1 --query Distribution.Id --output text >/dev/null   # 実在の確認（読み取り）
```

`REPO_SLUG`・`ACCOUNT_ID`・`FN`・`CF_DIST_ID`・`WAF_ARN` は検証済みで、`sed` の区切り文字 `|` や `&` を含みません。まず、置換した内容を作って確認します（AWSへの書き込みなし）。

```bash
work="$(mktemp -d)"
sed -e "s|<ACCOUNT_ID>|${ACCOUNT_ID}|g" -e "s|<OWNER>/<REPO>|${REPO_SLUG}|g" infra/cd/oidc-trust-policy.json > "$work/trust.json"
sed -e "s|<ACCOUNT_ID>|${ACCOUNT_ID}|g" -e "s|<FUNCTION_NAME>|${FN}|g" \
    -e "s|<DISTRIBUTION_ID>|${CF_DIST_ID}|g" -e "s|<WEB_ACL_ARN>|${WAF_ARN}|g" infra/cd/deploy-policy.json > "$work/policy.json"
if grep -nE '<(ACCOUNT_ID|FUNCTION_NAME|DISTRIBUTION_ID|WEB_ACL_ARN|OWNER|REPO)>' "$work/trust.json" "$work/policy.json"; then die "未置換の目印が残っている"; fi
jq -e . "$work/trust.json" "$work/policy.json" >/dev/null || die "JSONとして不正"
grep -qF "\"repo:${REPO_SLUG}:environment:demo\"" "$work/trust.json" || die "sub が repo:<リポジトリ>:environment:demo の完全一致になっていない"
# 置換後の内容を人が目で確認する（実在のアカウントIDが出るため、出力はチャット・文書に貼らない）
cat "$work/trust.json" "$work/policy.json"
```

内容を人が確認し、問題がなければ、次のブロックで作成します。`create-role` が既存のロール名で失敗した場合はセッションが終了し、`put-role-policy` は実行されません。既存のロールを再利用・上書きせず、人が判断します。

```bash
aws iam create-role --role-name "$DEPLOY_ROLE" --assume-role-policy-document "file://$work/trust.json" --max-session-duration 3600
aws iam put-role-policy --role-name "$DEPLOY_ROLE" --policy-name deploy-demo-only --policy-document "file://$work/policy.json"
```

ロールのARNがリポジトリ変数 `AWS_ROLE_ARN` です。固定レイヤーの読み取りが実際のAWSで必要か・足りるかは未確認です（`AccessDenied` なら、固定ARNの読み取りだけを足りない操作に限って見直す）。

## 6. GitHubの設定（Environment・`CD_APPROVER_LOGIN`・`LAMBDA_FUNCTION_NAME`・`LAMBDA_LAYER_CODE_SHA256` は設定済み。`AWS_ROLE_ARN` とCloudFront関連は未設定）

リポジトリ変数（Settings → Secrets and variables → Actions → Variables）。いずれも秘密ではありません。人名は公開文書に書かず、ここへだけ入力します。

| 名前 | 値 | 状態 |
| --- | --- | --- |
| `CD_APPROVER_LOGIN` | 起動・承認する唯一の人のGitHubログイン。**リポジトリ変数**にする（buildジョブも参照する） | 設定済み |
| `AWS_ROLE_ARN` | 上のデプロイ用ロールのARN | 未設定（AWSの作成後） |
| `LAMBDA_FUNCTION_NAME` | `$FN` | 設定・読み戻し済み（Codex。値は書かない） |
| `CD_BASE_URL` | 公開URL。**CloudFrontのオリジン**（`https://<id>.cloudfront.net`、末尾の`/`なし）。関数URLは設定しない（コードも拒否する） | 未設定（CloudFront作成後） |
| `CLOUDFRONT_DISTRIBUTION_ID` | CloudFrontのディストリビューションID（`$CF_DIST_ID`） | 未設定（CloudFront作成後） |
| `CLOUDFRONT_OAC_ID` | Origin Access ControlのID（作成済みなら、そのIDを読み取りで確認して設定する） | 未設定（OACの作成状況は未確認） |
| `WAF_WEB_ACL_ARN` | Web ACLのARN（us-east-1・CLOUDFRONT scope、`$WAF_ARN`） | 未設定（WAF作成後） |
| `LAMBDA_LAYER_CODE_SHA256` | 上で記録したレイヤーのCodeSha256（base64） | 設定・読み戻し済み（Codex。値は書かない） |

Environment `demo`（名前は運用上の選択）。設定済みですが、CDは自動作成に頼らず、既存のEnvironmentが次の設定であることを確認できないと、AWSの資格情報の前に止まります。

- 必須レビュー担当者: ちょうど1人のユーザー（`CD_APPROVER_LOGIN` と一致）
- 「レビュー担当者による自己承認の禁止」: **意図して無効**（唯一の人が起動し承認するため。合意済みで、承認の独立性は確保されない）
- 管理者による保護の迂回の許可: 無効にする設定を要求したが、APIの応答にフィールドがなく無効とは**確認できていない**（事前確認は「未確認」の注意を出す）
- デプロイブランチ: 「選択したブランチ」のカスタムポリシーで `main`（ブランチ）の1件だけ。ワイルドカード・タグは不可

設定は既に行われています。まず**読み取りで現状を確認**します（書き込みなし。出力に人名・数値IDが出るため、チャット・文書に貼らない）。この節の `gh` コマンドは、GitHubに認証済みの利用者自身の端末で実行します。CloudShellで `gh` が使えるか・認証があるかは未確認で、GitHubのトークンをCloudShellへ置きません。別の端末で実行する場合は、「1. 準備」と同じ形で `die` を定義し、`REPO_SLUG` を設定・検証してから実行します。

```bash
gh api "repos/${REPO_SLUG}/environments/demo"
gh api "repos/${REPO_SLUG}/environments/demo/deployment-branch-policies"
gh variable list --repo "$REPO_SLUG"
```

次の `PUT` は、**新しい環境を作る場合、または人が承認した変更のときだけ**の手動の例です。`PUT` は既存のEnvironmentの設定を**置き換える**ため、現在の設定（設定済み）には実行しません。実行する場合は、上の読み取りの結果と照らして置換後の設定を人が確認してから行います。`APPROVER_ID` は承認者のGitHubの数値IDです（人が確認して入力）。

```bash
APPROVER_ID='REPLACE_APPROVER_NUMERIC_ID'
[[ "$APPROVER_ID" =~ ^[0-9]+$ ]] || die "APPROVER_ID は数値"
gh api -X PUT "repos/${REPO_SLUG}/environments/demo" --input - <<JSON
{"reviewers":[{"type":"User","id":${APPROVER_ID}}],"prevent_self_review":false,"can_admins_bypass":false,
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON
gh api -X POST "repos/${REPO_SLUG}/environments/demo/deployment-branch-policies" -f name=main -f type=branch
```

## 7. 配布（CD）の流れと、止まったときの確認

Actionsの「CD」を `main` から手動で起動し、`source_sha` に承認する完全なSHAを入れます。buildジョブのサマリー（CIのrun、ソースSHA、実測したZIPのSHA-256、成果物）を確認してから、Environment `demo` を承認します。結果は `cd-result-<run>-<attempt>` の `cd-result/result.json` とジョブのサマリーに残ります。

結果JSONの見方: `deployment`（配布処理）・`smoke`（新しい版）・`rollback` は別々に記録されます。`overall` が `success`（新しい版を配布し、スモーク確認に合格）または `no-change-success`（既に同じソース・ZIPの版が現行で、変更も新しい版の発行もしていない。新しい配布の成功ではない）以外なら失敗です。ロールバックが成功しても全体は失敗として終了します。`mutationStarted` が `true` なら、AWSの変更操作を始めており（成功を意味しない）、結果ファイルが無い場合もAWSの操作の有無は**未確認**で、人が確認します。`alias.state` が `unknown-outcome` / `conflict-not-switched` の場合、エイリアスの実際の版は不明（`unknown-outcome` の値は読み直しの観測値）で、自動の戻しはしていません。観測した版が今回の版でも、人が確認するまで上書きしません。`overall` が `in-progress` のまま残っている場合は、処理が強制終了した可能性があるため、`recoveryNote` に従って次を人が確認します。

以下のコマンドも、「1. 準備」の設定・検証・呼び出し元アカウントの確認を済ませたセッションで実行します。`<版>` のような `<…>` は貼り付けるとリダイレクトと解釈されるため、引用符つきの `REPLACE_*` を書き換えて使います。

```bash
aws lambda get-alias --function-name "$FN" --name demo --region "$AWS_REGION"   # 指す版と RevisionId
VERSION='REPLACE_VERSION'                                                        # 確認したい版
[[ "$VERSION" =~ ^[1-9][0-9]*$ ]] || die "VERSION は版番号（数字）"
aws lambda get-function-configuration --function-name "$FN" --qualifier "$VERSION" --query '{d:Description,c:CodeSha256}' --region "$AWS_REGION"
```

直前の正常版へ戻す場合は、現在の `RevisionId` を指定して競合を避けます（版・成果物は削除しない）。書き込みなので、`get-alias` の結果を人が確認してから実行します。

```bash
PREV_VERSION='REPLACE_PREVIOUS_GOOD_VERSION'
REVISION_ID='REPLACE_REVISION_ID_FROM_GET_ALIAS'
[[ "$PREV_VERSION" =~ ^[1-9][0-9]*$ ]] || die "PREV_VERSION は版番号（数字）"
[[ "$REVISION_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die "REVISION_ID の形式が不正"
aws lambda update-alias --function-name "$FN" --name demo --function-version "$PREV_VERSION" \
  --revision-id "$REVISION_ID" --routing-config '{"AdditionalVersionWeights":{}}' --region "$AWS_REGION"
```

公開を止めるとき（初回の失敗時、原因不明の異常時、費用の通知などを見て人が必要と判断したとき。費用の通知は必須の停止条件ではない）は、予約済み同時実行数を0にします（関数URLは429を返す。この手順はDBを作らず、削除するデータはない）。再開は原因を修正し、人が承認してからです。

公開停止中（予約済み同時実行数0）は、現行版のスモーク確認が失敗するため、配布ヘルパーは何も変更せずに止まります。これは意図した動作で、自動の迂回はありません。先に人が原因を確認し、公開してよいと判断してから予約済み同時実行数を戻します。新しい権限の要求や対応期限（SLA）はありません。

## 8. 費用の設定と通知（保証ではない）

メモリ512MiB、タイムアウト10秒、予約済み同時実行数1、ログ保持7日、プロビジョニング済み同時実行なし。これらは想定外の使用量を抑える目安で、**請求額の上限や月額ゼロ円の保証ではありません**。実際のアカウント種別・無料枠の対象・費用は未確認です。

月0円や月1,000円を超える見込みを理由に、この手順を止めることはしません（現行の方針）。以前に合意した予算アラート（300・500・800円の通知）は、**参考・任意の監視案**で、必須ではなく、作成も未実施です。採る場合の通知先（メールなど）は、利用者が自分でAWS側に非公開で入力します（リポジトリ・チャット・Linearには書かない。宛先を作り話で置かない）。

- 通貨: AWS Budgetsの予算額の単位は、公式のSpend APIの例ではUSDです（https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_budgets_Spend.html 。Codexが本文で確認）。**円建ての単位を使えるとは確認していません**。300・500・800円に相当するUSDの閾値は、為替レート・税の扱いを人が確認して決め、**作成前に人が確認します**（為替レートは作り話で置かない）。
- 費用: AWS Budgetsの監視と通知は無料です（https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/ 。Codexが確認）。有料のレポートやアクション（自動対応）は追加しません。
- 通知は請求データの反映が遅れるため、利用の上限ではありません。500円相当の通知が届いても、必須の停止はしません。人が利用量と採用リソースを見て、公開停止（「7.」）が必要かを判断します（自動停止は作りません）。

### 利用量・費用の確認方法と記録欄（独自の判断。確認できた範囲だけ記録する）

- 確認方法: AWSコンソールの請求ページ・Cost Explorerで、サービス・リージョン別の実績を読み取りで確認します（反映に遅れがあり、直近の分は未反映の可能性がある）。Lambdaの呼び出し回数・実行時間は、CloudWatchのメトリクスで確認します。見積もりは公式の料金ページ（Lambda: https://aws.amazon.com/lambda/pricing/）を根拠にし、確認できない部分は「未確認」とします。
- 実行のたびに、次の表へ追記します。アカウントID・認証情報・署名つきURL・通知先などの秘密値は書きません。「実請求」は、請求ページで確認するまで「未確認」のままにし、見積もりを実績として扱いません。

| サービス | リージョン | 設定 | 実行日 | 見積もりの根拠 | 実請求 |
| --- | --- | --- | --- | --- | --- |
| （記入例）Lambda | ap-northeast-1 | 512MiB・10秒・予約済み同時実行1 | YYYY-MM-DD | 公式料金ページの単価と想定の呼び出し回数 | 未確認 |

## 9. 成果物の保管と復元

GitHub Actionsの成果物の保管は最長90日で、期限後は消えます。Lambdaでは、現在の版と直前の版を、より新しい版がスモーク確認に合格するまで削除しません。成果物が期限切れでも、各版の description の `zip=<SHA-256>` と照合すれば正確なZIPを取り戻せます（IAM管理者などの権限で）。

```bash
VERSION='REPLACE_VERSION'
[[ "$VERSION" =~ ^[1-9][0-9]*$ ]] || die "VERSION は版番号（数字）"
aws lambda get-function --function-name "$FN" --qualifier "$VERSION" --query Code.Location --output text --region "$AWS_REGION"
# 出力されるURLは短時間だけ有効な署名つきなので、文書やチャットに貼らない。ダウンロード後に sha256sum で zip= と照合する
```

ソースの承認、CIとCDのrunの記録は、担当Issueのworkpadに残します。成果物が永久に残るとは扱いません。
