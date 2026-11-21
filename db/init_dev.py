"""開発用DBの初期化。docker compose の db-init サービスが実行する。

不足するテーブルを追加し、初回に設問と開発用ルールを同じトランザクションで登録する。
初期化済みの場合は既存データを保持する。既存テーブルの定義や初期データの変更は適用しない。
それらを初期状態から入れ直す場合は、開発用DBのボリュームを削除して再作成するため、
保存済みの案件も消える。

ローカルのPostgreSQL用。Aurora DSQLの初期化は db/init_dsql.py が担当する。
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DOCS = Path(os.environ.get("DOCS_DIR", "/repo/docs"))


def connect() -> psycopg.Connection:
    # DB_SCHEMA を指定すると、その専用スキーマの中で動く（テストで使い捨てにするため）。
    schema = os.environ.get("DB_SCHEMA", "")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""),
        options=f"-c search_path={schema}" if schema else None,
    )


def extract_ddl() -> list[str]:
    text = (DOCS / "decision-support-schema.md").read_text(encoding="utf-8")
    block = re.search(r"```sql\n(.*?)```", text, re.S).group(1)
    return [s.strip() for s in block.split(";") if s.strip()]


def main() -> int:
    sample = json.loads(
        (DOCS / "decision-support-sample.json").read_text(encoding="utf-8")
    )
    # 判定ルールは、スキーマ検証用のサンプルではなく、画面の受入例用の開発用ルールを入れる。
    demo_rules = json.loads(
        (DOCS / "decision-support-demo-rules.json").read_text(encoding="utf-8")
    )["decision_rules"]
    with connect() as conn:  # 成功時にコミット、例外時にロールバック
        initialized = conn.execute("SELECT to_regclass('review_questions')").fetchone()[0]
        for statement in extract_ddl():
            table = statement.split()[2]
            if not conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                conn.execute(statement)
        if initialized:
            print("開発用DB: 初期化済み（既存データを保持して不足テーブルを確認）")
            return 0
        for q in sample["review_questions"]:
            conn.execute(
                "INSERT INTO review_questions (question_code, category, prompt,"
                " auto_answer_key, source_kind, source_field, revision)"
                " VALUES (%(question_code)s, %(category)s, %(prompt)s,"
                " %(auto_answer_key)s, %(source_kind)s, %(source_field)s,"
                " %(revision)s)",
                q,
            )
        for rule in demo_rules:
            conn.execute(
                "INSERT INTO decision_rules (rule_id, revision, result_code,"
                " conditions, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    rule["rule_id"],
                    rule["revision"],
                    rule["result_code"],
                    Jsonb(rule["conditions"]),
                    rule["created_at"],
                ),
            )
    print(
        f"開発用DB: DDLと架空データを適用しました"
        f"（設問{len(sample['review_questions'])}件、"
        f"判定ルール{len(demo_rules)}件）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
