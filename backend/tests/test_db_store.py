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

from app import db_retry, db_store, rule_admin, shared_db
from app.domain import (
    InvalidProposal,
    JudgmentAlreadyRecorded,
    ProposalConflict,
    ProposalNotFound,
    QuestionBaseChanged,
    StoreBusy,
)
from app.question_validation import validate_model_output
from app.rule_matching import (
    Answer,
    ConditionState,
    Result,
    RowDisplay,
    Rule,
    match_rules,
)
from app.rule_validation import DemoRule, validate_rule_output
from app.shared_db import (
    DbQuestionStore,
    DbRuleStore,
    PostgresAiQuota,
    SharedConfig,
    SharedConfigDb,
)
from app.template_store import PostgresProposalTemplates
from app.usage_quota import DuplicateAiRequest, QuotaExceeded, QuotaLimits
from tests.test_intake import _Server, case_payload, make_app, send

DOCS = Path(os.environ.get("DB_DOCS_DIR", "/repo/docs"))
# 通常のbackendコンテナにも DB_HOST があるため、専用の目印（RUN_DB_TESTS）で切り替える。
HAVE_DB = os.environ.get("RUN_DB_TESTS") == "1"
# 条件の更新の検証で使う、架空のルールR04の追加（初期のルールはR01〜R03だけ）。
ADD_R04 = {
    "operation": "add_rule",
    "rule": {"id": "R04", "result_code": "caution", "conditions": {"A10": "no"}},
}


# 結果コード（APIの値）から、照合ロジックの結果への対応。
# 照合そのものは app.rule_matching.match_rules を使う。
RESULT_OF_CODE = {
    "account_deletion": Result.ACCOUNT_DELETION,
    "account_suspension": Result.ACCOUNT_SUSPENSION,
    "caution": Result.CAUTION,
    "no_issue": Result.NO_ISSUE,
}


class _StubRuleProposer:
    """生成のスタブ（固定の出力を返す。AIにもAWSにも接続しない）。"""

    def __init__(self) -> None:
        self.output = None

    def generate(self, prompt, current, next_id, questions):
        return self.output


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

    def test_reuse_registers_only_applied_proposals_and_keeps_deleted_seed_deleted(
        self,
    ):
        def connect():
            return _connect(self.schema)

        templates = PostgresProposalTemplates(connect)
        templates.bootstrap()
        self.assertIn("設問A13を廃止して", templates.list_prompts("question"))
        templates.remove("question", "設問A13を廃止して")
        templates.bootstrap()
        self.assertEqual(templates.list_prompts("question"), [])

        db = SharedConfigDb(connect, reuse_enabled=True)
        store = DbQuestionStore(db)
        revision, questions = store.snapshot()
        proposal = validate_model_output(
            {
                "operation": "add_question",
                "question": {"id": "A13", "text": "架空の設問"},
            },
            questions,
            revision,
        )
        declined = store.register_for_reuse(proposal, "架空の依頼")
        waiting = store.register_for_reuse(proposal, "再利用する依頼")
        # Noでは定型文を作らない。
        store.reject(declined)
        self.assertIsNone(templates.lookup("question", "架空の依頼"))
        # 反映の途中で失敗したら、変更・処理済み記録・自動登録のすべてを取り消す。
        with mock.patch.object(
            shared_db, "save_template", side_effect=RuntimeError("検証用")
        ):
            with self.assertRaises(RuntimeError):
                store.approve(waiting)
        self.assertEqual(store.snapshot()[0], revision)
        self.assertIsNone(templates.lookup("question", "再利用する依頼"))
        self.assertEqual(
            self.rows(
                "SELECT 1 FROM processed_shared_proposals WHERE proposal_id = %s",
                (waiting,),
            ),
            [],
        )
        # 別の接続（再起動後）で承認し直すと、変更・処理済み記録・自動登録が確定する。
        restarted = DbQuestionStore(SharedConfigDb(connect, reuse_enabled=True))
        restarted.approve(waiting)
        with self.assertRaises(ProposalNotFound):  # 二重承認は、変更を繰り返さない
            restarted.approve(waiting)
        self.assertEqual(restarted.snapshot()[0], revision + 1)
        # 保存するのは、サーバーが検証して反映した操作。採番したIDは保存しない。
        self.assertEqual(
            templates.lookup("question", "再利用する依頼"),
            {"operation": "add_question", "question": {"text": "架空の設問"}},
        )
        stored = self.rows(
            "SELECT applied, payload FROM processed_shared_proposals"
            " WHERE proposal_id = %s",
            (waiting,),
        )
        self.assertTrue(stored[0][0])
        self.assertNotIn("reuse_prompt", stored[0][1])
        # 同じ対象・文言は、今回反映した操作で置き換わり、1行のまま。
        revision, questions = restarted.snapshot()
        alternate = validate_model_output(
            {
                "operation": "add_question",
                "question": {"id": "A14", "text": "別の架空設問"},
            },
            questions,
            revision,
        )
        restarted.approve(restarted.register_for_reuse(alternate, "再利用する依頼"))
        self.assertEqual(
            templates.lookup("question", "再利用する依頼")["question"],
            {"text": "別の架空設問"},
        )
        self.assertEqual(
            self.rows(
                "SELECT count(*) FROM proposal_templates"
                " WHERE target = 'question' AND prompt = %s",
                ("再利用する依頼",),
            ),
            [(1,)],
        )

    def test_concurrent_approvals_of_the_same_prompt_leave_one_template_row(self):
        def connect():
            return _connect(self.schema)

        PostgresProposalTemplates(connect).bootstrap()
        store = DbQuestionStore(SharedConfigDb(connect, reuse_enabled=True))
        revision, questions = store.snapshot()
        identifiers = [
            store.register_for_reuse(
                validate_model_output(
                    {
                        "operation": "add_question",
                        "question": {"id": "A13", "text": text},
                    },
                    questions,
                    revision,
                ),
                "同時に出した依頼",
            )
            for text in ("架空の設問その1", "架空の設問その2")
        ]

        def approve(identifier):
            try:
                return DbQuestionStore(
                    SharedConfigDb(connect, reuse_enabled=True)
                ).approve(identifier)
            except Exception as error:  # noqa: BLE001  結果として比べる
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, identifiers))
        winners = [o for o in outcomes if not isinstance(o, Exception)]
        self.assertEqual(len(winners), 1, outcomes)  # 基準の版が同じなので、片方だけ
        rows = self.rows(
            "SELECT operation FROM proposal_templates"
            " WHERE target = 'question' AND prompt = %s",
            ("同時に出した依頼",),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0]["question"]["text"], winners[0][1][-1].text)

    def test_rule_reuse_restores_canonical_operation_from_approved_receipt(self):
        def connect():
            return _connect(self.schema)

        db = SharedConfigDb(connect, reuse_enabled=True)
        store = shared_db.DbRuleStore(db)
        revision, current = store.snapshot()
        question_revision, questions = DbQuestionStore(db).snapshot()
        proposal = validate_rule_output(
            {"operation": "remove_rule", "rule_id": "R03"},
            current,
            questions,
            revision,
            question_revision=question_revision,
        )
        identifier = store.register_for_reuse(proposal, "架空ルールを削除する")
        store.approve(identifier)
        templates = PostgresProposalTemplates(connect)
        raw = templates.lookup("rule", "架空ルールを削除する")
        self.assertEqual(raw, {"operation": "remove_rule", "rule_id": "R03"})
        self.assertIsNone(templates.lookup("question", "架空ルールを削除する"))
        revision, current = store.snapshot()
        with self.assertRaises(InvalidProposal):
            validate_rule_output(
                raw,
                current,
                questions,
                revision,
                question_revision=question_revision,
            )

    def _approve_rule(self, db, store, raw, prompt=None, reuse_prompt=None):
        """依頼の文面と生成の出力から、検証した提案を作り、承認する（DB）。"""
        revision, current = store.snapshot()
        question_revision, questions = DbQuestionStore(db).snapshot()
        proposal = validate_rule_output(
            raw,
            current,
            questions,
            revision,
            question_revision=question_revision,
            prompt=prompt,
        )
        if reuse_prompt is None:
            identifier = store.register(proposal)
        else:
            identifier = store.register_for_reuse(proposal, reuse_prompt)
        return proposal, identifier

    def test_approved_rule_update_is_visible_and_registered_judgments_keep_theirs(self):
        """承認後に別接続で同じIDの更新を読める。判定未登録の案件は更新後のルールを使い、
        登録済みの判定は当時の版（採用ルールの版・結果）のまま変わらない。"""

        def connect():
            return _connect(self.schema)

        db = SharedConfigDb(connect)
        store = DbRuleStore(db)
        # 準備: 初期のルールに、架空のR04（版1）を足して承認する。
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        # 準備: 更新前に、R04（版1）を採用して登録した案件と、未登録の案件。
        registered, _ = self.intake("456")
        self.assertEqual(
            self.judge(
                registered,
                {"result_code": "caution", "rule_id": "R04", "rule_revision": 1},
            )[0],
            204,
        )
        unregistered, _ = self.intake("456")
        registered_before = self.get(registered)
        unregistered_before = self.get(unregistered)
        rules_before = send("GET", f"{self.server.base_url}/api/rules")[1]
        r04_before = next(r for r in rules_before if r["rule_id"] == "R04")
        self.assertEqual(r04_before["conditions"], {"A10": "no"})

        # 更新（R04にA09=Yes）を承認する。
        update = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        proposal, identifier = self._approve_rule(
            db, store, update, prompt="R04にA09がYesを追加して"
        )
        self.assertEqual(
            [r.rule_id for r in proposal.after], ["R01", "R02", "R03", "R04"]
        )
        revision, rules = store.approve(identifier)

        # 別の接続（新しいDb・ストア）で、同じIDのまま更新を読める。R05は作られない。
        other = DbRuleStore(SharedConfigDb(connect))
        other_revision, other_rules = other.snapshot()
        self.assertEqual((other_revision, other_rules), (revision, rules))
        self.assertEqual([r.rule_id for r in other_rules], ["R01", "R02", "R03", "R04"])
        r04 = other_rules[-1]
        self.assertEqual(
            (r04.revision, r04.result_code, dict(r04.conditions)),
            (2, "caution", {"A09": "yes", "A10": "no"}),
        )
        self.assertEqual(other_rules[:3], store.snapshot()[1][:3])  # 他のルールは同じ
        # 旧い版の行は残り、内容も変わらない（登録済みの判定が参照できる）。
        stored = self.rows(
            "SELECT revision, result_code, conditions FROM decision_rules"
            " WHERE rule_id = 'R04' ORDER BY revision"
        )
        self.assertEqual(
            stored,
            [
                (1, "caution", {"A10": "no"}),
                (2, "caution", {"A09": "yes", "A10": "no"}),
            ],
        )

        # 判定未登録の案件は、最新（更新後）のルールで照合する（画面が読む一覧）。
        listed = send("GET", f"{self.server.base_url}/api/rules")[1]
        self.assertEqual(
            next(r for r in listed if r["rule_id"] == "R04"),
            {
                "rule_id": "R04",
                "revision": 2,
                "result_code": "caution",
                "conditions": {"A09": "yes", "A10": "no"},
            },
        )
        self.assertEqual(len(listed), len(rules_before))
        # 登録済みの判定は当時のまま。未登録の案件も、更新だけでは登録されない。
        self.assertEqual(self.get(registered), registered_before)
        self.assertEqual(
            self.get(registered)["result"]["adopted_rule"],
            {"rule_id": "R04", "rule_revision": 1},
        )
        self.assertEqual(self.get(unregistered), unregistered_before)
        self.assertIsNone(self.get(unregistered)["result"])
        # 未登録の案件は、更新後の版を採用して登録できる。
        self.assertEqual(
            self.judge(
                unregistered,
                {"result_code": "caution", "rule_id": "R04", "rule_revision": 2},
            )[0],
            204,
        )
        self.assertEqual(
            self.get(unregistered)["result"]["adopted_rule"],
            {"rule_id": "R04", "rule_revision": 2},
        )

    def test_a_stale_rule_update_is_refused_and_leaves_the_rows_unchanged(self):
        def connect():
            return _connect(self.schema)

        db = SharedConfigDb(connect)
        store = DbRuleStore(db)
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        prompt = "R04にA09がYesを追加して"
        first_op = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        second_op = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        _, first = self._approve_rule(db, store, first_op, prompt=prompt)
        _, second = self._approve_rule(db, store, second_op, prompt=prompt)
        store.approve(first)
        after_first = (store.snapshot(), self.rows("SELECT * FROM decision_rules"))
        # 同じ基準で作った2つ目は、対象の版が進んだため反映しない（部分反映もしない）。
        with self.assertRaises(ProposalConflict):
            store.approve(second)
        self.assertEqual(
            (store.snapshot(), self.rows("SELECT * FROM decision_rules")), after_first
        )
        with self.assertRaises(ProposalNotFound):  # 使い切った提案
            store.approve(second)

    RULE_ROWS_SQL = (
        "SELECT rule_id, revision, result_code, conditions FROM decision_rules"
        " ORDER BY rule_id, revision"
    )

    def _update_op(self, **additions):
        return {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": additions,
        }

    def _db_with_r04(self):
        db = SharedConfigDb(lambda: _connect(self.schema))
        store = DbRuleStore(db)
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        return db, store

    def test_sequential_rule_updates_keep_one_id_and_every_revision_row(self):
        """R04（版1）にA09を追加して承認し、最新の基準でA11を追加して承認する。
        同じIDのまま版3になり、別接続で読め、版1・2・3の行が内容のまま残り、
        最新の一覧と一致する。"""

        def connect():
            return _connect(self.schema)

        db, store = self._db_with_r04()
        rows_v1 = self.rows(self.RULE_ROWS_SQL)
        history_v1 = self._history()
        listed_v1 = send("GET", f"{self.server.base_url}/api/rules")[1]
        _, identifier = self._approve_rule(
            db, store, self._update_op(A09="yes"), prompt="R04にA09がYesを追加して"
        )
        revision_2, _ = store.approve(identifier)
        # 最新の基準（R04は版2）から、2つ目の更新案を作る。
        proposal, identifier = self._approve_rule(
            db, store, self._update_op(A11="yes"), prompt="R04にA11がYesを追加して"
        )
        self.assertEqual(proposal.target.revision, 2)
        self.assertEqual(dict(proposal.target.conditions), {"A09": "yes", "A10": "no"})
        revision_3, rules_v3 = store.approve(identifier)
        self.assertEqual(revision_3, revision_2 + 1)

        # 別の接続（新しいDb・ストア）で、同じIDのR04が版3で読める。R05は作られない。
        other_revision, other_rules = DbRuleStore(SharedConfigDb(connect)).snapshot()
        self.assertEqual((other_revision, other_rules), (revision_3, rules_v3))
        self.assertEqual([r.rule_id for r in other_rules], ["R01", "R02", "R03", "R04"])
        r04 = other_rules[-1]
        self.assertEqual(
            (r04.revision, r04.result_code, dict(r04.conditions)),
            (3, "caution", {"A09": "yes", "A10": "no", "A11": "yes"}),
        )
        # 版1・2・3の行が、それぞれの内容のまま残る（旧い版を書き換えない）。
        rows_v3 = self.rows(self.RULE_ROWS_SQL)
        self.assertEqual(len(rows_v3), len(rows_v1) + 2)
        self.assertEqual(
            [r for r in rows_v3 if r[0] != "R04"], [r for r in rows_v1 if r[0] != "R04"]
        )
        self.assertEqual(
            [r for r in rows_v3 if r[0] == "R04"],
            [
                ("R04", 1, "caution", {"A10": "no"}),
                ("R04", 2, "caution", {"A09": "yes", "A10": "no"}),
                ("R04", 3, "caution", {"A09": "yes", "A10": "no", "A11": "yes"}),
            ],
        )
        self.assertEqual(len(self._history()), len(history_v1) + 2)  # 承認ごとに1版
        # 最新の一覧（画面・照合が読む）は、版3と一致する。ルールの総数も同じ。
        listed = send("GET", f"{self.server.base_url}/api/rules")[1]
        self.assertEqual(
            [r["rule_id"] for r in listed], [r["rule_id"] for r in listed_v1]
        )  # R05は増えず、総数も同じ
        self.assertEqual(
            next(r for r in listed if r["rule_id"] == "R04"),
            {
                "rule_id": "R04",
                "revision": 3,
                "result_code": "caution",
                "conditions": {"A09": "yes", "A10": "no", "A11": "yes"},
            },
        )
        # 使い切った提案は、もう反映できない。
        with self.assertRaises(ProposalNotFound):
            store.approve(identifier)
        self.assertEqual(self.rows(self.RULE_ROWS_SQL), rows_v3)

    def test_updating_several_conditions_at_once_persists_one_new_row(self):
        def connect():
            return _connect(self.schema)

        db, store = self._db_with_r04()
        rows_before = self.rows(self.RULE_ROWS_SQL)
        history_before = self._history()
        _, identifier = self._approve_rule(
            db,
            store,
            self._update_op(A09="yes", A11="no"),
            prompt="R04にA09がYesとA11がNoを追加して",
        )
        revision, rules = store.approve(identifier)
        # 必要最小限: 増えた行はR04の版2の1行だけ（条件は2つ入る）。承認で進む版は1つ。
        rows_after = self.rows(self.RULE_ROWS_SQL)
        self.assertEqual(
            [r for r in rows_after if r not in rows_before],
            [("R04", 2, "caution", {"A09": "yes", "A10": "no", "A11": "no"})],
        )
        self.assertEqual(len(rows_after), len(rows_before) + 1)
        self.assertEqual(len(self._history()), len(history_before) + 1)
        # 別の接続で、同じID・同じ総数のまま、2つの条件を読める。
        other_revision, other_rules = DbRuleStore(SharedConfigDb(connect)).snapshot()
        self.assertEqual((other_revision, other_rules), (revision, rules))
        self.assertEqual([r.rule_id for r in other_rules], ["R01", "R02", "R03", "R04"])
        self.assertEqual(
            dict(other_rules[-1].conditions),
            {"A09": "yes", "A10": "no", "A11": "no"},
        )

    def test_partly_existing_conditions_over_http_persist_nothing(self):
        """複数の条件の一部だけ既存なら、新規の条件が先でも後でも、422で全体を拒否し、
        DBに提案も行も版も増やさない。"""
        server, proposer = self._admin_server()
        self._add_r04_over_http(server, proposer)
        rows_before = self.rows(self.RULE_ROWS_SQL)
        history_before = self._history()
        pending_before = self.rows("SELECT count(*) FROM shared_proposals")
        listed_before = send("GET", f"{server.base_url}/api/admin/rules")[1]
        cases = (
            ("R04にA09がYesとA10がNoを追加して", {"A09": "yes", "A10": "no"}),
            ("R04にA10がNoとA11がYesを追加して", {"A10": "no", "A11": "yes"}),
        )
        for prompt, additions in cases:
            proposer.output = self._update_op(**additions)
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/rule-proposals",
                {"prompt": prompt},
            )
            self.assertEqual(status, 422, prompt)
            self.assertIn("追加済み", body["detail"])
            self.assertNotIn("proposal_id", body)
            self.assertEqual(self.rows(self.RULE_ROWS_SQL), rows_before, prompt)
            self.assertEqual(self._history(), history_before, prompt)
            self.assertEqual(
                self.rows("SELECT count(*) FROM shared_proposals"), pending_before
            )
            self.assertEqual(
                send("GET", f"{server.base_url}/api/admin/rules")[1], listed_before
            )

    def _refuse_several_when_one_became_present(self, already):
        db, store = self._db_with_r04()
        _, identifier = self._approve_rule(
            db,
            store,
            self._update_op(A09="yes", A11="no"),
            prompt="R04にA09がYesとA11がNoを追加して",
        )
        drifted_rules = tuple(
            DemoRule(
                r.rule_id,
                r.revision,
                r.result_code,
                tuple(sorted({*r.conditions, (already, "yes")})),
            )
            if r.rule_id == "R04"
            else r
            for r in db.latest().rules
        )
        drifted = self._drift(db, drifted_rules)
        self._refused_update_changes_nothing(db, store, identifier, drifted)
        # 版2のR04の行は作られない（一方だけ足す部分反映もしない）。
        self.assertEqual(
            self.rows(
                "SELECT revision FROM decision_rules WHERE rule_id = 'R04'"
                " ORDER BY revision"
            ),
            [(1,)],
        )

    def test_several_conditions_update_is_refused_when_the_first_became_present(self):
        # 提案の後、版は同じまま、追加条件のうち先（A09）が既にあれば全体を拒否する。
        self._refuse_several_when_one_became_present("A09")

    def test_several_conditions_update_is_refused_when_the_last_became_present(self):
        # 後（A11）が既にあれば、先の条件だけを足す部分反映もせず拒否する。
        self._refuse_several_when_one_became_present("A11")

    def test_rule_update_receipt_becomes_a_reusable_update_operation(self):
        def connect():
            return _connect(self.schema)

        db = SharedConfigDb(connect, reuse_enabled=True)
        store = DbRuleStore(db)
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        prompt = "R04にA09がYesを追加して"
        update = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        _, identifier = self._approve_rule(
            db, store, update, prompt=prompt, reuse_prompt=prompt
        )
        store.approve(identifier)
        raw = PostgresProposalTemplates(connect).lookup("rule", prompt)
        self.assertEqual(raw, update)  # 対象のIDと追加した条件だけを保存する
        # 再利用のときも、現行のルールと依頼の文面で検証し直す（追加済みなので拒否）。
        revision, current = store.snapshot()
        question_revision, questions = DbQuestionStore(db).snapshot()
        with self.assertRaises(InvalidProposal) as caught:
            validate_rule_output(
                raw,
                current,
                questions,
                revision,
                question_revision=question_revision,
                prompt=prompt,
            )
        self.assertIn("追加済み", str(caught.exception))

    def _admin_server(self):
        """管理画面のAPI（判定ルール）を、DBの保存先で動かす。呼び出しごとに新しい接続・ストア。"""
        db = SharedConfigDb(lambda: _connect(self.schema))
        app = make_app(self.store, [])
        app.include_router(rule_admin.router)
        app.state.rule_store = DbRuleStore(db)
        app.state.question_store = DbQuestionStore(db)
        proposer = _StubRuleProposer()
        app.state.rule_proposer = proposer
        app.state.persistence = "database"
        server = _Server(app)
        server.__enter__()
        self.addCleanup(server.__exit__)
        return server, proposer

    def _add_r04_over_http(self, server, proposer):
        proposer.output = ADD_R04
        status, created = send(
            "POST",
            f"{server.base_url}/api/admin/rule-proposals",
            {"prompt": "設問A10がNoのとき注意とするルールを追加して"},
        )
        self.assertEqual((status, created["kind"]), (201, "add"), created)
        status, body = send(
            "POST",
            f"{server.base_url}/api/admin/rule-proposals/{created['proposal_id']}/decision",
            {"decision": "yes"},
        )
        self.assertEqual(status, 200, body)

    def test_rule_update_over_http_is_read_back_by_a_separate_server_under_the_same_id(
        self,
    ):
        """DB構成のHTTP経路（提案 → 決定 → 別のサーバー・接続で同じIDの更新を取得）。"""
        writer, proposer = self._admin_server()
        reader, _ = self._admin_server()  # 別のストア・接続（別のインスタンスの代わり）
        self._add_r04_over_http(writer, proposer)
        before = send("GET", f"{reader.base_url}/api/admin/rules")[1]
        self.assertEqual([r["rule_id"] for r in before["rules"]][-1], "R04")
        self.assertEqual(before["rules"][-1]["conditions"], {"A10": "no"})

        proposer.output = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        status, proposal = send(
            "POST",
            f"{writer.base_url}/api/admin/rule-proposals",
            {"prompt": "R04にA09がYesを追加して"},
        )
        self.assertEqual(status, 201, proposal)
        self.assertEqual(
            (proposal["kind"], proposal["target"]["rule_id"]), ("update", "R04")
        )
        self.assertEqual(proposal["added_conditions"], {"A09": "yes"})
        self.assertEqual(len(proposal["after"]), len(proposal["before"]))  # R05は出ない
        # 承認前は、別のサーバーから見ても変わらない。
        self.assertEqual(send("GET", f"{reader.base_url}/api/admin/rules")[1], before)

        proposals = f"{writer.base_url}/api/admin/rule-proposals"
        decision = f"{proposals}/{proposal['proposal_id']}/decision"
        status, decided = send("POST", decision, {"decision": "yes"})
        self.assertEqual((status, decided["applied"]), (200, True), decided)
        self.assertEqual(
            [r["rule_id"] for r in decided["rules"]], ["R01", "R02", "R03", "R04"]
        )
        # 別のサーバーから、同じIDのR04の更新（版2・追加した条件・同じ結果）を読める。
        read_back = send("GET", f"{reader.base_url}/api/admin/rules")[1]
        self.assertEqual(read_back["rules"], decided["rules"])
        self.assertEqual(
            read_back["rules"][-1],
            {
                "rule_id": "R04",
                "revision": 2,
                "result_code": "caution",
                "conditions": {"A09": "yes", "A10": "no"},
            },
        )
        self.assertEqual(read_back["rules"][:3], before["rules"][:3])
        listed = send("GET", f"{reader.base_url}/api/rules")[1]
        self.assertEqual(
            [r["rule_id"] for r in listed], ["R01", "R02", "R03", "R04"]
        )  # R05はない
        self.assertEqual(
            next(r for r in listed if r["rule_id"] == "R04")["conditions"],
            {"A09": "yes", "A10": "no"},
        )
        # 使い切った提案は、もう一度決められない。
        self.assertEqual(send("POST", decision, {"decision": "yes"})[0], 404)

    def test_unregistered_case_matches_the_updated_rule_registered_keeps_its_own(
        self,
    ):
        """判定未登録の案件の実際の回答を、GET /api/rules で取得した最新ルールと、既存の
        照合ロジック（app.rule_matching.match_rules）で照合する。更新で追加した条件により、
        同じ案件の照合の表示が更新前後で変わる。登録済みの判定は当時のまま。"""
        server, proposer = self._admin_server()
        self._add_r04_over_http(server, proposer)
        # 奇数のユーザーは、A09の自動回答がNo。更新で足す条件A09=Yesと食い違う。
        registered, _ = self.intake("457")
        self.assertEqual(
            self.judge(
                registered,
                {"result_code": "caution", "rule_id": "R04", "rule_revision": 1},
            )[0],
            204,
        )
        unregistered, answers = self.intake("457")
        self.assertEqual(answers, {"A09": "no"})
        registered_before = self.get(registered)

        def match_r04():
            answers_now = {
                q["question_code"]: q["answer"] or "unanswered"
                for q in self.get(unregistered)["questions"]
            }
            latest = send("GET", f"{server.base_url}/api/rules")[1]
            rules = tuple(
                Rule(
                    r["rule_id"],
                    RESULT_OF_CODE[r["result_code"]],
                    {q: Answer(a) for q, a in r["conditions"].items()},
                )
                for r in latest
            )
            return next(
                m for m in match_rules(answers_now, rules).rules if m.rule_id == "R04"
            )

        before = match_r04()  # R04 = A10=No。A09は条件にないので食い違わない
        self.assertEqual(before.display, RowDisplay.NORMAL)
        self.assertEqual(before.states["A09"], ConditionState.ANY)

        proposer.output = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        _, proposal = send(
            "POST",
            f"{server.base_url}/api/admin/rule-proposals",
            {"prompt": "R04にA09がYesを追加して"},
        )
        proposals = f"{server.base_url}/api/admin/rule-proposals"
        decision = f"{proposals}/{proposal['proposal_id']}/decision"
        self.assertEqual(send("POST", decision, {"decision": "yes"})[0], 200)

        after = match_r04()  # R04 = A09=Yes かつ A10=No。A09=No の回答と食い違う
        self.assertEqual(after.display, RowDisplay.GRAY)
        self.assertEqual(after.states["A09"], ConditionState.MISMATCH)
        self.assertEqual(after.mismatched_questions, ("A09",))
        # 登録済みの判定は、当時のまま（版1を採用）。
        # 未登録の案件は、更新だけでは登録されない。
        self.assertEqual(self.get(registered), registered_before)
        self.assertEqual(
            self.get(registered)["result"]["adopted_rule"],
            {"rule_id": "R04", "rule_revision": 1},
        )
        self.assertIsNone(self.get(unregistered)["result"])

    def test_pending_update_is_rebuilt_from_the_stored_payload(self):
        db = SharedConfigDb(lambda: _connect(self.schema))
        store = DbRuleStore(db)
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        update = {
            "operation": "update_rule",
            "rule_id": "R04",
            "add_conditions": {"A09": "yes"},
        }
        proposal, identifier = self._approve_rule(
            db, store, update, prompt="R04にA09がYesを追加して"
        )
        # 別のストア（別の接続）が、保存された内容から、
        # 検証済みの提案と同じ内容を作り直す。
        rebuilt = DbRuleStore(SharedConfigDb(lambda: _connect(self.schema))).pending(
            identifier
        )
        self.assertEqual(rebuilt, proposal)
        self.assertEqual(rebuilt.updated.revision, rebuilt.target.revision + 1)
        self.assertEqual(rebuilt.added_conditions, (("A09", "yes"),))
        # 読むだけでは、提案も承認済みのルールも変わらない。
        self.assertEqual(store.pending(identifier), proposal)
        self.assertEqual(store.snapshot()[1][-1].revision, 1)

    def _drift(self, db, rules):
        """ルール一覧の版（rule_revision）を進めずに、ルールの中身だけが違う新しい行を足す。

        通常の承認・復元は、ルールを変えるたびに rule_revision を進める。そのため、版の
        比較を通ったあとに「対象がない」「条件が追加済み」になる状態は、通常の操作では
        作れない。承認の防御分岐だけを確かめるため、行を直接足して作る。
        """
        latest = db.latest()
        drifted = SharedConfig(
            latest.config_revision + 1,
            latest.question_revision,
            latest.rule_revision,
            latest.questions,
            rules,
            latest.removed_rule_ids,
        )
        with _connect(self.schema) as conn:
            shared_db._insert_config(conn, drifted, "approval")
        return drifted

    def _update_proposal_on_r04(self):
        db = SharedConfigDb(lambda: _connect(self.schema))
        store = DbRuleStore(db)
        _, identifier = self._approve_rule(db, store, ADD_R04)
        store.approve(identifier)
        _, identifier = self._approve_rule(
            db,
            store,
            {
                "operation": "update_rule",
                "rule_id": "R04",
                "add_conditions": {"A09": "yes"},
            },
            prompt="R04にA09がYesを追加して",
        )
        return db, store, identifier

    def _refused_update_changes_nothing(self, db, store, identifier, drifted):
        rules_sql = "SELECT * FROM decision_rules ORDER BY 1, 2"
        rule_rows = self.rows(rules_sql)
        history = self._history()
        with self.assertRaises(ProposalConflict) as caught:
            store.approve(identifier)
        self.assertNotIsInstance(caught.exception, QuestionBaseChanged)
        self.assertEqual(db.latest(), drifted)  # 設定は、準備した状態のまま
        self.assertEqual(self.rows(rules_sql), rule_rows)
        self.assertEqual(self._history(), history)  # 版は増えない
        # 提案は使い切る（再試行で反映しない）。
        with self.assertRaises(ProposalNotFound):
            store.approve(identifier)

    def test_update_is_refused_when_the_target_is_gone_despite_matching_versions(self):
        db, store, identifier = self._update_proposal_on_r04()
        drifted = self._drift(
            db, tuple(r for r in db.latest().rules if r.rule_id != "R04")
        )
        # 版は一致しているので、版の比較では拒否されない。対象がないことで拒否する。
        self.assertEqual(drifted.rule_revision, db.latest().rule_revision)
        # 読むだけの確認（pending）も、対象がなければ拒否する。
        with self.assertRaises(ProposalConflict):
            store.pending(identifier)
        self._refused_update_changes_nothing(db, store, identifier, drifted)
        self.assertEqual(
            self.rows(
                "SELECT revision FROM decision_rules WHERE rule_id = 'R04'"
                " ORDER BY revision"
            ),
            [(1,)],
        )

    def test_update_is_refused_when_the_condition_is_already_there_despite_versions(
        self,
    ):
        db, store, identifier = self._update_proposal_on_r04()
        latest = db.latest()
        already = tuple(
            DemoRule(
                r.rule_id, r.revision, r.result_code, (("A09", "yes"), *r.conditions)
            )
            if r.rule_id == "R04"
            else r
            for r in latest.rules
        )
        drifted = self._drift(db, already)
        self._refused_update_changes_nothing(db, store, identifier, drifted)
        # 版2のR04の行は作られない（重複した条件を足さない）。
        self.assertEqual(
            self.rows(
                "SELECT revision FROM decision_rules WHERE rule_id = 'R04'"
                " ORDER BY revision"
            ),
            [(1,)],
        )

    def test_initial_templates_match_exactly_per_target_and_stay_deleted(self):
        def connect():
            return _connect(self.schema)

        templates = PostgresProposalTemplates(connect)
        templates.bootstrap()
        retire = {"operation": "retire_question", "question_id": "A13"}
        remove = {"operation": "remove_rule", "rule_id": "R03"}
        self.assertEqual(templates.lookup("question", "設問A13を廃止して"), retire)
        self.assertEqual(templates.lookup("rule", "ルールR03を削除して"), remove)
        # 保存層は完全一致だけ。対象違い・表記違い・空白違い・否定は一致しない。
        for target, prompt in (
            ("rule", "設問A13を廃止して"),
            ("question", "ルールR03を削除して"),
            ("question", " 設問A13を廃止して"),
            ("question", "設問A13を廃止しないで"),
            ("rule", "ルールr03を削除して"),
            ("rule", "ルールR03の条件を編集して"),
        ):
            self.assertIsNone(templates.lookup(target, prompt), (target, prompt))
        self.assertEqual(templates.list_prompts("rule"), ["ルールR03を削除して"])
        # 対象と文言の組は一意。同じ文言でも対象が違えば別の行にできる。
        insert = (
            "INSERT INTO proposal_templates (target, prompt, operation)"
            " VALUES (%s, %s, %s::jsonb)"
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with connect() as conn:
                conn.execute(insert, ("rule", "ルールR03を削除して", "{}"))
        with connect() as conn:
            conn.execute(insert, ("question", "ルールR03を削除して", "{}"))
        # 削除した初期定型文は、起動（bootstrap）を繰り返しても復活しない。
        templates.remove("rule", "ルールR03を削除して")
        PostgresProposalTemplates(connect).bootstrap()
        self.assertIsNone(templates.lookup("rule", "ルールR03を削除して"))
        self.assertEqual(templates.lookup("question", "設問A13を廃止して"), retire)

    def test_double_approval_of_one_proposal_changes_the_config_once(self):
        db = SharedConfigDb(lambda: _connect(self.schema), reuse_enabled=True)
        questions = DbQuestionStore(db)
        identifier = self._add_question(questions, "架空の設問その1")

        def approve(_):
            try:
                return questions.approve(identifier)
            except Exception as error:  # noqa: BLE001  結果として比べる
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, range(2)))
        failures = [o for o in outcomes if isinstance(o, Exception)]
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIsInstance(failures[0], ProposalNotFound | ProposalConflict)
        self.assertEqual(self._history(), [(1, "seed"), (2, "approval")])
        processed = self.rows(
            "SELECT applied FROM processed_shared_proposals WHERE proposal_id = %s",
            (identifier,),
        )
        self.assertEqual(processed, [(True,)])

    def test_failed_rule_approval_rolls_back_the_change_and_the_processed_record(
        self,
    ):
        db = SharedConfigDb(lambda: _connect(self.schema), reuse_enabled=True)
        questions, rules_store = DbQuestionStore(db), DbRuleStore(db)
        identifier = self._add_rule(db, rules_store, questions)
        before = self._history()
        # 自動登録の失敗は、直前に確定しかけた版と処理済み記録も取り消す。
        with mock.patch.object(
            shared_db, "save_template", side_effect=RuntimeError("検証用")
        ):
            with self.assertRaises(RuntimeError):
                rules_store.approve(identifier)
        self.assertEqual(self.rows("SELECT count(*) FROM proposal_templates"), [(0,)])
        self.assertEqual(self._history(), before)
        self.assertEqual(
            self.rows(
                "SELECT 1 FROM processed_shared_proposals WHERE proposal_id = %s",
                (identifier,),
            ),
            [],
        )
        # 提案は未処理のまま残り、やり直せば、変更と記録が1回だけ確定する。
        rules_store.approve(identifier)
        self.assertEqual(self._history()[-1][1], "approval")
        self.assertEqual(len(self._history()), len(before) + 1)
        self.assertEqual(
            self.rows("SELECT target, prompt FROM proposal_templates"),
            [("rule", "ルール追加の依頼")],
        )

    def test_no_and_blocked_approval_mark_unapplied_and_keep_version_and_templates(
        self,
    ):
        db = SharedConfigDb(lambda: _connect(self.schema), reuse_enabled=True)
        questions = DbQuestionStore(db)

        def register(prompt, output):
            revision, current = questions.snapshot()
            proposal = validate_model_output(output, current, revision)
            return questions.register_for_reuse(proposal, prompt)

        def state():
            return (
                self._history(),
                self.rows("SELECT target, prompt FROM proposal_templates"),
            )

        def add(identifier, text):
            return {
                "operation": "add_question",
                "question": {"id": identifier, "text": text},
            }

        declined = register("Noの依頼", add("A13", "架空の設問その1"))
        stale = register("遅れた依頼", add("A13", "架空の設問その2"))
        winner = register("先の依頼", add("A13", "架空の設問その3"))
        before = state()

        # No: 処理済みに「反映なし」で残り、版も定型文も変わらない。
        questions.reject(declined)
        self.assertEqual(state(), before)
        flags = dict(
            self.rows("SELECT proposal_id, applied FROM processed_shared_proposals")
        )
        self.assertEqual(flags, {declined: False})
        with self.assertRaises(ProposalNotFound):  # Noのあとの承認は、反映しない
            questions.approve(declined)
        self.assertEqual(state(), before)

        # 反映して版が進んだあと、基準の古い提案は拒否される。
        questions.approve(winner)
        after_winner = state()
        self.assertEqual(len(after_winner[0]), len(before[0]) + 1)
        with self.assertRaises(ProposalConflict):
            questions.approve(stale)
        self.assertEqual(state(), after_winner)  # 版も定型文も増えない
        flags = dict(
            self.rows("SELECT proposal_id, applied FROM processed_shared_proposals")
        )
        self.assertEqual(flags, {declined: False, winner: True, stale: False})
        self.assertEqual(
            self.rows("SELECT prompt FROM proposal_templates"), [("先の依頼",)]
        )

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
        return rules_store.register_for_reuse(proposal, "ルール追加の依頼")

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
