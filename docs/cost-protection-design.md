# 大量アクセスによるAWS料金の増大を防ぐ対策（調査・比較・推奨案）

2026-11-09作成（AWS公式資料の確認日も同日、日本時間）。公開デモへの大量アクセス、認証情報の漏えい、正当な画面の並列取得で料金が増えるリスクを整理し、対策を比較した文書です。

**この文書の位置づけ**

- 調査・比較・推奨案の文書です。**推奨案は提案であり、未合意です。** 閾値、公開範囲、認証方式、予算通知先は未決です。
- AWSの設定、小規模試験、通知の到達確認、緊急停止の実地確認は**一つも実施していません**。AWSの資格情報も使っていません（[未実施・人の判断待ちの一覧](#未実施人の判断承認待ち)）。
- 費用は、AWS公式の料金ページと技術文書に書かれた数値から式で組み立てた概算です。確認できなかった数値は空欄または「未確認」とし、推測で埋めていません。為替レートも置いていません。
- 構成は合意済みの前提（Lambda ZIP、FastAPI、Aurora DSQL。[要件](requirements.md)・[配布の運用](development-workflow.md#検証環境への配布)）を変えない案を基本にします。変更が必要なものは「構成変更の提案」と明記します。

## 1. 前提（合意済みの内容と、この文書の仮定）

| 項目 | 内容 | 出所 |
| --- | --- | --- |
| 構成 | Lambda（ZIP）上のFastAPIが静的画面とAPIを配信。DBはAurora DSQL（初回配布では作らない） | 合意済み |
| 公開方法 | Lambda関数URL（認証なし）。架空データのみを扱う誰でもアクセスできるデモで、常時公開を目指す | 配布設計の合意（2026-11-08）。実行は未実施 |
| 現行の設計値 | 512MiB、予約済み同時実行数1、タイムアウト10秒、ログ保持7日、プロビジョニング済み同時実行なし | 配布設計の合意。AWSでの設定は未実施 |
| 費用の目標 | 月0円は目標で、保証でも停止条件でもない。利用者の許容上限は最大1,000円との回答 | 利用者の回答 |
| 公開停止 | 予約済み同時実行数を0にする（人が行う）。自動停止は実装しない | 配布設計の合意 |
| アカウント種別 | 古いアカウントのため従量課金・無料利用枠のアカウントと仮定。実際の種別と無料枠の対象は**未確認** | 仮定 |
| リージョン | 東京（ap-northeast-1） | 利用者の回答 |

この文書の費用は、特に断らない限りAWS公式ページに載っていたUSD建ての価格です。**東京リージョンの価格は、確認できた範囲ではLambda・CloudWatch Logs・Aurora DSQLのいずれも載っておらず**、US EastまたはOhioの価格を「参考値」として使います。東京の価格は人がAWSの料金ページで確認してください。

## 2. 公開経路と課金対象の洗い出し

| 経路・要素 | 課金されるもの | 確認した公式の根拠 | 大量アクセス時の挙動 |
| --- | --- | --- | --- |
| Lambda関数URL（認証なし）→ Lambda | 呼び出し回数、実行時間（GB秒）。関数URL自体の追加料金はない。初期化（Init）の時間も課金対象 | 関数URLの追加料金なし（[HTTP呼び出し方法の比較](https://docs.aws.amazon.com/lambda/latest/dg/furls-http-invoke-decision.html)）、料金（[Lambda料金](https://aws.amazon.com/lambda/pricing/)）、Init課金（[実行環境のライフサイクル](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html)） | 同時実行数の上限を超えた要求は429。上限内の要求は課金される |
| Lambdaからのデータ転送 | 同一リージョン内の特定サービス間は無料。それ以外の外向きはEC2のデータ転送料金 | [Lambda料金](https://aws.amazon.com/lambda/pricing/) | 画面アセットやAPI応答のサイズ×回数で増える。東京のEC2転送料金は**未確認** |
| CloudWatch Logs | 取り込み（US Eastの例で1GBあたり0.50USD）、保存。無料枠は取り込み・保存・クエリで合計5GB／月 | [CloudWatch料金](https://aws.amazon.com/cloudwatch/pricing/) | 1回の呼び出しごとにログが出る。保持期間を短くしても取り込み料金は減らない |
| CloudWatchアラーム | 標準解像度のアラーム10個まで無料。超過分はUS Eastの例で1アラームメトリクスあたり月0.10USD | [CloudWatch料金](https://aws.amazon.com/cloudwatch/pricing/) | 通知用。課金は固定費に近い |
| Aurora DSQL（作成後） | DPU（Ohioで100万DPUあたり8USD）、保存（1GB月あたり0.33USD）。毎月の最初の100,000 DPUと1GBは無料。リージョン外・インターネット向けの転送は標準転送料金 | [Aurora DSQL料金](https://aws.amazon.com/rds/aurora/dsql/pricing/) | このアプリの実DPUは**未測定**。受付処理や保存のたびにDPUが増える |
| GitHub Actions・成果物 | AWS外（GitHub側の上限） | — | 公開URLへのアクセスとは無関係。この文書では扱わない |
| Lambdaのバージョン・ZIP | 現行設計ではS3・ECRを作らない。Lambdaのコード保管の費用は未確認 | 配布設計 | 大量アクセスでは増えない |
| AWS Budgets | 監視と通知は無料（配布設計が2026-11-08に確認済み）。予算アクションの料金は今回確認していない | [Budgetsの概要](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html) | 通知のみ。上限ではない（4章） |

## 3. 想定シナリオと影響

| # | 想定 | 経路 | 主な影響 | 現行設計（予約済み同時実行数1・10秒）での見立て |
| --- | --- | --- | --- | --- |
| S1 | 悪意ある大量アクセス（関数URLを知られた、またはスキャンされた） | 関数URL | 呼び出し、実行時間、ログ、外向き転送の増加。429を返す分にも通知・監視の負荷 | 同時実行数1・最大およそ10リクエスト／秒（4章参照）に抑えられる。ただし**総リクエスト数や料金の上限ではない**。正当な利用者も429になる（S3） |
| S2 | 認証情報の漏えい（AWSのアクセスキー、配布用ロール、利用者のアカウント） | AWS API | 同時実行数やログに関係なく、任意のAWSサービスの作成・課金が起こり得る。予約済み同時実行数の変更・削除も権限があれば可能 | 同時実行数1では守れない。Budgetsは通知のみ。権限の最小化、長期キーを作らない、MFA、漏えい時の失効手順が必要（8.1） |
| S3 | 正当な画面の並列取得（ブラウザがJS・CSS・APIを同時取得、複数の担当者が同時に操作） | 関数URL | 同時実行数1だと並列の要求が429になり、画面が壊れる・設問が確認待ちになる | **誤遮断が起きやすい**。上限値は課金と画面の動作のトレードオフ（5.1） |
| S4 | アプリ自身のHTTP呼び出し（受付時に自分のモックAPIを関数URL経由で呼ぶ実装にした場合） | 関数URL → Lambda → 関数URL | 1回の受付が2つの同時実行を必要とし、同時実行数1では自己呼び出しが429になり得る。呼び出しが増えれば課金も増える | 呼び出し方式（プロセス内か、関数URLか）は未決のため、実装計画で確認が必要（9章） |
| S5 | タイムアウト・無限ループ・重い処理 | アプリ内 | 1回あたり最大10秒（512MiBで5GB秒） | タイムアウトが上限。10秒は現行設計値で、実測に基づく見直しは未実施 |
| S6 | 公開停止後に残る課金 | — | ログの保存、DSQLの保存量、すでに発生した転送など | 7章 |
| S7 | 迂回経路（後でCloudFrontやAPI Gatewayを前段に置いても、関数URLが公開のままなら直接アクセスできる） | 関数URL | 前段のWAFやレート制限を迂回される | 前段を置く場合は関数URLを認証付きにして直接アクセスを閉じる必要がある（5.3） |

## 4. 公式資料から確認できた制約（対策の前提）

- **予約済み同時実行数は、総リクエスト数や料金の上限ではありません。** 同時に処理する数を制限し、超えた要求は429になります。関数URLでは、最大のリクエスト率は予約済み同時実行数の10倍とされています（例: 100なら1,000リクエスト／秒）。予約済み同時実行数の設定自体に追加料金はありません。0にすると全リクエストが429になり、緊急停止に使えます（[関数URLの設定](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)・[予約済み同時実行数](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)）。
- **AWS Budgetsは通知であり、強制上限ではありません。** 情報の更新は1日最大3回で、前回から通常8〜12時間後です。公式も、通知前に予算の閾値を超える費用や利用が発生し得て、通知後も増減し続け得ると述べています（[Budgetsの概要](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)）。予算アクションとして確認できた内容は、IAMポリシーやSCPの適用、EC2・RDSインスタンスの対象指定です（[予算アクション](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html)）。Lambdaの同時実行数を自動で0にする機能は、確認した資料には記載がありません。
- **関数URLに認証なし（NONE）を使うと、URLを知っている誰でも関数を呼び出せます**（[アクセス制御](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)）。`AWS_IAM`にすると、SigV4署名したAWSの認証情報が必要です。通常のブラウザは署名できません。
- **AWS WAFを直接つなげるリソースの一覧に、Lambda関数URLは含まれていません**（CloudFront、API GatewayのREST API、ALB、AppSyncなど。[AWS WAF](https://docs.aws.amazon.com/waf/latest/developerguide/waf-chapter.html)）。関数URLにWAFを使うには、CloudFrontなどを前段に置く必要があります。
- **API Gatewayの絞り込みは「目標」であり、保証された上限ではありません**（best-effortで、トークンバケット方式。[HTTP APIのスロットリング](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html)）。
- **CloudFrontからLambda関数URLを保護するOAC（オリジンアクセスコントロール）は、関数URLを`AWS_IAM`にする前提です。** PUT・POSTでは、クライアントがボディのSHA-256を計算して`x-amz-content-sha256`ヘッダーに入れる必要があります（Lambdaは署名なしペイロードをサポートしない。[OAC（Lambda関数URL）](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html)）。このアプリのPOST（受付、判定の保存）に画面側の変更が必要になる可能性があります。
- 関数URLのCloudWatchメトリクスは`UrlRequestCount`・`Url4xxCount`・`Url5xxCount`・`UrlRequestLatency`です。`InvokeFunctionUrl`はCloudTrailのデータイベントで、既定では記録されません（[関数URLの監視](https://docs.aws.amazon.com/lambda/latest/dg/urls-monitoring.html)）。429が`Url4xxCount`に含まれるかは、確認した資料に記載がなく**未確認**です。

## 5. 対策の比較

比較の列は、利用形態への適合、追加料金（固定費・従量）、迂回できる経路、誤遮断、運用負荷です。費用はUSD、参考値の価格を使います。

### 5.1 比較表

| 対策 | 利用形態への適合 | 追加料金（固定費／従量） | 迂回できる経路・守れないもの | 誤遮断のリスク | 運用負荷 |
| --- | --- | --- | --- | --- | --- |
| A. 予約済み同時実行数（現行1） | 現構成のまま使える | 固定費0・従量0（設定に追加料金なし） | 総リクエスト数・料金の上限ではない。ログ・転送・DSQLは別。認証情報の漏えいは防げない | **高い**。1だと画面の並列取得が429になり得る | 低い。値の調整と、0にする緊急停止 |
| B. タイムアウト（現行10秒） | 現構成のまま | 0 | 1回あたりの上限だけ。回数は減らない | 重い処理が10秒を超えると失敗 | 低い。実測で見直す |
| C. ログ保持7日・ログ量の抑制 | 現構成のまま | 保存は減るが、取り込み料金（1GBあたり0.50USD、US East参考）は減らない。5GBまで無料（他のLambdaと合算） | 取り込みは呼び出しごと | なし | 低い |
| D. アラーム（CloudWatch、Budgets） | 追加構成なし | Budgetsの通知は無料。CloudWatchアラームは10個まで無料 | 通知だけ。遅延あり。通知先が見ていなければ無効 | なし | 中。通知先の運用と、閾値の調整。時間外の対応は未決 |
| E. 緊急停止（予約済み同時実行数0） | 現構成のまま | 0 | 停止後も残る課金あり（7章） | 全員が429になる（意図的） | 低い。人が実行し、再開も人 |
| F. 関数URLを認証付き（`AWS_IAM`）にする | 「誰でもアクセスできる公開」と両立しない。運用者だけが使う用途なら適合 | 0 | 認証情報の漏えいは別問題 | ブラウザの通常アクセスは403 | 中。署名できるクライアントが必要 |
| G. アプリ内の認証（FastAPI側のキー、ログインなど） | 初版は「ログインなし・担当者1名固定」で合意済みのため、構成変更の提案になる | 0 | **未認証の要求もLambdaを起動するため、呼び出し・実行時間の課金は防げない**。拒否の応答のコストだけが小さくなる | 鍵の配布方法しだい | 中〜高 |
| H. CloudFront（OAC＋関数URLを`AWS_IAM`）＋WAF | 構成変更の提案。公開は維持でき、関数URLへの直接アクセスを閉じられる | 下の5.2 | OACが正しく設定されていれば関数URLへの直接アクセスは403。CloudFrontのドメインは公開のまま | レート制限の閾値しだい | 中〜高 |
| I. API Gateway（HTTP API／REST API）＋関数URLを閉じる | 構成変更の提案 | HTTP APIは1,000,000件あたり1.00USDの例（公式ページの例示）、RESTは3.50USD。無料枠は新規顧客の12か月で、古いアカウントでは**適用されない前提** | スロットリングは保証でなく目標。関数URLを閉じないと迂回される。WAFはREST APIで使う想定（HTTP APIでの可否は**未確認**） | 閾値しだい | 中 |

### 5.2 Hの費用比較（月0円目標との関係）

| 構成 | 固定費 | 従量 | 確認できた制約・未確認 |
| --- | --- | --- | --- |
| CloudFrontの定額プラン「Free」 | 月0USD | 追加の超過料金なし | 月100万リクエスト、100GB、WAFルール5個、S3 5GB。DDoS対策あり。**上限に達したときの挙動（制限・遮断）は公式ページに記載がなく未確認**。関数URLをオリジンにできるか、レート制限ルールを使えるかも**未確認**。他の割引・特典との併用は不可（[CloudFront料金](https://aws.amazon.com/cloudfront/pricing/)） |
| CloudFrontの従量課金 | 0 | 毎月、データ転送1TBとHTTP・HTTPSリクエスト1,000万件まで無料。超過は日本でHTTPS 1万件あたり0.0120USD、インターネット向けは1TB超から1GBあたり0.114USD。AWSオリジンからの取得は無料 | [従量課金の料金](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/)。関数URLが「AWSオリジン」に当たるかは**未確認** |
| AWS WAF（CloudFrontに付ける。従量課金時） | Web ACL 1個で月5.00USD＋ルール1個で月1.00USD（最小でおよそ月6USD） | 100万リクエストあたり0.60USD。ログを出すとCloudWatch Logsの料金も発生 | 一般的な無料枠の記載は確認できず。レート制限ルールの別料金は記載なし（ルールとして1.00USD）。**月0円目標とは両立しない**（従量課金で単独に使う場合）（[WAF料金](https://aws.amazon.com/waf/pricing/)） |

月0円の目標との関係では、「CloudFront Freeプランで関数URLをオリジンにでき、WAFのレート制限ルールまで使える」と確認できた場合に限り、追加固定費0で前段を置ける可能性があります。確認できるまでは、固定費0の保証はありません。

### 5.3 迂回できる経路の整理

- 前段（CloudFront、API Gateway）を置いても、関数URLが認証なしのままなら、関数URLへ直接アクセスされて前段のレート制限・WAFを迂回されます。関数URLを`AWS_IAM`にして直接アクセスを閉じるまで、守れていません。
- 関数URLを`AWS_IAM`に変えると、現行の配布の事前確認（関数URLが認証なしであることを前提にしたスモーク確認や設定検証）が合わなくなります。配布担当との調整が必要です（9章）。
- 認証付きの関数を、権限のある認証情報で直接Invoke APIから呼ぶ経路は、関数URLと別に残ります。

## 6. 費用の概算

式と前提を示します。実際の請求を予測するものではありません。価格は参考値（US East）で、東京の価格は未確認です。

### 6.1 Lambda

価格（参考値）: リクエスト 100万件あたり0.20USD、実行時間 0.0000166667USD／GB秒（x86）。無料枠は毎月100万リクエストと40万GB秒で、アカウント全体で共有（既存のAmplify用の関数4件など、他の関数の使用量と合算。[Lambda料金](https://aws.amazon.com/lambda/pricing/)）。

- **通常のデモ**（配布設計の試算）: 月10,000回、平均1秒、512MiB → 0.5GB × 1秒 × 10,000回 = 5,000GB秒。無料枠の範囲内なら0USD。
- **同時実行数1・攻撃が止まらない月（天井の概算）**: 30日 = 2,592,000秒。
  - 実行時間: 同時実行数1なので、最大 2,592,000秒 × 0.5GB = 1,296,000GB秒。無料枠40万GB秒を使える場合は（1,296,000 − 400,000）× 0.0000166667 ≈ **14.93USD**。無料枠が他で使われている場合は 1,296,000 × 0.0000166667 ≈ **21.60USD**。
  - リクエスト: 最大およそ10リクエスト／秒 × 2,592,000秒 = 25,920,000件。無料枠100万件を使える場合は（25,920,000 − 1,000,000）× 0.20／1,000,000 ≈ **4.98USD**。使えない場合は ≈ **5.18USD**。
  - 実行時間とリクエストの両方が同時に最大になることはない（短い要求が多いほど件数は増え、1件あたりの時間は短い）ため、両者の合計（無料枠あり≈19.9USD、なし≈26.8USD）は、Lambda分だけの保守的な上限の目安です。
  - この見積もりは、同時実行数1と10倍ルールが働くことを前提にした**導出値で、請求の保証ではありません**。Initの時間、ログ、転送、DSQL、他のサービスを含みません。

### 6.2 ログ・転送・DSQLと、含まれないもの

- **ログ**: 取り込み量 = リクエスト数 × 1件あたりのログのバイト数。例（仮定）として1件1KBなら、25,920,000件で約26GB。US Eastの参考値0.50USD／GBで約13USD（無料枠5GB分を引く前）。1件のバイト数は**未測定**で、仮定です。
- **データ転送**: 転送量 = リクエスト数 × 応答サイズ。東京のLambdaからのインターネット向け転送単価は**未確認**のため、金額は出しません。**総リクエスト数に比例する費用で、同時実行数の制限では上限になりません**（応答サイズが大きい画面アセットほど影響が大きい）。実際の応答サイズの実測が必要です。
- **Aurora DSQL**: 月間DPU × 8USD／100万（Ohioの参考値）＋ 保存GB × 0.33USD。無料枠は100,000 DPUと1GB。このアプリのDPUは**未測定**です。
- **含まれないもの**: 認証情報の漏えい（S2）による別サービスの課金。Budgetsの通知遅延中の増加。為替・税。

### 6.3 対策自体の費用（月0円目標との比較）

| 対策 | 固定費 | 従量 |
| --- | --- | --- |
| A〜C、E（現構成の設定） | 0 | 0 |
| D アラーム | 0（Budgets通知は無料、CloudWatchアラームは10個まで無料） | 0 |
| H CloudFront Free＋OAC | 0（ただし、適用できるかは未確認） | 追加の超過料金なし（上限到達時の挙動は未確認） |
| H CloudFront従量＋WAF | およそ月6USD以上 | WAFはリクエスト100万件あたり0.60USD、CloudFrontは無料枠超過分 |
| I API Gateway（HTTP API） | 0 | 100万件あたり1.00USD程度（例示の価格。無料枠は新規顧客のみ） |
| G アプリ内認証 | 0（開発コストは別） | 拒否する要求にも通常のLambda課金 |

## 7. 緊急停止・停止後に残る課金・保護できない経路

**緊急停止の方法（案）**

1. **予約済み同時実行数を0にする（推奨・現行の手順）**。全リクエストが429になる。URLは変わらず、再開は予約済み同時実行数を戻すか削除する（[関数URLの停止](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)）。人が実行し、再開も人の承認後。
2. 関数URLの削除: 元に戻せず、作り直すとURLが変わる。リソースベースポリシーは自動で消えないため手動削除が要る（同上）。復旧が重いため、通常の停止には使わない。
3. 前段（CloudFront等）を置いた場合は、前段の無効化も手段になるが、関数URLが閉じていることが前提。

**停止しても残る課金・止まらないもの**

- 停止前に発生済みの呼び出し、実行時間、転送の費用。
- CloudWatch Logsの保存（保持期間7日の間）と、アラーム・（使っていれば）CloudFront・WAFの固定費。
- Aurora DSQLの保存量（作成後）。
- 429応答の処理自体の費用の有無は、確認した資料に記載がなく**未確認**。
- 認証情報が漏えいしている場合は、停止操作の前後に別サービスが課金され続ける。

**保護できない経路**

- 認証情報の漏えい（予約済み同時実行数の変更権限を持つ者による解除を含む）。
- 前段を置いた後も開いている関数URL。
- Budgetsの通知遅延（1日最大3回の更新）。

## 8. 推奨案（提案・未合意）

どれも**提案であり、利用者が合意するまで実施しません**。

### 8.1 第1段階（構成変更なし・追加固定費0）

1. 現行の設計値（予約済み同時実行数、10秒、ログ7日）を維持する。ただし**予約済み同時実行数1が画面の並列取得とモックの呼び出し方式（S3・S4）を満たすかを、実装後に小規模な試験で確認し、必要なら2〜数個へ引き上げる判断を人が行う**。上げるほど、攻撃時の費用の天井（6.1）も比例して上がる。
2. ログは必要最小限にし、リクエスト本文や大きな応答をログに出さない。
3. 通知を置く: Budgetsの月次予算通知（閾値はUSD。為替・税は人が決める）と、CloudWatchの`UrlRequestCount`などのアラーム（無料の10個の範囲）。**通知は強制上限ではなく、遅れて届く**前提で、通知先と見る頻度を人が決める。
4. 緊急停止は予約済み同時実行数0とし、手順・実行者・再開の承認を文書化する（実地確認は未実施）。
5. 認証情報は長期のアクセスキーを作らず、配布用ロールの権限を最小化する（現行の配布設計と同じ方針）。

この段階の残存リスク: 総リクエスト数・データ転送の上限がない。関数URLが公開のままで、攻撃中は正当な利用者も429になる。通知は遅い。

### 8.2 第2段階（条件付き・構成変更の提案）

関数URLが知られて攻撃が現実になった場合、または常時公開の期間が長くなる場合に検討する。**CloudFront（Freeプラン）＋OACで関数URLを`AWS_IAM`に閉じる**案を、次が確認できたときだけ進める。

- Freeプランで関数URLをオリジンにでき、WAFのレート制限ルールが使えること。
- 上限（100万リクエスト・100GB）に達したときの挙動を、許容できること。
- 画面のPOSTにSHA-256ヘッダーを付けられること（OACの要件）。
- 配布の事前確認とスモーク確認が、新しい構成に合わせて変更できること（配布担当との調整が必要）。

確認できない、または固定費が出る場合は、第1段階のまま、通知と緊急停止で運用する。**WAFの単独利用（月およそ6USD以上）とAPI Gatewayは、月0円の目標と合わないため推奨しません**。ただし、許容上限（最大1,000円）の範囲で人が固定費を許容するなら、候補として残ります。

## 9. 配布担当・DB担当との調整が必要な点

この文書の対策は、配布の設定（関数URL、同時実行数、ログ）とDBの設定に隣接します。**重複・競合を避けるため、次を調整してから実施します。**

| 調整先 | 内容 |
| --- | --- |
| 配布担当 | 予約済み同時実行数・ログ保持・関数URLの認証方式は、配布の初回構築手順（[手順書](../infra/cd/bootstrap.md)）が所有している。この文書は値を決めず、変更が必要なら提案として渡す |
| 配布担当 | 関数URLを`AWS_IAM`にする、またはCloudFrontを前段に置く場合、配布の事前確認とスモーク確認（認証なしの関数URLを前提）の変更が必要 |
| 配布担当 | 公開停止の手順は配布設計にあり、この文書の緊急停止はそれと同じ操作（予約済み同時実行数0）。手順を二重に作らない |
| DB担当 | DSQLのクラスタ作成・接続・権限は別担当。この文書は料金の確認と、DPUの実測が必要という指摘にとどめる |
| 実装計画 | 受付時のモックAPI呼び出しがプロセス内か、関数URL経由か（S4）。後者なら、同時実行数1では動かない可能性がある |

## 10. 未実施・人の判断／承認待ち

| 項目 | 状態 |
| --- | --- |
| 予約済み同時実行数・タイムアウト・ログ保持の実際の設定 | **未実施**（AWSの操作は未承認） |
| Budgetsの作成と通知先、閾値（USD・為替・税） | **未決・未実施**。通知先は利用者がAWS側で非公開に入力する |
| CloudWatchアラームの作成、`Url4xxCount`に429が含まれるかの確認 | **未実施・未確認** |
| 通知の到達確認 | **未実施** |
| 緊急停止（予約済み同時実行数0）の実地確認と再開の確認 | **未実施** |
| 小規模な負荷試験（画面の並列取得、モックAPIの呼び出し方式） | **未実施**（人の承認が必要） |
| Lambda・CloudWatch Logs・Aurora DSQL・EC2転送の東京の料金 | **未確認** |
| 実際のアカウント種別と無料枠の対象（他の関数との合算を含む） | **未確認** |
| CloudFront Freeプランで関数URLをオリジンにできるか、WAFレート制限を使えるか、上限到達時の挙動 | **未確認** |
| API Gateway HTTP APIへのWAF適用可否 | **未確認** |
| 429応答のLambda課金の有無 | **未確認** |
| アプリの実DPU、応答サイズ、1リクエストあたりのログ量 | **未測定** |
| 推奨案の採否、予約済み同時実行数の値、公開範囲、認証方式、第2段階へ進む条件 | **未決（人の判断）** |
| 許容する固定費（月0円目標との優先順位） | **未決（人の判断）** |

## 11. 参照した公式資料（確認日: 2026-11-09、日本時間）

本文を取得して確認した資料です。

- [Lambda関数URLの作成と管理](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)
- [Lambda関数URLのアクセス制御](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)
- [Lambda関数URLの監視](https://docs.aws.amazon.com/lambda/latest/dg/urls-monitoring.html)
- [HTTPでLambdaを呼び出す方法の選択（関数URLとAPI Gateway）](https://docs.aws.amazon.com/lambda/latest/dg/furls-http-invoke-decision.html)
- [Lambdaの予約済み同時実行数](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html)
- [Lambdaの実行環境のライフサイクル](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html)
- [AWS Lambda料金](https://aws.amazon.com/lambda/pricing/)
- [AWS Budgetsによる費用管理](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
- [AWS Budgetsの予算アクション](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html)
- [Amazon CloudFront料金（定額プラン）](https://aws.amazon.com/cloudfront/pricing/)
- [Amazon CloudFront料金（従量課金）](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/)
- [CloudFrontのLambda関数URLオリジンのアクセス制限（OAC）](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html)
- [AWS WAF料金](https://aws.amazon.com/waf/pricing/)
- [AWS WAFの概要（保護できるリソース）](https://docs.aws.amazon.com/waf/latest/developerguide/waf-chapter.html)
- [AWS WAFのレートベースルール](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html)
- [API Gateway料金](https://aws.amazon.com/api-gateway/pricing/)
- [API Gateway HTTP APIのスロットリング](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html)
- [Amazon CloudWatch料金](https://aws.amazon.com/cloudwatch/pricing/)
- [Aurora DSQL料金](https://aws.amazon.com/rds/aurora/dsql/pricing/)

料金ページは取得時点の内容で、リージョン・為替・税・特典で変わります。契約前に最新の料金を確認してください。
