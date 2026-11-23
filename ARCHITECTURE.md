# アーキテクチャ（層と依存の向き）

層と、許される依存の向きを定める。依存の向きは、機械的に検査する（違反すると、直し方つきのエラーで失敗する）。この規則は、プロジェクト独自の判断で、OpenAIやFastAPIの公式の規定ではない。考え方は、OpenAIの「ハーネスエンジニアリング」の記事（層と依存の向きを、リンターと構造テストで強制する）と、bulletproof-react（依存を一方向にする）を、デモの規模に合わせて小さくしたもの。

## バックエンド（`backend/app`）

| 層 | モジュール | 役割 | 取り込んでよいもの |
| --- | --- | --- | --- |
| ドメイン | `domain`、`rule_matching`、`question_validation`、`rule_validation` | 定数・例外・ID生成・照合・設問案とルール案の検証。純粋で、DBにもHTTPにも依存しない | 標準ライブラリだけ（検査しているのは、`fastapi`・`starlette`・`pydantic`・`psycopg` と、アプリ内の他モジュールを取り込まないこと） |
| 保存先の実装 | `db_store`（PostgreSQL・Aurora DSQL）、`db_retry`（衝突の再試行） | SQL、トランザクション、DSQLのIAMトークン接続（boto3）、40001の再試行 | `domain`、`psycopg`、`boto3` |
| 設問案・ルール案の保存先・AIの生成 | `question_store`・`rule_store`（メモリ）、`question_proposer`・`rule_proposer`（モック）、`bedrock_proposer`（Amazon Bedrockの実接続） | デモの設問一覧・判定ルール一覧と提案の保存・承認、AIの応答の生成（既定はモック。設定で実接続）。DBなし | `domain`、`question_validation`・`rule_validation`（型）、`boto3`・`botocore`（`bedrock_proposer`だけ）、標準ライブラリ |
| HTTP層 | `cases`、`intake`、`mock_api`、`question_admin`、`rule_admin`、`rules`、`sample_cases` | ルーター、入力の型と検査、HTTPのステータス | `domain`、`question_validation`、`rule_validation`、`fastapi`、`pydantic`、同じ層（`intake` → `mock_api`、`rule_admin` → `question_admin`） |
| 組み立て | `main` | 保存先・AIの生成（モックか実接続。`bedrock_proposer.build_proposers`）を選び、ルーターをつなぐ | すべて |

- 設問の廃止案（`docs/admin-proposal-demo.md`）: 廃止の参照・影響の判定は、純粋な関数（`question_validation` の `find_rule_references`・`find_case_impact`）で行い、ルールと案件は `question_admin` が `app.state.case_store` から読み取って渡す。廃止済みの設問は、`cases` が `app.state.question_store` の `retired_ids()` から読み取り、案件の応答に印を付ける（案件の保存データは変えない）。いずれも `app.main` が組み立てた保存先を、共通の形だけで呼ぶ。
- 判定ルールの追加案・削除案（`docs/admin-proposal-demo.md`）: 検証（参照する設問・結果コード・重複・対象）は、純粋な関数（`rule_validation`）で行う。ルールの一覧は、案件の保存先（`case_store`。審査画面のルール）とは別の、メモリ上のデモ用（`app.state.rule_store`）に持ち、互いに影響しない。設問の一覧は `rule_admin` が `app.state.question_store` から読み取って検証に渡す。設問の廃止（`question_admin`）は、参照の確認で、`case_store` のルールに加えて `rule_store` のルールも数える（どちらも `app.main` が組み立てた保存先を、共通の形だけで呼ぶ）。ルール提案は、ルール一覧の版と設問一覧の版の両方を基準に持ち、どちらかが変わっていたYesは拒否する。設問の決定とルールの決定は、`app.state.admin_lock`（`app.main` が1つ置く）で1つずつ順に行い、設問の確認からルールの反映までの間に、設問の廃止が割り込めない（1プロセス内。`question_admin.admin_lock` が取り出し、`rule_admin` が同じものを使う）。
- 定型文（`docs/admin-proposal-demo.md`）: 完全一致の照合・一覧・削除の入口は HTTP層（`question_admin` の `find_template`、`template_admin`）で、`app.state.proposal_reuse`（lookup・list_prompts・remove）だけを呼ぶ。Yesで反映したときの自動登録は、HTTP層ではなく保存先が行う。DBでは `shared_db` が、設定の新しい版・処理済み記録・定型文（`template_store.save_template`）を同じトランザクションで確定する。DBなしでは `question_store`・`rule_store` が、承認と同じロックの中で `template_memory` へ登録する。生成方法（定型文かBedrockか）の選択とBedrockへの依存は、保存層に持ち込まない。
- HTTP層は、保存先の実装（`db_store`・`db_retry`）、設問案・ルール案の保存先・AIの生成（`question_store`・`question_proposer`・`rule_store`・`rule_proposer`・`bedrock_proposer`）、`psycopg`、`boto3`・`botocore`（DSQLのIAMトークンとBedrockの実接続）を取り込まない。生成元（モックか実接続か）の型 `GenerationSource` は `domain` に置き、HTTP層は、生成の実装が申告する `source` を読むだけ（`docs/aws-environment.md`）。保存先は、`main` で組み立て、`app.state`（`case_store`・`question_store`・`question_proposer`・`rule_store`・`rule_proposer`）から受け取る。
- 保存先の実装は、HTTP層と `fastapi` を取り込まない。設問案・ルール案の保存先・AIの生成（`bedrock_proposer` を含む）も、HTTP層・`fastapi`・`db_store`・`psycopg` を取り込まない。ドメインは `boto3`・`botocore` も取り込まない（設問の追加案・ルールの追加案はDBなしで動く。`docs/admin-proposal-demo.md`、`docs/admin-proposal-demo.md`）。
- ドメイン（`rule_validation` を含む）は、`fastapi`・`starlette`・`pydantic`・`psycopg`・他のアプリ内モジュールを取り込まない。
- 検査: `docker compose run --rm --no-deps --build backend lint-imports`（`import-linter`。契約は `backend/pyproject.toml`）。契約の名前（違反時に表示される）に、直し方を書いている。

### 現状の課題（検査ではまだ強制していない）

- 判定の保存に関する検査のルール（許される結果コード、確認待ちへの変更の制限、案件にない設問の拒否）が、`db_store` の中にある。DBなしで確認できず、テスト用の保存先に写している。純粋な関数として `domain` に出すのが次の段階。
- 保存先の共通の形（`CaseStore`）は、受付の2メソッドだけで `intake.py` にある。判定の保存・再表示のメソッドは、HTTP層が `hasattr` で確認している。
- 保存先の選択は、`main.py` の読み込み時の環境変数（`DB_HOST`）で決まる。

## フロント（`frontend/src`）

| 種類 | ファイル | 役割 | Reactを取り込んでよいか |
| --- | --- | --- | --- |
| ロジック・データ | `src` 直下の camelCase の `.ts`（`matching.ts`、`route.ts`、`api.ts`、`resultCodes.ts` など）、`data/*.ts` | 照合の判断、ルートの解釈、APIの呼び出し（`fetch`）、結果コードの対応、固定の架空データ。純粋な関数とデータ | **取り込まない** |
| Reactを使うもの | 画面の部品（PascalCaseの `.tsx`: `CaseDetail`、`CaseList` など）、フック（`use〇〇.ts`: `useRoute.ts`、`useApi.ts`） | 描画、操作、Reactの状態やブラウザの機能を使う処理 | 取り込んでよい |

- ロジック・データは、React を取り込まない。画面の都合は、部品かフックに置き、ロジックは純粋な関数・データとして切り出す。
- 命名で見分ける: 部品は PascalCase の `.tsx`、フックは `use〇〇.ts`、ロジックはそれ以外の camelCase の `.ts`。
- 検査: `docker compose run --rm --no-deps frontend npm run lint`（ESLint の `no-restricted-imports`。設定は `frontend/eslint.config.js`）。メッセージに、直し方を書いている。テスト（`*.test.ts`）、フック（`use*.ts`）、型定義（`*.d.ts`）は、検査の対象外。
- 検査が見ていないもの（既知の抜け）: ロジックが画面の部品やフックを取り込むこと、動的な `import()`。今はフロントの構造が小さいため、React を取り込まない、という最も大事な決まりだけを検査している。
- 機能ごとのフォルダ分けと、機能どうしの取り込み禁止は、画面とAPIの統合のときに、検査を増やすかも含めて検討する。

## 違反したとき

- エラーメッセージの「直し方」に従って、コードを直す。
- 検査を緩める・無視する（契約や ESLint の設定の変更）のは、理由を示して、人に相談する。
