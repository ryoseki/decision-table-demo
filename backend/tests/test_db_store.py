"""PostgreSQLでの保存と再表示の検証（受入例4〜7と、DBが関わる受入条件S1・S4〜S15・S17）。

ローカルのPostgreSQLだけで実行する（RUN_DB_TESTS=1 でなければ全件スキップ）。
実Aurora DSQLの結果ではない。uvicornを実際に起動し、
受付が実HTTPでモックを呼ぶ。案件・値はすべて架空。
実行: bash scripts/backend-db-test.sh
"""

import json
import os
import re
import secrets
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from uuid import uuid4

import psycopg
import psycopg.errors

from app import db_retry, db_store, shared_db
from app.domain import (
    JudgmentAlreadyRecorded,
    ProposalConflict,
    ProposalNotFound,
    QuestionBaseChanged,
    StoreBusy,
)
from app.question_validation import validate_model_output
from app.rule_validation import validate_rule_output
from app.shared_db import DbQuestionStore, DbRuleStore, PostgresAiQuota, SharedConfigDb
from app.usage_quota import DuplicateAiRequest, QuotaExceeded, QuotaLimits
from tests.test_intake import _Server, case_payload, make_app, send

DOCS = Path(os.environ.get("DB_DOCS_DIR", "/repo/docs"))
# 通常のbackendコンテナにも DB_HOST があるため、専用の目印（RUN_DB_TESTS）で切り替える。
HAVE_DB = os.environ.get("RUN_DB_TESTS") == "1"


def _connect(schema: str | None = None, autocommit: bool = False):
    conn = psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        autocommit=autocommit,
        options=f"-c search_path={schema}" if schema else None,
    )
    if not autocommit:
        # アプリの接続と同じ分離レベル（Aurora DSQLはREPEATABLE READだけ）
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


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


class _ConflictOnceConnection(_FailingConnection):
    """最初の1回だけ、指定のSQLでOCCの衝突（40001）を起こす接続のラッパー。"""

    def __init__(self, conn, fail_prefix: str, counter: list[int]):
        super().__init__(conn, fail_prefix)
        self._counter = counter
        counter[0] += 1

    def execute(self, sql, *args):
        if self._counter[0] == 1 and sql.lstrip().startswith(self._fail_prefix):
            raise psycopg.errors.SerializationFailure("OC000（検証用）")
        return self._conn.execute(sql, *args)


class _PauseAfterLockConnection(_FailingConnection):
    """行ロック（FOR UPDATE）を取ったあと、合図があるまで止まる接続のラッパー。"""

    def __init__(self, conn, locked: threading.Event, release: threading.Event):
        super().__init__(conn, "")
        self._locked, self._release = locked, release

    def execute(self, sql, *args):
        result = self._conn.execute(sql, *args)
        if sql.rstrip().endswith("FOR UPDATE"):
            self._locked.set()
            if not self._release.wait(10):
                raise RuntimeError("検証用の待ちが解除されなかった")
        return result


@unittest.skipUnless(HAVE_DB, "RUN_DB_TESTS=1 でないためスキップ（要ローカルDB）")
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

    def test_shared_quota_deduplicates_and_enforces_both_caps(self):
        now = datetime(2026, 11, 16, 14, 59, tzinfo=UTC)
        quota = PostgresAiQuota(
            lambda: _connect(self.schema),
            b"x" * 32,
            QuotaLimits(2, 3),
            clock=lambda: now,
        )
        request_id = str(uuid4())

        def reserve_once(_):
            try:
                quota.reserve("v4:192.0.2.1", request_id)
            except DuplicateAiRequest:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reserve_once, range(2))), [False, True])
        self.assertEqual(quota.used("v4:192.0.2.1"), (1, 1))
        quota.reserve("v4:192.0.2.1", str(uuid4()))
        with self.assertRaises(QuotaExceeded) as ip_exceeded:
            quota.reserve("v4:192.0.2.1", str(uuid4()))
        self.assertEqual(ip_exceeded.exception.scope, "ip")
        quota.reserve("v4:192.0.2.2", str(uuid4()))
        with self.assertRaises(QuotaExceeded) as global_exceeded:
            quota.reserve("v4:192.0.2.3", str(uuid4()))
        self.assertEqual(global_exceeded.exception.scope, "global")
        self.assertEqual(quota.used("v4:192.0.2.3"), (0, 3))
        now += timedelta(minutes=1)
        with self.assertRaises(DuplicateAiRequest):
            quota.reserve("v4:192.0.2.1", request_id)
        self.assertEqual(quota.used("v4:192.0.2.1"), (0, 0))

    def test_approved_question_is_visible_to_a_new_store(self):
        def connect():
            return _connect(self.schema)

        first = DbQuestionStore(SharedConfigDb(connect))
        revision, questions = first.snapshot()
        proposal = validate_model_output(
            {
                "operation": "add_question",
                "question": {"id": "A13", "text": "架空の追加設問"},
            },
            questions,
            revision,
        )
        proposal_id = first.register(proposal)
        second = DbQuestionStore(SharedConfigDb(connect))
        second.approve(proposal_id)
        self.assertEqual(first.snapshot()[1][-1].text, "架空の追加設問")
        restored = SharedConfigDb(connect).restore(1)
        self.assertEqual(restored.config_revision, 3)
        self.assertTrue(first.snapshot()[1][-1].retired)
        self.assertEqual(first.snapshot()[1][-1].id, "A13")

    # --- 共有設定・利用枠の同時操作 -------------------------------------------------

    def _shared(self):
        db = SharedConfigDb(lambda: _connect(self.schema))
        return db, DbQuestionStore(db), DbRuleStore(db)

    def _history(self):
        with _connect(self.schema) as conn:
            return conn.execute(
                "SELECT config_revision, origin FROM shared_config_revisions"
                " ORDER BY config_revision"
            ).fetchall()

    @staticmethod
    def _add_question(store, text):
        revision, questions = store.snapshot()
        number = len(questions) + 1
        proposal = validate_model_output(
            {
                "operation": "add_question",
                "question": {"id": f"A{number:02d}", "text": text},
            },
            questions,
            revision,
        )
        return store.register(proposal)

    @staticmethod
    def _add_rule(db, rules_store, questions_store):
        question_revision, questions = questions_store.snapshot()
        revision, rules = rules_store.snapshot()
        proposal = validate_rule_output(
            {
                "operation": "add_rule",
                "rule": {
                    "id": "R04",
                    "result_code": "caution",
                    "conditions": {"A01": "yes"},
                },
            },
            rules,
            questions,
            revision,
            question_revision=question_revision,
        )
        return rules_store.register(proposal)

    def _race(self, first, second):
        """2つの承認が、同じ最新の版を読み終えてから、書き込みへ進むようにそろえる。"""
        barrier = threading.Barrier(2)
        seen = threading.local()
        real = shared_db._latest

        def latest(conn):
            config = real(conn)
            if not getattr(seen, "done", False):
                seen.done = True
                barrier.wait(10)
            return config

        def run(call):
            try:
                return call()
            except Exception as error:  # noqa: BLE001  結果として比べる
                return error

        with (
            mock.patch.object(shared_db, "_latest", latest),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [pool.submit(run, first), pool.submit(run, second)]
            return [future.result(timeout=30) for future in futures]

    def test_stale_no_and_actual_approvals_keep_history_append_only(self):
        _, questions, _ = self._shared()
        stale = self._add_question(questions, "架空の設問その1")
        other = self._add_question(questions, "架空の設問その2")
        questions.approve(stale)
        self.assertEqual(self._history(), [(1, "seed"), (2, "approval")])
        with self.assertRaises(ProposalConflict):
            questions.approve(other)  # 基準が古い: 追加せず、提案は使い切る
        self.assertEqual(self._history(), [(1, "seed"), (2, "approval")])
        with self.assertRaises(ProposalNotFound):
            questions.approve(other)
        declined = self._add_question(questions, "架空の設問その3")
        questions.reject(declined)  # 「いいえ」: 履歴は増えない
        self.assertEqual(len(self._history()), 2)
        with self.assertRaises(ProposalNotFound):
            questions.approve(declined)
        questions.approve(self._add_question(questions, "架空の設問その4"))
        self.assertEqual(
            self._history(), [(1, "seed"), (2, "approval"), (3, "approval")]
        )
        with _connect(self.schema) as conn:  # 過去の版は変わらない
            first = conn.execute(
                "SELECT jsonb_array_length(questions), question_revision"
                " FROM shared_config_revisions WHERE config_revision = 1"
            ).fetchone()
        self.assertEqual(first[1], 1)

    def test_concurrent_approvals_of_the_same_base_leave_one_revision_and_no_500(self):
        _, questions, _ = self._shared()
        first = self._add_question(questions, "架空の設問その1")
        second = self._add_question(questions, "架空の設問その2")
        outcomes = self._race(
            lambda: questions.approve(first), lambda: questions.approve(second)
        )
        failures = [o for o in outcomes if isinstance(o, Exception)]
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIsInstance(failures[0], ProposalConflict)
        self.assertNotIsInstance(failures[0], psycopg.errors.UniqueViolation)
        self.assertEqual(self._history(), [(1, "seed"), (2, "approval")])

    def test_concurrent_question_and_rule_approvals_never_return_unique_errors(self):
        db, questions, rules_store = self._shared()
        question_id = self._add_question(questions, "架空の設問その1")
        rule_id = self._add_rule(db, rules_store, questions)
        outcomes = self._race(
            lambda: questions.approve(question_id),
            lambda: rules_store.approve(rule_id),
        )
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                self.assertIsInstance(outcome, (ProposalConflict, QuestionBaseChanged))
        succeeded = sum(1 for o in outcomes if not isinstance(o, Exception))
        history = self._history()
        self.assertEqual([r for r, _ in history], list(range(1, len(history) + 1)))
        self.assertEqual(len(history), 1 + succeeded)

    def test_a_revision_conflict_that_never_clears_is_busy_not_a_unique_error(self):
        db, questions, _ = self._shared()
        proposal = self._add_question(questions, "架空の設問その1")
        with (
            mock.patch.object(db_retry, "sleep"),
            mock.patch.object(
                shared_db,
                "_insert_config",
                side_effect=psycopg.errors.SerializationFailure("検証用"),
            ),
            self.assertRaises(StoreBusy),
        ):
            questions.approve(proposal)
        self.assertEqual(self._history(), [(1, "seed")])
        questions.approve(proposal)  # 失敗した承認は提案を消費していない

    def _quota(self, limits, clock):
        return PostgresAiQuota(
            lambda: _connect(self.schema), b"x" * 32, limits, clock=clock
        )

    def _burst(self, quota, keys):
        def run(key):
            try:
                quota.reserve(key, str(uuid4()))
            except QuotaExceeded as error:
                return error.scope
            except StoreBusy:
                return "busy"
            return "ok"

        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            return list(pool.map(run, keys))

    def test_concurrent_distinct_ids_never_exceed_the_per_ip_limit(self):
        now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
        quota = self._quota(QuotaLimits(2, 50), lambda: now)
        results = self._burst(quota, ["v4:192.0.2.1"] * 6)
        self.assertLessEqual(results.count("ok"), 2)
        self.assertEqual(set(results) - {"ok", "ip", "busy"}, set())
        self.assertEqual(quota.used("v4:192.0.2.1")[0], results.count("ok"))
        for _ in range(2):  # 混雑で数えなかった分を順に確認: 合計でちょうど2件で止まる
            try:
                quota.reserve("v4:192.0.2.1", str(uuid4()))
            except QuotaExceeded:
                break
        self.assertEqual(quota.used("v4:192.0.2.1"), (2, 2))

    def test_concurrent_distinct_ids_never_exceed_the_global_limit(self):
        now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
        quota = self._quota(QuotaLimits(50, 3), lambda: now)
        keys = [f"v4:192.0.2.{n}" for n in range(1, 7)]
        results = self._burst(quota, keys)
        self.assertLessEqual(results.count("ok"), 3)
        self.assertEqual(set(results) - {"ok", "global", "busy"}, set())
        self.assertEqual(quota.used(keys[0])[1], results.count("ok"))
        for key in keys:
            try:
                quota.reserve(key, str(uuid4()))
            except QuotaExceeded:
                break
        self.assertEqual(quota.used(keys[0])[1], 3)
        with self.assertRaises(QuotaExceeded) as exceeded:
            quota.reserve("v4:192.0.2.99", str(uuid4()))
        self.assertEqual(exceeded.exception.scope, "global")

    def test_same_request_id_after_the_day_rolls_over_is_still_a_duplicate(self):
        now = [datetime(2026, 11, 16, 14, 59, tzinfo=UTC)]  # 日本時間 23:59
        quota = self._quota(QuotaLimits(2, 3), lambda: now[0])
        request_id = str(uuid4())
        quota.reserve("v4:192.0.2.1", request_id)
        now[0] += timedelta(minutes=2)  # 日本時間の翌日
        with self.assertRaises(DuplicateAiRequest):
            quota.reserve("v4:192.0.2.1", request_id)
        self.assertEqual(quota.used("v4:192.0.2.1"), (0, 0))  # 新しい日は数えない
        quota.reserve("v4:192.0.2.1", str(uuid4()))  # 別のIDは、新しい日の枠で通る
        self.assertEqual(quota.used("v4:192.0.2.1"), (1, 1))

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

    def test_s16_unknown_fields_are_not_stored_in_the_columns(self):
        payload = case_payload("456", source_case_id="X-1")
        payload["user"]["identity_verified"] = True
        payload["inquiry"]["received_at"] = "2026-10-20T09:00:00+09:00"
        status, body = send("POST", f"{self.server.base_url}/api/intake/cases", payload)
        self.assertEqual(status, 201, body)
        [(user_data, inquiry_data)] = self.rows(
            "SELECT user_data, inquiry_data FROM review_cases WHERE case_id = %s",
            (body["case_id"],),
        )
        self.assertEqual(user_data, {"user_id": "456"})
        self.assertEqual(set(inquiry_data), {"subject", "body"})

    def test_list_rules_returns_the_latest_revision_of_each_rule_in_rule_id_order(self):
        with _connect(self.schema) as conn:
            for rule_id, revision, code, conditions in [
                ("R02", 1, "no_issue", {"A01": "yes"}),
                ("R01", 1, "caution", {"A01": "yes", "A02": "yes"}),
                ("R01", 2, "no_issue", {"A01": "no"}),
            ]:
                conn.execute(
                    "INSERT INTO decision_rules (rule_id, revision, result_code,"
                    " conditions, created_at) VALUES (%s, %s, %s, %s, now())",
                    (rule_id, revision, code, json.dumps(conditions)),
                )
        status, body = send("GET", f"{self.server.base_url}/api/rules")
        self.assertEqual(status, 200, body)
        self.assertEqual(
            body,
            [
                {
                    "rule_id": "R01",
                    "revision": 2,
                    "result_code": "no_issue",
                    "conditions": {"A01": "no"},
                },
                {
                    "rule_id": "R02",
                    "revision": 1,
                    "result_code": "no_issue",
                    "conditions": {"A01": "yes"},
                },
            ],
        )

    def test_list_cases_returns_summary_rows_in_case_id_order(self):
        first, _ = self.intake("456")  # A09 は yes
        second, _ = self.intake("457")  # A09 は no
        self.judge(first, {"result_code": "caution", "answers": {"A01": "yes"}})
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            third, _ = self.intake("458", demo_controls={"fault_questions": ["A09"]})
        rows = self.store.list_cases()
        self.assertEqual([r["case_id"] for r in rows], sorted([first, second, third]))
        by_id = {r["case_id"]: r for r in rows}
        self.assertEqual(by_id[first]["user_id"], "456")
        self.assertEqual(by_id[first]["result_code"], "caution")
        self.assertEqual(
            by_id[first]["counts"], {"yes": 2, "no": 0, "pending": 0, "unanswered": 10}
        )
        # 取得に失敗した案件は、確認待ち（pending）が1件
        self.assertEqual(
            by_id[third]["counts"], {"yes": 0, "no": 0, "pending": 1, "unanswered": 11}
        )
        self.assertIsNone(by_id[second]["result_code"])  # 未判定
        self.assertEqual(
            by_id[second]["counts"], {"yes": 0, "no": 1, "pending": 0, "unanswered": 11}
        )
        # HTTP経由でも同じ内容（再表示と同じく、HTTPモックは呼ばない）
        calls = len(self.mock_calls)
        status, body = send("GET", f"{self.server.base_url}/api/cases")
        self.assertEqual((status, body), (200, rows))
        self.assertEqual(len(self.mock_calls), calls)

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

    def test_example5_manual_no_is_saved_with_the_result_without_history(self):
        # 初回保存前に、確認待ちを手入力のNoで解消し、結果「問題なし」で保存する。
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            case_id, _ = self.intake("458", demo_controls={"fault_questions": ["A09"]})
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
        # S15: 人がA09を変えて保存しても、受付時の自動回答は残る（別の案件の初回保存）
        other, _ = self.intake("456")
        self.judge(
            other, {"result_code": "account_suspension", "answers": {"A09": "no"}}
        )
        self.assertEqual(
            self.rows(
                "SELECT answer, automated_answer FROM case_checks"
                " WHERE case_id = %s AND question_code = 'A09'",
                (other,),
            ),
            [("no", "yes")],
        )
        self.assertEqual(len(self.mock_calls), 2)  # 受付の2回だけ

    # --- 結果・採用ルール・検査 -------------------------------------------------------

    def add_rule(self, rule_id, revision):
        with _connect(self.schema) as conn:
            conn.execute(
                "INSERT INTO decision_rules (rule_id, revision, result_code,"
                " conditions, created_at) VALUES (%s, %s, 'caution', %s, now())",
                (rule_id, revision, json.dumps({"A01": "yes"})),
            )

    def test_adopting_a_rule_that_does_not_exist_is_rejected_and_nothing_changes(self):
        self.add_rule("R01", 1)
        case_id, _ = self.intake("456")
        before = self.get(case_id)
        for rule_id, revision in (("NO-SUCH-RULE", 1), ("R01", 2)):
            status, body = self.judge(
                case_id,
                {
                    "result_code": "no_issue",
                    "rule_id": rule_id,
                    "rule_revision": revision,
                    "answers": {"A01": "yes"},
                },
            )
            self.assertEqual(status, 422, (rule_id, revision))
            self.assertIn("採用ルールが存在しません", body["detail"])
            self.assertEqual(self.get(case_id), before)  # 結果も回答も変わらない

    def test_an_older_revision_of_an_existing_rule_can_be_adopted(self):
        # 検査するのは、(rule_id, revision) の行があるかだけ。最新の版かどうかは見ない。
        self.add_rule("R01", 1)
        self.add_rule("R01", 2)
        case_id, _ = self.intake("456")
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 1}
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            self.get(case_id)["result"]["adopted_rule"],
            {"rule_id": "R01", "rule_revision": 1},
        )

    def test_adopting_and_reopening_do_not_call_the_mock_again(self):
        self.add_rule("R01", 1)
        case_id, _ = self.intake("456")
        calls = len(self.mock_calls)
        self.judge(
            case_id, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 1}
        )
        self.get(case_id)
        self.get(case_id)
        self.assertEqual(len(self.mock_calls), calls)  # 受付のときの呼び出しだけ

    def test_s9_s10_adopted_rule_is_recorded_only_when_given(self):
        self.add_rule("R01", 7)
        without_rule, _ = self.intake("456")
        self.judge(without_rule, {"result_code": "no_issue"})  # S9: 結果だけ
        self.assertIsNone(self.get(without_rule)["result"]["adopted_rule"])
        case_id, _ = self.intake("456")
        # S10: 採用ルールの結果と違う結果でも保存できる（参考の記録）
        status, _ = self.judge(
            case_id,
            {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 7},
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            self.get(case_id)["result"]["adopted_rule"],
            {"rule_id": "R01", "rule_revision": 7},
        )
        # 一方だけは422（未登録の案件で確認する）
        unregistered, _ = self.intake("456")
        for bad in ({"rule_id": "R"}, {"rule_revision": 1}):
            self.assertEqual(
                self.judge(unregistered, {"result_code": "no_issue", **bad})[0], 422
            )

    # --- 登録済みの案件は再保存できない（2026-11-15の合意。旧「上書き」を置換） ---

    def snapshot(self, case_id):
        return (
            self.rows(
                "SELECT result_code, rule_id, rule_revision, recorded_by, recorded_at"
                " FROM case_results WHERE case_id = %s",
                (case_id,),
            ),
            self.rows(
                "SELECT question_code, answer, automated_answer FROM case_checks"
                " WHERE case_id = %s ORDER BY question_code",
                (case_id,),
            ),
        )

    def test_resaving_a_registered_case_is_409_and_stored_rows_do_not_change(self):
        self.add_rule("R01", 1)
        case_id, _ = self.intake("456")
        self.judge(
            case_id,
            {
                "result_code": "caution",
                "rule_id": "R01",
                "rule_revision": 1,
                "answers": {"A01": "yes"},
            },
        )
        before = self.snapshot(case_id)
        view = self.get(case_id)
        for payload in (
            {"result_code": "caution", "answers": {"A01": "yes"}},  # 同じ内容の再送
            {"result_code": "no_issue", "answers": {"A01": "no", "A02": "yes"}},
            {"result_code": "no_issue"},  # 採用を渡さない再保存で、採用が消えない
        ):
            status, _ = self.judge(case_id, payload)
            self.assertEqual(status, 409, payload)
            self.assertEqual(self.snapshot(case_id), before)  # 登録日時も不変
        self.assertEqual(self.get(case_id), view)

    def test_a_stale_check_cannot_overwrite_a_registered_case(self):
        # 保存先の事前の確認が古く（未登録に見え）ても、条件付きの更新が上書きを止める。
        # ロックの待ち方に依存しない（PostgreSQLでもAurora DSQLでも成り立つ）。
        case_id, _ = self.intake("456")
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        before = self.snapshot(case_id)

        class _StaleRead(_FailingConnection):
            def execute(self, sql, *args):
                if sql.lstrip().startswith("SELECT result_code FROM case_results"):
                    return self._conn.execute("SELECT NULL::text")  # 未登録に見える
                return self._conn.execute(sql, *args)

        stale = db_store.PostgresCaseStore(
            lambda: _StaleRead(_connect(self.schema), "")
        )
        with self.assertRaises(JudgmentAlreadyRecorded):
            stale.save_judgment(case_id, "no_issue", {"A01": "no", "A02": "yes"}, None)
        self.assertEqual(self.snapshot(case_id), before)  # 回答の行も不変

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

    # --- OCCの衝突と再試行（Aurora DSQLでも成り立つ挙動） ----------------------------

    def _no_wait(self):
        waits: list[float] = []
        patch = mock.patch.object(db_retry, "sleep", waits.append)
        patch.start()
        self.addCleanup(patch.stop)
        return waits

    def test_retry_after_conflict_does_not_double_save_judgment(self):
        case_id, _ = self.intake("456")
        waits = self._no_wait()
        counter = [0]
        flaky = db_store.PostgresCaseStore(
            lambda: _ConflictOnceConnection(
                _connect(self.schema), "UPDATE case_results", counter
            )
        )
        flaky.save_judgment(case_id, "caution", {"A01": "yes"}, None)
        self.assertEqual((counter[0], len(waits)), (2, 1), "1回衝突して再試行")
        view = self.get(case_id)
        self.assertEqual(view["result"]["result_code"], "caution")
        self.assertEqual(self.answers_of(view)["A01"], "yes")
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM case_checks WHERE case_id = %s"
                " AND question_code = 'A01'",
                (case_id,),
            ),
            [(1,)],
        )

    def test_retry_after_conflict_does_not_double_create_case(self):
        waits = self._no_wait()
        counter = [0]
        flaky = db_store.PostgresCaseStore(
            lambda: _ConflictOnceConnection(
                _connect(self.schema), "INSERT INTO case_results", counter
            )
        )
        self.start(flaky)
        case_id, answers = self.intake("456")
        # 接続: 1回目の保存（衝突→再試行で2回）＋2回目の保存（1回）
        self.assertEqual((counter[0], len(waits)), (3, 1))
        self.assertEqual(answers, {"A09": "yes"})
        self.assertEqual(
            self.rows("SELECT case_id FROM review_cases"), [(case_id,)], "案件は1件"
        )
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM case_questions WHERE case_id = %s", (case_id,)
            ),
            [(12,)],
        )

    def test_connect_from_env_uses_repeatable_read(self):
        """アプリの接続関数が、DSQLと同じ分離レベル（REPEATABLE READ）で接続する。"""
        env = {"DB_SCHEMA": self.schema}
        with mock.patch.dict(os.environ, env):
            with db_store.connect_from_env() as conn:
                level = conn.execute("SHOW transaction_isolation").fetchone()
        self.assertEqual(level, ("repeatable read",))

    def test_concurrent_saves_first_committed_stays_and_later_is_rejected(self):
        """同じ案件の同時保存。後から入った側は、衝突（40001）を再試行したあと、
        登録済みとして拒否される。先にコミットした側の結果と回答だけが残る。

        確かめるのは「衝突したら再試行し、登録済みを上書きしない」で、DSQLでも成り立つ。
        衝突の起こし方（行ロックの待ちと、コミット後の40001）はPostgreSQL固有で、
        ローカルで衝突を再現するための手段にすぎない。実DSQLの挙動は確認していない。

        Aは行ロックを取って止まる。BはAのロック待ちに入る。AがコミットするとBは
        40001になり、再試行で保存できる。REPEATABLE READ（DSQLと同じ）で再現する。
        """
        case_id, _ = self.intake("456")
        self._no_wait()
        locked, release = threading.Event(), threading.Event()
        store_a = db_store.PostgresCaseStore(
            lambda: _PauseAfterLockConnection(_connect(self.schema), locked, release)
        )
        counter = [0]

        def connect_b():
            counter[0] += 1
            return _connect(self.schema)

        store_b = db_store.PostgresCaseStore(connect_b)
        errors: list[BaseException] = []

        def save(store, result_code, answer):
            try:
                store.save_judgment(case_id, result_code, {"A01": answer}, None)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        thread_a = threading.Thread(target=save, args=(store_a, "caution", "yes"))
        thread_a.start()
        self.assertTrue(locked.wait(10), "Aがロックを取った")
        thread_b = threading.Thread(target=save, args=(store_b, "no_issue", "no"))
        thread_b.start()
        # BがAのロック待ちに入ったことを、ロックの待ち状況で確認する
        for _ in range(100):
            waiting = self.rows(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND datname = current_database() AND query LIKE '%%FOR UPDATE%%'"
            )
            if waiting[0][0] >= 1:
                break
            threading.Event().wait(0.05)
        else:
            self.fail("BがAのロックを待つ状態にならなかった")
        release.set()
        thread_a.join(20)
        thread_b.join(20)
        self.assertEqual(
            [type(error) for error in errors],
            [JudgmentAlreadyRecorded],
            "先に確定したAは成功し、Bだけが登録済みで拒否される",
        )
        self.assertGreaterEqual(counter[0], 2, "Bは衝突して再試行した")
        view = self.get(case_id)
        self.assertEqual(view["result"]["result_code"], "caution", "先の保存が残る")
        self.assertEqual(self.answers_of(view)["A01"], "yes", "Bの回答は入らない")

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
