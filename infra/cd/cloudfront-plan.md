# 公開経路の計画: CloudFront + WAF + Lambda関数URL（AWS_IAM）+ OAC

この文書は**計画と手順案**です。利用者の報告（2026-11-09）では、CloudFront（無効化・`Deployed`・Web ACL紐付け済み）、WAFのWeb ACL、OAC（`lambda`・`sigv4`・`always`）は**作成済み**です。これらは**再作成・削除せず、再利用して設定を確認**します。以降の「作成」の手順は、初回構築の履歴、または該当のリソースが無いと人が確認できた場合だけの手順で、作成済みなら飛ばして読み取りで確認します。利用者の確認（2026-11-10）では、Web ACLの規則は4件（接続元IPごとのレート制限のBlock 1件＋既知の観測グループ3種の`Count`）で、全体のレート制限の規則はありません。同日、利用者の承認により、全体1,500件/5分の規則を要件から外しました（必須はIPごとの1件だけ）。有効化・Lambdaの許可・公開HTTPの確認・CD・ロールバックは未実施です。このファイルのコマンド自体の実行結果は受け取っていません。AWSの作成・変更は、利用者本人が内容を確認し、判断してから実行します。コード・設定はこの文書の外にあります（検証は `scripts/deploy-lambda.py`、WAFの規則は `infra/cd/waf-rules.json`）。実在のアカウントID・ディストリビューションID・URLの実値は書きません（引用符つきの `REPLACE_*` の目印を使う）。

## 区分

| 区分 | 内容 |
| --- | --- |
| 履歴（初回構築の前の確認。現在の状態ではない） | 作成前は、既存のCloudFrontディストリビューションは0件（`null`）、WAF（CLOUDFRONT scope）のWeb ACLは0件（`[]`）だった。この「0件」は現在の状態として扱わない |
| 履歴（利用者の報告、2026-11-09。2026-11-10の確認で更新） | CloudFrontのディストリビューションは**作成済み**（無効化・`Deployed`、WAFのWeb ACLを紐付け済み）。OACは**作成済み**で、種類`lambda`・署名`sigv4`・署名の動作`always`。コンソールでFreeプランの「$0」の表示を確認した（料金の保証ではない）。WAFには、既知のAWSマネージド規則グループ3種が`OverrideAction=Count`で入っており、当時の必須のレート制限2規則（Block）はまだ無かった。Lambda関数URLは**作成済み**で、認証方式 `AWS_IAM`・`BUFFERED`・エイリアス `demo`。未署名の匿名GETが403になることをCodexが実測した。予約済み同時実行数は1 |
| 確認済み（利用者の確認、2026-11-10。現在の状態） | 作成済みのWeb ACLの規則は**4件**: 接続元IPごとのレート制限（Block）1件と、既知のAWSマネージド規則グループ3種（`Count`）。**全体のレート制限の規則は無い**。ディストリビューションは無効のまま。これらのリソースは**再作成・削除せず再利用**する |
| 方針の変更（利用者の承認、2026-11-10） | 全体1,500件/5分の規則を要件から外す。必須は、実際の接続元IPごとの300件/300秒のBlock **1件だけ**（既知の観測グループ3種の`Count`は任意で0〜3件、合計1〜4件）。経緯（利用者の報告）: 全体の規則（URIパスとHTTPメソッドの条件つき）を保存しようとして、`WAFFeatureNotIncludedInPricingPlanException` で失敗した。**どの機能がプランに含まれないのかは特定していない**ため、原因を断定せず、Freeプランで使える・使えない機能の一般論も主張しない |
| 合意（利用者の判断） | 関数URLを匿名で直接公開する旧手順（`NONE`）はbotの懸念から採らない。独自ドメインは不要 |
| 利用者の報告（実適合は未確認） | CloudFrontのFreeプランを選択でき、コンソールで「$0」の表示を確認した。ただし料金の保証ではなく、実際の請求・適用範囲は未確認 |
| 未実施（現在の状態） | 有効化、Lambdaの許可、公開のHTTP確認、CD（配布）、ロールバック。ディストリビューションは無効のままで、公開していない |
| 計画案（プロジェクト独自の判断。未採用・未実測） | 以下の構成と順序 |
| 未確認 | Freeプランの実際の請求・適用可否、全体の規則の保存が失敗した具体的な原因（どの機能が対象か）、Lambda関数URL + OACとFreeプランの組み合わせ、コンソールがLambdaの許可を自動で追加したか（作成済みのディストリビューションで、関数のリソースポリシーを読み取りで確認する）、実際の費用、実際のレート制限・スロットリングの動作 |

## 構成（これ以外は作らない）

```
利用者 → CloudFront（WAFのWeb ACL）→ OAC（SigV4署名）→ Lambda関数URL（AWS_IAM, エイリアス demo）→ Lambda
```

- 公開URLはCloudFrontの `https://<id>.cloudfront.net` だけ。関数URLを公開URL（`CD_BASE_URL`）にしない。フォールバックも置かない。
- 新規に作るのは、Web ACL 1件、ディストリビューション 1件と、OAC 1件（**いずれも作成済み。再利用し、新規に作らない・削除しない**。重複作成しない）だけ。**独自ドメイン、S3、NAT、Aurora DSQLは新規に作らない**。既存のLambda・関数URL・実行ロール・ロググループは再作成しない（特に、準備済みの関数URL（AWS_IAM・BUFFERED）を作り直さない）。既存のCloudFront・WAF・Lambdaの他のリソースは変更しない。
- 公開後も予約済み同時実行数は1を維持する。公開後に異常があれば、人が0にして止める（関数URLは429を返す）。CDは変更の前に、読み取り（`get-function-concurrency`）でちょうど1であることを確認し、違えば止まる（自動では設定しない。下の「同時実行数1の意味」）。

## 設定（公開の前に、この表と実際の設定が一致することを確認する）

| 対象 | 設定 | 備考 |
| --- | --- | --- |
| OAC | 種類 `lambda`、署名 `sigv4`、署名の動作 `always` | `never`・`no-override` は不可。検証コードは `always` だけを許す。作成済みのOACがあれば、それを選んで再利用する（手順0の2） |
| オリジン | 1件。ドメインは関数URLのホスト（エイリアス `demo` の実値）、パスなし、`https-only`、OACを関連付け | 関数URLの値はコミット・文書に書かない |
| デフォルトのビヘイビア | `redirect-to-https`（または `https-only`）。許可メソッド `GET`・`HEAD`・`OPTIONS`。キャッシュ対象メソッドは `GET`・`HEAD`（`OPTIONS` を足してもよい） | POSTなどは許可しない。将来POSTを足す場合、OACは本文のSHA-256ヘッダが必要という要件が公式にあるため、別途設計する |
| キャッシュポリシー | `CachingDisabled`（Managed）。ID `4135ea2d-6df8-44a3-9df3-4b5a84be39ad` | 初期はデフォルトのビヘイビア1件だけ。追加のビヘイビア・Lambda@Edge・CloudFront Functions・カスタムエラー応答・オリジングループ・既定のルートオブジェクトは設定しない |
| オリジンリクエストポリシー | `AllViewerExceptHostHeader`（Managed）。ID `b689b0a8-53d0-40ab-baf2-68738e2966ac` | Hostヘッダを転送しない（関数URLのホストでの署名のため） |
| 継続的デプロイ | 使わない（ステージングのディストリビューション・継続的デプロイのポリシーなし） | 本番と別の設定でトラフィックが流れ得るため、検証コードは拒否する |
| WAF | CLOUDFRONT scope（us-east-1）。デフォルト動作 `Allow`。規則は `infra/cd/waf-rules.json` の**必須の1件**（接続元IPごと）と、任意で既知のAWSマネージド規則グループ3種（`Count` で観測だけ）を0〜3件（合計1〜4件）。Firewall Managerの管理・前処理/後処理の規則グループなし | 下の「WAFの規則」参照。必須の1件が欠けた・変わった・重複した場合、全体（`CONSTANT`）の規則など未対応・未知の規則・規則グループ、Count以外の規則グループ、Firewall Managerの規則グループがあると、検証コードは拒否する |
| 関数のリソースポリシー | 最後に、このディストリビューションだけに `lambda:InvokeFunctionUrl` と `lambda:InvokeFunction` を許可 | 下の「最後の許可」参照。公開の許可は、ここまでの確認が終わるまで作らない |

アセットのキャッシュ（ハッシュ付きファイルだけを長期キャッシュ）は**将来案で未実装**です。初期はキャッシュしません。`health`・`release`・HTMLは今後もキャッシュしません。

### WAFの規則

| 規則 | 内容 |
| --- | --- |
| IPごと（必須・1件だけ） | レートベース、実際の接続元IP（`AggregateKeyType=IP`）、300件/300秒（`EvaluationWindowSec=300`）、超過はBlock。`ForwardedIPConfig`・`ScopeDownStatement`・独自の集計キー・`RuleLabels`・独自応答はなし |

履歴: 2026-11-09の計画では、全体のレート制限（`AggregateKeyType=CONSTANT`、1,500件/5分、`ScopeDownStatement` つき）も必須としていました。2026-11-10に利用者の承認で要件から外し、テンプレート・検証コードからも削除しました（検証コードは、全体の規則が残っていれば未対応の規則として拒否します）。

レート制限は近似です（検出に遅れがあり、共有IPなどの巻き添えもあり得る。公式の注意点を参照）。Bot ControlやJavaScriptチャレンジは含めません。しきい値の根拠は、現状のデモの想定負荷に対する独自の判断で、公式の推奨値ではありません。

#### 同時実行数1の意味（全体の規則の代わりではない）

AWSの公式資料（Lambda関数URLのスロットリング。Codexが2026-11-10に公式の本文を確認）では、関数URLの最大のリクエスト数は、予約済み同時実行数の10倍のRPSで、超えたリクエストは429になります。予約済み同時実行数1なら、最大10RPSです。

- これは、外した全体1,500件/5分（平均5RPS相当）の規則と同じではありません。上限の考え方も単位も違い、10RPSが続けば計算上は月に約2,592万リクエスト（10 × 86,400秒 × 30日）まで関数に届き得ます。
- 月額の上限でもありません。CloudFront・Lambda・ログなどの料金は、届いたリクエストの量に応じて発生し得ます。
- IPごとの規則（300件/300秒、平均1RPS相当）は、多数のIPから分散したアクセスでは効きません。10件以上のIPから分散すれば、IPごとの上限内のまま同時実行数1の上限に達し得ます。そのとき正規の利用者にも429が返り得ます。
- このため、分散したアクセスや、継続的なアクセスによるオリジン（Lambda）側の料金は残ります。異常時は、これまでどおり人が予約済み同時実行数を0にして止めます。429の応答に料金がかかるかは、この文書では確認していません。
- CDは予約済み同時実行数を自動で設定・変更しません。読み取りで1でなければ、変更の前に止まります（未設定・0・2以上など。結果JSONの `reservedConcurrency` に種類を記録）。

#### 任意の観測グループ（Count）

上の必須の1件は常に必要です。これに加えて、次の3種のAWSマネージド規則グループだけを、**観測専用（`Count`）** として0〜3件残してよいことにします（コンソールがWeb ACLの自動作成時に付け得るもの。採否はプロジェクト独自の判断で、AWSの推奨や必須事項ではありません）。

| 規則グループ（`VendorName` は `AWS`） | 条件 |
| --- | --- |
| `AWSManagedRulesAmazonIpReputationList` | 各1件まで（重複は不可） |
| `AWSManagedRulesCommonRuleSet` | 同上 |
| `AWSManagedRulesKnownBadInputsRuleSet` | 同上 |

- 規則グループの上書きは、ちょうど `OverrideAction` が `{"Count": {}}`。`Action`、別の上書き（`None` など）、`Count` の追加設定、`ScopeDownStatement`、`Version`、規則ごとの動作の上書き（`RuleActionOverrides`）、除外（`ExcludedRules`）、`RuleLabels` は不可です。APIが空の配列で返す任意の項目（`ExcludedRules`・`RuleActionOverrides`・`ManagedRuleGroupConfigs`）は、空なら許します。
- 規則は合計4件まで（必須1件＋観測3件）。ほかのベンダー・ほかの規則グループ・ほかの規則（全体の`CONSTANT`の規則を含む）は、件数が4件以内でも拒否します。
- 公式資料では、規則グループの動作を `Count` に上書きすると、一致しても終端せず評価が続きます（https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-rule-group-override-options.html ）。このため、観測グループの優先順位が必須の1件より前でも、そのBlockを迂回しない、とプロジェクトとして判断しています。実際のWeb ACLでの動作は未確認です。
- 検証コード（`scripts/deploy-lambda.py`）は、必須の1件をレートベースの内容（しきい値300・期間300秒・集計キー`IP`・`ForwardedIPConfig`と`ScopeDownStatement`なし・`Block`）で判定し、観測グループは別の判定で、上の条件に完全に一致するときだけ受け入れます。

## 費用（未確認。合計金額は出さない）

| 案 | 内容 | 状態 |
| --- | --- | --- |
| 既定（第一候補）: CloudFront Flat-rateプラン「Free」 | 公式資料では月$0（100万リクエスト・100GBの範囲。2026-11-09にCodexが確認した記載の引き継ぎ。WAFの機能・規則数がどこまで含まれるかは、この文書では約束しない）。独自ドメイン不要。**コンソールでディストリビューションとWAFを一緒に作る**（下の手順1） | Freeを選択できることは利用者の報告。Web ACLは作成済み（利用者の報告）。利用アカウントでの契約・適用可否、Lambda関数URL + OACとの組み合わせは**未確認**。全体の規則（URIパス・HTTPメソッドの条件つき）は保存が `WAFFeatureNotIncludedInPricingPlanException` で失敗した（利用者の報告。対象の機能は未特定）。確認できるまで「無料」とは言わない |
| Lambda・ログ | アプリのLambda（呼び出し・実行時間）とCloudWatch Logsは**別料金**。Freeプランに含まれるとは確認していない | 既存の無料利用枠の対象かも未確認 |
| 代替（有料）: 従量課金のWAF（Web ACLを単独で作る） | Web ACLと規則の件数に応じた月額＋リクエスト課金（以前の試算「Web ACL 1件＋規則2件で基本約$7/月＋$0.60/百万リクエスト」は2規則の前提の履歴で、規則1件での金額は再確認していない）。これにCloudFront（従量）・Lambda・ログ・税が加わる。**Web ACLを単独で先に作ると、ディストリビューションに紐付ける前から従量の費用が生じ得る** | **Freeを使えない場合だけ**、この構成の費用を人に示して判断を求める。既定の手順ではWeb ACLを単独で作らない |

Freeに適合しない場合・契約できない場合も、**自動で有料プラン・従量課金へ切り替えません**。その時点の構成・概算・影響を示して、人が判断します（月0円・月1,000円は停止条件ではないが、有料化は人の判断）。

## 手順案（順序を守る。1つずつ実行し、出力を人が確認する）

実行のルールは `infra/cd/bootstrap.md` の「実行のルール」と同じです（専用セッション、`set -euo pipefail`、1ブロックずつ、失敗したら以降を貼らない、実在のIDを出力・チャットに貼らない）。リージョンは、CloudFront・WAFのAPIがus-east-1、Lambdaが東京（ap-northeast-1）で、**コマンドごとに明示します**。

既定の手順（Free）: 0（初回構築の前の確認。作成済みなら適用しない）→ 0の2（OACの読み取り確認）→ 1（ディストリビューションとWAFの作成。作成済みなら作らず、読み取りで確認）→ 2（作成済みの専用Web ACLを確認・調整）→ 3（OAC・キャッシュなど全設定を確認）→ 4（読み取りで再確認）→ 5（特定のディストリビューションだけにLambdaの許可を最後に追加）→ 6（公開後の確認）。Web ACLを単独で作るCLIの案は、有料になり得る代替として末尾に分けて置きます。

### 0. 読み取りの確認（変更なし。初回構築の前だけ）

この手順の「期待: 無い」は、**初回構築の前**（履歴）の確認です。ディストリビューションとWeb ACLが作成済み（利用者の報告）の現在は**適用しません**。作成済みのリソースは再作成・削除せず、手順0の2・2・3・4で読み取りで確認します。関数URLの確認（3つ目のコマンド）だけは、現在も使えます。

```bash
set -euo pipefail
die() { echo "停止: $*" >&2; exit 1; }
FN='REPLACE_FUNCTION_NAME'
[[ "$FN" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]{0,56}$ ]] || die "FN の形式が不正"

aws cloudfront list-distributions --region us-east-1 --query 'DistributionList.Items[].Id' --output text   # 期待: 無い（null）
aws wafv2 list-web-acls --scope CLOUDFRONT --region us-east-1 --query 'WebACLs[].Name' --output text        # 期待: 無い
aws lambda get-function-url-config --function-name "$FN" --qualifier demo --region ap-northeast-1 \
  --query '{auth:AuthType,mode:InvokeMode}' --output json                                                  # 期待: AWS_IAM / BUFFERED
```

出力が期待と違えば、以降へ進まず、人が既存のリソースを確認します（再利用・上書き・削除は自動で行わない）。関数URLが `AWS_IAM`・`BUFFERED` でなければ、人が判断します（既にある関数URLを自動で作り直さない）。

### 0の2. OACの確認（変更なし）

OACは利用者の報告では**作成済み**（`lambda`・`sigv4`・`always`）です。作らず、読み取りでIDと値を確認します（フィールド名は `aws cloudfront list-origin-access-controls help` で確認。未実行・未確認）。

```bash
aws cloudfront list-origin-access-controls --region us-east-1 \
  --query 'OriginAccessControlList.Items[].{id:Id,name:Name,type:OriginAccessControlOriginType,behavior:SigningBehavior,protocol:SigningProtocol}' \
  --output json
```

出力にはIDが含まれるため、チャット・文書に貼りません。人が次のとおり判断します（自動の上書き・削除・重複作成はしない）。

- 種類が `lambda`、署名の動作が `always`、署名が `sigv4` のOACが**1件**ある: そのIDを控えます（リポジトリ変数 `CLOUDFRONT_OAC_ID`）。**手順1で、そのOACを選択して再利用**します。新しいOACは作りません。
- 該当するOACが複数ある、または一部の値が違う（`never`・`no-override`・別の種類など）: 進まず、人が判断します。既存のOACを書き換えず、重複して作りません。
- 該当するOACが**無いと、この出力で人が確認できた**: 末尾の「OACを単独で作る」を、人が内容を確認して実行します（手順1より前）。コンソールの自動作成には頼りません。作成後にこの確認をやり直し、IDを控えます。
- 取得に失敗した（権限・通信など）: 無いとは推測せず、止まって人が判断します。

### 1. ディストリビューションとWeb ACLをコンソールで作る（Free・既定）

**この手順は、初回構築（最初の作成）だけに適用します。ディストリビューションとWeb ACLは作成済み（利用者の報告）なので、現在は作成せず・削除せず、飛ばして手順2から読み取り・調整で確認します。** 以下は、作成が必要な場合の記録です。

**コンソール**で、上の「設定」表のとおりにディストリビューションを作り、FreeプランでWAFを一緒に作ります。Web ACLを単独で先に作らないのは、紐付け前の従量の費用を避けるためです。Freeプランを選択できることは利用者の報告ですが、**WAFを同時に作れるかは報告がなく未確認**で、実際のアカウントでの適合・契約・費用も**未確認**です。オリジンのOACは、**手順0の2で確認した作成済みのOACを選択**します（コンソールが新しいOACを自動で作る設定には頼らず、既存のOACを上書き・重複作成しません）。独自ドメイン・追加のビヘイビア・関数の関連付けは設定しません。

- **Lambdaの許可は付けない**: コンソールが関数のリソースポリシーへ許可を自動で追加するか、追加を避けられるかは**未確認**です。追加しない選択があれば選びます。**自動の追加を避けられない場合は、ここで止まり**、人が制御できる手順（公開の許可を最後に特定のディストリビューションだけに付ける順序を守れるか）を確認します。確認できないまま進めません。
- この段階では、関数のリソースポリシーに許可がないため、CloudFront経由でも403が返るのが正常です。作成後、状態が `Deployed` になるまで待ちます。
- コンソールの画面で、手順0の2で確認したOAC以外が選ばれていたり、新しいOACの作成が既定で選ばれていたりする場合は、そのOACを選び直します。選び直せない場合は止まり、人が判断します。
- Freeに選べない・WAFを同時に作れない・適合しない場合は、**ここで止め**、費用の案（上の表）を人が判断します。有料の代替は末尾の「代替（有料）」で、自動では実行しません。

ディストリビューションIDがリポジトリ変数 `CLOUDFRONT_DISTRIBUTION_ID`、`https://<ドメイン>` のオリジンが `CD_BASE_URL` です。

### 2. 新規に作られた専用Web ACLを確認・調整する

初回構築の前の確認（手順0。履歴）では、既存のWeb ACLは0件でした。現在は、作成済みの**この1件の専用Web ACLだけ**を対象にし、既存のWeb ACLや他のリソースには触れません。コンソールで次を確認し、違えば調整します。

- 規則に `infra/cd/waf-rules.json` の**必須の1件**（実際の接続元IPごと 300件/300秒、Block）がちょうど1件あり、デフォルト動作が `Allow`。全体（`CONSTANT`）の規則は**追加しません**（要件から外した。残っていると検証コードが拒否します）。
- 必須の1件以外は、上の「任意の観測グループ（Count）」の3種だけ（0〜3件、各1件まで、`OverrideAction` が `Count`）。合計1〜4件。それ以外の規則・規則グループ、`Count` 以外の動作の規則グループがあると、検証コードが拒否し、そのままでは配布が止まります。調整できなければ止まり、人が判断します。
- Firewall Managerの管理・前処理/後処理の規則グループが付いていない。

**現在の状態（利用者の確認、2026-11-10）**: 作成済みのWeb ACLは、IPごとの規則（Block）1件と既知の観測グループ3種（`Count`）の**4件**で、全体の規則はありません。このWeb ACLを、作り直さず・削除せず・規則を追加せずにそのまま使い、上の条件（特にIPごとの規則が300件/300秒・接続元IP・Blockちょうど1件であること）を手順4の読み取りで確認するだけです。違っていた場合は、人がコンソールで直すか判断します（観測グループは外さず・動作を変えずに残します）。

- AWS CLIの `aws wafv2 update-web-acl` で、必須の1件**だけ**を `--rules` に渡して更新しないでください。`UpdateWebACL` は規則の一覧を**丸ごと置き換える**ため、渡さなかった観測グループが消えます（部分的な追加の操作ではありません）。CLIで更新する場合は、`get-web-acl` で得た現在の規則すべてを含む完全な一覧と `LockToken` を渡す必要があり、既定の手順ではありません（このリポジトリの手順では、CLIでの更新は案内しません。必要になったら人が判断します）。
- 履歴: 2026-11-09の時点では、ここで必須の2件（IPごと・全体）をコンソールで追加する手順でした。全体の規則は保存が `WAFFeatureNotIncludedInPricingPlanException` で失敗し（利用者の報告。対象の機能は未特定）、2026-11-10に要件から外しました。

作成されたWeb ACLのARNが `arn:aws:wafv2:us-east-1:<アカウント>:global/webacl/<名前>/<ID>` の形であることを確認します。**このARNがリポジトリ変数 `WAF_WEB_ACL_ARN`** です（名前は手順4の `WAF_NAME`、末尾のUUIDが `WAF_ID`）。

### 3. OAC・キャッシュなど、全設定を確認する

コンソールで、上の「設定」表の**すべて**（OAC `lambda`・`sigv4`・`always`、オリジン、`CachingDisabled`・`AllViewerExceptHostHeader` のManagedポリシー、メソッド、追加のビヘイビア・関数の関連付けなし、継続的デプロイ・ステージングなし）が一致することを確認します。OACは、手順0の2で確認した作成済みのOACを再利用します（コンソールの自動作成を前提にしません）。オリジンに関連付いたOACのIDが、手順0の2で控えたIDと一致することを確認します。このIDがリポジトリ変数 `CLOUDFRONT_OAC_ID` です。OACが無い場合の作成は末尾の「OACを単独で作る」で、手順1より前に行います（OACの作成に費用がかかるかは未確認で、作る前に人が確認します）。

### 4. 公開の前に、設定を確認する（読み取りのみ）

```bash
DIST_ID='REPLACE_DISTRIBUTION_ID'
OAC_ID='REPLACE_OAC_ID'
WAF_NAME='REPLACE_WEB_ACL_NAME'
WAF_ID='REPLACE_WEB_ACL_ID'      # ARNの末尾のUUID
[[ "$DIST_ID" =~ ^[A-Z0-9]{8,32}$ ]] || die "DIST_ID の形式が不正"
[[ "$OAC_ID" =~ ^[A-Z0-9]{8,32}$ ]] || die "OAC_ID の形式が不正"
[[ "$WAF_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die "WAF_ID の形式が不正"
[[ "$WAF_NAME" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die "WAF_NAME の形式が不正"

aws cloudfront get-distribution --id "$DIST_ID" --region us-east-1 --output json
aws cloudfront get-origin-access-control --id "$OAC_ID" --region us-east-1 --output json
aws wafv2 get-web-acl --name "$WAF_NAME" --scope CLOUDFRONT --id "$WAF_ID" --region us-east-1 --output json
```

出力（実在のIDを含むため、チャット・文書に貼らない）を人が読み、「設定」表と一致することを確認します。確認の項目: OACが `lambda`・`sigv4`・`always`、オリジンが1件・`https-only`・パスなし・関数URLのホスト・OAC関連付け、`CachingDisabled` と `AllViewerExceptHostHeader`、メソッドが `GET`・`HEAD`・`OPTIONS`、追加のビヘイビア・関数の関連付けなし、`ContinuousDeploymentPolicyId` が空・`Staging` が偽、WAFが関連付け済み・IPごとの規則がちょうど1件（`Limit` 300・`EvaluationWindowSec` 300・`AggregateKeyType` `IP`・`ScopeDownStatement` と `ForwardedIPConfig` なし・`Block`）、ほかは既知の観測グループ（`Count`）0〜3件だけ（合計1〜4件。全体の規則なし）、Firewall Managerの管理・規則グループがない。予約済み同時実行数が1であることも、`aws lambda get-function-concurrency --function-name "$FN" --region ap-northeast-1`（読み取り）で確認します。**同じ項目は、CDの配布時にも `scripts/deploy-lambda.py` が読み取りだけで再検証し、1つでも違えばAWSの変更をせずに止まります**（コードの検証であり、実際のAWSでの動作は未確認）。

### 5. 最後に、このディストリビューションだけに許可する

手順4がすべて期待どおりのときだけ、関数のリソースポリシーに、**特定のディストリビューションだけ**を許可する2件を追加します。これまで、公開の許可（`Principal: *`）は作っていません。

```bash
ACCOUNT_ID='REPLACE_ACCOUNT_ID'
[[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || die "ACCOUNT_ID は12桁"
DIST_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${DIST_ID}"

aws lambda add-permission --function-name "$FN" --qualifier demo --region ap-northeast-1 \
  --statement-id AllowCloudFrontInvokeFunctionUrl --action lambda:InvokeFunctionUrl \
  --principal cloudfront.amazonaws.com --source-arn "$DIST_ARN" --function-url-auth-type AWS_IAM
aws lambda add-permission --function-name "$FN" --qualifier demo --region ap-northeast-1 \
  --statement-id AllowCloudFrontInvokeFunction --action lambda:InvokeFunction \
  --principal cloudfront.amazonaws.com --source-arn "$DIST_ARN" --invoked-via-function-url
```

`--invoked-via-function-url` と `--function-url-auth-type` が、お使いのCLIで使えるかは `aws lambda add-permission help` で確認します（未確認）。`Principal: *`・`NONE` の許可は作りません。

これらが作る条件の形（公式CLIの説明に基づく想定。実際のAWSの出力は未確認）と、検証コード（`condition_ok`）が許すものは次のとおりです。

| 文 | 条件 | 検証コード |
| --- | --- | --- |
| `lambda:InvokeFunctionUrl` | `ArnLike` の `AWS:SourceArn`（このディストリビューションのARN）と、`StringEquals` の `lambda:FunctionUrlAuthType` = `AWS_IAM` | 許す。`AuthType` が `NONE`・複数値・ワイルドカード・`StringLike` などは拒否 |
| `lambda:InvokeFunction` | `ArnLike` の `AWS:SourceArn` と、`Bool` の `lambda:InvokedViaFunctionUrl` = `true` | 許す。`false`・未知の条件キー・未知の演算子は拒否 |

`AWS:SourceArn` は常に対象ディストリビューションのARNとの**完全一致**だけを許します（ワイルドカード・別のディストリビューション・複数値は拒否）。

### 6. 公開後の確認

1. 関数URLへの未署名の直接GETが、引き続き403であること（CloudFront経由だけが許可されること）。
2. CloudFront経由の初期画面が200、`GET /health` が200で本文 `{"status":"ok"}`、`/release` が承認したSHAであること。`CD_BASE_URL=https://<id>.cloudfront.net CD_EXPECTED_SHA=<40桁のSHA> bash scripts/cd-smoke.sh` で行います（実行は未実施）。
3. 予約済み同時実行数が1のままであること。
4. 失敗・異常があれば、直ちに予約済み同時実行数を0にして止めます（`infra/cd/bootstrap.md`「7.」）。許可（リソースポリシー）の削除は、人が必要と判断した場合の別操作で、自動では行いません。

## 代替（有料）: Web ACLをCLIで単独で作る

**Freeを使えない・適合しない場合だけ**、費用（上の表）を人が確認し、有料になることを承知した上で行います。既定の手順ではありません。Web ACLをディストリビューションより先に単独で作るため、**紐付け前から従量の費用が生じ得ます**。作成後は、手順2のとおり新規の専用Web ACLを確認し、手順3以降へ進みます。

`infra/cd/waf-rules.json` は `Rules` キーを持つオブジェクトで、`--rules` にはその**配列だけ**を渡します（ファイルを `file://` で渡さない）。中身はIPごとの規則1件だけで、blob（バイナリ）の項目はありません。

```bash
WAF_NAME='REPLACE_WEB_ACL_NAME'
[[ "$WAF_NAME" =~ ^[A-Za-z0-9_-]{1,128}$ ]] || die "WAF_NAME の形式が不正"

# .Rules の配列だけを取り出し、1件であることを確認してから渡す
RULES="$(jq -c '.Rules' infra/cd/waf-rules.json)"
jq -e 'type == "array" and length == 1' <<<"$RULES" >/dev/null || die "Rules が1件の配列ではない"

aws wafv2 create-web-acl --name "$WAF_NAME" --scope CLOUDFRONT --region us-east-1 \
  --default-action 'Allow={}' \
  --rules "$RULES" \
  --visibility-config "SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=${WAF_NAME}" \
  --query 'Summary.ARN' --output text
```

このコマンドは**AWS CLIの生データでの検証は未実施**です（`aws wafv2 create-web-acl help` で入力の形式を人が確認する。`--rules` の文字列入力の受理は未確認）。既存のWeb ACL（作成済み・4件）がある現在は使いません。作成された ARN が `arn:aws:wafv2:us-east-1:<アカウント>:global/webacl/<名前>/<ID>` の形であることを確認し、**このARNがリポジトリ変数 `WAF_WEB_ACL_ARN`** です。

## OACを単独で作る（手順0の2で、該当するOACが無いと人が確認できた場合だけ）

上の有料の代替とは別の節です（Free・既定の手順でも、OACが無い場合はここで作ります）。既に作成済み・作成中かもしれないOACを重複して作らないため、**手順0の2の確認を直前にやり直して**から実行します。同名のOACが既にあれば作成が失敗して止まるので、既存のものを再利用・上書きせず、人が判断します。フィールド名は `aws cloudfront create-origin-access-control help` で確認します（未実行・未確認）。

```bash
OAC_NAME='REPLACE_OAC_NAME'
[[ "$OAC_NAME" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "OAC_NAME の形式が不正"
aws cloudfront create-origin-access-control --region us-east-1 --origin-access-control-config \
  "Name=${OAC_NAME},SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=lambda" \
  --query 'OriginAccessControl.Id' --output text
```

## CDへの影響（実装済み・未実行）

- 公開URLはCloudFrontだけ。関数URLのホストは `get-function-url-config`（エイリアス `demo`）の実値から導き、CloudFrontのオリジンと一致することを検証する。
- リポジトリ変数: `CD_BASE_URL`（CloudFrontのオリジン）、`CLOUDFRONT_DISTRIBUTION_ID`、`CLOUDFRONT_OAC_ID`、`WAF_WEB_ACL_ARN`（us-east-1・globalのWeb ACLのARN）、ほかは従来どおり。**いずれもAWS作成後に人が設定する（未設定）**。
- 配布用ロールの追加権限は読み取りだけ: `cloudfront:GetDistribution`（対象のディストリビューション）、`cloudfront:GetOriginAccessControl`（OACの読み取りはリソース指定ができないため `Resource: *`）、`wafv2:GetWebACL`（対象のWeb ACL）、`lambda:GetPolicy`（エイリアス `demo`）、`lambda:GetFunctionConcurrency`（修飾子なしの対象関数だけ。別の文 `ReadTheDemoFunctionReservedConcurrencyOnly`）。CloudFront・WAF・IAMの書き込み、同時実行数の設定・削除（`PutFunctionConcurrency`・`DeleteFunctionConcurrency`）と `iam:PassRole` は含めない。
- 検証が失敗した場合は、AWSの変更を1件もせず、スモーク確認もせずに止まる。予約済み同時実行数がちょうど1でない場合（未設定・0・2以上・取得失敗）も同じで、結果JSONの `reservedConcurrency` に `missing`・`zero`・`not-one`・`read-failed` を記録する。
- 検証コードが拒否する項目（初期構成）: 必須のIPごとの規則の欠落・変更・重複、全体（`CONSTANT`）の規則など未対応の規則、既知の観測グループ（`Count`）3種以外のWAF規則・規則グループ、観測グループの重複・`Count` 以外の動作・絞り込みや上書き、合計5件以上の規則、Firewall Managerの管理・前処理/後処理の規則グループ、CloudFrontの継続的デプロイのポリシー・ステージング、`AuthType` が `AWS_IAM` 以外のLambdaの許可、ワイルドカードの `AWS:SourceArn` など。これらの項目名（`ManagedByFirewallManager`・`PreProcessFirewallManagerRuleGroups`・`PostProcessFirewallManagerRuleGroups`・`ContinuousDeploymentPolicyId`・`Staging`）について、Codexが2026-11-09に公式API本文で項目名（`Staging`・`ContinuousDeploymentPolicyId`・`ManagedByFirewallManager`・`PreProcessFirewallManagerRuleGroups`・`PostProcessFirewallManagerRuleGroups`）の存在を確認済みです。実際のAWS応答の値との突合は未実施です。

## 出典（2026-11-09にCodexが本文を確認した記録を引き継ぐ。この作業では再読していない）

- CloudFront Flat-rateプラン: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html
- CloudFront料金: https://aws.amazon.com/cloudfront/pricing/
- OACでLambda関数URLへのアクセスを制限: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html
- Managedキャッシュポリシー・オリジンリクエストポリシー: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html ・ https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html
- WAFのレートベース規則: https://docs.aws.amazon.com/waf/latest/APIReference/API_RateBasedStatement.html ・ https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based-caveats.html
- WAFのバイト一致: https://docs.aws.amazon.com/waf/latest/APIReference/API_ByteMatchStatement.html
- WAFの規則グループの上書き（`Count` は終端しない）: https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-rule-group-override-options.html
- WAF料金: https://aws.amazon.com/waf/pricing/
- Lambda関数URLの認可: https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
- Lambda関数URLのスロットリング（予約済み同時実行数の10倍のRPS、超過は429）: https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html#urls-throttling （Codexが2026-11-10に公式の本文を確認）
- AWS CLI（`lambda add-permission`、`wafv2 get-web-acl` のus-east-1・CLOUDFRONT scopeとblobの形式）はCodexが2026-11-09に確認した記録を引き継ぐ
- OpenAIの事例（運用の考え方の例のみ。AWSの設計はプロジェクト独自）: https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex （2026-11-10 JSTに検索と本文を確認した記録。合意した要件と検証の根拠を分けて残す例として参考にした）
