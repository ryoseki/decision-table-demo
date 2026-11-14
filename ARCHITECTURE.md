# アーキテクチャ（層と依存の向き）

層と、許される依存の向きを定める。依存の向きは、機械的に検査する（違反すると、直し方つきのエラーで失敗する）。この規則は、プロジェクト独自の判断で、OpenAIやFastAPIの公式の規定ではない。考え方は、OpenAIの「ハーネスエンジニアリング」の記事（層と依存の向きを、リンターと構造テストで強制する）と、bulletproof-react（依存を一方向にする）を、デモの規模に合わせて小さくしたもの。

## バックエンド（`backend/app`）

| 層 | モジュール | 役割 | 取り込んでよいもの |
| --- | --- | --- | --- |
| ドメイン | `domain`、`rule_matching`、`question_validation` | 定数・例外・ID生成・照合・設問案の検証。純粋で、DBにもHTTPにも依存しない | 標準ライブラリだけ（検査しているのは、`fastapi`・`starlette`・`pydantic`・`psycopg` と、アプリ内の他モジュールを取り込まないこと） |
| 保存先の実装 | `db_store`（PostgreSQL） | SQLとトランザクション | `domain`、`psycopg` |
| 設問案の保存先・AIモック | `question_store`（メモリ）、`question_proposer`（モック） | デモの設問一覧と提案の保存・承認、AIの応答のモック。DBなし | `domain`、`question_validation`（型）、標準ライブラリ |
| HTTP層 | `cases`、`intake`、`mock_api`、`question_admin`、`rules` | ルーター、入力の型と検査、HTTPのステータス | `domain`、`question_validation`、`fastapi`、`pydantic`、同じ層（`intake` → `mock_api`） |
| 組み立て | `main` | 保存先・AIモックを選び、ルーターをつなぐ | すべて |

- HTTP層は、保存先の実装（`db_store`）、設問案の保存先・AIモック（`question_store`・`question_proposer`）と `psycopg` を取り込まない。保存先は、`main` で組み立て、`app.state`（`case_store`・`question_store`・`question_proposer`）から受け取る。
- 保存先の実装は、HTTP層と `fastapi` を取り込まない。設問案の保存先・AIモックも、HTTP層・`fastapi`・`db_store`・`psycopg` を取り込まない（設問の追加案はDBなしで動く。`docs/admin-question-proposal-demo.md`）。
- ドメインは、`fastapi`・`starlette`・`pydantic`・`psycopg`・他のアプリ内モジュールを取り込まない。
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
