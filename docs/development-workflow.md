# Issueから検証環境までの開発運用

2026-11-08更新。GitHubを使う運用案です。Codexが案件別コピーでブランチを作り、変更・検証・PR作成まで進めます。人はGitHub上で差分とCI結果を確認し、マージを判断します。GitHubへの書き込み権限、ブランチ保護、CI/CDは導入前に確認します。業務要件の根拠は[要件書](requirements.md)です。

## 着手条件と担当

人のIssue準備担当が、成果物、範囲、受入条件、依存関係、必要な権限と、マージを判断する担当者を確認します。不足している場合は質問と推奨案をIssueに残し、実行対象にしません。実装では入力と期待結果も先に合意します。

| 担当 | 責任 |
| --- | --- |
| Issue準備担当（人） | 前提・受入条件を整え、実行対象を選ぶ。 |
| Symphony | Issueごとに分離した作業コピーを用意し、Codexを実行する。 |
| Codex | 専用ブランチで変更・検証し、コミット、push、GitHubのPR作成まで行う。実施結果と未確認事項をworkpadに残す。 |
| マージ承認者（人） | GitHub上でPRの差分と最新コミットのCI結果を確認し、マージするか判断する。 |
| 検証環境への配布承認者（人） | 配布対象コミット、検証先、復旧手順を確認して配布を承認する。 |

リポジトリ管理担当をマージ承認者、検証環境管理担当を配布承認者とする案です。同じ人の兼任も可とし、担当者と代理をLinearに記録します。配布については、唯一の人が起動し承認する運用を合意済みです（後述の「自己承認の扱い」）。承認者が決まるまでマージ・配布は保留します。

現在の対象はREADME記載のSymphony v0.0.3／Codex 0.166.0です。現行の`WORKFLOW.md`は元リポジトリをIssueごとに別の場所へクローンし、その中でCodexを起動します。元の作業ツリーのブランチは切り替わりません。クローンに入るのはコミット済みの内容です。準備確認、Linearへの実接続、GitHubへのPR作成、アプリの確認は分けて記録します。

## 実行とレビュー待ち

1. 人が前提を確認したIssueを、選択したプロジェクトで`symphony-pilot`付きの`Todo`にする。起動・停止は[README](../README.md#起動停止)に従う。
2. CodexはIssue・状態一覧・既存コメントを読み、`Todo`を`In Progress`にする。再試行は既存workpadから続ける。実行対象外なら終了する。
3. ファイル変更前に、AGENTS.mdに従い公式資料の検索・本文確認と根拠の記録を行う。変更を伴うIssueでは、案件別コピー内で専用ブランチを使い、必要な検証後にGitHubへpushしてPRを作る。
4. 同じworkpadにPRのURL、変更ファイルの相対パス、基点と成果のコミット、検証結果と未実施の確認を記録する。認証情報、絶対パス、端末固有の情報は書かない。
5. GitHubに成果が保存されたことを確認し、`In Review`へ移す。この状態では人がPRを確認する。調査だけでファイル変更がないIssueはworkpadで結果を渡す。

必要な認証・ツール・権限がない場合も、最後に成功した段階と再開条件を残して`In Review`へ渡します。修正が必要なら、人が指摘を同じIssueに記録して`Todo`へ戻します。Linearに接続できないときは未反映のworkpad・状態更新をチャットで報告します。

## 専用ブランチとGitHubへの保存

1. 案件別コピーで、合意したGitHubリポジトリとPRのベースブランチを確認する。初回はベースからIssue専用の`codex/`ブランチを作る。現行のクローン元は元リポジトリなので、GitHubへのpushには設定済みのGitHub向けリモートを使う。
2. 担当範囲のファイルを検証し、設定済みのGit名義でコミットする。未追跡ファイルも確認し、認証情報、実行状態、配布バイナリを除く。
3. 専用ブランチをpushし、同じブランチからPRを開く。PRの最新コミットが成果のSHAと一致することを確認し、workpadにPRとSHAを記録する。GitHub上のブランチをコピー外の保存先とする。
4. 再試行では、workpadとGitHubで既存ブランチとPRを確認し、同じPRに修正を追加する。競合などで安全に続けられない場合は、成果を保持して再開条件を記録する。force pushは行わない。

導入前に、案件別コピーでブランチ作成・コミットができることと、承認された認証で専用ブランチへのpush・PR作成ができることを確認します。過去の案件別実行ではGitの書き込み権限がなくコミットに失敗しています。失敗した場合は未追跡ファイルも含めて案件別コピーを保持し、最後に成功した段階、対象ファイル、再開条件をworkpadに残します。

`Done`などの終了状態は案件別コピーの削除対象です。GitHubへの保存、人によるマージ、残る受入条件を確認してから`Done`に進めます。

## PRの受入条件と失敗時の戻し先

PRには目的・変更点、対象Issueとworkpad、基点／成果／PRのSHA、確認コマンド・結果、未確認事項を記載します。内部リンクはPRとLinearに置き、公開文書には記載しません。

CI構築タスクのDone時点の記録では、`require-ci-main`ルールセットでmainブランチに以下の必須チェックを設定しています。2026-11-08に、CodexがGitHubのAPIで`main`の`protect-main`と`require-ci-main`のルールセットが有効であることを確認しました。

| CIで判定する検査 | 受入結果 |
| --- | --- |
| React + ViteのESLintとビルド | 新規チェックアウトから依存関係を導入して成功する。 |
| FastAPIのRuff lintと整形チェック | 差分を書き換えず合否を判定する。 |
| E2E環境のスモークテスト | 初期画面とAPIの`GET /health`を確認する。業務E2Eは合意したシナリオ・入力・期待結果を別途追加する。 |

人が最新PRコミットで全必須チェックの成功と差分を確認してから、GitHub上でマージします。ブランチ保護でPRと必須チェックを要求し、AIに渡す認証ではベースブランチへの直接push・マージ・保護の迂回ができないことを導入前に確認します。未実行・スキップ・保留は成功とみなしません。文書だけのPRのチェック適用範囲はCI構築時に合意します。

失敗時はPRからチェック名、対象SHA、実行URL、失敗理由、ログ、E2Eレポートまたはトレースを確認できるようにします。再実行は初回の失敗記録を残し、再実行URL・結果と結び付けます。

- 変更が原因なら担当Issueへ指摘を残し、人が`Todo`へ戻す。修正コミットを同じPRに反映しCIを再実行する。
- テスト基盤・CI設定の問題なら対応する子課題へ人が記録し、依存するPRのマージを保留する。失敗チェックの無効化で通過扱いにしない。
- 一時的な基盤障害なら原因と再実行理由をPRに記録する。新しいコミットには改めてCIを実行する。

AIレビューを使う場合も、機械的な合否はCIが判定し、マージは人が判断します。

## 検証環境への配布

この節は配布手順の案と、その実装（コードのみ）の状況です。実際の配布・承認・マージ・AWSの構築は未実施です（GitHubのEnvironment `demo` とリポジトリ変数 `CD_APPROVER_LOGIN` はCodexが設定済み）。AWSで行った操作は、利用者本人がCloudShellで実行した読み取り専用の関数一覧（`list-functions`、ap-northeast-1指定）だけです。**案の合意と実行は別**です。AWSの構築・配布は、利用者本人がAWS CloudShellで手順を1段階ずつ実行する方式とし、いずれも未実施です（この文書の作業ではAWSを操作しません）。実際のCD（配布・ロールバック）の動作は未検証です。現在、配布を承認したコミットSHAはありません。

### 現在の状況

| 区分 | 内容 |
| --- | --- |
| 要件・合意済みの前提 | FastAPIがViteでビルドした静的UIを配信する。AWS Lambda（ZIP、Python 3.14）で実行し、データベースはAurora DSQLとする（初回配布ではDBを作らない）。月額ゼロ円は目標であり、保証でも停止条件でもない。 |
| 利用者の直接回答で確認済み（2026-11-08） | リージョンは東京（ap-northeast-1）。当初「既存のLambda関数とAurora DSQLクラスタはない」との回答があったが、その後の実測で訂正する。利用者がCloudShellでap-northeast-1を指定した`list-functions`を実行して成功し、同じアカウントに既存のAmplify用の関数が4件あることを確認した（関数名は公開文書に書かない）。今回のデモ用のLambda関数が既にあるかどうかと、Aurora DSQLクラスタの有無は**未確認**（他のAWSリソースも未確認）。月額の許容上限として最大1,000円との回答があったが、現行の停止条件ではない（後述の「費用と超過時の制御」）。公開URLは架空データのみを扱う、誰でもアクセスできるデモとし、常時公開を目指す。AWSアカウントは非常に古い（利用者の報告）。 |
| 設計の合意（2026-11-08。実行許可ではない） | 唯一の人が配布を起動し承認する。Environmentの自己承認禁止（`prevent_self_review`）は意識して無効（false）にする。費用の通知は、過去に300・500・800円を任意の監視案として検討したもので、現行の必須停止条件ではない。請求データの反映遅れのため、1,000円の厳密な上限は作れない（説明済み）。 |
| 前提（仮定） | 古いアカウントのため、現行の「Freeアカウントプラン」ではなく従来の従量課金・無料利用枠のアカウントと仮定する。コンソールでの実際の種別・無料枠の対象は未確認で、確認するまで仮定として扱う。既存アカウントの利用を推奨し、新たなFreeプランやクレジットを使い回す運用はしない。既存のAmplify用の関数が4件あることは実測で確認済み。今回のデモ用のLambda関数とAurora DSQLの有無は未確認で、「ない」とは仮定しない。他のAWSリソースがないとも仮定しない。 |
| 実装・ローカル確認の状況（Codex、2026-11-08） | ZIPのビルド・スモーク確認はローカルで実装済みで、最終ZIPでローカルのコンテナとブラウザの確認を実行した記録がある（後述の「ローカルでの確認結果」）。手動のCDワークフロー（`.github/workflows/cd.yml`）、事前確認（`scripts/cd-preflight.py`）、配布ヘルパー（`scripts/deploy-lambda.py`）、IAMの雛形、初回構築の手順書（`infra/cd/bootstrap.md`）はコードと文書として追加した。ヘルパーのオフラインテスト（`tests/cd/`。偽のAWS・偽のGitHub APIを使う）は、最終コミット`76e584f`で87件が、ホストのPython 3.10と固定したDockerのPython 3.14の両方で成功した（Codexの報告）。PR7のCI run `42480126262`では`frontend`・`backend`・`e2e`・`cd-offline`の4件が成功した。クリーンな作業ツリーでのZIPのビルドと検証も成功し、`releaseEligible=true`だった（これはクリーンな作業ツリーであることだけを示し、人の承認ではない）。ただし**いずれも実際のAWS・GitHubのCDの検証ではない**。この最新の文書コミットに対するCIはまだ実行していない。`cd.yml`はGitHub上で実行しておらず、承認されたコミットはない。`cd-offline`ジョブはCIに追加済み（PR・`main`で実行。必須チェック名には含めていない）。 |
| 確認済み（2026-11-08、Codex） | GitHubリポジトリは公開で、GitHub APIで`main`の`protect-main`と`require-ci-main`のルールセットが有効。Codexが既存の`main`（CIのrunは成功）を読み取りで確認し、Environment `demo`を設定した（必須レビュー担当者1人、自己承認の禁止は無効、デプロイブランチは`main`のブランチのみ）。リポジトリ変数`CD_APPROVER_LOGIN`も設定済み。管理者による迂回の無効化は要求したがAPIの応答にフィールドがなく**未確認**。AWS関連の変数は**すべて未設定**で、Codexは、AWSの認証・操作を行っていない（AWSで行われたのは、利用者本人による読み取り専用の関数一覧の取得だけ）。 |
| 範囲外 | SLAは依頼・要件に含まれていないため、運用前の決定事項にしない。 |
| 未確認・未決 | 今回のデモ用のLambda関数とAurora DSQLクラスタが既にあるか（既存のAmplify用の関数4件は確認済みで、別物として扱う）。AWSコンソールで読み取り専用に確認する、実際のアカウント種別と無料枠の対象。任意の予算アラートを使う場合の宛先（利用者がAWS側で非公開に入力する。円建ての閾値をUSDへ換算する為替・税の扱いは、作成前に人が確認する）。アダプタのレイヤー実体のハッシュと、実際のLambda上での動作。AWS関連のリポジトリ変数（AWS作成後）。実際のCD（配布・ロールバック）の動作。常時公開の最終判断。 |

以前の「配布ワークフローを作らない」という記述は、当時の計画段階の範囲に関するものです。担当の配布Issueの受入条件は、人が承認したCI通過済みの正確なコミットSHAを実際にLambdaのZIPとして配布し、SHAからURLと結果までたどれ、FastAPIがビルド済みのVite画面とヘルスを配信することを初期画面とヘルスで別々に確認し、結果を別に記録して、直前の正常版へ戻せることです。実際の配布・レビュー・マージが完了するまで、そのIssueを`Done`にはしません。現時点では、配布・レビュー・マージのいずれも未実施です。

### 公開経路（2026-11-09の方針。計画のみ）

関数URLを匿名（認証なし）で直接公開する旧案は採りません（botへの露出を避けるため）。公開は、**CloudFront + WAF + Lambda関数URL（`AWS_IAM`）+ OAC**だけで行います。公開URLはCloudFrontの`https://<id>.cloudfront.net`のみで、関数URLを公開URLにも、失敗時のフォールバックにもしません。詳細と手順案は[公開経路の計画](../infra/cd/cloudfront-plan.md)です。

| 区分 | 内容 |
| --- | --- |
| 確認済み（利用者・Codexの報告） | 既存のCloudFrontディストリビューションは0件、WAFのWeb ACL（CLOUDFRONT scope）は0件。Lambda関数URLは作成済み（`AWS_IAM`・`BUFFERED`、エイリアス`demo`）で、未署名の匿名GETが403になることをCodexが実測した。**CloudFront・OAC・WAFは未作成**。関数URLは再作成しない |
| 計画案（プロジェクト独自の判断。未実施） | OACは種類`lambda`・`sigv4`・`always`。デフォルトのビヘイビア1件だけで`CachingDisabled`と`AllViewerExceptHostHeader`、メソッドは`GET`・`HEAD`・`OPTIONS`。WAFはIPごと300件/5分と全体1,500件/5分（`CONSTANT`・`ScopeDownStatement`はURIパスが`/`で始まるもの）のレート制限2規則。独自ドメイン・S3・NAT・新規のAurora DSQLは使わない。予約済み同時実行数は1を維持し、公開後の異常時は人が0にして止める |
| 順序（既定はFree） | FreeプランのコンソールでディストリビューションとWAFを一緒に作る（Lambdaの許可は付けない。自動で付く場合は避けられなければ進まず、制御できる手順を人が確認する）→ 新規の専用Web ACLを2規則に確認・調整 → OAC・キャッシュなど全設定を確認 → 読み取りで再確認 → **最後に**、そのディストリビューションだけに`lambda:InvokeFunctionUrl`と`lambda:InvokeFunction`を許可する。保護を迂回した状態で先に公開しない。既存のリソースは変更しない |
| 費用（未確認） | CloudFrontのFlat-rateプラン「Free」（公式資料では月$0）が第一候補。選択できることは利用者の報告で、利用アカウントでの契約・適用可否、Lambda関数URL + OACとの組み合わせ、上記のWAF規則の適用、費用は未確認。アプリのLambdaとログは別料金。Web ACLを単独で先に作ると、紐付け前から従量の費用が生じ得るため、Web ACLをCLIで単独で作る案は、Freeを使えない場合の**有料の代替**として分けている（自動では実行せず、従量課金のWAF（Web ACL 1件＋規則2件で基本約$7/月＋$0.60/百万リクエスト）＋CloudFront・Lambda・ログ・税の構成を示して人が判断する） |
| 配布コードの検証 | `scripts/deploy-lambda.py`が、AWSの変更の前に、読み取りだけで、OAC・ディストリビューション・Web ACL・関数のリソースポリシー・キャッシュ設定を検証する（偽のAWSでのオフラインテスト。実際のAWSでの動作は未確認）。2件以外のWAF規則、Firewall Managerの管理・前処理/後処理の規則グループ、CloudFrontの継続的デプロイ・ステージングの設定、`AuthType`が`AWS_IAM`以外のLambdaの許可は拒否する（これらのAPI項目名が実際の応答と一致するかは未確認）。配布用ロールの追加権限は読み取りだけ |

アセットのキャッシュは将来案で未実装です。レート制限は近似で、遅延と巻き添えがあり得ます。Bot Controlは含めません。

### 初回構築（配布とは別の操作）

配布の前に、IAMロール、Lambda関数、エイリアス`demo`、関数URL（`AWS_IAM`）、ログの保持期間、配布用のOIDCロールを作る初回構築が必要です。公開用のCloudFront・OAC・WAFと、最後の許可は[計画](../infra/cd/cloudfront-plan.md)で別に作ります。この構築は配布ワークフローとは別の、人が承認する操作で、具体的なコマンド・IAMの雛形・GitHubの設定は[初回構築の手順書](../infra/cd/bootstrap.md)にあります（**未実行**。AWSでの作成は未承認。利用者本人による読み取り専用の関数一覧の取得だけが実施済み）。CDは、そこで作った既存で正常なエイリアスを更新するだけで、IAM・関数URL・同時実行数・ログの操作を含みません。

データベースはAurora DSQLのままとします。ただし、現在の最小構成のアプリはDBを使っていないため、初期画面とヘルスの初回配布の前にAurora DSQLを作る必要はありません。クラスタの作成、接続、スキーマはDB接続・スキーマを扱う別の課題で、Lambda/CD担当とは別の担当が行います。両担当の連携前に、既存のLambda・DSQLクラスタの状況を確認します（現時点でDSQLの有無は未確認。Lambdaは既存のAmplify用の関数4件を確認済みで、今回のデモ用の関数の有無は未確認）。

### 配布の流れ（実装済み・未実行）

詳細はワークフローとスクリプトのコメントを参照し、ここでは要点だけ記します。

1. **起動**: 人が`workflow_dispatch`で、`main`から、マージ済み`main`上の完全な40桁のSHAを指定する。ブランチ名・タグ・短縮SHAは受け付けない。`main`以外から起動した場合は、スキップ（成功に見える）ではなく、buildジョブが明示的に失敗する。自動の配布はなく、手動のみ。同時に動かせる配布は1つで、実行中のものはキャンセルしない。
2. **事前確認（buildジョブ。AWSの資格情報なし）**: 起動したコミットの配布コードで、オフラインテスト、Environment`demo`の保護設定の確認（必須レビュー担当者がリポジトリ変数`CD_APPROVER_LOGIN`の1人のユーザー、自己承認禁止は無効、デプロイブランチは`main`のみ、管理者の迂回なし）、対象SHAが`origin/main`の祖先であること、同じSHAの`ci.yml`のpushのrun（最新）と、その最新の試行の`frontend`・`backend`・`e2e`のジョブがすべて成功していること（スキップ・部分成功は不可）を確認する。Environmentが無い・読めない場合は止まり、自動作成には頼らない。
3. **ビルドと保管**: 確認後に対象SHAをチェックアウトし、`scripts/build-lambda.sh`でリリースモードのZIPとマニフェストを作る。同じrun内の専用名で上書きなしに保存し、ZIPの実測ハッシュ、CIのrun、ソースSHA、成果物IDをサマリーに出す。**承認者はこのサマリーを見てから承認する**。
4. **承認と再確認（deployジョブ）**: Environment`demo`の承認後、AWSの資格情報の前に、設定・承認記録・必須変数（`AWS_ROLE_ARN`、`LAMBDA_FUNCTION_NAME`、`CD_BASE_URL`（CloudFrontのオリジン）、`CLOUDFRONT_DISTRIBUTION_ID`、`CLOUDFRONT_OAC_ID`、`WAF_WEB_ACL_ARN`、`LAMBDA_LAYER_CODE_SHA256`、`CD_APPROVER_LOGIN`）を確認する。配布コードは対象SHAではなく起動したコミットのもの。続けて、ダウンロードした成果物（manifestとZIP）を、ソースSHA・ビルドジョブのZIPハッシュ・`releaseEligible=true`・`dirty=false`と照合する（`deploy-lambda.py --verify-artifact-only`。AWS CLI・資格情報・通信を使わない）。改ざんがあれば資格情報の前に止まる。その後にOIDCで一時資格情報を得る（ロールセッション名はrun番号と試行）。`CD_ALLOW_LOCAL`は常にfalseで、ワークフローとヘルパーは拒否する。
5. **変更前の検証（`deploy-lambda.py`。失敗したら変更しない）**: マニフェストとZIPの内容、ビルドの出力とのハッシュ照合、`releaseEligible=true`・`dirty=false`、関数がPython 3.14・x86_64・ハンドラ`run.sh`・固定レイヤー・`/opt/bootstrap`であること、レイヤーのCodeSha256が記録値と一致すること、関数URLがエイリアス`demo`で`AWS_IAM`・BUFFERED・CloudFrontのオリジンと一致するホストであること、CloudFrontのディストリビューション（有効・Deployed・WAF関連付け・単一オリジン・https-only・OACの関連付け・`CachingDisabled`と`AllViewerExceptHostHeader`・`GET`・`HEAD`・`OPTIONS`のみ・追加のビヘイビアや関数の関連付けなし）、OAC（`lambda`・`sigv4`・`always`）、Web ACL（us-east-1・CLOUDFRONT scope・2規則のしきい値）、関数のリソースポリシーが特定のディストリビューションだけを許可していること（いずれも読み取りのみ。スモーク確認は公開URL（CloudFront）で行う）、エイリアスが加重なしの数値の版を指し、その版のdescription（`source=<sha>;zip=<hex>;run=<id>`）がコードのハッシュと一致すること、現在の版のスモーク確認が成功すること。
6. **更新**: 検証時に取得した関数のRevisionIdを条件にコードを更新する（現行版のスモーク確認の間に設定が変わっていれば、更新は拒否される。更新の直前に読み直した値は使わない）。返ってきたCodeSha256がマニフェストと一致することを確認し、更新完了を待つ（AWS CLIの`wait function-updated-v2`。`GetFunction`を使う）。更新後に設定を読み直し、検査対象の設定とコードのハッシュ、こちらの更新が返したRevisionIdのままであること（他者の変更がないこと）を確認してから、そのRevisionIdとコードのSHA-256を条件に版を発行する（descriptionに出所を記録）。発行された版（再利用する既存の版も同じ）は、Pendingのことがあるため、AWS CLIの`wait function-active-v2 --qualifier <版>`（上限つき。追加の権限は不要）でActiveを待ってから確認し、その後にエイリアスを切り替える。待機の失敗・タイムアウトはエイリアスを動かさず失敗とする。AWSはコード・設定が変わらないと新しい版を作らず既存の版を返すため、返された版を読み、descriptionのソースSHA・ZIPのハッシュ、コードのハッシュ、検査対象の設定がすべて今回と一致すると証明できたときだけ再利用する。証明できなければ、エイリアスを動かさず止まり、手動確認とする（設定を変えて版を作らせることはしない）。続いて、エイリアスの`RevisionId`を条件に（加重なしで）切り替え、承認したSHAでブラウザのスモーク確認を行う。配布処理とスモーク確認は別々に記録する。エイリアスが既に今回と同じソースSHA・ZIPの版を指し、現行版のスモーク確認が合格した場合は、変更なしの成功（`overall=no-change-success`、`deployment=already-current`）として終え、新しい版の発行も新しい配布のスモーク確認もしない。新しい配布の成功とは扱わない。
7. **失敗時の戻し**: 切り替えた後にスモーク確認が失敗したら、**こちらの更新が成功として返した**`RevisionId`だけを条件に、記録した直前の版へ戻す。戻しの応答（版・`RevisionId`）を検証できたときだけ、直前のSHAでスモーク確認を行う。応答が不正なら戻しの失敗とし、手動復旧とする。戻しが成功しても全体は失敗として終了する。エイリアス更新の結果が不明な場合（エラー応答、期待と異なる応答）は、読み直した`RevisionId`は外部の変更かもしれず戻しの根拠にしないため、自動では戻さず、観測した状態を記録して全体を失敗とし、手動復旧とする（観測した版が今回の版でも上書きしない）。エイリアスが外部で変更されていた場合（条件付き更新の拒否）も、上書きせず、エイリアスの実際の版は不明と記録する。切り替え前の失敗では、無関係な変更を戻さない。版・成果物・DBは削除しない。
8. **記録**: 結果JSON（ソースSHA、ZIPのハッシュ、CIのrun、配布・スモーク・戻しの状態、版、エイリアスのRevisionId、AWSの変更操作の開始の有無`mutationStarted`）を、処理の各段階と最後に書き、ビルド成果物とは別の成果物として常に保存する。秘密・ローカルの絶対パス・Lambda設定の全体は含めない。プロセスが強制終了・タイムアウトした場合は戻し処理が走らないことがあり、その場合は結果JSONの復旧メモと手順書で人が確認する（保証はしない）。

**成果物の保管**: GitHub Actionsの成果物は90日で消えます。Lambdaの現在の版と直前の版は、より新しい版がスモーク確認に合格するまで削除しません。成果物が期限切れでも、版のdescriptionのZIPハッシュと照合して正確なZIPを取り戻せます（[手順書](../infra/cd/bootstrap.md)）。ソースの承認とrunの記録はworkpadに残します。成果物が永久に残るとは扱いません。

### ZIPのビルドと確認（コマンド）

実行はWSLのシェルでリポジトリルートから行います。AWSへは接続しません。ローカルでの確認結果は、この節の末尾の「ローカルでの確認結果」にまとめています。

固定情報（実行方式の候補。採用・動作は未確認）:

| 項目 | 値 |
| --- | --- |
| ランタイム・アーキテクチャ | Python 3.14（`python3.14`）・x86_64 |
| 依存のwheel | `pip --platform manylinux2014_x86_64 --python-version 3.14 --abi cp314 --only-binary=:all: --require-hashes`（`backend/requirements.txt`のハッシュ固定。固定済みの本番依存を、Lambdaの公式手順と同じくpipで取得する）。ホストのwheelは使わない |
| ビルドの実行基盤 | `docker buildx build --platform linux/amd64`で全ステージをx86_64に固定（arm64ホストではエミュレーションが必要） |
| Lambda Web Adapter | v1.1.0（コミット`596bdbcea78a89084fd0b522807d04cd2f1f3f7e`）のREADMEに記載の東京x86レイヤー`arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28` |
| 関数の設定 | 環境変数`AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`・`PORT=8000`、ハンドラ`run.sh` |
| 未検証 | レイヤー実体のハッシュ、このレイヤーとPython 3.14ランタイムの互換性 |

```bash
# 1. ビルド（承認済みのクリーンなHEADだけ。dist/cd はGit無視）
bash scripts/build-lambda.sh <40桁のSHA> dist/cd/<40桁のSHA>
#    ローカル検証専用（releaseEligible=false。配布には使わない）
bash scripts/build-lambda.sh --allow-dirty <40桁のSHA> dist/cd/local

# 2. 外部のURLに対するスモーク確認（httpsと releaseEligible=true が必須）
CD_BASE_URL=https://<公開URL> CD_EXPECTED_SHA=<40桁のSHA> bash scripts/cd-smoke.sh

# 3. 配布ヘルパーと事前確認のオフラインテスト（標準ライブラリのみ。偽のAWS・偽のGitHub APIを使う）
python3 -B -I -m unittest discover -s tests/cd
```

3のテストは配布ロジックの確認で、実際のAWS・Lambda・GitHubの動作の検証ではありません。実行記録（履歴）: 過去の時点で、Codexが固定したPython 3.14のホストで83件成功し、actionlint 1.7.12は`ci.yml`・`cd.yml`の両方で成功、`git diff --check`も成功した。その後、発行した版のActive待機のテスト4件を追加して87件になり、Claude Codeがローカル（Python 3.10.12）で87件成功を確認した。最新の記録（Codexの報告）: 最終コミット`76e584f`で87件が、ホストのPython 3.10と固定したDockerのPython 3.14で成功した。PR7のCI run `42480126262`で`cd-offline`を含む4件（`frontend`・`backend`・`e2e`・`cd-offline`）が成功した。この文書の最新コミットに対するCIは未実行。CIの`cd-offline`ジョブは、同じテストを固定済みのPython 3.14イメージで、リポジトリを読み取り専用にマウントし、通信なし・資格情報なしで実行します。`python -B -I`でも、`-I`は`PYTHON*`環境変数を無視するだけなので、バイトコードを作らないよう`-B`を必ず付けます（`__pycache__`が作業ツリーに残ると、リリースのビルドが未コミットの変更として止まる原因になります）。

ローカルのスモーク確認は、ZIPを空の専用ディレクトリに展開し、Python 3.14のコンテナで`run.sh`を起動して行います。ZIPの内容は信頼せずに扱い、展開は標準ライブラリの`zipfile`で行います（パスの逸脱を拒否します。実行権限は復元されませんが、`sh run.sh`で起動するため不要です）。

```bash
sha=<40桁のSHA>
python3 -I -m zipfile -e "dist/cd/$sha/lambda-$sha.zip" "dist/cd/$sha/unpacked"

docker network create cd-local
docker run -d --rm --name cd-local-app --network cd-local \
  -v "$PWD/dist/cd/$sha/unpacked:/var/task:ro" -w /var/task \
  python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d \
  sh /var/task/run.sh
CD_ALLOW_LOCAL=true CD_BASE_URL=http://cd-local-app:8000 CD_EXPECTED_SHA="$sha" \
  bash scripts/cd-smoke.sh --network cd-local
docker stop cd-local-app && docker network rm cd-local
```

注意: ZIPを置く場所を`--tmpfs`にする場合は`--tmpfs /var/task:exec`のように`exec`を付けます。Dockerの`--tmpfs`は既定で`noexec`で、pydanticなどのネイティブ共有ライブラリを読み込めず起動に失敗します（`noexec`のファイルシステムに展開した場合も同様）。これはテスト環境の設定の問題で、アプリの不具合ではありません。`CD_ALLOW_LOCAL=true`はローカル確認専用で、CDワークフローとヘルパーでは使えません。

- `manifest.json`には、ZIPのSHA-256（16進とbase64。後者がAWSの`CodeSha256`）、ソースSHA、`dirty`、`releaseEligible`、ランタイム、アダプタの固定情報が入ります。`releaseEligible`はクリーンな作業ツリーかつHEADが指定SHAと一致したことだけを示し、人による承認を意味しません。ZIPのバイト単位の再現性は保証しません。
- スモーク確認は、画面のHTTP 200・見出し・バンドルの読み込み、`/health`の完全一致、`/release`のSHAと配布可否を確認します。期待するSHAと違えば失敗します。`CD_BASE_URL`はオリジンのみ（認証情報・パス・クエリ・`#`を含むと拒否）で、httpは`CD_ALLOW_LOCAL=true`のときだけ許し、そのときだけ`releaseEligible=false`も許します。再試行はしません。レポートは`e2e/cd/playwright-report/<ID>`、トレース等は`e2e/cd/test-results/<ID>`に出ます（Git無視）。既存のE2E（`scripts/e2e.sh`）は`e2e/playwright-report`と`e2e/test-results`を整理するため、CDの結果はその外の`e2e/cd`配下に分けています。環境変数`CD_REPORT_ID`（任意。英数字で始まる64文字以内の英数字・`_`・`.`・`-`）を指定すると、それぞれの下の`<CD_REPORT_ID>/`に出力し、成功と失敗の記録を別々に残せます。未指定の場合は`_default/`だけに出力し、再実行で上書きされるのは`_default`のみです。正式な配布確認では、実行ごとに一意な`CD_REPORT_ID`を使います（CDヘルパーは`run<run番号>-<試行>-<previous|new|rollback>`を使う）。
- FastAPIは環境変数`STATIC_DIR`と`RELEASE_FILE`が設定されたときだけ、ビルド済み画面と`/release`を配信します。未設定の開発時は従来どおり`/`が404です。

#### ローカルでの確認結果

過去の時点（2026-11-08）にCodexが、アプリ・基盤の修正をすべて入れた後の最終ZIPで実行した結果の履歴として残しています（このレビューで再実行したものではありません）。最新の状況は上の「現在の状況」を参照してください。ローカルのコードとコンテナ・ブラウザでの確認であり、AWS上での動作の確認ではありません。**この記録は、スモーク確認・ZIPビルドまでの過去の実測で、今回追加した配布ヘルパー・事前確認・ワークフローの検証ではありません。** 修正前の初期ビルド（SHA-256が`88e3effb…`で始まるもの）の記録は過去のもので、下の最終ZIPの値とは別です。

最終ZIP（過去のローカル検証用。承認されたものではない）:

| 項目 | 値 |
| --- | --- |
| 置き場所 | `dist/cd/local`のような汎用パス（例。Git無視） |
| ビルド方法 | `--allow-dirty`。当時の変更は未コミット |
| ソースSHAのラベル | `a9914ec77e36b7470e7c38186c81588e7f0fcf67`（作業ツリーが未コミットのため、このSHAの内容とは一致しない。承認されたSHAではない） |
| ZIPのSHA-256（実測） | `44fc6c3aae3d12edc0ea28cad8afe5ea8850d30b34759a8144e0ca5d57c12532` |
| `releaseEligible` | `false`（配布には使えない） |

| 項目 | 結果（Codexが実際に観測したもの） |
| --- | --- |
| マニフェストとZIPの内容 | 実測したZIPのハッシュ（16進・base64）がマニフェストと一致。キャッシュと`node_modules`を除いたZIPの内容と、起動スクリプトの実行権限ビットを確認 |
| 公式Lambda Python 3.14イメージ（digest `sha256:cf4c3a72…da9a29`、Amazon Linux 2023）上の最終ZIP単体（アダプタ・AWSのサービスなし）、正常系 | ブラウザのスモーク確認が4件中4件成功、終了コード0（`CD_REPORT_ID=final-positive`）。確認できたのはAmazon Linux 2023のネイティブライブラリでの読み込みまで |
| 意図的に誤ったSHAを期待値にした異常系 | 3件成功・1件失敗、終了コード1。失敗は`/release`の不一致のみ。トレースは残っている（`CD_REPORT_ID=final-negative`） |
| `CD_REPORT_ID`未指定の通常実行 | 4件中4件成功、終了コード0。実行後も名前付きの正常系・異常系のHTMLレポートと異常系のトレースが残っていた |
| URLの検証 | パス・認証情報・クエリを含む`CD_BASE_URL`は、HTTP通信の前に`config --list`が非ゼロで終了して拒否された |
| 拒否の確認 | 作業ツリーが未コミットのままのリリースモードのビルド、存在しないSHA、空でない出力先は、いずれも期待どおり拒否された |
| 静的検査 | Ruffのlintと整形チェックが成功。シェルの構文確認と`git diff --check`が成功 |
| 既存のE2E（`bash scripts/e2e.sh`） | 2件中2件成功。後片付けも完了 |
| 最初の試行（記録済み） | 失敗。テスト側が`--tmpfs`を`noexec`のまま使い、ネイティブ共有ライブラリを読み込めなかった。`exec`を付けて成功した（上の注意を参照）。アプリの不具合ではない |

名前付きレポートの保存先（Git無視）: `e2e/cd/playwright-report/final-positive/index.html`、`e2e/cd/playwright-report/final-negative/index.html`、異常系のトレースは`e2e/cd/test-results/final-negative`。

この記録の時点では未実施だった項目のうち、その後に実施されたもの（Codexの報告）: 最終コミット`76e584f`での87件のテスト成功、PR7のCI run `42480126262`の4件成功（`cd-offline`を含む）、クリーンな作業ツリーでの`releaseEligible=true`のZIPのビルドと検証の成功。いずれも承認を意味せず、実際のAWS・GitHubのCDの検証ではありません。

現時点でも未実施（成功とは扱わない）:

- `cd.yml`（CDワークフロー）のGitHub上での実行
- この文書の最新コミットに対するCI（`cd-offline`を含む）
- AWS関連のリポジトリ変数の設定（Environmentと`CD_APPROVER_LOGIN`は設定済み）
- AWSアカウントでの認証、アダプタのレイヤー実体のハッシュと、アダプタ・Lambdaサービスでの動作の確認
- 実際の配布とロールバック（偽のAWSでのテストは実際のAWSの検証ではない）
- 人の承認、マージ

### Lambdaの設定と操作

具体的なコマンドは[初回構築の手順書](../infra/cd/bootstrap.md)（未実行）です。

- 設定: ランタイムpython3.14、アーキテクチャx86_64、ハンドラ`run.sh`、上記アダプタのレイヤー、環境変数`AWS_LAMBDA_EXEC_WRAPPER`・`PORT`、512MiB・予約済み同時実行数1・10秒・ログ7日。初回構築は予約済み同時実行数0で始め、公開の許可を作り、承認した初回デモの確認時だけ1にする。
- 配布: 上記の「配布の流れ」のとおり。
- ロールバック: 切り替え後のスモーク確認の失敗で、こちらが切り替えたエイリアスだけを記録した直前の正常版へ戻し、直前のSHAで同じスモーク確認を行って別に記録する。
- 公開停止: 予約済み同時実行数を0にする（人が行い、再開は人の承認後）。公開経路の導入後も、この停止方法は変わらない。
- 関数URL: `AWS_IAM`。匿名の公開許可は作らず、最後にCloudFrontの特定のディストリビューションだけを許可する（[計画](../infra/cd/cloudfront-plan.md)）。

### 自己承認の扱い

合意（2026-11-08）: 唯一の人が配布を起動し、同じ人が承認する。GitHub Environmentの「レビュー担当者による自己承認の禁止」は意識して無効にする。これは独立した承認者を置かないことを意味し、承認の独立性は確保されない。その代わり、CDは承認前にサマリー（CIのrun、ソースSHA、実測したZIPのハッシュ）を示し、承認後にEnvironmentの設定・承認記録（承認者が`CD_APPROVER_LOGIN`の1人であること）・成果物のハッシュを再確認し、`main`以外のブランチからの配布を拒否する。Environmentがこの設定でない場合は、AWSの資格情報の前に止まる。承認者のログインはリポジトリ変数に置き、公開文書には書かない。

### 権限の分離

IAMの初期構築、Lambda関数URLの設定、CloudFront・OAC・WAFの作成と変更、関数のリソースポリシーの許可、予約済み同時実行数の変更（公開停止を含む）は、配布ワークフローの権限に含めず、人が承認した別の操作（前述の初回構築）にします。Aurora DSQLのクラスタ作成・接続と権限は、Lambda/CD担当とは別の担当・別の課題で、人が承認する別の操作とします。連携前に、既存のLambda関数・エイリアス・関数URL・DSQLクラスタの有無と状態を読み取りで確認して記録し、重複作成や上書きを避けます。配布用ロールの権限は、対象関数のコードの更新・版の発行・エイリアスの参照と更新・設定の読み取りと、固定したレイヤー版の読み取りに加え、公開経路の検証のための読み取り（対象のディストリビューションとWeb ACLの`Get`、OACの`Get`（リソース指定不可のため`Resource: *`）、エイリアス`demo`のリソースポリシーの`GetPolicy`）に限り、CloudFront・WAF・IAMの書き込みと`iam:PassRole`や管理者権限は含みません（雛形は`infra/cd/deploy-policy.json`、`infra/cd/oidc-trust-policy.json`）。

### ロールバック

- 失敗時は次の配布を保留し、記録した直前の正常版へエイリアスを戻す。戻した後に、同じスモーク確認を行って別に記録する。
- 現在の正常版と直前の正常版の成果物・Lambdaの版は、より新しい版がスモーク確認に合格するまで保管する。
- 初回配布には戻す先の正常版がない。失敗時は、後述の「費用と超過時の制御」の手動停止（予約済み同時実行数を0にする）を使う。
- データベースとの互換性が必要な変更は、自動では戻さない。スキーマの変更を伴う場合は、人が別に判断する。

### 費用と超過時の制御

月額ゼロ円は目標であり、保証ではありません。月0円の目標、1,000円の上限、500円での停止は、いずれも現行の必須の停止条件ではありません。利用者は月額の許容上限を最大1,000円と回答していますが、請求データの反映に遅れがあるため、1,000円の厳密な上限は作れず、1,000円以内に収まることも保証できません。公開URLは常時公開を目指します。

**AWSアカウントの前提**

利用者の報告ではアカウントは非常に古いため、従量課金・無料利用枠のアカウントと仮定します（前述の「前提（仮定）」）。実際の種別と無料枠の対象は配布前に読み取り専用で確認し、登録日や無料枠の残りは推測しません。プランの変更・アップグレードを起こす操作は行いません。仮定が外れた場合（期限やクレジットで終了するプランなど）は、常時公開の可否を含め人が改めて判断します。

**構成**（設計の合意。AWSでの作成・設定は未実施で、実際の費用は未検証）

| 項目 | 内容 |
| --- | --- |
| Lambdaのメモリ | 512MiB |
| 予約済み同時実行数 | 1 |
| タイムアウト | 10秒 |
| ログの保持期間 | 7日 |
| プロビジョニング済み同時実行 | 使わない |
| NAT・ECR・S3・独自ドメイン | このデモのためには作らない |
| CloudFront・WAF・OAC | 公開経路として作る計画（未作成。上の「公開経路」と[計画](../infra/cd/cloudfront-plan.md)）。費用は未確認 |

**条件付きの試算**（実際の予測ではありません）

- Lambdaの呼び出しが月合計10,000回、平均1秒・512MiBの場合、0.5GB×1秒×10,000回＝5,000GB秒です。回数は利用者の訪問数ではなく、アセットの配信やモックの実行も含むすべての呼び出しの合計です。
- Lambdaの無料利用枠（月100万リクエスト・40万GB秒）はアカウント全体で共有されます。アカウントが対象で、ほかで使っていなければ、この部分は0円になり得ます。これはLambda分だけの条件付きの見方で、アカウント全体の費用の見積もりではありません。
- Aurora DSQLの無料利用枠は月100,000DPU・1GBですが、このアプリの実際のDPUは測定していません（初回配布ではDSQLを作らない）。
- ログ、通信量、GitHub Actions、成果物の保存容量と、実際のアカウント種別・無料枠の対象が未確認のため、合計の円額はまだ見積もれません。

**任意の費用監視案と公開停止**（AWSでの設定は未実施。自動停止は実装しません。現行の必須停止条件ではありません）

- 予算アラートを300円・500円・800円の通知とする案は、過去に任意の監視案として検討したもので、必須ではない（作成は未承認）。使う場合も通知先は利用者がAWS側で非公開に入力し、リポジトリ・チャット・Linearには書かない。
- AWS Budgetsの予算額の単位は、公式のSpend APIの例ではUSDで、円建ての単位を使えるとは確認していない。300・500・800円に相当するUSDの閾値は、為替レート・税の扱いを人が確認して、作成前に決める（為替レートを作り話で置かない）。AWS Budgetsの監視と通知は無料で、有料のレポート・アクションは追加しない（[Spend API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_budgets_Spend.html)・[Budgetsの料金](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)をCodexが2026-11-08に本文確認）。
- 予算アラートは通知であって利用の上限ではありません（請求データの反映の遅れ）。
- 初回配布が失敗したとき（戻す先の正常版がない）は、人が手動でLambdaの予約済み同時実行数を0にして公開を止める。500円の通知での停止は必須ではなく、任意の監視を使う場合に人が判断する。0の間、関数URLは無効になり、リクエストには429が返ります。
- 公開の再開は、原因を修正し、人が承認してから行う。
- この停止はデータを削除しません（現在はDBなし）。残るリソースの費用は止まりません。

### 外部操作の前に必要な確認

AWSなど外部の操作を行う前に、次を人が確認します。未確認の間は操作を行いません。リソース作成・配布は、利用者本人がCloudShellで段階的に実行する前提で、各段階の実行前に確認します（すべて未実施）。

- 既存のAWSアカウント（リージョンは東京）の利用と、コンソールで読み取り専用に確認する実際のアカウント種別・無料枠の対象（プラン変更を起こす操作は行わない）。リソース作成・課金の権限と支払い方法の状態（内部の確認事項）
- 任意の予算アラートを使う場合の宛先（利用者がAWS側で非公開に入力）と、円建ての閾値をUSDへ換算する為替・税の扱い
- OIDCの正確なsub・aud（`repo:<リポジトリ>:environment:demo`と`sts.amazonaws.com`）と、AWS関連のリポジトリ変数の設定（Environmentと`CD_APPROVER_LOGIN`は設定済み）
- 初回のZIPにするクリーンなソースのSHAとCIの成功、公開してよいという人の承認

### 記録

配布ごとに、PR、配布SHA、承認記録、マニフェスト、ビルドとデプロイの実行URLと結果、公開URL、スモーク確認の結果、ロールバックや公開停止の有無をworkpadに結び付けます。確認済みの事実、合意済みの内容、未確認の内容は区別して記録します。

## 一連の流れの検証とレビュー判断

前提は、実接続検証の人による確認と、E2E・CI・CDの子課題の受入完了です。実接続のworkpadには連携成功の記録がありますが、操作者による単一起動・レビュー待ち移行後のポーリング確認などの残件があります。一連の検証はまだ実施していません。

前提がそろったら、人が小さな変更1件と期待結果を合意します。例として、初期画面の架空の案内文を変更し、期待する表示を確認する案があります。これは未承認であり、今は実装しません。

検証担当はそのIssueから、同じworkpad、専用ブランチと成果SHA、PR、CI初回結果と必要な再実行結果、人がマージしたSHA、配布承認、配布結果、検証URLとスモーク結果まで順にたどります。元の作業ツリーのブランチや未コミットの変更がそのままであることも確認します。未確認の項目と再開条件を記録し、人が受入を判定します。

レビューでは次を決めてください。

- GitHubの対象リポジトリとベースブランチ、AIの書き込み権限、CI基盤を決める。
- GitHub上でマージする担当者と、検証環境・配布承認者を決める。
- 必須チェック名、文書のみPRの適用範囲、最初の小さな変更と期待結果を合意する。

## 公式資料と適用範囲

2026-11-07（日本時間）に、Symphony v0.0.3のIssueごとの作業場所と準備フック、OpenAIのCodexレビュー運用、GitHubのPR・必須チェックによるブランチ保護を確認しました。案件別コピーのクローン方法、専用ブランチからのPR作成、人によるマージ、配布条件は、このプロジェクトの運用案です。固定版でのGitHubへの書き込みと保護設定の適用は未検証です。 [Symphony v0.0.3の仕様](https://github.com/openai/symphony/blob/v0.0.3/SPEC.md) · [Codexレビュー運用](https://learn.chatgpt.com/docs/third-party/github) · [GitHubのブランチ保護](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

2026-11-08（日本時間）に、Symphony v0.0.3のSPEC.mdを再確認しました。CD・デプロイに関する記述はなく、CDワークフローの設計はプロジェクト独自の判断です。同日、Codexが[Codex開発ワークフローの反復改善](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)の本文を確認しました。既存の規約を維持し、実測と未確認を分けて記録する考え方を例として参考にしており、必須の設計としては扱っていません。AWSの設計と費用の扱いはプロジェクト独自の判断です。

CD案の一次資料として、Codexが2026-11-08（日本時間）に本文を確認した次の資料も参照しています（この作業では本文を独立に再読していません）。AWSの料金・仕様と、GitHubの設定は変わり得るため、実行前に再確認します。費用の試算は条件付きで、合計金額は見積もっていません。AWS Budgetsの請求反映の遅れと、予約済み同時実行数0で関数URLが無効になり429を返すことも、同日にCodexが本文を確認した資料によります。ワークフローが固定した公式アクション（checkout、upload-artifact、download-artifact、configure-aws-credentials）のコミットSHAは、Codexがタグから確認した値です。

- [AWS Budgetsによる費用の管理](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
- [Lambda関数URLの設定](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)
- [GitHub ActionsのOIDCによるAWS認証](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHubのデプロイと環境](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Lambdaのバージョン](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html)
- [LambdaのPython ZIP配布](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)
- [Lambda UpdateFunctionCode API](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateFunctionCode.html)（CodeSha256とRevisionIdによる条件付き更新）
- [Lambda PublishVersion API](https://docs.aws.amazon.com/lambda/latest/api/API_PublishVersion.html)（`CodeSha256`・`RevisionId`で競合を防ぐ）
- [Lambda UpdateAlias API](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateAlias.html)（`RevisionId`で外部の変更の上書きを防ぐ）
- [Lambda Web Adapter（v1.1.0の固定コミット）](https://github.com/awslabs/aws-lambda-web-adapter/tree/596bdbcea78a89084fd0b522807d04cd2f1f3f7e)（READMEのレイヤーARN・設定値は2026-11-08にCodexがGitHub CLIで確認。レイヤー実体のハッシュと互換性は未検証）
- [Lambdaの料金](https://aws.amazon.com/lambda/pricing/)
- [Aurora DSQLの料金](https://aws.amazon.com/rds/aurora/dsql/pricing/)
