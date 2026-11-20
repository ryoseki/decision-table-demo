"""E2E専用: 案件が1件もない、空の専用スキーマを作る（共有セットの「空の一覧から」を確認するため）。

compose.e2e.yaml の e2e-empty-db-init サービスだけが実行する（E2E専用プロジェクトのDBに限る）。
同じDBの中に、専用スキーマ（DB_SCHEMA）を作り、docs のDDLと設問・開発用ルールを入れる
（db/init_dev.py をそのまま使う）。案件は入れない。ほかのspecが作る案件とは交わらない。
アプリの本番経路にテスト専用のAPIは足さない。
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
