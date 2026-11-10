# DBの接続・スキーマ適用環境

2026-11-08更新。ローカルはDocker ComposeのPostgreSQL、AWS側はAurora DSQLです。**ローカルPostgreSQLでの成功は、Aurora DSQLへの適合を示しません。** 適用するDDLは[スキーマ案](decision-support-schema.md)で、最終版ではありません。

## 状況

| 項目 | 状態 |
| --- | --- |
| ローカルPostgreSQL（16.15）でスキーマ案の適用、制約、一括保存、再判定、ロールバック、再表示を検証 | Codexの実行結果（2026-11-08）: 修正前の34/34を再現。修正後（検証ごとの一意なプロジェクト名と中断時の片付け、未回答NULLの保存・再表示と訂正の検査追加、結果のない案件の回答検査の修正）は36/36成功、終了コード0、専用のコンテナ・ボリューム・ネットワークの削除も成功。`bash -n`と`git diff --check`も成功 |
| 検証スクリプトの終了処理 | Dockerを模擬した制御試験6件（Codex）: 正常終了0、起動失敗7、後片付け失敗9、双方失敗7、TERMで143かつ後片付け実行、乱数取得失敗でDockerを呼ばない。**実DockerでのINT/TERMと並行実行は未試験** |
| 未試験の範囲 | 実DSQL、DSQL固有のOCC、3,000行上限、10MiB上限、CI、Lambda ZIPでの互換性、保存処理の同時実行。ローカルPostgreSQLではDSQL固有のOCCを再現できない（一般的な衝突試験は可能） |
| スキーマ案の最終適用 | 未実施。受信情報と分ける補完情報の保存先、ルールの版管理など、具体構造が未決のため止めている |
| Aurora DSQLのクラスタ作成・接続・DDL適用・保存の検証 | 未実施。リソース作成は未承認。利用可能な資格情報と対象は未確認 |
| アプリ（FastAPI）からのDB接続 | 未実装（業務機能は範囲外） |

## ローカルの検証

```bash
bash scripts/db-verify.sh
```

実行ごとに一意のプロジェクト名（`dtd-db-verify-<乱数>`）、専用のDB名・ユーザー名、乱数パスワードで、ポートを公開せずに起動します。乱数を取得できなければ何も起動せず非0で停止します。終了時（成功、検証失敗、起動失敗、Ctrl+C（INT）、TERM）に、そのプロジェクトのコンテナ・ボリューム・ネットワークだけを`down -v`で削除します。他のプロジェクトのコンテナやボリュームは触らないため、`--remove-orphans`は使いません。検証失敗・起動失敗・後片付け失敗のいずれも非0で終了します。SIGKILLなど強制終了では後片付けを保証しないため、資源が残った場合は表示されたプロジェクト名のものだけを手動で削除します。

検証する内容:

- スキーマ案のSQLを文書から取り出し、依存順に1トランザクション1文で適用する（DSQLの制約に合わせた手順）。
- 架空データ（`decision-support-sample.json`）の登録、CHECK・UNIQUE・外部キーの違反が弾かれること。
- ［判定する］の保存処理案（結果のupsertと回答の置換を1トランザクションで実行）、回答0件・確認待ちを含む保存、再判定で最新のみ残ること、途中失敗で親子ともロールバックされること、再表示。
- 未回答（`answer`がNULL。回答者・回答日時もNULL）と確認待ち（`pending`）を同じ保存で扱い、別の接続から再表示できること。`yes`からNULLへ訂正しても回答者・回答日時がNULLで戻ること。
- 外部キーの検査のうち、回答の親（結果）の検査は、案件はあるが結果がない案件（`FK-A`）で行う。
- DSQLが対応しないと公式資料にある構文（SERIAL、トリガー、通常の`CREATE INDEX`など）がDDLにないこと（静的な文字列確認のみ）。公式資料のCREATE INDEX頁とaccessing頁では同期indexの記述に差があるため、この静的確認はDSQL対応を網羅的に示すものではない。

開発用にDBを起動する場合（ポートは公開しない）:

```bash
read -rs -p "ローカル用DBパスワード: " DB_PASSWORD; echo
export DB_PASSWORD
docker compose -f compose.yaml -f compose.db.yaml up -d db
docker compose -f compose.yaml -f compose.db.yaml exec db psql -U demo_admin -d decision_demo
```

`DB_PASSWORD=<値> docker compose ... up -d db`と1行目だけに一時代入すると、次の行の`exec`では値が消えてComposeの変数補間に失敗します。同じシェルで`export`してください。`read -rs`は秘密値を画面とシェル履歴に残さないための手順で、ここで使う値は**ローカル用のみ**です。パスワードはリポジトリに書きません。

既存のボリュームの`POSTGRES_PASSWORD`は、初期化後に再起動しても変更されません。パスワードを変えるときは、DB内で変更するか、データが不要と確認した専用ボリュームだけを再初期化します。他のボリュームは削除しません。

## 固定した版

| 対象 | 固定 | 理由 |
| --- | --- | --- |
| PostgreSQL | `postgres:16-bookworm`のdigest（16.15、`compose.db.yaml`） | DSQLがPostgreSQL 16互換のため |
| 検証用のPython | `python:3.14.10-slim-trixie`のdigest | `backend/`と同じ |
| uv | 0.13.7のdigest | `backend/`と同じ |
| ドライバー | psycopg 3.3.8（`db/requirements.txt`、ハッシュ付きロック） | Python 3.14のバイナリ配布を確認済み。uvの公式資料では、`uv pip sync`は記載済みのハッシュを既定で検証し、`--require-hashes`は全件のハッシュ記載を必須にする |

ドライバーは`backend/`のロックとは分けています。アプリの保存処理を実装する担当が、Lambda ZIPのビルド環境での互換性（ネイティブ依存）を確認してから`backend/`へ取り込みます。

## Aurora DSQLの接続（未検証）

AWSの公式資料に基づく設計で、実機では確認していません。

- 接続先は`<クラスタID>.dsql.<リージョン>.on.aws`、データベースは`postgres`（クラスタに1つ）。リージョンは東京（`ap-northeast-1`）と回答済み。
- パスワードの代わりに、IAMで署名した一時トークンを使う。既定の有効期限は15分で、接続後はトークンが切れても接続は有効。接続は最長60分で切れる。
- `admin`ロールは`dsql:DbConnectAdmin`、カスタムロールは`dsql:DbConnect`が必要。アプリはカスタムロールを使う案で、ロールの作成とIAMとの関連付け（`AWS IAM GRANT`）は`admin`で行う。
- 接続にはTLSが必要（`sslmode=require`など）。
- `scripts/db-verify.sh`はローカルPostgreSQL用で、DSQL向けの検証には使わない。
- 資格情報はコードやGitに書かず、実行環境のIAMロールなどで安全に渡す。

## DSQLでの注意（公式資料）と、実機で確認すること

公式資料上、次は対応とされています。**資料上の対応であって、実機で確認した結果ではありません。**

- `CREATE TABLE`の`CHECK`、`UNIQUE`、`PRIMARY KEY`、`REFERENCES`／`FOREIGN KEY`（`MATCH SIMPLE`、複合キーを含む）、`jsonb`列（索引は不可）、JSON関数、`INSERT ... ON CONFLICT`。

DSQL固有の制約（スキーマ案とアプリの保存処理で守る）:

- DDLとDMLは別トランザクションで、1トランザクションのDDLは1文。
- 1トランザクションで変更できるのは3,000行まで、変更するデータの合計は10MiBまで、トランザクション時間は5分まで（公式のクォータと制限。いずれも実機では未確認）。
- 分離レベルは`REPEATABLE READ`のみ。競合は`SQLSTATE 40001`（`OC000`・`OC001`）で返るため、保存処理には再試行が必要。DSQL固有のOCCはローカルのPostgreSQLでは再現できない（一般的な衝突試験は可能）。保存の同時実行は未試験。
- 外部キーの検査は追加の読み取りを伴うため、費用（DPU）を実測する。

実DSQLで確認する項目（未実施）:

1. スキーマ案の5つの`CREATE TABLE`が1文ずつ成功すること（`CHECK`内の`trim`、`jsonb_typeof`、`jsonb`の比較を含む）。
2. 外部キーと`CHECK`の違反が、ローカルと同じ組み合わせで弾かれること。
3. 保存処理案（upsert＋置換）のcommit、再表示、競合時の再試行。
4. DPUと保存量の実測（月0円は目標で、保証ではない）。

## 再開の条件

- スキーマ案の確定（補完情報の保存先、ルールの版管理など）と、その版に対する人のレビュー。
- AWSアカウント、クラスタ作成、接続用IAM権限、費用についての人の承認。配布環境の初回構築とは重複しないよう調整する。
- 資格情報は実行環境で安全に提供する。チャットやGitには載せない。

## 参照した公式資料（2026-11-08確認）

- [PostgreSQLからの移行と互換性](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-unsupported-features.html)
- [対応データ型](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-data-types.html)
- [CREATE TABLE](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/create-table-syntax-support.html)
- [DDLと分散トランザクション](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-ddl.html)
- [同時実行制御](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-concurrency-control.html)
- [外部キー](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-foreign-key-constraints.html)
- [対応SQL](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-sql-features.html)
- [認証と認可](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/authentication-authorization.html)
- [認証トークンの生成](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/SECTION_authentication-token.html)
- [クォータと制限](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/CHAP_quotas.html)
- [料金](https://aws.amazon.com/rds/aurora/dsql/pricing/)（無料枠は月100,000 DPUと1 GB。東京の単価は料金ページの取得結果に記載がなく、未確認）
- [docker compose down](https://docs.docker.com/reference/cli/docker/compose/down/)（削除範囲。2026-11-08にCodexが本文確認。プロジェクト単位の`down -v`の根拠）
- [Codexのカスタムレビュー指針](https://developers.openai.com/blog/custom-code-review-rules-for-codex)（具体的で範囲を限ったレビュー指針。2026-11-08 JSTにCodexが本文確認）
- [uv CLIリファレンス](https://docs.astral.sh/uv/reference/cli/)（`uv pip sync`のハッシュ検証の既定と`--require-hashes`。2026-11-08 JSTにCodexが本文確認）
- [Codex向けAGENTS.mdの仕様](https://learn.chatgpt.com/docs/agent-configuration/agents-md)（本作業の文書構成には影響せず、DB環境に関するOpenAIの規定は見つからなかった）
