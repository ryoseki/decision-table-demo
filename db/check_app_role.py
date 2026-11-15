"""アプリ用カスタムロールの権限を、管理者の接続でカタログから読み取って確認する。

何も変更しない（読み取りだけ）。`scripts/dsql-check-role.sh` 経由で実行する。
期待する権限は、`db/init_dsql.py` が付与するものと同じ:
- 専用スキーマの利用（USAGE）はできるが、オブジェクトの作成（CREATE）はできない。
- 案件系4テーブルは SELECT・INSERT・UPDATE だけ（DELETEなし）。
- 設問とルールのテーブルは SELECT だけ。
各項目を期待値と比べ、1つでも違えば終了コード1にする。
認証トークンは表示も保存もしない。
"""

import os
import re
import sys

import psycopg

NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
WRITE_TABLES = ("review_cases", "case_questions", "case_results", "case_checks")
READ_TABLES = ("review_questions", "decision_rules")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        autocommit=True,
    )


def ask(conn: psycopg.Connection, function: str, *args: str) -> object:
    """has_*_privilege の結果（True／False）。対応していない場合は、エラー名の文字列。"""
    marks = ", ".join(["%s"] * len(args))
    try:
        return conn.execute(f"SELECT {function}({marks})", args).fetchone()[0]
    except Exception as error:  # noqa: BLE001
        return f"error: {type(error).__name__}"


def main() -> int:
    schema = os.environ.get("DB_SCHEMA", "")
    role = os.environ.get("APP_DB_ROLE", "app_role")
    if not NAME.fullmatch(schema) or not NAME.fullmatch(role):
        print("DB_SCHEMA と APP_DB_ROLE は、小文字英字で始まる名前。", file=sys.stderr)
        return 2
    conn = connect()
    checks: list[tuple[str, object, bool]] = []

    def schema_check(target: str, privilege: str, expected: bool) -> None:
        got = ask(conn, "has_schema_privilege", role, target, privilege)
        checks.append((f"スキーマ {target} の {privilege}", got, expected))

    schema_check(schema, "USAGE", True)
    schema_check(schema, "CREATE", False)
    schema_check("public", "CREATE", False)
    for table in (*WRITE_TABLES, *READ_TABLES):
        allowed = {"SELECT"} if table in READ_TABLES else {"SELECT", "INSERT", "UPDATE"}
        for privilege in TABLE_PRIVILEGES:
            got = ask(
                conn, "has_table_privilege", role, f"{schema}.{table}", privilege
            )
            checks.append((f"{table} の {privilege}", got, privilege in allowed))

    failed = 0
    for name, got, expected in checks:
        ok = got is expected
        failed += 0 if ok else 1
        print(f"[{'OK' if ok else 'NG'}] {name}: 期待 {expected} / 結果 {got}")
    print(f"対象ロール: {role} / スキーマ: {schema}")
    print(f"結果: {len(checks) - failed}/{len(checks)} 件が期待どおり")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
