# 判定支援のデータベーステーブル案

[要件書](requirements.md)で合意した初版の範囲（アカウント・APIの不正利用、設問A01〜A12）に必要な列だけで、案件、設問、案件に出した設問、回答、判定結果、ルールをたどれる6テーブルを定義します。クレジット関連（C01〜C09）は初版の対象外なので、シナリオを区別する列は持ちません。後続で対象を広げるときに追加します。テーブル構成、型、制約はレビュー案です。識別子と記録例は架空です。

## AWSでのDB候補

東京リージョンの単一リージョンクラスターで **Amazon Aurora DSQL** を使う案です。PostgreSQL 16互換のサーバーレスDBで、毎月最初の100,000 DPUとストレージ1 GBが無料です。超過分や、利用したバックアップ・データ転送などは別途課金されます。少量の架空データを使うデモでは、実測したDPUと保存量を見て無料枠に収まるかを判断します。 [AWS料金](https://aws.amazon.com/rds/aurora/dsql/pricing/)・[対応リージョンと互換性](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/what-is-aurora-dsql.html)

接続にはTLSと一時的なIAM認証トークンを使います。後続の実装では、ローカルのDockerとCIでPostgreSQLのスキーマ検証を行い、AWSでは認証と実DBへの接続を確認する構成を検討します。Kubernetesでのアプリ展開はデプロイ設計のレビューで扱います。 [AWSの接続仕様](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/accessing.html)

## テーブルと関連

| テーブル | 1行が表すもの | キーと関連 |
| --- | --- | --- |
| `review_cases` | 受領した審査案件 | PKは`case_id`（アプリ側で採番する案）。概要`summary`、対象期間の開始・終了日時（ある場合のみ）、受信した利用者情報`user_data`と問い合わせ`inquiry_data`を持つ。 |
| `review_questions` | 設問マスター（現在の内容） | PKは`question_code`。設問文、カテゴリ、確認する資料・欄、設定がある場合は自動回答先のキーを持つ。編集は上書きで、履歴は持たない。更新のたびに`revision`を1増やす。 |
| `case_questions` | 案件に出す設問（受付時のコピー） | PKは`(case_id, question_code)`。案件の受付時に、マスターの全設問をコピーして固定する。設問文・カテゴリ・確認欄と、コピー時の`revision`を持つ。マスターを後で編集しても、案件の設問は変わらない。表示順は`question_code`順。 |
| `case_checks` | 案件の設問ごとの回答（結果の子） | PKは`(case_id, question_code)`。`yes`・`no`・`pending`だけを持ち、未回答の設問は行を作らない。`case_results`と`case_questions`を参照するFK。回答者と日時は、結果の`recorded_by`・`recorded_at`で代える。 |
| `decision_rules` | 設問の回答の組み合わせに対応する結果の1つの版（不変の行） | PKは`(rule_id, revision)`。結果を`result_code`で表し、組み合わせは`conditions`（jsonb）に、キーを`question_code`、値を`yes`または`no`としてまとめる。条件を変えるときは新しい`revision`の行を追加し、過去の行は更新しない。`created_at`を持つ。 |
| `case_results` | 案件に対して最後に保存した判定結果（回答の親） | PKは`case_id`。選んだ`result_code`と、補完情報`supplement_data`（jsonb、受信データと分ける）、登録者・登録日時を持つ。採用ルール`(rule_id, rule_revision)`は任意で、非NULLのときだけ同じ`result_code`の`decision_rules`行を参照する。 |

回答は案件と設問の組につき1件で、［判定する］で保存した結果（`case_results`）の子として持ちます。案件に出す設問は`case_questions`で固定し、回答は`case_questions`にある設問に対してだけ保存できます。同一案件の同じ設問で複数の取引やキーを個別に記録する必要があるかはレビューで決めます。

### 振り返りの方法

オペレータが過去の判定を見返すときは、次の順にたどります。
- 設問と回答: `case_checks`のYes/Noと、`case_questions`にコピーされた当時の設問文・`revision`をそのまま表示する。マスターが後で変わっても影響しない。
- 判定した日時と人: `case_results.recorded_at`と`recorded_by`。
- 当時のルール: `decision_rules`のうち、`recorded_at`以前に作成され、`rule_id`ごとに`revision`が最新の行を引いて再照合する。ルールの行は更新しないため、照合0件・ルール未選択の結果でも当時のルール群を再現できる。採用ルールの記録とは別である。

### 案件の受信データとの対応

1案件の主対象は利用者1人（`user_id`1つ）で、外部から利用者情報`user`と問い合わせ`inquiry`を受け取る想定です。アプリ側で内部の`case_id`を発行し、受信データに元の案件IDがあれば`source_case_ref`に保存して内部IDと区別します。

| 受信データ | DB列 | 内容 |
| --- | --- | --- |
| `user` | `review_cases.user_data` | 主対象の`user_id`を1つ含む想定のjsonbオブジェクト。細かい項目は未決。 |
| `inquiry` | `review_cases.inquiry_data` | 問い合わせ本文などのjsonbオブジェクト。細かい項目は未決。 |
| 元の案件ID | `review_cases.source_case_ref` | 任意。内部の`case_id`とは別に保持する。 |

他のユーザーIDを参照する場合も、主対象の判定に使う関連情報です。案件の主対象にはならず、必要なときに外部APIで参照して補い、画面に表示する設計案です。足りない情報を外部APIで補う案にしたため、以前の「受領後に資料を取得しない」という境界は見直しています。案件一覧は`user_data`の主対象`user_id`を表示します。DBのCHECKが検査するのはオブジェクト型だけで、両列は空オブジェクト`{}`で「情報が届いていない」ことを表せます。`user_data`に主対象の`user_id`が1つあることは、受付処理側で検査する案です。jsonb列とJSON関数はAurora DSQLの最新ドキュメントで対応を確認しています。 [Aurora DSQLの対応データ型](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-data-types.html)

## DDL案

Aurora DSQLでの適用時は、各`CREATE TABLE`を依存順（親から子へ。`review_cases`・`review_questions`・`decision_rules`、`case_questions`、`case_results`、`case_checks`の順）に1文ずつ確定します。DSQLは1トランザクションにつき1つのDDL文を許容し、DDLとデータ登録を別トランザクションに分けます。 [AWSのCREATE TABLE仕様](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/create-table-syntax-support.html)・[DDLのトランザクション仕様](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-ddl.html)

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
    PRIMARY KEY (rule_id, revision),
    UNIQUE (rule_id, revision, result_code)
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
    result_code text NOT NULL CHECK (trim(result_code) <> ''),
    rule_id text,
    rule_revision integer,
    supplement_data jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(supplement_data) = 'object'),
    recorded_by text NOT NULL CHECK (trim(recorded_by) <> ''),
    recorded_at timestamptz NOT NULL,
    CHECK ((rule_id IS NULL) = (rule_revision IS NULL)),
    FOREIGN KEY (rule_id, rule_revision, result_code)
        REFERENCES decision_rules (rule_id, revision, result_code)
);

CREATE TABLE case_checks (
    case_id text NOT NULL REFERENCES case_results (case_id),
    question_code text NOT NULL,
    answer text NOT NULL CHECK (answer IN ('yes', 'no', 'pending')),
    PRIMARY KEY (case_id, question_code),
    FOREIGN KEY (case_id, question_code) REFERENCES case_questions (case_id, question_code)
);
```

`case_results`の採用ルール`(rule_id, rule_revision)`は任意です。両方NULLか両方非NULLかをCHECKで揃え、両方NULLのときは複合FKが検査されず（`MATCH SIMPLE`）、選んだ`result_code`だけで保存できます。非NULLのときは、同じ`result_code`の`decision_rules`行だけを参照できます。実際に選べる`result_code`の集合は、保存処理側で検査する案です。結果コード表や不正を認定するルールは、この案では定めません。 [AWSのCREATE TABLE仕様（外部キーとMATCH SIMPLE）](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/create-table-syntax-support.html)

受領資料を見て答える設問は、設問マスターの`source_kind`と`source_field`で見るべき資料と欄を示します。どの経路で答えた場合も、担当者が登録する結果は`answer`に入れます。回答の行がない設問は未回答、`pending`は確認待ちとして登録した状態です。

## モックAPIの入出力例

案件の受付処理で、`auto_answer_key`を設定した設問のモックを呼ぶ案です。キーは設定済みのエンドポイントを指し、キーが空の設問ではオペレータが受領資料を確認します。`category`は設問を分類するために使います。`sample_number`は応答を再現するためのテスト用の整数です。

| 入力例 | 期待する応答 | ［判定する］で保存する`answer` |
| --- | --- | --- |
| `{"sample_number": 2}` | `{"answer": "yes"}` | `'yes'` |
| `{"sample_number": 3}` | `{"answer": "no"}` | `'no'` |
| タイムアウトを再現 | 応答なし | `'pending'` |

`case_checks`は`case_results`の子のため、受付時の応答はこの段階では`case_checks`に保存しません（案件に出す設問を固定する`case_questions`は、回答ではないので受付時に作ります）。入力済みの値として画面状態に取り込み、［判定する］の一括保存で初めて回答行になります。受付から審査までの応答の保持方法は未決です。

## 判定ルールと登録結果

ルールが参照する設問の期待値は、`decision_rules.conditions`に1行でまとめます。たとえば`{"A03": "no", "A09": "yes"}`は「A03はNo、A09はYes」を表し、両方に一致したときに対応する`result_code`を候補にする案です。未回答と確認待ちは、照合でもそれぞれの状態として扱います。

`conditions`のCHECKで検査できるのは、オブジェクトであることと空でないことだけです。JSONの中身は外部キーで検査されないため、キーが`review_questions`に存在する`question_code`であること、値が`yes`または`no`であることは、保存処理側で検査する案です。

### 保存の流れ（［判定する］のみ）

画面で入力中の回答と選んだ結果は、メモリなどの画面状態で扱い、DBには保存しません。下書きの作成や、回答だけの先行保存は行いません。［判定する］を押したときだけ、次を1トランザクションで実行する案です。

1. `case_results`を`case_id`でupsertする（`result_code`、任意の`rule_id`・`rule_revision`、`supplement_data`、`recorded_by`、`recorded_at`）。
2. その`case_id`の`case_checks`を、現在の入力に置き換える（既存行を削除し、回答のある設問だけを挿入する）。削除した回答が以前の値として残らないようにする。
3. commitする。

途中で失敗したら、親子ともrollbackします。DBには毎回最新の状態だけを保持し、訂正履歴は残しません。案件を開き直したときは、`case_results`と`case_checks`を`case_id`で取得して再表示し、再判定は同じ手順で上書きします。未回答や確認待ちが残っていても、照合が0件でも保存できます。競合やエラー時の画面の扱いと再試行は、既存の未決範囲のままです。

`case_results`には案件ID、選んだ結果コード、採用ルールの行（ある場合のみ）、補完情報、登録者、登録日時を保存します。現在の結果は案件ごとに1件です。採用ルールがある結果からは、当時の条件と結果コードをたどれます。ない結果は、「振り返りの方法」のとおり`recorded_at`で当時のルール群を引きます。[架空データ](decision-support-sample.json)の`demo_result`と`demo_manual_result`はテーブル間の関連を示すための結果コードで、実業務の結果や承認済みの処置ルールではありません。処置の条件、複数候補からの選択、訂正履歴はレビューで決めます。

## 架空の登録例

`CASE-A`はアカウント・APIの案件です。`case_id`はアプリが発行した内部IDで、主対象は`user_data`の`user_id` 456です。`inquiry_data`には問い合わせ本文などの架空情報を入れます。対象期間は2026-10-20の09:00〜12:00（日本時間）、結果の`recorded_by`は`OP-1`、`recorded_at`は同日13:05です。結果は採用ルールなしで選んだ`demo_manual_result`という架空例です。マスターのA03は、2026-10-25に改訂した例（revision 2）です。`CASE-A`は改訂前に受け付けているので、受付時にコピーしたrevision 1の当時の設問文のまま見えます。`DEMO-RULE-1`（revision 1）は、採用ルールを使う保存を検証する架空のルールです。

| 案件・設問 | 画面で確認する内容の例 | `answer` |
| --- | --- | --- |
| CASE-A・A03 | 受領したアカウント変更履歴で登録メールアドレスの変更回数が0回。 | `no` |
| CASE-A・A09 | モックAPIの応答がなく、新規発行の有無が不明。 | `pending` |

たとえばA09で、新規発行数が0件の資料を案件と一緒に受け取った場合は`no`です。応答が得られなかった場合は`pending`です。各設問の資料・欄とYes／Noの条件は[要件書](requirements.md)に従います。

## レビュー事項

| 論点 | 確認したいこと |
| --- | --- |
| AWSと予算 | 東京のAurora DSQLを採用するか。無料枠を超えた場合に許容する月額と、DPU・保存量の確認方法をどう決めるか。 |
| 適用範囲 | 初版に含める設問と、対象未確定・未回答・適用外の区別をどう決めるか。 |
| 最終的な認定と処置 | どの組み合わせをどの結果に結び付けるか。未確定の設問や複数候補をどう扱うか。保存できる`result_code`の集合と保存側の検査をどう定めるか。 |
| 設問・ルールの版 | 回答時点の設問とルールをどう参照するか。新版の公開時に進行中の案件をどう扱うか。 |
| 案件受領と外部API | 受領時に届く資料と項目、外部APIで扱う設問、案件の対象期間の決め方、消費量の単位と精度をどう定めるか。 |
| 受信データの項目と保存 | `user_data`・`inquiry_data`の具体的な項目、必須項目、個人情報の保存範囲、受信後の更新の扱いをどう決めるか。 |
| 不足情報の補完API | 補完用APIの接続先・認証・入出力、補完結果を保存するか表示だけにするか、取得失敗時のオペレータの操作をどう決めるか。他ユーザーIDの関連情報をどこまで参照するか。 |
| 設問の自動判定 | `auto_answer_key`の自動Yes／Noを実業務でどう運用するか（対象設問、訂正の可否、失敗時の扱い）。 |
| 案件IDの採番 | アプリ側`case_id`の採番形式と、`source_case_ref`の重複受信時の扱い（一意制約を設けるか）をどう決めるか。 |
| 権限・履歴・保存期間 | 誰が閲覧・回答・訂正・公開できるか。登録済みの回答や判定結果をいつまで保存するか。 |
