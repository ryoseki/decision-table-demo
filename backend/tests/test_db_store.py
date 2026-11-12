"""PostgreSQLでの保存と再表示の検証（受入例4〜7と、DBが関わる受入条件S1・S4〜S15・S17）。

ローカルのPostgreSQLだけで実行する（DB_HOSTがなければ全件スキップ）。実Aurora DSQLの
結果ではない。uvicornを実際に起動し、受付が実HTTPでモックを呼ぶ。案件・値はすべて架空。
実行: bash scripts/backend-db-test.sh
"""

import json
import os
import re
import secrets
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from app import db_store
from tests.test_intake import _Server, case_payload, make_app, send

DOCS = Path(os.environ.get("DB_DOCS_DIR", "/repo/docs"))
HAVE_DB = bool(os.environ.get("DB_HOST"))


def _connect(schema: str | None = None, autocommit: bool = False):
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        autocommit=autocommit,
        options=f"-c search_path={schema}" if schema else None,
    )


class _FailingConnection:
    """指定したSQLの先頭に一致する実行だけを失敗させる接続のラッパー（失敗の検証用）。"""

    def __init__(self, conn, fail_prefix: str):
        self._conn, self._fail_prefix = conn, fail_prefix

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def execute(self, sql, *args):
        if sql.lstrip().startswith(self._fail_prefix):
            raise RuntimeError("検証用の失敗")
        return self._conn.execute(sql, *args)


@unittest.skipUnless(HAVE_DB, "DB_HOST がないためスキップ（ローカルPostgreSQLが必要）")
class DbFlow(unittest.TestCase):
    def setUp(self):
        self.schema = f"t_{secrets.token_hex(6)}"
        with _connect(autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self._drop_schema)
        ddl_text = (DOCS / "decision-support-schema.md").read_text(encoding="utf-8")
        ddl = re.search(r"```sql\n(.*?)```", ddl_text, re.S).group(1)
        sample = json.loads(
            (DOCS / "decision-support-sample.json").read_text(encoding="utf-8")
        )
        with _connect(self.schema) as conn:
            for statement in (s.strip() for s in ddl.split(";") if s.strip()):
                conn.execute(statement)
            for q in sample["review_questions"]:
                conn.execute(
                    "INSERT INTO review_questions (question_code, category, prompt,"
                    " auto_answer_key, source_kind, source_field, revision)"
                    " VALUES (%(question_code)s, %(category)s, %(prompt)s,"
                    " %(auto_answer_key)s, %(source_kind)s, %(source_field)s,"
                    " %(revision)s)",
                    q,
                )
        self.store = db_store.PostgresCaseStore(lambda: _connect(self.schema))
        self.mock_calls: list[str] = []
        self.start(self.store)

    def start(self, store):
        self.server = _Server(make_app(store, self.mock_calls))
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)
        env = mock.patch.dict(os.environ, {"MOCK_BASE_URL": self.server.base_url})
        env.start()
        self.addCleanup(env.stop)

    def _drop_schema(self):
        with _connect(autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {self.schema} CASCADE")

    # --- ヘルパー ---------------------------------------------------------------

    def rows(self, sql, params=()):
        with _connect(self.schema) as conn:
            return conn.execute(sql, params).fetchall()

    def intake(self, user_id, **extra):
        status, body = send(
            "POST",
            f"{self.server.base_url}/api/intake/cases",
            case_payload(user_id, **extra),
        )
        self.assertEqual(status, 201, body)
        return body["case_id"], body["answers"]

    def get(self, case_id):
        status, body = send("GET", f"{self.server.base_url}/api/cases/{case_id}")
        self.assertEqual(status, 200, body)
        return body

    def judge(self, case_id, payload):
        return send(
            "PUT", f"{self.server.base_url}/api/cases/{case_id}/judgment", payload
        )

    @staticmethod
    def answers_of(view):
        return {q["question_code"]: q["answer"] for q in view["questions"]}

    # --- 受付と再表示（S1・S4・S17・U1） ------------------------------------------

    def test_s1_intake_creates_rows_and_redisplay_does_not_call_mock(self):
        case_id, answers = self.intake("456")
        self.assertEqual(answers, {"A09": "yes"})
        self.assertEqual(len(self.mock_calls), 1)
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM case_questions WHERE case_id = %s", (case_id,)
            ),
            [(12,)],
        )
        self.assertEqual(
            self.rows(
                "SELECT result_code, recorded_by, recorded_at FROM case_results"
                " WHERE case_id = %s",
                (case_id,),
            ),
            [(None, None, None)],  # S11: 未判定は3列とも空
        )
        # S17: 受付直後はA09の行だけ
        self.assertEqual(
            self.rows(
                "SELECT question_code, answer, automated_answer FROM case_checks"
                " WHERE case_id = %s",
                (case_id,),
            ),
            [("A09", "yes", "yes")],
        )
        view = self.get(case_id)
        self.get(case_id)
        self.assertEqual(len(self.mock_calls), 1, "再表示でHTTPが増えない")
        self.assertIsNone(view["result"])
        answers = self.answers_of(view)
        self.assertEqual(answers["A09"], "yes")
        self.assertTrue(all(v is None for k, v in answers.items() if k != "A09"))
        self.assertEqual(len(view["questions"]), 12)
        self.assertNotIn("automated_answer", json.dumps(view))  # 表示に使わない

    def test_s4_failure_leaves_pending(self):
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            case_id, answers = self.intake(
                "458", demo_controls={"fault_questions": ["A09"]}
            )
        self.assertEqual(answers, {"A09": "pending"})
        self.assertEqual(
            self.rows(
                "SELECT answer, automated_answer FROM case_checks WHERE case_id = %s",
                (case_id,),
            ),
            [("pending", None)],
        )
        self.assertEqual(self.answers_of(self.get(case_id))["A09"], "pending")

    # --- 受入例4〜7 -------------------------------------------------------------

    def test_example4_pending_with_caution(self):
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            case_id, _ = self.intake("458", demo_controls={"fault_questions": ["A09"]})
        before = self.answers_of(self.get(case_id))
        status, _ = self.judge(case_id, {"result_code": "caution"})  # 回答は変えない
        self.assertEqual(status, 204)
        view = self.get(case_id)  # 開き直す
        self.assertEqual(view["result"]["result_code"], "caution")
        self.assertEqual(view["result"]["recorded_by"], "OP-1")
        self.assertIsNone(view["result"]["adopted_rule"])
        self.assertEqual(self.answers_of(view), before)  # 確認待ちと他の設問はそのまま
        self.assertEqual(self.answers_of(view)["A09"], "pending")
        self.assertEqual(len(self.mock_calls), 1)

    def test_example5_manual_no_overwrites_without_history(self):
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            case_id, _ = self.intake("458", demo_controls={"fault_questions": ["A09"]})
        status, _ = self.judge(case_id, {"result_code": "caution"})
        self.assertEqual(status, 204)
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "answers": {"A09": "no"}}
        )
        self.assertEqual(status, 204)
        view = self.get(case_id)
        self.assertEqual(self.answers_of(view)["A09"], "no")
        self.assertEqual(view["result"]["result_code"], "no_issue")
        # 最新のみ。automated_answerは空のまま。行は増えない。
        self.assertEqual(
            self.rows(
                "SELECT question_code, answer, automated_answer FROM case_checks"
                " WHERE case_id = %s",
                (case_id,),
            ),
            [("A09", "no", None)],
        )
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM case_results WHERE case_id = %s", (case_id,)
            ),
            [(1,)],
        )
        self.assertEqual(len(self.mock_calls), 1)

    def test_example6_unsaved_changes_are_not_stored(self):
        case_id, _ = self.intake("456")
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        saved = self.get(case_id)
        # 保存せずに開き直す。入力途中を受け取る経路はなく、最後の保存値が返る。
        self.assertEqual(self.get(case_id), saved)
        self.assertEqual(self.answers_of(saved)["A01"], "yes")

    def test_example7_saved_values_redisplayed_and_automated_answer_kept(self):
        case_id, _ = self.intake("456")
        status, _ = self.judge(
            case_id,
            {
                "result_code": "account_suspension",
                "answers": {"A01": "yes", "A09": "yes"},
            },
        )
        self.assertEqual(status, 204)
        view = self.get(case_id)
        self.assertEqual(self.answers_of(view)["A09"], "yes")
        self.assertEqual(self.answers_of(view)["A01"], "yes")
        self.assertEqual(view["result"]["result_code"], "account_suspension")
        self.assertEqual(len(self.mock_calls), 1)
        # S15: 人がA09を変えても、受付時の自動回答は残る
        self.judge(
            case_id, {"result_code": "account_suspension", "answers": {"A09": "no"}}
        )
        self.assertEqual(
            self.rows(
                "SELECT answer, automated_answer FROM case_checks"
                " WHERE case_id = %s AND question_code = 'A09'",
                (case_id,),
            ),
            [("no", "yes")],
        )
        self.assertEqual(len(self.mock_calls), 1)

    # --- 結果・採用ルール・検査 -------------------------------------------------------

    def test_s9_s10_adopted_rule_is_recorded_only_when_given(self):
        case_id, _ = self.intake("456")
        self.judge(case_id, {"result_code": "no_issue"})  # S9: 結果だけ
        self.assertIsNone(self.get(case_id)["result"]["adopted_rule"])
        # S10: 採用ルールの結果と違う結果でも保存できる（参考の記録）
        status, _ = self.judge(
            case_id,
            {"result_code": "no_issue", "rule_id": "NO-SUCH-RULE", "rule_revision": 7},
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            self.get(case_id)["result"]["adopted_rule"],
            {"rule_id": "NO-SUCH-RULE", "rule_revision": 7},
        )
        # 再保存で採用を渡さなければ、最新の状態として空になる
        self.judge(case_id, {"result_code": "no_issue"})
        self.assertIsNone(self.get(case_id)["result"]["adopted_rule"])
        # 一方だけは422
        for bad in ({"rule_id": "R"}, {"rule_revision": 1}):
            self.assertEqual(
                self.judge(case_id, {"result_code": "no_issue", **bad})[0], 422
            )

    def test_s14_undefined_result_code_is_rejected_and_nothing_changes(self):
        case_id, _ = self.intake("456")
        for code in ("unknown_code", "", "CAUTION"):
            status, _ = self.judge(
                case_id, {"result_code": code, "answers": {"A01": "yes"}}
            )
            self.assertEqual(status, 422, code)
        view = self.get(case_id)
        self.assertIsNone(view["result"])
        self.assertIsNone(self.answers_of(view)["A01"])

    def test_invalid_answers_are_rejected_and_nothing_changes(self):
        case_id, _ = self.intake("456")
        for answers in (
            {"A99": "yes"},  # 案件にない設問
            {"A09": "pending"},  # yes／noから確認待ちには戻せない
            {"A01": "pending"},  # 未回答から確認待ちは作れない
            {"A01": "unanswered"},  # 回答を空に戻す操作はない
            {"A01": "yes", "A99": "no"},  # 1件でも不正なら全体を保存しない
        ):
            status, _ = self.judge(
                case_id, {"result_code": "caution", "answers": answers}
            )
            self.assertEqual(status, 422, answers)
        view = self.get(case_id)
        self.assertIsNone(view["result"])
        self.assertIsNone(self.answers_of(view)["A01"])
        self.assertEqual(self.answers_of(view)["A09"], "yes")

    def test_unknown_case_is_404(self):
        self.assertEqual(send("GET", f"{self.server.base_url}/api/cases/NOPE")[0], 404)
        self.assertEqual(self.judge("NOPE", {"result_code": "caution"})[0], 404)

    def test_s8_unanswered_questions_stay_unanswered_after_judging(self):
        case_id, _ = self.intake("456")
        self.judge(case_id, {"result_code": "caution"})
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM case_checks WHERE case_id = %s", (case_id,)
            ),
            [(1,)],
        )
        self.assertIsNone(self.answers_of(self.get(case_id))["A02"])

    # --- 失敗とロールバック（S5・S12） -----------------------------------------------

    def test_s5_failure_during_judgment_rolls_back_result_and_answers(self):
        case_id, _ = self.intake("456")
        failing = db_store.PostgresCaseStore(
            lambda: _FailingConnection(_connect(self.schema), "INSERT INTO case_checks")
        )
        with self.assertRaises(RuntimeError):
            failing.save_judgment(case_id, "caution", {"A01": "yes"}, None)
        view = self.get(case_id)
        self.assertIsNone(view["result"], "結果の更新も戻る")
        self.assertIsNone(self.answers_of(view)["A01"])

    def test_s12_second_save_failure_leaves_pending_without_refetch(self):
        failing = db_store.PostgresCaseStore(
            lambda: _FailingConnection(_connect(self.schema), "UPDATE case_checks")
        )
        self.start(failing)  # 別のアプリ（失敗する保存先）で受け付ける
        calls_before = len(self.mock_calls)
        case_id, answers = self.intake("456")
        self.assertEqual(answers, {"A09": "pending"})
        self.assertEqual(len(self.mock_calls) - calls_before, 1)
        self.assertEqual(
            self.rows(
                "SELECT answer, automated_answer FROM case_checks WHERE case_id = %s",
                (case_id,),
            ),
            [("pending", None)],
        )

    def test_s3_rejected_intake_creates_no_case_and_no_http(self):
        status, _ = send(
            "POST",
            f"{self.server.base_url}/api/intake/cases",
            case_payload("456") | {"inquiry": {"subject": "件名"}},
        )
        self.assertEqual((status, self.mock_calls), (422, []))
        self.assertEqual(self.rows("SELECT count(*) FROM review_cases"), [(0,)])
        # 期間の前後が逆なら案件を作らず422（DBのCHECKに任せて500にしない）
        status, _ = send(
            "POST",
            f"{self.server.base_url}/api/intake/cases",
            case_payload(
                "456",
                period_start_at="2026-10-20T12:00:00+09:00",
                period_end_at="2026-10-20T09:00:00+09:00",
            ),
        )
        self.assertEqual((status, self.mock_calls), (422, []))


if __name__ == "__main__":
    unittest.main()
