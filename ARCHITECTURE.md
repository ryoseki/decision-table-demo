# アーキテクチャ（層と依存の向き）

層と、許される依存の向きを定める。依存の向きは、機械的に検査する（違反すると、直し方つきのエラーで失敗する）。この規則は、プロジェクト独自の判断で、OpenAIやFastAPIの公式の規定ではない。考え方は、OpenAIの「ハーネスエンジニアリング」の記事（層と依存の向きを、リンターと構造テストで強制する）と、bulletproof-react（依存を一方向にする）を、デモの規模に合わせて小さくしたもの。

## バックエンド（`backend/app`）

| 層 | モジュール | 役割 | 取り込んでよいもの |
| --- | --- | --- | --- |
| ドメイン | `domain`、`rule_matching` | 定数・例外・ID生成・照合。純粋で、DBにもHTTPにも依存しない | 標準ライブラリだけ（検査しているのは、`fastapi`・`starlette`・`pydantic`・`psycopg` と、アプリ内の他モジュールを取り込まないこと） |
| 保存先の実装 | `db_store`（PostgreSQL） | SQLとトランザクション | `domain`、`psycopg` |
| HTTP層 | `cases`、`intake`、`mock_api` | ルーター、入力の型と検査、HTTPのステータス | `domain`、`fastapi`、`pydantic`、同じ層（`intake` → `mock_api`） |
| 組み立て | `main` | 保存先を選び、ルーターをつなぐ | すべて |

- HTTP層は、保存先の実装（`db_store`）と `psycopg` を取り込まない。保存先は、`main` で組み立て、`app.state.case_store` から受け取る。
- 保存先の実装は、HTTP層と `fastapi` を取り込まない。
- ドメインは、`fastapi`・`starlette`・`pydantic`・`psycopg`・他のアプリ内モジュールを取り込まない。
- 検査: `docker compose run --rm --no-deps --build backend lint-imports`（`import-linter`。契約は `backend/pyproject.toml`）。契約の名前（違反時に表示される）に、直し方を書いている。

### 現状の課題（検査ではまだ強制していない）

- 判定の保存に関する検査のルール（許される結果コード、確認待ちへの変更の制限、案件にない設問の拒否）が、`db_store` の中にある。DBなしで確認できず、テスト用の保存先に写している。純粋な関数として `domain` に出すのが次の段階。
- 保存先の共通の形（`CaseStore`）は、受付の2メソッドだけで `intake.py` にある。判定の保存・再表示のメソッドは、HTTP層が `hasattr` で確認している。
- 保存先の選択は、`main.py` の読み込み時の環境変数（`DB_HOST`）で決まる。

## 違反したとき

- エラーメッセージの「直し方」に従って、コードを直す。
- 検査を緩める・無視する（契約や ESLint の設定の変更）のは、理由を示して、人に相談する。
