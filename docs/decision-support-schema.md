# データベースとAPIの設計

[要件書](requirements.md)の初版（設問A01〜A12）に必要なテーブルと、受付・モックAPI・保存のAPI契約を定めます。テーブル構成は、DDL案（下の「DDL案」）が正本です。識別子と記録例は架空です。

## DB（Aurora DSQL）

東京リージョンの単一リージョンクラスターで Amazon Aurora DSQL（PostgreSQL 16互換）を使います。接続はTLSと一時的なIAM認証トークンです。DDLは、ローカルのPostgreSQLと実Aurora DSQL（専用スキーマ）の両方で適用・保存・再表示を検証済みです。接続・権限・OCC・実機の確認状況は[AWS・DB・Bedrockの接続環境](aws-environment.md)を参照します。

## テーブルと関連

| テーブル | 1行が表すもの | キーと関連 |
| --- | --- | --- |
| `review_cases` | 審査案件 | PKは`case_id`（アプリが採番）。`summary`、対象期間（任意）、`user_data`（`user_id`のみ）、`inquiry_data`（`subject`・`body`）を持つ。 |
| `review_questions` | 設問マスター（現在の内容） | PKは`question_code`。設問文、確認する資料・欄、自動回答のキー（`auto_answer_key`。初版はA09のみ）、`revision`。 |
| `case_questions` | 案件に出す設問（受付時のコピー） | PKは`(case_id, question_code)`。受付時にマスターの全設問をコピーして固定する。 |
| `case_checks` | 案件の設問ごとの回答 | PKは`(case_id, question_code)`。`answer`は`yes`・`no`・`pending`。未回答の設問は行を作らない。`automated_answer`（`yes`・`no`、空を許す）は受付時の自動回答で、受付後は更新しない。 |
| `decision_rules` | 判定ルールの1つの版（不変の行） | PKは`(rule_id, revision)`。`result_code`と、`conditions`（jsonb。キーは設問コード、値は`yes`・`no`）。条件を変えるときは新しい`revision`の行を追加する。 |
| `case_results` | 案件の判定結果（回答の親。受付時に未判定の行を作る） | PKは`case_id`。`result_code`・`recorded_by`・`recorded_at`は、受付時は全部空、［判定する］で全部入る（CHECKで揃える）。採用ルール`(rule_id, rule_revision)`は任意の参考記録で、`decision_rules`への外部キーは持たない。 |

- 受信データ: `user`は`user_data`、`inquiry`は`inquiry_data`に、定義した項目だけを保存する（余分な項目は無視）。`user_id`、`subject`、`body`が欠けた受付は、受付処理が拒否する（DBのCHECKはオブジェクト型だけ）。元システムの案件IDは保存しない。
- ルールの版: 画面は`decision_rules`の`rule_id`ごとの最新の`revision`で照合する。登録済みの案件も同じである。採用ルールは参考の記録で、結論は選んだ`result_code`である。
- `result_code`は`account_deletion`・`account_suspension`・`caution`・`no_issue`の4つ。値の検査と、採用ルールが`decision_rules`に実在するか（なければ422）の検査は、保存処理（アプリ）で行う。
- 共有サンプルの案件IDは `CASE-` と8桁の大文字の16進数（13文字）。操作IDと案件IDは`sample_case_operations`に一対一で保存し、同じ操作の再送は保存済みの案件を返す。案件との同時確定と、既存DBへの追加DDLは[開発・配布の手順](development-workflow.md#共有サンプル案件の作成post-apisample-cases)を参照する。

## DDL案

Aurora DSQLでの適用時は、各`CREATE TABLE`を依存順（親から子へ。`review_cases`・`review_questions`・`decision_rules`、`case_questions`、`case_results`、`case_checks`、`sample_case_operations`、`shared_config_revisions`、`shared_proposals`、`processed_shared_proposals`、`proposal_templates`、`proposal_template_migrations`、`ai_usage_requests`、`ai_usage_counters`の順）に1文ずつ確定します。DSQLは1トランザクションにつき1つのDDL文を許容し、DDLとデータ登録を別トランザクションに分けます。 [AWSのCREATE TABLE仕様](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/create-table-syntax-support.html)・[DDLのトランザクション仕様](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-ddl.html)

```sql
CREATE TABLE review_cases (
    case_id text PRIMARY KEY CHECK (trim(case_id) <> ''),
    summary text NOT NULL CHECK (trim(summary) <> ''),
    user_data jsonb NOT NULL CHECK (jsonb_typeof(user_data) = 'object'),
    inquiry_data jsonb NOT NULL CHECK (jsonb_typeof(inquiry_data) = 'object'),
    period_start_at timestamptz,
    period_end_at timestamptz,
    CHECK (
        (period_start_at IS NULL AND period_end_at IS NULL)
        OR (period_start_at IS NOT NULL AND period_end_at IS NOT NULL
            AND period_start_at < period_end_at)
    )
);

CREATE TABLE review_questions (
    question_code text PRIMARY KEY CHECK (trim(question_code) <> ''),
    category text NOT NULL CHECK (trim(category) <> ''),
    prompt text NOT NULL CHECK (trim(prompt) <> ''),
    auto_answer_key text CHECK (trim(auto_answer_key) <> ''),
    source_kind text NOT NULL CHECK (trim(source_kind) <> ''),
    source_field text NOT NULL CHECK (trim(source_field) <> ''),
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1)
);

CREATE TABLE decision_rules (
    rule_id text NOT NULL CHECK (trim(rule_id) <> ''),
    revision integer NOT NULL CHECK (revision >= 1),
    result_code text NOT NULL CHECK (trim(result_code) <> ''),
    conditions jsonb NOT NULL CHECK (
        jsonb_typeof(conditions) = 'object' AND conditions <> '{}'::jsonb
    ),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (rule_id, revision)
);

CREATE TABLE case_questions (
    case_id text NOT NULL REFERENCES review_cases (case_id),
    question_code text NOT NULL REFERENCES review_questions (question_code),
    revision integer NOT NULL CHECK (revision >= 1),
    category text NOT NULL CHECK (trim(category) <> ''),
    prompt text NOT NULL CHECK (trim(prompt) <> ''),
    source_kind text NOT NULL CHECK (trim(source_kind) <> ''),
    source_field text NOT NULL CHECK (trim(source_field) <> ''),
    PRIMARY KEY (case_id, question_code)
);

CREATE TABLE case_results (
    case_id text PRIMARY KEY REFERENCES review_cases (case_id),
    result_code text CHECK (trim(result_code) <> ''),
    rule_id text,
    rule_revision integer,
    recorded_by text CHECK (trim(recorded_by) <> ''),
    recorded_at timestamptz,
    CHECK (
        (result_code IS NULL AND recorded_by IS NULL AND recorded_at IS NULL)
        OR (result_code IS NOT NULL AND recorded_by IS NOT NULL AND recorded_at IS NOT NULL)
    ),
    CHECK ((rule_id IS NULL) = (rule_revision IS NULL))
);

CREATE TABLE case_checks (
    case_id text NOT NULL REFERENCES case_results (case_id),
    question_code text NOT NULL,
    answer text NOT NULL CHECK (answer IN ('yes', 'no', 'pending')),
    automated_answer text CHECK (automated_answer IN ('yes', 'no')),
    PRIMARY KEY (case_id, question_code),
    FOREIGN KEY (case_id, question_code) REFERENCES case_questions (case_id, question_code)
);

CREATE TABLE sample_case_operations (
    operation_key text PRIMARY KEY CHECK (trim(operation_key) <> ''),
    case_id text NOT NULL UNIQUE CHECK (trim(case_id) <> '')
);

CREATE TABLE shared_config_revisions (
    config_revision integer PRIMARY KEY CHECK (config_revision >= 1),
    question_revision integer NOT NULL CHECK (question_revision >= 1),
    rule_revision integer NOT NULL CHECK (rule_revision >= 1),
    questions jsonb NOT NULL CHECK (jsonb_typeof(questions) = 'array'),
    rules jsonb NOT NULL CHECK (jsonb_typeof(rules) = 'array'),
    removed_rule_ids jsonb NOT NULL CHECK (jsonb_typeof(removed_rule_ids) = 'array'),
    origin text NOT NULL CHECK (origin IN ('seed', 'approval', 'restore')),
    restored_from integer,
    created_at timestamptz NOT NULL
);

CREATE TABLE shared_proposals (
    proposal_id text PRIMARY KEY CHECK (trim(proposal_id) <> ''),
    kind text NOT NULL CHECK (kind IN ('question', 'rule')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL
);

CREATE TABLE processed_shared_proposals (
    proposal_id text PRIMARY KEY,
    target text NOT NULL CHECK (target IN ('question', 'rule')),
    payload jsonb NOT NULL,
    applied boolean NOT NULL
);

CREATE TABLE proposal_templates (
    target text NOT NULL CHECK (target IN ('question', 'rule')),
    prompt text NOT NULL,
    operation jsonb NOT NULL,
    PRIMARY KEY (target, prompt)
);

CREATE TABLE proposal_template_migrations (
    name text PRIMARY KEY
);

CREATE TABLE ai_usage_requests (
    request_id text PRIMARY KEY
);

CREATE TABLE ai_usage_counters (
    usage_day date NOT NULL,
    scope text NOT NULL CHECK (scope IN ('ip', 'global')),
    subject text NOT NULL CHECK (trim(subject) <> ''),
    used integer NOT NULL CHECK (used >= 0),
    PRIMARY KEY (usage_day, scope, subject)
);
```

`shared_config_revisions`・`shared_proposals`・`ai_usage_counters`などは、公開デモの共有データ（設問・判定ルールの承認結果、未処理の提案、Bedrock推論の日次の件数、定型文）です。外部キーは持たず、`shared_config_revisions`は追記だけで、承認のたびに「最新の版+1」の行を足します（同時の承認は同じ主キーの衝突で片方が失敗し、再試行されます）。`ai_usage_counters`の`subject`は、全体は`*`、接続元は鍵付きHMACの16進で、IPそのものは入れません。7日より古い行は消します。

## API契約

画面は相対パス（`/api/...`）でAPIを呼びます。保存先がDBでないとき（メモリ保存）は、一覧・再表示・判定は503です。どの操作もHTTPモックを呼びません（呼ぶのは受付時の1回だけ）。

### 受付（`POST /api/intake/cases`）

入力は`summary`、`user`（`user_id`）、`inquiry`（`subject`・`body`）、任意の`period_start_at`・`period_end_at`、任意の`demo_controls`です。`user_id`は整数にできる文字列に限ります（モックが偶奇判定に使うため）。`subject`・`body`は空白だけでも拒否します。文字数の上限は仮に`body`4,000文字、他は256文字です（独自の仮値）。

処理順:

1. 検査（不正は422。保存もHTTPもしない）。
2. 1回目の保存（1トランザクション）: 案件、全設問の`case_questions`、未判定の`case_results`、A09の`case_checks`（`answer`は`pending`、`automated_answer`は空）。
3. A09のモックをHTTPで呼ぶ（トランザクションの外。タイムアウト3秒）。
4. 2回目の保存（1トランザクション）: 成功したらA09の`answer`と`automated_answer`に`yes`・`no`を入れる。失敗したら`pending`のまま。HTTPは再び呼ばない。
5. 201で`{"case_id": "...", "answers": {"A09": "yes"}}`を返す（取得失敗は`"pending"`）。

1回目の保存が失敗したときはHTTPを呼ばないので、再送しても重複して呼ばれません。重複受付の防止は初版の範囲外です。

### モックAPI

| 項目 | 内容 |
| --- | --- |
| 自動回答 | `POST /mock/v1/auto-answers/{question_code}`。対象はA09のみ。`auto_answer_key`未設定の設問は404 |
| 入力 | `{"user_id": "456"}`。任意で`sample_number`（整数）。省略時は`user_id`の整数値を使う（整数にできなければ422） |
| 成功応答 | 200。`{"question_code": "A09", "answer": "yes", "sample_number": 456}`。偶数がYes、奇数がNo |
| 取得失敗の扱い | 接続失敗、タイムアウト、200以外、応答が壊れている、`question_code`の不一致、`answer`が`yes`・`no`以外。いずれもA09は`pending`のまま残り、再試行しない |
| 失敗の再現 | 受付入力の`demo_controls.fault_questions`（例: `["A09"]`）で、モックへ`X-Mock-Fault: timeout`を付ける。検証用で、実サービス接続時に削除する |
| 呼び出し元 | 受付処理が`MOCK_BASE_URL`に対してHTTPで呼ぶ。同一アプリの`/mock/v1/`を自分自身から呼ぶ構成。Lambda上での可否は未確認 |

### 一覧・再表示・判定

| 操作 | 呼び出し | 応答 |
| --- | --- | --- |
| 一覧 | `GET /api/cases` | 200。配列。1行は`case_id`、`summary`、`user_id`、`inquiry`、`result_code`（未判定は`null`）、`counts`（`yes`・`no`・`pending`・`unanswered`）。案件ID順。絞り込みと状態は画面が導く |
| ルール | `GET /api/rules` | 200。`rule_id`ごとの最新の`revision`の行。`rule_id`順。1行は`rule_id`、`revision`、`result_code`、`conditions` |
| 再表示 | `GET /api/cases/{case_id}` | 200。案件、`user`、`inquiry`、期間、設問ごとの`answer`（回答の行がない未回答は`null`）、`result`（未判定は`null`）。案件がなければ404 |
| 判定 | `PUT /api/cases/{case_id}/judgment` | 204。本文は`result_code`（必須）、`answers`（変更する設問だけ）、`rule_id`と`rule_revision`（両方あるときだけ記録）。不正は422、案件がなければ404、登録済みの案件は409（何も変えない） |

- 画面の照合は、`GET /api/rules`で取得したルールを使う。条件のない設問は「問わない」で、どの回答でも食い違いにしない。
- 画面は、回答済みの設問を空に戻せない。`case_checks`の行は削除しない。

### ［判定する］の保存

画面で入力中の回答と結果はDBに保存しません。［判定する］だけが、次を1トランザクションで行います。

1. `case_results`を、未判定の行だけを対象に条件付きで更新する（`WHERE case_id = ? AND result_code IS NULL`。`result_code`、任意の`rule_id`・`rule_revision`、`recorded_by`、`recorded_at`を入れる）。更新が1行でなければ、以降の更新をせずrollbackして409で拒否する。登録は1回だけで、競合した保存も登録済みの値を上書きしない。
2. `case_checks`の`answer`を、現在の入力で更新または追加する。`automated_answer`は更新しない。
3. commitする。途中で失敗したら、親子ともrollbackする。

照合が0件でも、未回答や確認待ちが残っていても保存できます。訂正履歴は残しません。案件を開き直すと、`case_results`と`case_checks`を取得して再表示します。

### 判定ルールと条件

ルールの期待値は`decision_rules.conditions`に1行でまとめます（例: `{"A03": "no", "A09": "yes"}`）。CHECKで検査できるのはオブジェクトで空でないことだけなので、キーが存在する設問コードか、値が`yes`・`no`かは、保存処理で検査します。

## 未決

| 論点 | 決めること |
| --- | --- |
| 最終的な認定と処置 | どの組み合わせをどの結果に結び付けるか。未確定の設問や複数候補の扱い。 |
| 設問・ルールの版 | 新版の公開時に、進行中の案件をどう扱うか。ルール集合の版を記録するか。 |
| 案件受領と外部API | 受領する資料と項目、実サービスの接続、消費量の単位と精度。 |
| 受信データ | 個人情報の保存範囲、受信後の更新、他ユーザーIDの関連情報（A09〜A12）の参照方法。 |
| 重複受付 | 元システムの案件IDの保存と、一意制約（Aurora DSQLでの一意索引は未検証）。 |
| 権限・保存期間 | 閲覧・回答・公開の権限。回答と判定結果の保存期間。 |
