"""Aurora DSQL（公開環境）の初期化。専用スキーマにテーブルと初期データを作る。

管理者（admin）が、CloudShellなどから1回実行する。実行は `scripts/dsql-init.sh` 経由。
何度実行しても壊れない（すでにあるテーブルとデータは、そのまま残す）。
既存のテーブル・データの削除や変更はしない。

やること:
1. 専用スキーマを作る（なければ）。
2. `docs/decision-support-schema.md` のDDLを、依存順に1文ずつ適用する（なければ）。
   DSQLは1トランザクションに1つのDDLしか許さないので、自動コミットで1文ずつ実行する。
3. 設問と判定ルールの初期データを入れる（同じキーがあれば入れない）。
4. （任意）アプリ用のカスタムロールを作り、IAMロールと関連付け、最小限の権限を付ける。
   APP_DB_ROLE と APP_IAM_ROLE_ARN を両方指定したときだけ行う。

設問とルールは、承認済みの架空データ（docs の JSON）だけを入れる。
認証トークンは表示も保存もしない。ローカルPostgreSQLでも、手順1〜3は動く
（手順4はAurora DSQL専用の構文を使うので、ローカルでは使えない）。
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

DOCS = Path(os.environ.get("DOCS_DIR", "/repo/docs"))
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
IAM_ROLE_ARN = re.compile(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}")

# アプリの保存・再表示が使う権限だけ。設問とルールは読むだけ（アプリは書かない）。
# DDL（テーブルの作成・変更）と、削除の権限は付けない。
APP_WRITE_TABLES = ("review_cases", "case_questions", "case_results", "case_checks")
APP_READ_TABLES = ("review_questions", "decision_rules")


def connect(schema: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        autocommit=True,  # DSQLのDDLは1トランザクション1文。文ごとに確定する
        options=f"-c search_path={schema}",
    )


def extract_ddl() -> list[tuple[str, str]]:
    """(テーブル名, DDL) を、文書にある順（依存順）に返す。"""
    text = (DOCS / "decision-support-schema.md").read_text(encoding="utf-8")
    block = re.search(r"```sql\n(.*?)```", text, re.S).group(1)
    statements = [s.strip() for s in block.split(";") if s.strip()]
    return [
        (re.match(r"CREATE TABLE (\w+)", statement).group(1), statement)
        for statement in statements
    ]


def table_exists(conn: psycopg.Connection, schema: str, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchone()
        is not None
    )


def create_schema(conn: psycopg.Connection, schema: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
    ).fetchone()
    if exists:
        print(f"スキーマ {schema}: あり（そのまま使う）")
        return
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    print(f"スキーマ {schema}: 作成")


def create_tables(conn: psycopg.Connection, schema: str) -> None:
    for table, statement in extract_ddl():
        if table_exists(conn, schema, table):
            print(f"テーブル {table}: あり（変更しない）")
            continue
        conn.execute(statement)
        print(f"テーブル {table}: 作成")


def seed(conn: psycopg.Connection) -> None:
    sample = json.loads(
        (DOCS / "decision-support-sample.json").read_text(encoding="utf-8")
    )
    rules = json.loads(
        (DOCS / "decision-support-demo-rules.json").read_text(encoding="utf-8")
    )["decision_rules"]
    inserted = 0
    for question in sample["review_questions"]:
        inserted += conn.execute(
            "INSERT INTO review_questions (question_code, category, prompt,"
            " auto_answer_key, source_kind, source_field, revision)"
            " VALUES (%(question_code)s, %(category)s, %(prompt)s,"
            " %(auto_answer_key)s, %(source_kind)s, %(source_field)s,"
            " %(revision)s) ON CONFLICT DO NOTHING",
            question,
        ).rowcount
    print(
        f"設問: {inserted}件を追加（全{len(sample['review_questions'])}件。"
        "すでにある設問は変更しない）"
    )
    inserted = 0
    for rule in rules:
        inserted += conn.execute(
            "INSERT INTO decision_rules (rule_id, revision, result_code,"
            " conditions, created_at) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (
                rule["rule_id"],
                rule["revision"],
                rule["result_code"],
                Jsonb(rule["conditions"]),
                rule["created_at"],
            ),
        ).rowcount
    print(
        f"判定ルール: {inserted}件を追加（全{len(rules)}件。"
        "すでにあるルールは変更しない）"
    )


def setup_app_role(
    conn: psycopg.Connection, schema: str, role: str, iam_role_arn: str
) -> None:
    """カスタムロール（アプリ用）を作り、IAMロールと関連付け、最小限の権限を付ける。

    構文はAWSの公式資料（Using database roles and IAM authentication）に従う。
    """
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN").format(sql.Identifier(role)))
        print(f"DBロール {role}: 作成")
    else:
        print(f"DBロール {role}: あり（そのまま使う）")
    mappings = conn.execute("SELECT * FROM sys.iam_pg_role_mappings").fetchall()
    if any(iam_role_arn in map(str, row) for row in mappings):
        print("IAMロールとの関連付け: あり（そのまま使う）")
    else:
        conn.execute(
            sql.SQL("AWS IAM GRANT {} TO {}").format(
                sql.Identifier(role), sql.Literal(iam_role_arn)
            )
        )
        print("IAMロールとの関連付け: 作成")
    schema_id, role_id = sql.Identifier(schema), sql.Identifier(role)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, role_id))
    for tables, privileges in (
        (APP_WRITE_TABLES, "SELECT, INSERT, UPDATE"),
        (APP_READ_TABLES, "SELECT"),
    ):
        for table in tables:
            conn.execute(
                sql.SQL("GRANT {} ON {}.{} TO {}").format(
                    sql.SQL(privileges), schema_id, sql.Identifier(table), role_id
                )
            )
    print(
        f"権限: スキーマの利用、{', '.join(APP_WRITE_TABLES)} の読み書き、"
        f"{', '.join(APP_READ_TABLES)} の読み取りだけを付与"
    )


def main() -> int:
    schema = os.environ.get("DB_SCHEMA", "")
    role = os.environ.get("APP_DB_ROLE", "")
    iam_role_arn = os.environ.get("APP_IAM_ROLE_ARN", "")
    if not NAME.fullmatch(schema) or schema in ("public", "information_schema"):
        print("DB_SCHEMA は専用スキーマの名前（小文字英字で始まる英数字と_）。", file=sys.stderr)
        return 2
    if bool(role) != bool(iam_role_arn):
        print("APP_DB_ROLE と APP_IAM_ROLE_ARN は、両方指定するか、両方省く。", file=sys.stderr)
        return 2
    if role and (not NAME.fullmatch(role) or role == "admin"):
        print("APP_DB_ROLE は admin 以外の、小文字英字で始まる名前。", file=sys.stderr)
        return 2
    if iam_role_arn and not IAM_ROLE_ARN.fullmatch(iam_role_arn):
        print("APP_IAM_ROLE_ARN は IAMロールのARNの形式ではない。", file=sys.stderr)
        return 2

    print(f"接続先: {os.environ['DB_HOST']} / スキーマ: {schema}")
    with connect("public") as admin:  # スキーマを作る前は、既定のスキーマで接続する
        create_schema(admin, schema)
    with connect(schema) as conn:
        create_tables(conn, schema)
        seed(conn)
        if role:
            setup_app_role(conn, schema, role, iam_role_arn)
    print("初期化: 完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
