"""共有サンプルのE2Eを空の一覧から始めるため、専用スキーマを用意する。

E2E専用プロジェクトのDBでのみ、compose.e2e.yamlのe2e-empty-db-initサービスが実行する。
db/init_dev.py でDDL・設問・開発用ルールを適用し、案件はテストの画面操作から作成する。
本番APIとは独立した初期化サービスで、他のspecの案件と分離して初期件数を0件に保つ。
"""

import os
import re
import sys

import init_dev  # /db にある開発用DBの初期化（PYTHONPATH=/db）
import psycopg


def main() -> int:
    schema = os.environ["DB_SCHEMA"]
    if not re.fullmatch(r"e2e_[a-z0-9_]+", schema):
        print("DB_SCHEMA は e2e_ で始まる小文字英数字と _ だけ。", file=sys.stderr)
        return 1
    with psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""),
        autocommit=True,
    ) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    return init_dev.main()


if __name__ == "__main__":
    sys.exit(main())
