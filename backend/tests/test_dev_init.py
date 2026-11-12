"""開発用DBの初期化（db/init_dev.py）の検証。

`docker compose up` の `db-init` が動かすスクリプトが、DDLと架空データを正しく適用し、
2回目の実行では何も変えないことを確認する。使い捨てのスキーマの中で動かす。
ローカルPostgreSQL専用（RUN_DB_TESTS=1 でなければ全件スキップ）。
実Aurora DSQLへの適用には使わないスクリプトの確認で、DSQLでの動作の証拠ではない。
実行: bash scripts/backend-db-test.sh
"""

import importlib.util
import io
import json
import os
import secrets
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.test_db_store import DOCS, HAVE_DB, _connect

INIT_DEV = Path(os.environ.get("DB_INIT_DIR", "/repo/db")) / "init_dev.py"
TABLES = [
    "case_checks",
    "case_questions",
    "case_results",
    "decision_rules",
    "review_cases",
    "review_questions",
]


@unittest.skipUnless(HAVE_DB, "RUN_DB_TESTS=1 でないためスキップ（要ローカルDB）")
class DevInit(unittest.TestCase):
    def setUp(self):
        self.schema = f"t_{secrets.token_hex(6)}"
        with _connect(autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self._drop_schema)
        env = mock.patch.dict(
            os.environ, {"DOCS_DIR": str(DOCS), "DB_SCHEMA": self.schema}
        )
        env.start()
        self.addCleanup(env.stop)
        spec = importlib.util.spec_from_file_location("init_dev_under_test", INIT_DEV)
        self.init_dev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.init_dev)

    def _drop_schema(self):
        with _connect(autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def run_init(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.init_dev.main()
        return code, out.getvalue()

    def snapshot(self):
        with _connect(self.schema) as conn:
            tables = sorted(
                name
                for (name,) in conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = %s",
                    (self.schema,),
                )
            )
            questions = conn.execute(
                "SELECT question_code, category, prompt, auto_answer_key,"
                " source_kind, source_field, revision"
                " FROM review_questions ORDER BY question_code"
            ).fetchall()
            rules = conn.execute(
                "SELECT rule_id, revision, result_code, conditions, created_at"
                " FROM decision_rules ORDER BY rule_id, revision"
            ).fetchall()
        return tables, questions, rules

    def test_first_run_applies_ddl_and_sample_data_and_second_run_changes_nothing(self):
        sample = json.loads(
            (DOCS / "decision-support-sample.json").read_text(encoding="utf-8")
        )
        code, out = self.run_init()
        self.assertEqual(code, 0)
        self.assertIn("適用しました", out)
        tables, questions, rules = self.snapshot()
        self.assertEqual(tables, TABLES)  # DDLの6テーブルがすべてできる
        self.assertEqual(len(questions), 12)  # 設問は12件
        self.assertEqual(
            [q[0] for q in questions],
            sorted(q["question_code"] for q in sample["review_questions"]),
        )
        self.assertEqual(len(rules), len(sample["decision_rules"]))  # 判定ルールの件数
        first = (tables, questions, rules)

        code, out = self.run_init()  # 2回目
        self.assertEqual(code, 0)
        self.assertIn("初期化済み", out)
        self.assertEqual(self.snapshot(), first)  # テーブルも中身も変わらない


if __name__ == "__main__":
    unittest.main()
