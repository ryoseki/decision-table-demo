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

from app.rule_matching import DEMO_RULES, Result
from tests.test_db_store import DOCS, HAVE_DB, _connect

# 照合ロジックの結果（表示名）と、DBの結果コードの対応（画面と同じ4つ）。
CODE_OF_RESULT = {
    Result.ACCOUNT_DELETION: "account_deletion",
    Result.ACCOUNT_SUSPENSION: "account_suspension",
    Result.CAUTION: "caution",
    Result.NO_ISSUE: "no_issue",
}

INIT_DEV = Path(os.environ.get("DB_INIT_DIR", "/repo/db")) / "init_dev.py"
TABLES = [
    "ai_usage_counters",
    "ai_usage_requests",
    "case_checks",
    "case_questions",
    "case_results",
    "decision_rules",
    "processed_shared_proposals",
    "proposal_template_migrations",
    "proposal_templates",
    "review_cases",
    "review_questions",
    "sample_case_operations",
    "shared_config_revisions",
    "shared_proposals",
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
        self.assertEqual(tables, TABLES)  # DDLのテーブルがすべてできる
        self.assertEqual(len(questions), 12)  # 設問は12件
        self.assertEqual(
            [q[0] for q in questions],
            sorted(q["question_code"] for q in sample["review_questions"]),
        )
        # 判定ルールは、画面の受入例用の開発用ルール（R01〜R03）。
        self.assertEqual([r[0] for r in rules], ["R01", "R02", "R03"])
        self.assertEqual(
            [r[2] for r in rules], ["caution", "no_issue", "account_suspension"]
        )
        self.assertTrue(all(len(r[3]) == 12 for r in rules))  # 条件はA01〜A12の12問
        # 条件の中身も、照合ロジックの定義（rule_matching.DEMO_RULES）と同じ。
        for rule_id, _, code, conditions, _ in rules:
            expected = next(r for r in DEMO_RULES if r.rule_id == rule_id)
            self.assertEqual(
                conditions, {q: a.value for q, a in expected.conditions.items()}
            )
            self.assertEqual(code, CODE_OF_RESULT[expected.result])
        first = (tables, questions, rules)

        code, out = self.run_init()  # 2回目
        self.assertEqual(code, 0)
        self.assertIn("初期化済み", out)
        self.assertEqual(self.snapshot(), first)  # テーブルも中身も変わらない

    def test_old_dev_db_gets_missing_tables_and_keeps_existing_data(self):
        # DDL更新前に作った開発用DB（定型文・処理済み提案・サンプル案件の操作の対応表が
        # ない）を模す。
        new_tables = {
            "processed_shared_proposals",
            "proposal_templates",
            "proposal_template_migrations",
            "sample_case_operations",
        }
        with _connect(self.schema) as conn:
            for statement in self.init_dev.extract_ddl():
                if statement.split()[2] not in new_tables:
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO review_questions (question_code, category, prompt,"
                " auto_answer_key, source_kind, source_field, revision)"
                " VALUES ('A01', '架空', '利用者が編集した設問', NULL,"
                " 'ログイン履歴', '対象ログインの結果', 7)"
            )
            conn.execute(
                "INSERT INTO decision_rules (rule_id, revision, result_code,"
                " conditions, created_at)"
                " VALUES ('R09', 3, 'caution', '{\"A01\": \"yes\"}',"
                " '2026-01-01T00:00:00+00:00')"
            )
        before = self.snapshot()
        self.assertTrue(new_tables.isdisjoint(before[0]))

        code, out = self.run_init()
        self.assertEqual(code, 0)
        self.assertIn("初期化済み", out)
        tables, questions, rules = self.snapshot()
        self.assertEqual(tables, TABLES)  # 不足テーブルだけが足される
        # 既存データは、そのまま。架空データの再投入も、上書きもしない。
        self.assertEqual(questions, before[1])
        self.assertEqual(rules, before[2])
        self.assertEqual([q[2] for q in questions], ["利用者が編集した設問"])
        # 足したテーブルは空。初期の定型文は、アプリの起動時の移行で入れる。
        self.assert_empty(new_tables)

        self.assertEqual(self.run_init()[0], 0)  # 2回目も何も変えない
        self.assertEqual(self.snapshot(), (tables, questions, rules))
        self.assert_empty(new_tables)  # 足したテーブルも空のまま

    def assert_empty(self, tables):
        with _connect(self.schema) as conn:
            for table in sorted(tables):
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                self.assertEqual(count, 0, table)


if __name__ == "__main__":
    unittest.main()
