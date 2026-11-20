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

この節は配布手順の案と、その実装の状況です。**案の合意と実行は別**です。AWSの構築・配布は、利用者本人がAWS CloudShellで手順を1段階ずつ実行する方式です（この文書の作業ではAWSを操作しません）。**2026-11-08〜11-09時点の「未実施」「未承認」「未作成」「無効のまま」「承認したコミットSHAはない」という記述は履歴で、次の「現況（2026-11-10更新）」と矛盾する場合は、現況が優先します。**

### 現況（2026-11-10更新）

利用者・Codexの報告（実環境。このレビューでは再実行していない）。アカウントID・URLの実値・秘密は書かない。

| 区分 | 内容 |
| --- | --- |
| 公開経路 | CloudFrontは有効。CloudFront経由の `/`・`/health`・`/release`・JSの参照が200、関数URLへの直接の匿名アクセスは403。先行して行ったブラウザ確認（Playwright）は4件成功。配布用ロールとGitHubのリポジトリ変数（AWS・CloudFront関連を含む）は設定済み。エイリアス `demo` の現在の版は版1（ソース `526db5bb`）。利用者の承認があったと報告を受けているが、内容・記録の所在はLinearで確認する（このレビューでは未確認） |
| 実際のCD | 試行1: OIDCの `sub` 不一致で認証に失敗し、利用者が信頼ポリシーの `sub` を不変のsubへ修正した。試行2: 変更前の検証を通ってコード更新まで進み、更新後の関数はコードのハッシュが期待値と一致し、`State=Active`・`LastUpdateStatus=Successful`だった。しかし旧実装が「更新の応答のRevisionId＝完了後のRevisionId」を前提にしていたため、不一致で発行の前に止まり、エイリアスは版1のまま変わっていない |
| 解釈の注意 | 不一致の原因が、AWSの通常の遷移か、同時期の手動変更かは**未確定**（利用者は手動の変更の有無を確認できていない）。通常の遷移であるとは主張しない |
| 未実施 | 修正後のCDの実行（新しい版の発行・エイリアスの切り替え）、ロールバックと公開停止の実行確認 |

### 現在の状況（2026-11-08時点。履歴）

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
| 履歴（初回構築の前。現在の状態ではない） | 作成前は、既存のCloudFrontディストリビューションは0件、WAFのWeb ACL（CLOUDFRONT scope）は0件だった |
| 履歴（利用者・Codexの報告、2026-11-09） | CloudFrontのディストリビューションは**作成済み**（無効化・`Deployed`、WAFのWeb ACLを紐付け済み）。OACは**作成済み**（`lambda`・`sigv4`・`always`）。コンソールでFreeプランの「$0」の表示を確認した（料金の保証ではない）。WAFには、既知のAWSマネージド規則グループ3種が`Count`（`OverrideAction`）で入っており、当時の必須のレート制限2規則（Block）はまだ無かった。Lambda関数URLは作成済み（`AWS_IAM`・`BUFFERED`、エイリアス`demo`）で、未署名の匿名GETが403になることをCodexが実測した |
| 確認済み（利用者の確認、2026-11-10の確認時点。「ディストリビューションは無効のまま」はその後に変わった。現況は上の「現況」） | Web ACLの規則は**4件**（接続元IPごとのレート制限のBlock 1件＋既知の観測グループ3種の`Count`）で、全体のレート制限の規則は無い。ディストリビューションは無効のまま。作成済みのリソースは**再作成・削除せず再利用**する |
| 方針の変更（利用者の承認、2026-11-10） | 全体1,500件/5分の規則を要件から外し、必須は実際の接続元IPごとの300件/300秒のBlock **1件だけ**とする。経緯（利用者の報告）: 全体の規則（URIパスとHTTPメソッドの条件つき）の保存が`WAFFeatureNotIncludedInPricingPlanException`で失敗した。どの機能がプランに含まれないのかは特定しておらず、原因やFreeプランの機能の一般論は主張しない |
| 履歴（2026-11-10の確認時点。その後、CloudFrontの有効化とCloudFront経由のHTTP確認は実施された。上の「現況」） | 有効化、Lambdaの許可、公開のHTTP確認、CD（配布）、ロールバック。ディストリビューションは無効のままで、公開していない |
| 計画案（プロジェクト独自の判断。未実施） | OACは種類`lambda`・`sigv4`・`always`。デフォルトのビヘイビア1件だけで`CachingDisabled`と`AllViewerExceptHostHeader`、メソッドは`GET`・`HEAD`・`OPTIONS`。WAFは実際の接続元IPごと300件/300秒のレート制限1件（必須。超過はBlock。`ForwardedIPConfig`・`ScopeDownStatement`なし）。任意で、既知のAWSマネージド規則グループ3種（`AWSManagedRulesAmazonIpReputationList`・`AWSManagedRulesCommonRuleSet`・`AWSManagedRulesKnownBadInputsRuleSet`）を`Count`の観測専用として0〜3件残してよい（合計1〜4件。`Count`は終端しないため必須のBlockを迂回しない、という独自の判断。実動作は未確認）。独自ドメイン・S3・NAT・新規のAurora DSQL・API Gatewayなどの新しいサービスは使わない。予約済み同時実行数は3を維持し（2026-11-15に1から3へ変更）、公開後の異常時は人が0にして止める |
| 順序（既定はFree） | 初回構築では、FreeプランのコンソールでディストリビューションとWAFを一緒に作る。**作成済みの現在は、この作成を飛ばし、再作成・削除せず、読み取りで確認する**（Lambdaの許可は付けない。自動で付いていないか関数のリソースポリシーを読み取りで確認し、付いていて避けられなければ進まず、制御できる手順を人が確認する）→ 作成済みの専用Web ACL（現在の4件）に必須のIPごとの規則がちょうど1件あり、ほかは既知の観測グループ（`Count`）だけであることを読み取りで確認する（規則は追加しない。全体の規則は追加しない。1件だけを渡す`update-web-acl`は規則の一覧を丸ごと置き換え、観測グループを消すため使わない）→ OAC・キャッシュなど全設定を確認 → 読み取りで再確認 → **最後に**、そのディストリビューションだけに`lambda:InvokeFunctionUrl`と`lambda:InvokeFunction`を許可する。保護を迂回した状態で先に公開しない。既存のリソースは変更しない |
| 費用（未確認） | CloudFrontのFlat-rateプラン「Free」（公式資料では月$0）が第一候補。選択できることは利用者の報告で、利用アカウントでの契約・適用可否、Lambda関数URL + OACとの組み合わせ、費用は未確認。アプリのLambdaとログは別料金。Web ACLを単独で先に作ると、紐付け前から従量の費用が生じ得るため、Web ACLをCLIで単独で作る案は、Freeを使えない場合の**有料の代替**として分けている（自動では実行しない。以前の試算「Web ACL 1件＋規則2件で基本約$7/月＋$0.60/百万リクエスト」は2規則の前提の履歴で、規則1件での金額は再確認していない） |
| 同時実行数3の意味（2026-11-15に1から3へ変更） | AWSの公式資料（関数URLのスロットリング。Codexが2026-11-10に公式の本文を確認）では、関数URLの上限は予約済み同時実行数の10倍のRPSで、超過は429。予約済み同時実行数3なら最大30RPS。1から3へ上げたのは、2026-11-15の最初の実機のCDで、画面のHTML・JS・CSSの並行した読み込みがコールドスタートで429（TooManyRequests）になり、新しい版のスモーク確認が失敗したため（前の版へ戻して成功）。これは外した全体1,500件/5分（平均5RPS相当）の規則と同じではなく、月額の上限でもない（30RPSが続けば計算上は月に約7,776万リクエストが届き得る）。IPごとの規則は分散したIPからのアクセスには効かず、継続的なアクセスによるオリジン側（Lambda・ログなど）の料金は残る。詳細は[計画](../infra/cd/cloudfront-plan.md)の「同時実行数1の意味」 |
| 配布コードの検証 | `scripts/deploy-lambda.py`が、AWSの変更の前に、読み取りだけで、OAC・ディストリビューション・Web ACL・関数のリソースポリシー・キャッシュ設定・予約済み同時実行数を検証する（偽のAWSでのオフラインテスト。実際のAWSでの動作は未確認）。必須のIPごとの規則の欠落・変更・重複、全体（`CONSTANT`）の規則など未対応の規則、既知の観測グループ（`Count`）3種以外のWAF規則・規則グループ、観測グループの重複・`Count`以外の動作・絞り込みや上書き、合計5件以上の規則、Firewall Managerの管理・前処理/後処理の規則グループ、CloudFrontの継続的デプロイ・ステージングの設定、`AuthType`が`AWS_IAM`以外のLambdaの許可、予約済み同時実行数がちょうど3でないこと（未設定・0・3以外・取得失敗）は拒否する（これらのAPI項目名が実際の応答と一致するかは未確認）。同時実行数は自動では設定しない。配布用ロールの追加権限は読み取りだけ（`lambda:GetFunctionConcurrency`は修飾子なしの対象関数だけ） |

アセットのキャッシュは将来案で未実装です。レート制限は近似で、遅延と巻き添えがあり得ます。Bot Controlは含めません。

### 初回構築（配布とは別の操作）

配布の前に、IAMロール、Lambda関数、エイリアス`demo`、関数URL（`AWS_IAM`）、ログの保持期間、配布用のOIDCロールを作る初回構築が必要です。公開用のCloudFront・OAC・WAFと、最後の許可は[計画](../infra/cd/cloudfront-plan.md)で別に作ります。この構築は配布ワークフローとは別の、人が承認する操作で、具体的なコマンド・IAMの雛形・GitHubの設定は[初回構築の手順書](../infra/cd/bootstrap.md)にあります（2026-11-08時点では未実行・未承認。その後、主要なAWSリソースは準備された。上の「現況」と[手順書](../infra/cd/bootstrap.md)の現況を参照）。CDは、そこで作った既存で正常なエイリアスを更新するだけで、IAM・関数URL・同時実行数・ログの操作を含みません。

データベースはAurora DSQLのままとします。ただし、現在の最小構成のアプリはDBを使っていないため、初期画面とヘルスの初回配布の前にAurora DSQLを作る必要はありません。クラスタの作成、接続、スキーマはDB接続・スキーマを扱う別の課題で、Lambda/CD担当とは別の担当が行います。両担当の連携前に、既存のLambda・DSQLクラスタの状況を確認します（現時点でDSQLの有無は未確認。Lambdaは既存のAmplify用の関数4件を確認済みで、今回のデモ用の関数の有無は未確認）。

### 配布の流れ（実装済み。実際のAWSでは試行2で停止し、修正後の実行は未実施）

詳細はワークフローとスクリプトのコメントを参照し、ここでは要点だけ記します。

1. **起動**: 人が`workflow_dispatch`で、`main`から、マージ済み`main`上の完全な40桁のSHAを指定する。ブランチ名・タグ・短縮SHAは受け付けない。`main`以外から起動した場合は、スキップ（成功に見える）ではなく、buildジョブが明示的に失敗する。自動の配布はなく、手動のみ。同時に動かせる配布は1つで、実行中のものはキャンセルしない。
2. **事前確認（buildジョブ。AWSの資格情報なし）**: 起動したコミットの配布コードで、オフラインテスト、Environment`demo`の保護設定の確認（必須レビュー担当者がリポジトリ変数`CD_APPROVER_LOGIN`の1人のユーザー、自己承認禁止は無効、デプロイブランチは`main`のみ、管理者の迂回なし）、対象SHAが`origin/main`の祖先であること、同じSHAの`ci.yml`のpushのrun（最新）と、その最新の試行の`frontend`・`backend`・`e2e`のジョブがすべて成功していること（スキップ・部分成功は不可）を確認する。Environmentが無い・読めない場合は止まり、自動作成には頼らない。
3. **ビルドと保管**: 確認後に対象SHAをチェックアウトし、`scripts/build-lambda.sh`でリリースモードのZIPとマニフェストを作る。同じrun内の専用名で上書きなしに保存し、ZIPの実測ハッシュ、CIのrun、ソースSHA、成果物IDをサマリーに出す。**承認者はこのサマリーを見てから承認する**。
4. **承認と再確認（deployジョブ）**: Environment`demo`の承認後、AWSの資格情報の前に、設定・承認記録・必須変数（`AWS_ROLE_ARN`、`LAMBDA_FUNCTION_NAME`、`CD_BASE_URL`（CloudFrontのオリジン）、`CLOUDFRONT_DISTRIBUTION_ID`、`CLOUDFRONT_OAC_ID`、`WAF_WEB_ACL_ARN`、`LAMBDA_LAYER_CODE_SHA256`、`CD_APPROVER_LOGIN`）を確認する。配布コードは対象SHAではなく起動したコミットのもの。続けて、ダウンロードした成果物（manifestとZIP）を、ソースSHA・ビルドジョブのZIPハッシュ・`releaseEligible=true`・`dirty=false`と照合する（`deploy-lambda.py --verify-artifact-only`。AWS CLI・資格情報・通信を使わない）。改ざんがあれば資格情報の前に止まる。その後にOIDCで一時資格情報を得る（ロールセッション名はrun番号と試行）。`CD_ALLOW_LOCAL`は常にfalseで、ワークフローとヘルパーは拒否する。
5. **変更前の検証（`deploy-lambda.py`。失敗したら変更しない）**: マニフェストとZIPの内容、ビルドの出力とのハッシュ照合、`releaseEligible=true`・`dirty=false`、関数がPython 3.14・x86_64・ハンドラ`run.sh`・固定レイヤー・`/opt/bootstrap`であること、レイヤーのCodeSha256が記録値と一致すること、関数URLがエイリアス`demo`で`AWS_IAM`・BUFFERED・CloudFrontのオリジンと一致するホストであること、CloudFrontのディストリビューション（有効・Deployed・WAF関連付け・単一オリジン・https-only・OACの関連付け・`CachingDisabled`と`AllViewerExceptHostHeader`・`GET`・`HEAD`・`OPTIONS`のみ・追加のビヘイビアなし。読み取り構成は関数の関連付けなし。7メソッドの書き込み構成は、承認した `viewer-request` のエッジ関数の関連付けがちょうど1件で、LIVEのコードがZIPの中のレビュー済みソースと一致すること。[公開時の書き込みと利用枠](#公開時の書き込みと利用枠)）、OAC（`lambda`・`sigv4`・`always`）、Web ACL（us-east-1・CLOUDFRONT scope・必須の接続元IPごとの規則1件（300件/300秒・Block）、ほかは既知の観測グループ（`Count`）0〜3件だけ。全体の規則などは拒否）、予約済み同時実行数がちょうど3であること（`get-function-concurrency`。自動では設定しない）、関数のリソースポリシーが特定のディストリビューションだけを許可していること（いずれも読み取りのみ。スモーク確認は公開URL（CloudFront）で行う）、エイリアスが加重なしの数値の版を指し、その版のdescription（`source=<sha>;zip=<hex>;run=<id>`）がコードのハッシュと一致すること、現在の版のスモーク確認が成功すること。
6. **更新と版の発行**: 検証時に取得した関数のRevisionIdを条件にコードを更新する（現行版のスモーク確認の間に関数が変わっていれば、更新は拒否される）。返ってきたCodeSha256がマニフェストと一致することを確認し、更新完了を待つ（AWS CLIの`wait function-updated-v2`）。更新の応答のRevisionIdは完了後と同じとは限らないため、比較にも発行の条件にも使わない（旧実装がこの一致を前提にして、実際のCD実行で止まった。不一致が通常の遷移か、同時期の手動変更かは未確定）。完了後に設定を読み直し、検査対象の設定（ランタイム・アーキテクチャ・ハンドラ・固定レイヤー・`/opt/bootstrap`・`PORT`）、コードのハッシュ、`State=Active`・`LastUpdateStatus=Successful`（どちらも明示的な値が必須）、空でない完了後のRevisionIdを確認し、すべて合格したときだけ、完了後に読んだRevisionIdとコードのSHA-256を条件に版を発行する（読み直しの後に関数が変われば発行は拒否される。descriptionに出所を記録）。メモリ・実行ロール・追加の環境変数など、検査対象外の設定の変更は、検証時と完了後を項目ごとに比べず、RevisionIdの条件で守る（同じRevisionIdを読んだ後の変更だけを検出する。読み直しの前の別の操作は区別できない）。発行された版は、Pendingのことがあるため`wait function-active-v2 --qualifier <版>`（上限つき）でActiveを待ち、ソースSHA・ZIPのハッシュ・印・コードのハッシュ・検査対象の設定が今回と一致すると確認できたときだけ使う。AWSの公式資料は、コード・設定が最後の版から変わらないと新しい版を発行しないと述べるが、既存の版を返すとは明記していない（実際の応答は未確認）。そのため、返された版が前回の試行が発行して止まった同一内容の版（run番号以外が一致）と確認できる場合だけ、そのまま使う。確認できない版、またはエイリアスの現在の版なら、エイリアスを動かさず止まり、手動確認とする（設定を変えて版を作らせることはしない。[手順書](../infra/cd/bootstrap.md)）。続いて、エイリアスの`RevisionId`を条件に（加重なしで）切り替え、承認したSHAでスモーク確認を行う。エイリアスが既に今回と同じソースSHA・ZIPの版を指し、現行版のスモーク確認が合格した場合は、変更なしの成功（`overall=no-change-success`）として終え、新しい版の発行も新しい配布のスモーク確認もしない。新しい配布の成功とは扱わない。
7. **失敗時の戻し**: 切り替えた後にスモーク確認が失敗したら、**こちらの切り替えが成功として返した**`RevisionId`だけを条件に、記録した直前の版へ戻し、戻した応答（版・`RevisionId`）を検証できたときだけ、直前のSHAでスモーク確認を行う。戻しが成功しても全体は失敗として終了する。エイリアス更新の結果が確認できない場合（エラー応答、期待と異なる応答、外部の変更による条件の不一致）は、読み直した値を根拠にせず、自動では戻さず、結果JSONの`alias=unknown`と`recoveryNote`を残して全体を失敗とし、手動復旧とする（新しい版を指していても上書きしない）。プロセスの強制終了・タイムアウトも自動では復旧せず、同じく`recoveryNote`で人が確認する（エイリアスの更新を呼ぶ前に書く）。切り替え前の失敗では、無関係な変更を戻さない。版・成果物・DBは削除しない。
8. **記録**: 結果JSON（ソースSHA、ZIPのハッシュ、CIのrun、段階、スモーク（現行版・新しい版・戻し）、ロールバック、版、エイリアスの状態（`untouched`・`unknown`・`switched`・`rolled-back`）、AWSの変更操作の開始の有無`mutationStarted`）を、処理の各段階と最後に書き、ビルド成果物とは別の成果物として常に保存する。秘密・ローカルの絶対パス・Lambda設定の全体は含めない。

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

2026-11-08時点の未実施項目（履歴。`cd.yml` の実行とAWS関連の設定などは、その後に進んだ。現況は上の「現況」が優先。未実施のものは成功とは扱わない）:

- `cd.yml`（CDワークフロー）のGitHub上での実行
- この文書の最新コミットに対するCI（`cd-offline`を含む）
- AWS関連のリポジトリ変数の設定（Environmentと`CD_APPROVER_LOGIN`は設定済み）
- AWSアカウントでの認証、アダプタのレイヤー実体のハッシュと、アダプタ・Lambdaサービスでの動作の確認
- 実際の配布とロールバック（偽のAWSでのテストは実際のAWSの検証ではない）
- 人の承認、マージ

### Lambdaの設定と操作

具体的なコマンドは[初回構築の手順書](../infra/cd/bootstrap.md)です（実施状況は上の「現況」と手順書の現況を参照）。

- 設定: ランタイムpython3.14、アーキテクチャx86_64、ハンドラ`run.sh`、上記アダプタのレイヤー、環境変数`AWS_LAMBDA_EXEC_WRAPPER`・`PORT`、512MiB・予約済み同時実行数3・10秒・ログ7日。初回構築は予約済み同時実行数0で始め、公開の許可を作り、承認した初回デモの確認時だけ3にする。
- 配布: 上記の「配布の流れ」のとおり。
- ロールバック: 切り替え後のスモーク確認の失敗で、こちらが切り替えたエイリアスだけを記録した直前の正常版へ戻し、直前のSHAで同じスモーク確認を行って別に記録する。
- 公開停止: 予約済み同時実行数を0にする（人が行い、再開は人の承認後）。公開経路の導入後も、この停止方法は変わらない。0の間は、CDが変更の前の読み取りで止まる（予約済み同時実行数がちょうど3ではないため）。
- 関数URL: `AWS_IAM`。匿名の公開許可は作らず、最後にCloudFrontの特定のディストリビューションだけを許可する（[計画](../infra/cd/cloudfront-plan.md)）。

### 自己承認の扱い

合意（2026-11-08）: 唯一の人が配布を起動し、同じ人が承認する。GitHub Environmentの「レビュー担当者による自己承認の禁止」は意識して無効にする。これは独立した承認者を置かないことを意味し、承認の独立性は確保されない。その代わり、CDは承認前にサマリー（CIのrun、ソースSHA、実測したZIPのハッシュ）を示し、承認後にEnvironmentの設定・承認記録（承認者が`CD_APPROVER_LOGIN`の1人であること）・成果物のハッシュを再確認し、`main`以外のブランチからの配布を拒否する。Environmentがこの設定でない場合は、AWSの資格情報の前に止まる。承認者のログインはリポジトリ変数に置き、公開文書には書かない。

### 権限の分離

IAMの初期構築、Lambda関数URLの設定、CloudFront・OAC・WAFの作成と変更、関数のリソースポリシーの許可、予約済み同時実行数の変更（公開停止を含む）は、配布ワークフローの権限に含めず（配布用ロールは予約済み同時実行数の読み取り`lambda:GetFunctionConcurrency`だけを持つ）、人が承認した別の操作（前述の初回構築）にします。Aurora DSQLのクラスタ作成・接続と権限は、Lambda/CD担当とは別の担当・別の課題で、人が承認する別の操作とします。連携前に、既存のLambda関数・エイリアス・関数URL・DSQLクラスタの有無と状態を読み取りで確認して記録し、重複作成や上書きを避けます。配布用ロールの権限は、対象関数のコードの更新・版の発行・エイリアスの参照と更新・設定の読み取りと、固定したレイヤー版の読み取りに加え、公開経路の検証のための読み取り（対象のディストリビューションとWeb ACLの`Get`、OACの`Get`（リソース指定不可のため`Resource: *`）、エイリアス`demo`のリソースポリシーの`GetPolicy`）に限り、CloudFront・WAF・IAMの書き込みと`iam:PassRole`や管理者権限は含みません（雛形は`infra/cd/deploy-policy.json`、`infra/cd/oidc-trust-policy.json`）。

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
| 予約済み同時実行数 | 3 |
| タイムアウト | 10秒 |
| ログの保持期間 | 7日 |
| プロビジョニング済み同時実行 | 使わない |
| NAT・ECR・S3・独自ドメイン | このデモのためには作らない |
| CloudFront・WAF・OAC | 公開経路として作成済み（利用者の報告。無効化・未公開。上の「公開経路」と[計画](../infra/cd/cloudfront-plan.md)）。実際の費用は未確認 |

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

AWSなど外部の操作を行う前に、次を人が確認します。未確認の間は操作を行いません。リソース作成・配布は、利用者本人がCloudShellで段階的に実行する前提で、各段階の実行前に確認します（2026-11-08時点の記述。実施状況は上の「現況」）。

- 既存のAWSアカウント（リージョンは東京）の利用と、コンソールで読み取り専用に確認する実際のアカウント種別・無料枠の対象（プラン変更を起こす操作は行わない）。リソース作成・課金の権限と支払い方法の状態（内部の確認事項）
- 任意の予算アラートを使う場合の宛先（利用者がAWS側で非公開に入力）と、円建ての閾値をUSDへ換算する為替・税の扱い
- OIDCの正確なsub・aud（GitHubに認証済みの端末で`gh api repos/<所有者>/<リポジトリ>/actions/oidc/customization/sub`により確認したsub接頭辞（不変のsubは所有者・リポジトリの数値IDを含む。実在の値は公開文書に書かない）＋`:environment:demo`と、`sts.amazonaws.com`。ワイルドカード・推測・旧形式への黙った戻しはしない。CloudShellにはGitHubの認証がなく、トークンを送らず、確認した接頭辞の値だけを貼る。手順は[初回構築の手順書](../infra/cd/bootstrap.md)の「OIDCのsub接頭辞の確認」）と、AWS関連のリポジトリ変数の設定（Environmentと`CD_APPROVER_LOGIN`は設定済み）
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
- [GitHub ActionsのOIDC（subクレーム・不変のsubの数値ID）](https://docs.github.com/en/actions/reference/security/oidc)（2026-11-10にCodexが本文を確認。新しいリポジトリでは不変のsubの数値IDが必要。AWSの信頼ポリシーの書き方はプロジェクト独自の判断）
- [GitHubのデプロイと環境](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Lambdaのバージョン](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html)
- [LambdaのPython ZIP配布](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html)
- [Lambda UpdateFunctionCode API](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateFunctionCode.html)（CodeSha256とRevisionIdによる条件付き更新）
- [Lambda PublishVersion API](https://docs.aws.amazon.com/lambda/latest/api/API_PublishVersion.html)（`CodeSha256`・`RevisionId`で競合を防ぐ）
- [Lambda UpdateAlias API](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateAlias.html)（`RevisionId`で外部の変更の上書きを防ぐ）
- [Lambda Web Adapter（v1.1.0の固定コミット）](https://github.com/awslabs/aws-lambda-web-adapter/tree/596bdbcea78a89084fd0b522807d04cd2f1f3f7e)（READMEのレイヤーARN・設定値は2026-11-08にCodexがGitHub CLIで確認。レイヤー実体のハッシュと互換性は未検証）
- [Lambdaの料金](https://aws.amazon.com/lambda/pricing/)
- [Aurora DSQLの料金](https://aws.amazon.com/rds/aurora/dsql/pricing/)


## 公開時の書き込みと利用枠

既存のCloudFrontとLambda関数URLのOACを使う。公開する書き込みは案件の受付POST、判定保存PUT、設問・判定ルールの提案POSTと決定POST、共有サンプル案件の作成POST（`POST /api/sample-cases`。次の節）。CloudFrontで7メソッドを許可したときも、アプリはこの組み合わせだけを受け付ける。受付が同じインスタンスの内部HTTPモックを呼ぶときは、インスタンス内で生成したトークンで確認する。トークンのヘッダー（`X-Internal-Mock-Token`）は、エッジが利用者の送ったものを捨てるため、公開側からは届かない。トークンが複数あるとアプリは拒否する。専用の接続元ヘッダー（`X-Demo-Viewer-Ip`）がある要求は、トークンが付いていても内部の呼び出しとして扱わない。

Bedrockを呼ぶ公開構成では、Aurora DSQLでIP別5回と全体25回の枠を、依頼IDと同じトランザクションで確保する。日本時間の午前0時が日付の境界。接続元は、エッジ（CloudFront Functions、viewer-request）が `event.viewer.ip` から付ける専用ヘッダー `X-Demo-Viewer-Ip`（ポートなしのIP1つ）だけを使い、アプリは正規化して、DBには鍵付きHMACを保存する。他のヘッダーへのフォールバックはない。ヘッダーがない・複数ある・IPの形でない要求は、枠の予約もBedrockの呼び出しもせず400で拒否する。IPv4射影のIPv6はIPv4として、IPv6は /64 を同じ枠にまとめる（これまでの正規化と同じで、保存済みのカウンターの主体は変わらない）。日別カウンターは7日を過ぎたものを正常な予約の機会に削除する。依頼IDは、日付変更をまたぐ再送でも同じ推論を開始しないよう、追記のみで保持する。1日最大25件で、依頼文・生IP・生成出力はこの記録に含めない。予約が確定した後に推論を1回試み、応答が不明でも枠を返さない。処理済みIDの再送は409で返す。承認やモック応答は枠を使わない。

### 接続元IPの渡し方（比較と採用）

| 案 | 内容 | 判断 |
| --- | --- | --- |
| 前の設計（採用しない） | 専用のオリジンリクエストポリシーで、`CloudFront-Viewer-Address` などを許可リストにして転送する | 専用ポリシーの設定・検証が増える。接続元の上書きが保証されるかを、実機で確認する必要があった（AWSの資料の説明であって、このリポジトリでは未確認）。値が「IP:ポート」の形で、解析も要る |
| 採用（プロジェクト独自の判断） | AWSのManagedポリシー `AllViewerExceptHostHeader` のまま、CloudFront Functions（viewer-request）が `event.viewer.ip` を専用ヘッダー `x-demo-viewer-ip` へ書く。利用者が送った同名のヘッダーは、複数値（`multiValue`）も含めて項目ごと捨てて置き換え、`x-internal-mock-token` も捨てる | 置き換えの動作を、コードとテストで確認できる（`infra/edge/viewer-ip.test.js`）。CDが関連付けとLIVEのコードを読み取りで検証できる。接続元IPが取れない・IPの形でないときは、エッジが400で止める。本文ハッシュ（`x-amz-content-sha256`）と依頼ID（`Idempotency-Key`）は変えない |
| 不採用 | Lambda関数URLへの直接の公開、アプリ側でのX-Forwarded-Forの解釈 | OAC・WAFの前提と合わない。信頼できる値かを確かめられない |

出典（Codexが2026-11-18に確認した記録を引き継ぐ。この作業では再読していない）: [CloudFront Functionsのイベント構造](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html)（`viewer.ip`、ヘッダーの `value`・`multiValue`）、[GetFunction API](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_GetFunction.html)、[Managedのオリジンリクエストポリシー](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html)、[Lambda関数URLへのOAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html)、[Flat-rateプラン](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html)。CloudFrontのFlat-rateプラン「Free」を選べることは利用者の報告で、アカウントでの実際の契約・適用範囲は未確認（前述の「費用（未確認）」）。CloudFront Functionsが、そのプランまたは従量課金のどちらで使えるか・費用は、**未確認**（人がコンソールで確認する。確認できなければ、書き込みを許可しない）。

### CDの検証（変更の前。実AWSは未確認）

- 3メソッド（読み取り）: Managedポリシー、エッジ関数の関連付けなし。印のない版（`guard=1`・`viewerip=1` がない版）からも配布できる。エッジ関数は読まない。
- 7メソッド（書き込み）: Managedポリシーのまま、`viewer-request` の関連付けがちょうど1件で、承認した関数（リポジトリ変数 `CLOUDFRONT_VIEWER_FUNCTION_ARN`）と完全一致。LIVEのコード（`aws cloudfront get-function --stage LIVE` の出力ファイル）のSHA-256が、**ZIPの中のレビュー済みソース**と一致。取得の失敗・出力が空・不一致は、AWSの変更の前に失敗で止まる（切り替えもロールバックもしない）。今回のZIPと戻し先の版の両方が、`guard=1` と `viewerip=1` の印を持つ。印は、ZIPの内容から作る `manifest.json` の `capabilities`（`publicWriteGuard`・`viewerIpContract`）と、配布した版のdescriptionで確認する。`viewerIpContract` は、アプリが読む専用ヘッダー名とエッジのヘッダー名が一致するときだけ宣言される。
- 一時ファイルは専用の一時ディレクトリに作り、成功・失敗のどちらでも削除する。配布用ロールに足す権限は、承認した関数の `cloudfront:GetFunction`（読み取り）だけ。`cloudfront:GetOriginRequestPolicy` は不要になった。`UpdateFunction`・`PublishFunction`・`UpdateDistribution` は含めない。

### 共有サンプル案件の作成（`POST /api/sample-cases`）

審査対象一覧の「サンプル案件を作成」ボタンが呼ぶ。公開デモでは、来訪者全員が同じ架空の4件を使う（来訪者別のデータ、リセット、既存案件の削除はない）。初回に押した人が共有の4件を作り、以降は作成済みの案件をそのまま表示する。構成は `scripts/seed-demo-cases.sh` と同じで、未審査2件、A09の取得エラーで確認待ち1件、登録済み（問題なし）1件。

**仕組み（アプリ側。ローカルのテストで確認済み）**

- 案件IDは固定（`CASE-SAMPLE-01`〜`04`）。1件の作成は1トランザクションで、案件・設問のコピー・結果・A09の回答（登録済みの1件は結果も）を一度に入れる。行の追加は `INSERT ... ON CONFLICT (case_id) DO NOTHING` だけで、行ロック（`FOR UPDATE`）に頼らない。Aurora DSQLの衝突（40001）は既存の再試行（`app/db_retry.py`）で再試行し、再試行後は既存の行を見て何もしない。
- すでにある案件には書き込まない。判定は作成と同時に入れるだけなので、保存済みの判定を後から上書きすることはない。同時に到達した要求も、各案件は1回だけ作られ、共有セットは最大4件。
- 途中で失敗しても、作成済みの案件は残り、失敗した案件の行は残らない。再試行は足りない案件だけを作る。保存先が混み合って作れなかったときは503で、画面は理由と再試行できることを表示する。
- Bedrockも内部のHTTPモックも呼ばない（A09の回答は、モックと同じ規則の固定値）。AIの日次の利用枠（IP別・全体）は使わず、消費もしない。
- アプリ用のDBロールは、既存の案件テーブルへのSELECT・INSERTだけで足りる。ロールや表の追加・DDLの変更は要らない。

**公開経路のアクセス制限と費用対策（承認済みの方針。実機は未確認）**

2026-11-20に、運営者が次の方針を承認した（文書上の決定。公開環境ではまだ確認していない）。

- 公開時のアクセス条件は「来訪者全員が押せる（ログインなし）」。
- 既存のCloudFront、WAF（接続元IPごとのレート制限）、OAC、エッジ関数（`X-Demo-Viewer-Ip` の付与）、Lambdaの予約済み同時実行数3、書き込みガード（`app/public_guard.py`）を変えない。公開ポリシーが転送するヘッダーも増やさない（画面はこの要求にも `Content-Type` と本文のSHA-256を付ける）。
- 作る案件は固定の4件だけで、Bedrockは呼ばず、AIの利用枠も使わない。この経路専用のレート制限は置かない（理由は下の費用対策）。
- ガードに足したのは、固定経路への `POST /api/sample-cases` だけ。同じ経路へのPUT・PATCH・DELETE、`/api/sample-cases/` 以下の別経路（リセットや削除を含む）は、アプリに届く前に405で拒否する。案件の削除経路はもともとない。
- この経路は接続元IPを使わない。利用者が `X-Demo-Viewer-Ip` を偽って送っても、件数・AIの利用枠のカウンターに影響しない（接続元の置き換え自体は、既存の確認「運営者の手動切り替え」の手順5で行う）。

費用対策の根拠（アプリ側。ローカルのテストで確認済み）:

- 書き込み量の上限が固定: 全部で4案件（約60行）。作成済みの再操作は、読み取り1回だけで、書き込みも行の追加もない。来訪者が何度押しても、DSQLの行数・サイズは増えない。
- AIを呼ばないので、Bedrockの費用は発生しない。Lambdaの実行は、既存の同時実行数3とWAFのIPごとのレート制限が上限になる。
- 作成済みの案件IDが公開DBに既にある場合は、上書きせず、その案件が共有サンプルとして表示される。有効化の前に、公開DBに同じ案件IDの案件がないことを、読み取りで確認する。

**有効化前と、担当者による実機確認（実AWS・実Aurora DSQLでは未実施）**

1. 有効化前: 上の方針が、実際のCloudFront・WAF・OAC・Lambdaの設定と一致することを、読み取りで確認する（書き込みの構成は、下の「運営者の手動切り替え」の第2段階まで済んでいること）。
2. 公開環境で、少数の操作だけを行う（大量の要求は送らない）: ① サンプルが無い状態で、画面のボタンを1回押し、4件（未審査2・取得エラー1・登録済み1）が再読み込みなしで出る。② もう一度押し、「作成済み」と出て、一覧の総数が増えない。別ブラウザでも同じ4件が見える。③ 1件を開ける。登録済みの1件は閲覧のみで、再保存は409。④ 2〜3つのタブからほぼ同時に押しても、4件のまま。⑤ `PUT` と `DELETE` で `/api/sample-cases` を呼ぶと405。⑥ 偽の `X-Demo-Viewer-Ip` を付けた要求でも、件数が増えず、AIの利用枠のカウンターが変わらない。
3. 採用した制限について、確認できることとできないことを分ける。確認できる: ②④⑤で、件数の上限と経路の制限（ガード）が機能すること。確認できない: WAFのIPごとのレート制限の超過拒否。少数の操作では、しきい値（300件/300秒）に達しないため、発動を証明できない。WAFの規則の存在と設定値は、読み取りで確認し、超過時の拒否は**未確認**と記録する。
4. 異常があれば、CloudFrontを読み取り構成に戻す（「運営者の手動切り替え」の手順6）。作成済みのサンプルを戻す・消す操作は、このデモにはない。

実AWSでの動作（CloudFrontのポリシー適用、エッジ関数の実行、DSQLの衝突検出と費用、権限）と、DSQLでの `INSERT ... ON CONFLICT (case_id) DO NOTHING` の動作は、この変更では確認していない。AWSの機能表（[サポートされるSQL機能](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-sql-features.html)）は、`INSERT` の `ON CONFLICT` 句を挙げるが、`DO NOTHING` や衝突対象の列の指定までは書いていない（2026-11-20に本文を確認。表は網羅的ではないとされている）。ローカルのPostgreSQL・偽のAWSによるテストで確認したのは、アプリとCDの検査の論理だけである。

### 運営者の手動切り替え

AWSの作成・変更は、利用者本人が内容を確認し、判断してから実行する（1ブロックずつ。実在のIDは出力・チャットに貼らない）。値はすべて目印（`REPLACE_*`）。リージョンは、CloudFrontが `us-east-1`、Lambdaが `ap-northeast-1`。

1. CloudFrontを既存の読み取り構成に保った状態で、Aurora DSQLの共有設定・提案・利用枠カウンター・依頼IDの追加4テーブルを、スキーマ文書のDDLから1文ずつ適用する。専用のアプリロールへ `db/init_dsql.py` の対象テーブルに限った権限を反映する。既存の案件・判定・ルールの履歴は削除しない。
2. **第1段階（読み取り構成のまま、候補を配布する）。** Lambda側に `AI_PROVIDER=bedrock`、許可したモデルとリージョン、有効な `DSQL_ENDPOINT` とアプリ用DB接続設定、シークレットとして32バイト以上の `AI_QUOTA_HMAC_KEY` を設定する。実行ロールのBedrock権限は対象モデルの呼び出しに絞る。公開用の回数は既定値の5・25を維持する。CloudFrontは許可メソッド3種・Managedポリシー・関連付けなしのまま、ガード・利用枠・エッジのソースを含むリリースZIPをCDで承認・配布し、通常のスモークを成功させる。続けて、エイリアスの版のdescriptionが `…;guard=1;viewerip=1` で終わることを読み取りで確認する。
   ```bash
   set -euo pipefail
   FN='REPLACE_FUNCTION_NAME'
   VER="$(aws lambda get-alias --function-name "$FN" --name demo --region ap-northeast-1 --query FunctionVersion --output text)"
   aws lambda get-function-configuration --function-name "$FN" --qualifier "$VER" --region ap-northeast-1 \
     --query Description --output text      # 期待: source=…;zip=…;run=…;guard=1;viewerip=1
   ```
3. **第2段階の準備（まだ公開しない・関連付けない）。** ZIPの中のレビュー済みソースを、新しい空のディレクトリへ取り出し、`manifest.json` の `edge.sha256Hex` と一致することを確認してから、関数を開発ステージ（DEVELOPMENT）に作ってテストする。
   以降のbashブロックは、それぞれ独立したスクリプトとして `bash` で実行できる（先頭で `set -euo pipefail`。値は `REPLACE_*` を置き換える）。照合に失敗したときは、その場で終了コード1になり、次のAWSの操作へ進まない。
   ```bash
   set -euo pipefail
   WORK="$(mktemp -d)"; echo "作業ディレクトリ: $WORK"   # 空の新しいディレクトリ（ZIP・manifestは信頼しない入力）。以降のブロックの WORK に控える
   ART='REPLACE_ARTIFACT_DIR'               # CDの成果物（manifest.json と lambda-<SHA>.zip）
   SHA='REPLACE_SOURCE_SHA'
   unzip -p "$ART/lambda-$SHA.zip" edge/viewer-ip.js > "$WORK/viewer-ip.js"
   EXPECTED="$(jq -r '.edge.sha256Hex // empty' "$ART/manifest.json")"
   ACTUAL="$(sha256sum "$WORK/viewer-ip.js" | cut -d' ' -f1)"
   if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
     echo 'ZIPの中のソースが manifest.json の edge.sha256Hex と一致しません。中止します。' >&2
     exit 1
   fi
   EDGE_FN='REPLACE_EDGE_FUNCTION_NAME'
   DEV_ETAG="$(aws cloudfront create-function --name "$EDGE_FN" --region us-east-1 \
     --function-config 'Comment=viewer ip for demo,Runtime=cloudfront-js-2.0' \
     --function-code "fileb://$WORK/viewer-ip.js" --query ETag --output text)"
   echo "DEV_ETAG=$DEV_ETAG"               # 次のブロックの DEV_ETAG に控える（チャットには貼らない）
   # DEVELOPMENTのコードも取り出し、レビュー済みソースとバイト単位で一致することを確認する
   aws cloudfront get-function --name "$EDGE_FN" --stage DEVELOPMENT "$WORK/dev.js" --region us-east-1 > /dev/null
   if ! cmp -s "$WORK/dev.js" "$WORK/viewer-ip.js"; then
     echo 'DEVELOPMENTのコードがレビュー済みソースと一致しません。公開しないでください。' >&2
     exit 1
   fi
   ```
   `event.json`（架空のIPと、偽の重複ヘッダー・偽のトークンを含む例）を、下のJSONで `"$WORK/event.json"` に作り、DEVELOPMENTでテストする。`FunctionOutput` はJSONの文字列。期待: `FunctionErrorMessage` が空、`x-demo-viewer-ip` が `192.0.2.10` の1件だけ（`multiValue` なし）、`x-internal-mock-token` なし。外れたら終了コード1で止まり、公開しない。
   ```bash
   set -euo pipefail
   WORK='REPLACE_WORK_DIR'; EDGE_FN='REPLACE_EDGE_FUNCTION_NAME'; DEV_ETAG='REPLACE_DEV_ETAG'
   aws cloudfront test-function --name "$EDGE_FN" --if-match "$DEV_ETAG" --stage DEVELOPMENT \
     --event-object "fileb://$WORK/event.json" --region us-east-1 > "$WORK/test.json"
   if ! jq -e '
       (.TestResult.FunctionErrorMessage // "") == ""
       and (.TestResult.FunctionOutput | fromjson | .headers
            | (."x-demo-viewer-ip" == {value: "192.0.2.10"}) and (has("x-internal-mock-token") | not))
     ' "$WORK/test.json" > /dev/null; then
     echo 'DEVELOPMENTのテスト結果が期待と違います。公開しません。' >&2
     exit 1
   fi
   ```
   `viewer` を除いた入力（IPなし）では、関数がオリジンへ送らず `statusCode` 400を返す（`FunctionOutput` が `statusCode` 400を含む）ことも、同じ手順で確認する。
   ```json
   {"version":"1.0","context":{"eventType":"viewer-request"},"viewer":{"ip":"192.0.2.10"},
    "request":{"method":"POST","uri":"/api/admin/question-proposals","querystring":{},"cookies":{},
     "headers":{"x-demo-viewer-ip":{"value":"203.0.113.1","multiValue":[{"value":"203.0.113.1"},{"value":"203.0.113.2"}]},
                "x-internal-mock-token":{"value":"forged"}}}}
   ```
4. **第2段階（公開と、関連付け・7メソッドを同時に反映する）。** `publish-function` は、関数をLIVEにする。LIVEのコードは、`get-function --stage LIVE` でしか読めず、コンソールのテストはDEVELOPMENTを使う。公開したら、LIVEのコードを取り出して、手順3のファイルとバイト単位で一致することを確認する。続けて、ディストリビューションの**1回の更新**で、`viewer-request` の関連付け1件と7メソッドを同時に設定する（オリジンリクエストポリシーは触らない。Managedのまま）。更新後、状態が `Deployed` になるまで待つ。
   ```bash
   set -euo pipefail
   WORK='REPLACE_WORK_DIR'; EDGE_FN='REPLACE_EDGE_FUNCTION_NAME'; DEV_ETAG='REPLACE_DEV_ETAG'
   [ -s "$WORK/viewer-ip.js" ] || { echo 'レビュー済みソースがありません（手順3）。中止します。' >&2; exit 1; }
   aws cloudfront publish-function --name "$EDGE_FN" --if-match "$DEV_ETAG" --region us-east-1
   aws cloudfront get-function --name "$EDGE_FN" --stage LIVE "$WORK/live.js" --region us-east-1 > /dev/null
   # 不一致なら、ここで終了コード1になり、下の関連付けと7メソッドの更新へ進まない（`cmp && echo` は使わない）
   if ! cmp -s "$WORK/live.js" "$WORK/viewer-ip.js"; then
     echo 'LIVEのコードがレビュー済みソースと一致しません。関連付けと7メソッドの更新はしません。' >&2
     exit 1
   fi
   echo 'LIVE == レビュー済み'

   DIST_ID='REPLACE_DISTRIBUTION_ID'
   ACCOUNT_ID='REPLACE_ACCOUNT_ID'
   EDGE_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:function/${EDGE_FN}"
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
   # 反映後の設定を読み戻し、7メソッドと関連付け1件・Managedポリシーのままであることを確認する
   aws cloudfront get-distribution-config --id "$DIST_ID" --region us-east-1 > "$WORK/dist-after.json"
   if ! jq -e --arg arn "$EDGE_ARN" '.DistributionConfig.DefaultCacheBehavior
       | (.AllowedMethods.Quantity == 7)
         and (.FunctionAssociations.Quantity == 1)
         and (.FunctionAssociations.Items[0] == {FunctionARN: $arn, EventType: "viewer-request"})
         and (.OriginRequestPolicyId == "b689b0a8-53d0-40ab-baf2-68738e2966ac")' "$WORK/dist-after.json" > /dev/null; then
     echo '反映後の設定が期待と違います。手順6で読み取り構成へ戻してください。' >&2
     exit 1
   fi
   ```
   `CachedMethods` は読み取りの2〜3種のまま変えない（CDは、書き込みメソッドのキャッシュを拒否する）。リポジトリ変数 `CLOUDFRONT_VIEWER_FUNCTION_ARN` に `EDGE_ARN` を設定すると、以後のCDが7メソッドの構成を検証できる（未設定のままでは、CDは変更の前に止まる）。
5. `Deployed` になってから、トラフィックを受ける前に、次を確認する（**確認できるまで利用者へ公開しない**）。
   1. 架空の案件の受付（201）・判定保存（200）・開き直して保存済みが表示される。設問またはルールの提案とYes、別ブラウザでの共有一覧、最新ルールでの未判定案件の照合。保存済み判定の再保存と、公開側からの内部モックPOSTが拒否されること（`POST /mock/v1/auto-answers/A09` は、トークンなし・偽のトークンありのどちらも405）。依頼IDを固定した再送は409で、枠が1つだけ消費されること。枠超過は検証用のカウンターを事前に用意し、不要な推論を発生させず429を確認する。
   2. **偽の接続元ヘッダーの確認（必須）。** 自分の実際の接続元の枠を、検証日（日本時間）の上限（5）まで事前に埋め、全体の枠には空き（24以下）を残す。実際のIPの枠が使われていれば、偽のIPを付けても429（接続元の枠超過）になる。偽の値が使われると別の枠で通り、推論が始まるため、事前の用意なしでは行わない。主体の値は、秘密の鍵（`AI_QUOTA_HMAC_KEY`）で計算する（鍵と計算結果は書き残さない）。
      ```bash
      # 主体: IPv4は "v4:<IP>"、IPv6は "v6:<先頭64ビットのネットワーク>/64"（アプリと同じ正規化）
      python3 -I -c 'import hashlib,hmac,os,sys; print(hmac.new(os.environ["AI_QUOTA_HMAC_KEY"].encode(), sys.argv[1].encode(), hashlib.sha256).hexdigest())' 'v4:REPLACE_YOUR_PUBLIC_IP'
      # 検証用の行（ai_usage_counters）: usage_day=検証日(JST)、scope='ip'、subject=上の値、used=5（人がDSQLで実行する）
      ```
      次のスクリプトは、ステータスと本文の両方を確認し、期待と違えば終了コード1で止まる。期待は、**429で、本文が接続元ごとの枠（「この接続元からの」）を示す**こと。全体の枠の本文（「デモ全体の」）の429や、201・400・403などは失敗として扱う（枠の超過がIPごとでなく全体で起きている・偽の値が使われた・要求が別の理由で拒否された、のいずれかのため）。
      ```bash
      set -euo pipefail
      BASE='https://REPLACE_ID.cloudfront.net'
      BODY='{"prompt":"検証"}'
      HASH="$(printf '%s' "$BODY" | sha256sum | cut -d' ' -f1)"
      TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
      expect_ip_429() {   # 引数: 偽のヘッダーの -H 指定（1つ以上）
        local code
        code="$(curl -sS -o "$TMP" -w '%{http_code}' -X POST "$BASE/api/admin/question-proposals" \
          -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" -H "x-amz-content-sha256: $HASH" \
          "$@" --data "$BODY")"
        if [ "$code" != 429 ] || ! grep -q 'この接続元からの' "$TMP" || grep -q 'デモ全体の' "$TMP"; then
          echo "期待外れ: HTTP $code。接続元ごとの429ではありません。7メソッドと関連付けを戻してください（手順6）。" >&2
          exit 1
        fi
        echo "OK: 429（接続元の枠）"
      }
      expect_ip_429 -H 'X-Demo-Viewer-Ip: 203.0.113.9'                                   # 偽の単一ヘッダー
      expect_ip_429 -H 'X-Demo-Viewer-Ip: 203.0.113.9' -H 'X-Demo-Viewer-Ip: 203.0.113.10'   # 偽の重複ヘッダー
      expect_ip_429 -H 'X-Demo-Viewer-Ip: 203.0.113.9' -H 'x-internal-mock-token: forged'    # 偽のIPと偽のトークン
      ```
      429が返っても、枠は増えない（接続元の枠は上限のまま）ことを確認する。全体の枠・偽のIP（`v4:203.0.113.9`）の枠の行が増えていないことを、DSQLの読み取りで確認する。201が返った・スクリプトが終了コード1で止まったときは、直ちに7メソッドと関連付けを戻す（手順6）。
   3. Lambda関数URLへの未署名の直接アクセスが403のままであること。
6. 問題があれば最初にCloudFrontを元の読み取り構成に戻す。**1回の更新で、許可メソッドを3種（GET・HEAD・OPTIONS）へ戻し、エッジ関数の関連付けを外す**（同じ `get-distribution-config` → `jq` → `update-distribution`。`AllowedMethods` を3種、`FunctionAssociations` を `{Quantity:0}` にする）。`Deployed` になるまで待ち、読み取り（`/`・`/health`・`/release` が200）と、書き込み（POST）が拒否されることを確認する。伝播（`Deployed`）が終わるまでは、一部のエッジが古い構成のまま応答し得るため、確認は `Deployed` の後にだけ行う。
   ```bash
   set -euo pipefail
   DIST_ID='REPLACE_DISTRIBUTION_ID'; BASE='https://REPLACE_ID.cloudfront.net'
   WORK="$(mktemp -d)"
   aws cloudfront get-distribution-config --id "$DIST_ID" --region us-east-1 > "$WORK/dist.json"
   ETAG="$(jq -r .ETag "$WORK/dist.json")"
   jq '.DistributionConfig
     | .DefaultCacheBehavior.AllowedMethods.Quantity = 3
     | .DefaultCacheBehavior.AllowedMethods.Items = ["GET","HEAD","OPTIONS"]
     | .DefaultCacheBehavior.FunctionAssociations = {Quantity:0}' "$WORK/dist.json" > "$WORK/dist-read.json"
   aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" \
     --distribution-config "file://$WORK/dist-read.json" --region us-east-1
   aws cloudfront wait distribution-deployed --id "$DIST_ID" --region us-east-1
   for path in / /health /release; do
     code="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE$path")"
     [ "$code" = 200 ] || { echo "読み取り $path が $code です（期待 200）。" >&2; exit 1; }
   done
   code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/admin/question-proposals" \
     -H 'Content-Type: application/json' --data '{}')"
   case "$code" in 403|405) echo "POST は拒否されました（$code）";; *) echo "POST が $code です（期待 403 または 405）。" >&2; exit 1;; esac
   ```
   **この確認の後にだけ**、必要ならLambdaの版を戻す。この戻しは、保存済みの共有データを変えない。設定データを戻す場合は、権限のある作業環境で `SharedConfigDb.restore(target_revision)` を使い、過去版を新しい共有設定として追加する（過去版は変えない）。反映前後の共有一覧を照合する。復元で無効になる設問も廃止済みとしてIDを保持する。公開された判定の登録済みの結果は読み取り専用で、承認した共有設定の変更・復元でも更新しない（運営も、判定の編集・解除は行わない）。設定を戻した後に開く判定未登録の案件だけ、戻した後のルールで照合される。
7. Lambdaの版を戻す場合、書き込みを許可した構成のままでは、`guard=1` と `viewerip=1` の両方の印のない版へは戻せない（CDも、印のない版を現在の版とする書き込み構成の配布を止める）。印のない古い版（接続元の専用ヘッダーを読まない版）へ戻すときは、先に手順6でCloudFrontを読み取り構成へ戻し、読み取りと書き込みの拒否を確認してから、エイリアスを戻す（この順序を逆にすると、古い版が偽の接続元を信じ得る）。

### LIVEの公開とアクセスの限界

- エッジ関数は、LIVEステージだけがディストリビューションに関連付けられる。`publish-function` は、その関数を使う全ディストリビューションの動作を即時に変えるため、公開の前に、DEVELOPMENTでのテストと手順3のソース照合を終える。
- CDが確認できるのは、配布の実行時点のLIVEのコードだけ。公開後に別の人がLIVEを更新・再公開しても、次のCDまで検出しない（継続的な監視ではない）。配布用ロールに `UpdateFunction`・`PublishFunction` を与えない。関数を変更できる人は、利用枠の迂回ができるため、AWSの権限を最小にする。
- `get-function --stage LIVE` の出力ファイルは、読み取り専用の確認用で、コードに秘密は含まない。CDは一時ディレクトリで読み、すぐ削除する。
- 実AWSでの動作（CloudFront Functionsの実行、`event.viewer.ip` の実際の値、関連付けの反映、`get-function` の出力形式、7メソッドの更新、費用・Freeプランでの利用可否）は、この変更では確認していない。ローカルで確認したのは、エッジ関数のNodeでの実行、アプリの検証、偽のAWSによるCDの検査の論理だけである。

本文ハッシュはブラウザでシリアライズした書き込み本文のUTF-8バイト列から計算する（変更なし）。公開環境はHTTPSが必要。ローカル開発で共有設定をPostgreSQLに保存する場合は、初期化済みのDBに対して `SHARED_CONFIG_DB=1` を指定する。ローカル開発・E2Eでは、エッジがないため、必要なテストが専用ヘッダー（`X-Demo-Viewer-Ip`）を直接付ける。

[AWSのLambda OAC要件](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html) · [CloudFront Functionsのイベント構造](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html) · [GetFunction](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_GetFunction.html) · [配布ロールの権限表](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudfront.html)
