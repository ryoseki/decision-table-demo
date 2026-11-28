"""判定ルールの追加案・削除案（管理者AIの試作）のAPI契約の検証（モック・DBなし）。

見るのはHTTPの入力と出力（ステータスと本文）と、拒否・承認前・Noで現行データが変わらないこと、
基準データの競合を部分反映なしで拒否すること。純粋な検証は、関数のテストで確認する。
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
値はすべて架空（R01〜R03はデモの初期ルール。追加案は、架空のR04）。
"""

import threading
import unittest
from typing import Any

from fastapi import FastAPI

from app import cases, question_admin, rule_admin, rules
from app.domain import InvalidProposal, ProposerUnavailable
from app.question_proposer import MockQuestionProposer
from app.question_store import InMemoryQuestionStore
from app.question_validation import DemoQuestion
from app.rule_proposer import MockRuleProposer
from app.rule_store import InMemoryRuleStore
from app.rule_validation import (
    DemoRule,
    ValidatedRuleAdd,
    ValidatedRuleRemoval,
    next_rule_id,
    validate_rule_output,
)
from tests.test_intake import _Server, send
from tests.test_question_retirement import RetirementStore

ADD_PROMPT = "設問A09がYes、設問A10がNoのとき注意とするルールを追加して"
UNKNOWN_QUESTION_PROMPT = "設問A99がYesのとき注意とするルールを追加して"
REMOVE_PROMPT = "Remove rule R03"
INITIAL_IDS = ["R01", "R02", "R03"]


def question(id_: str, retired: bool = False) -> DemoQuestion:
    return DemoQuestion(id_, f"設問{id_}？", retired)


QUESTIONS = tuple(question(f"A{n:02d}") for n in range(1, 13))


def rule(rule_id: str, result: str, **conditions: str) -> DemoRule:
    return DemoRule(rule_id, 1, result, tuple(sorted(conditions.items())))


def add_output(
    conditions: dict | None = None,
    id_: str = "R04",
    result: Any = "caution",
) -> dict:
    return {
        "operation": "add_rule",
        "rule": {
            "id": id_,
            "result_code": result,
            "conditions": {"A09": "yes"} if conditions is None else conditions,
        },
    }


class TestValidation(unittest.TestCase):
    CURRENT = (
        rule("R01", "caution", A01="yes", A02="no"),
        rule("R02", "no_issue", A03="yes"),
    )

    def validate(self, raw: Any, current=None, questions=QUESTIONS, used=frozenset()):
        return validate_rule_output(
            raw,
            self.CURRENT if current is None else current,
            questions,
            3,
            used,
            question_revision=7,
        )

    def rejected(self, raw: Any, message: str, **kwargs: Any) -> None:
        with self.assertRaises(InvalidProposal) as caught:
            self.validate(raw, **kwargs)
        self.assertIn(message, str(caught.exception))

    def test_valid_add_returns_proposal_with_base_and_normalized_conditions(
        self,
    ) -> None:
        proposal = self.validate(add_output({"A10": "no", "A09": "yes"}, id_="R03"))
        self.assertIsInstance(proposal, ValidatedRuleAdd)
        self.assertEqual(proposal.base, self.CURRENT)
        self.assertEqual(proposal.base_revision, 3)
        self.assertEqual(proposal.base_question_revision, 7)
        self.assertEqual(proposal.added, rule("R03", "caution", A09="yes", A10="no"))
        self.assertEqual(proposal.after, (*self.CURRENT, proposal.added))

    def test_rejects_a_condition_on_a_question_that_does_not_exist(self) -> None:
        self.rejected(
            add_output({"A99": "yes"}, id_="R03"), "Question A99 does not exist"
        )

    def test_rejects_a_condition_on_a_retired_question(self) -> None:
        questions = (*QUESTIONS[:8], question("A09", retired=True), *QUESTIONS[9:])
        self.rejected(
            add_output({"A09": "yes"}, id_="R03"),
            "Question A09 is deactivated",
            questions=questions,
        )

    def test_reports_every_missing_question(self) -> None:
        self.rejected(
            add_output({"A98": "yes", "A99": "no"}, id_="R03"),
            "Question A98 does not exist",
        )

    def test_rejects_an_unknown_result_code(self) -> None:
        self.rejected(
            add_output(result="ban_forever", id_="R03"), "The result code is invalid"
        )
        self.rejected(add_output(result=None, id_="R03"), "The result code is invalid")
        # 表示名ではなく、コードで受け取る。
        self.rejected(
            add_output(result="注意", id_="R03"), "The result code is invalid"
        )

    def test_rejects_malformed_conditions(self) -> None:
        self.rejected(add_output({}, id_="R03"), "conditions are empty")
        self.rejected(add_output({"A09": "maybe"}, id_="R03"), "must be yes or no")
        self.rejected(add_output({"A09": True}, id_="R03"), "must be yes or no")
        self.rejected(add_output({"9": "yes"}, id_="R03"), "question ID format")

    def test_rejects_malformed_output(self) -> None:
        self.rejected("add", "format is invalid")
        self.rejected({"operation": "add_rule"}, "format is invalid")
        self.rejected({"operation": "none", "rule": {}}, "operation is invalid")
        self.rejected(
            {"operation": "add_rule", "rule": {"id": "R03"}}, "invalid format"
        )
        self.rejected(add_output(id_="r3"), "The rule ID format")

    def test_rejects_the_same_id(self) -> None:
        self.rejected(
            add_output({"A09": "yes"}, id_="R02"), "Rule ID R02 already exists"
        )

    def test_rejects_the_same_conditions_and_result_in_any_order(self) -> None:
        self.rejected(
            add_output({"A02": "no", "A01": "yes"}, id_="R03", result="caution"),
            "Rule R01 with the same conditions and result already exists (duplicate)",
        )

    def test_same_conditions_with_a_different_result_is_not_a_duplicate(self) -> None:
        # 優先順位や禁止は決めない（デモの方針）。
        proposal = self.validate(
            add_output({"A02": "no", "A01": "yes"}, id_="R03", result="no_issue")
        )
        self.assertEqual(proposal.added.result_code, "no_issue")

    def test_a_subset_or_different_answer_is_not_a_duplicate(self) -> None:
        self.validate(add_output({"A01": "yes"}, id_="R03"))
        self.validate(add_output({"A01": "no", "A02": "no"}, id_="R03"))

    def test_only_the_next_id_is_allowed_and_removed_ids_are_not_reused(self) -> None:
        self.rejected(
            add_output(id_="R09"), "The only rule ID that can be added is R03"
        )
        self.rejected(
            add_output({"A09": "yes"}, id_="R03"),
            "The only rule ID that can be added is R04",
            used=frozenset({"R03"}),
        )
        self.assertEqual(next_rule_id(self.CURRENT, frozenset({"R05"})), "R06")

    def test_valid_removal_returns_the_target_and_the_list_without_it(self) -> None:
        proposal = self.validate({"operation": "remove_rule", "rule_id": "R01"})
        self.assertIsInstance(proposal, ValidatedRuleRemoval)
        self.assertEqual(proposal.target.rule_id, "R01")
        self.assertEqual(proposal.base_question_revision, 7)
        self.assertEqual([r.rule_id for r in proposal.after], ["R02"])

    def test_rejects_removing_a_rule_that_does_not_exist(self) -> None:
        self.rejected(
            {"operation": "remove_rule", "rule_id": "R09"}, "Rule ID R09 does not exist"
        )
        self.rejected({"operation": "remove_rule", "rule_id": 9}, "The rule ID format")
        self.rejected({"operation": "remove_rule"}, "format is invalid")


class TestIdBeyondTwoDigits(unittest.TestCase):
    """IDがR99を超えても、追加も削除もできる（採番の上限を設けない）。"""

    CURRENT = (rule("R99", "caution", A01="yes"),)

    def validate(self, raw: Any, current=None) -> Any:
        return validate_rule_output(
            raw,
            self.CURRENT if current is None else current,
            QUESTIONS,
            3,
            question_revision=7,
        )

    def test_next_id_after_r99_is_r100_and_can_be_added(self) -> None:
        self.assertEqual(next_rule_id(self.CURRENT, frozenset()), "R100")
        proposal = self.validate(add_output({"A09": "yes"}, id_="R100"))
        self.assertEqual(proposal.added.rule_id, "R100")
        self.assertEqual(next_rule_id(proposal.after, frozenset()), "R101")

    def test_a_three_digit_rule_can_be_removed(self) -> None:
        current = (*self.CURRENT, rule("R100", "no_issue", A02="no"))
        proposal = self.validate(
            {"operation": "remove_rule", "rule_id": "R100"}, current=current
        )
        self.assertEqual([r.rule_id for r in proposal.after], ["R99"])

    def test_a_malformed_id_is_still_rejected(self) -> None:
        for bad in ("R1", "R1x0", "r100"):
            with self.assertRaises(InvalidProposal, msg=bad) as caught:
                self.validate(add_output({"A09": "yes"}, id_=bad))
            self.assertIn("The rule ID format", str(caught.exception))


class FakeRuleProposer:
    """生の出力、または例外を返すテスト用のAI。"""

    def __init__(self, output: Any = None, error: Exception | None = None) -> None:
        self.output, self.error = output, error

    def generate(self, prompt: str, current: Any, next_id: str, questions: Any) -> Any:
        if self.error is not None:
            raise self.error
        return self.output


def make_app(
    proposer: Any = None,
    rule_store: InMemoryRuleStore | None = None,
    question_store: InMemoryQuestionStore | None = None,
    case_store: Any = None,
    admin_lock: Any = None,
) -> FastAPI:
    application = FastAPI()
    if admin_lock is not None:
        application.state.admin_lock = admin_lock
    application.state.rule_proposer = proposer or MockRuleProposer()
    application.state.rule_store = rule_store or InMemoryRuleStore()
    application.state.question_store = question_store or InMemoryQuestionStore()
    application.state.question_proposer = MockQuestionProposer()
    if case_store is not None:
        application.state.case_store = case_store
    application.include_router(rule_admin.router)
    application.include_router(question_admin.router)
    application.include_router(cases.router)
    application.include_router(rules.router)
    return application


class RuleAdminTestCase(unittest.TestCase):
    proposer: Any = None

    def setUp(self) -> None:
        self.rule_store = InMemoryRuleStore()
        self.question_store = InMemoryQuestionStore()
        self.case_store = RetirementStore()
        self.case_store.rule_rows = [
            {
                "rule_id": "R01",
                "revision": 1,
                "result_code": "caution",
                "conditions": {"A01": "yes"},
            }
        ]
        server = _Server(
            make_app(
                self.proposer,
                self.rule_store,
                self.question_store,
                self.case_store,
            )
        )
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.base = server.base_url

    def get_list(self) -> dict:
        status, body = send("GET", f"{self.base}/api/admin/rules")
        self.assertEqual(status, 200)
        return body

    def propose(self, prompt: str = ADD_PROMPT) -> tuple[int, dict]:
        return send("POST", f"{self.base}/api/admin/rule-proposals", {"prompt": prompt})

    def decide(self, proposal_id: str, decision: Any) -> tuple[int, dict]:
        return send(
            "POST",
            f"{self.base}/api/admin/rule-proposals/{proposal_id}/decision",
            {"decision": decision},
        )

    def ids(self, body: dict) -> list[str]:
        return [r["rule_id"] for r in body["rules"]]

    def question_decision(self, prompt: str, decision: str = "yes") -> dict:
        """設問の追加案・廃止案を作って決める。作った提案の本文を返す。"""
        status, proposal = send(
            "POST", f"{self.base}/api/admin/question-proposals", {"prompt": prompt}
        )
        self.assertEqual(status, 201)
        send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal['proposal_id']}/decision",
            {"decision": decision},
        )
        return proposal

    def add_a13(self) -> None:
        self.question_decision("APIキーの失効履歴を確認する設問を追加して")


class TestList(RuleAdminTestCase):
    def test_returns_the_initial_demo_rules_marked_as_mock_in_memory(self) -> None:
        body = self.get_list()
        self.assertEqual(self.ids(body), INITIAL_IDS)
        first = body["rules"][0]
        self.assertEqual(first["result_code"], "caution")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["conditions"]["A06"], "no")
        self.assertEqual(len(first["conditions"]), 12)
        self.assertIs(body["mock"], True)
        self.assertEqual(body["persistence"], "memory")
        self.assertEqual(body["revision"], 1)


class TestAddProposal(RuleAdminTestCase):
    def test_returns_before_after_and_added_without_changing_the_list(self) -> None:
        before = self.get_list()
        status, body = self.propose()
        self.assertEqual(status, 201)
        self.assertEqual(body["kind"], "add")
        self.assertEqual([r["rule_id"] for r in body["before"]], INITIAL_IDS)
        self.assertEqual([r["rule_id"] for r in body["after"]], [*INITIAL_IDS, "R04"])
        self.assertEqual(
            body["added"],
            {
                "rule_id": "R04",
                "revision": 1,
                "result_code": "caution",
                "conditions": {"A09": "yes", "A10": "no"},
            },
        )
        self.assertEqual(body["base_revision"], 1)
        self.assertTrue(body["proposal_id"])
        self.assertEqual(self.get_list(), before)  # 承認前は現行データを変えない

    def test_rejects_a_rule_that_refers_to_a_question_that_does_not_exist(
        self,
    ) -> None:
        before = self.get_list()
        status, body = self.propose(UNKNOWN_QUESTION_PROMPT)
        self.assertEqual(status, 422)
        self.assertIn("Question A99 does not exist", body["detail"])
        self.assertEqual(self.get_list(), before)

    def test_rejects_a_rule_that_refers_to_a_retired_question(self) -> None:
        # どのルールも参照しない設問（追加したA13）を、廃止する。
        self.add_a13()
        self.question_decision("Deactivate question A13")
        before = self.get_list()
        status, body = self.propose("設問A13がYesのとき注意とするルールを追加して")
        self.assertEqual(status, 422)
        self.assertIn("Question A13 is deactivated", body["detail"])
        self.assertEqual(self.get_list(), before)

    def test_rejects_a_duplicate_of_an_existing_rule_in_any_condition_order(
        self,
    ) -> None:
        # 既存のR01と同じ条件・結果（12問すべて）を、逆順で出す。
        conditions = dict(
            reversed(list(self.get_list()["rules"][0]["conditions"].items()))
        )
        server = _Server(
            make_app(
                FakeRuleProposer(add_output(conditions, id_="R04", result="caution")),
                self.rule_store,
                self.question_store,
            )
        )
        with server:
            before = send("GET", f"{server.base_url}/api/admin/rules")[1]
            status, body = send(
                "POST", f"{server.base_url}/api/admin/rule-proposals", {"prompt": "x"}
            )
            self.assertEqual(status, 422)
            self.assertIn(
                "Rule R01 with the same conditions and result already exists "
                "(duplicate)",
                body["detail"],
            )
            self.assertEqual(
                send("GET", f"{server.base_url}/api/admin/rules")[1], before
            )

    def test_rejects_an_empty_or_unsupported_request(self) -> None:
        before = self.get_list()
        for prompt in ("天気を教えて", "ルールを追加して", "設問A09がYesのルール"):
            status, _ = self.propose(prompt)
            self.assertEqual(status, 422, prompt)
        status, _ = send(
            "POST", f"{self.base}/api/admin/rule-proposals", {"prompt": "  "}
        )
        self.assertEqual(status, 422)
        self.assertEqual(self.get_list(), before)


class TestAddDecision(RuleAdminTestCase):
    def test_yes_applies_only_the_validated_proposal(self) -> None:
        _, proposal = self.propose()
        status, body = send(
            "POST",
            f"{self.base}/api/admin/rule-proposals/{proposal['proposal_id']}/decision",
            # 承認の要求に追加内容を含めても、無視する。
            {"decision": "yes", "rule": {"id": "R04", "conditions": {"A01": "no"}}},
        )
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], True)
        self.assertEqual(self.ids(body), [*INITIAL_IDS, "R04"])
        self.assertEqual(body["revision"], 2)
        self.assertEqual(body["rules"][-1]["conditions"], {"A09": "yes", "A10": "no"})
        self.assertEqual(
            self.get_list(), {k: v for k, v in body.items() if k != "applied"}
        )

    def test_no_keeps_the_current_rules(self) -> None:
        before = self.get_list()
        _, proposal = self.propose()
        status, body = self.decide(proposal["proposal_id"], "no")
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], False)
        self.assertEqual(self.get_list(), before)

    def test_rejects_an_invalid_decision_and_keeps_the_proposal(self) -> None:
        before = self.get_list()
        _, proposal = self.propose()
        self.assertEqual(self.decide(proposal["proposal_id"], "maybe")[0], 422)
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)

    def test_a_proposal_is_used_once_and_unknown_ids_are_404(self) -> None:
        _, proposal = self.propose()
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)
        after = self.get_list()
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.decide(proposal["proposal_id"], "no")[0], 404)
        self.assertEqual(self.decide("unknown", "yes")[0], 404)
        self.assertEqual(self.get_list(), after)

    def test_a_rejected_proposal_cannot_be_approved_later(self) -> None:
        _, proposal = self.propose()
        self.decide(proposal["proposal_id"], "no")
        before = self.get_list()
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.get_list(), before)

    def test_refuses_a_conflict_when_the_base_data_changed_without_partial_apply(
        self,
    ) -> None:
        _, first = self.propose()
        _, second = self.propose(REMOVE_PROMPT)  # 同じ基準の版（1）で作った別の案
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 200)
        after_first = self.get_list()
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("changed", body["detail"])
        self.assertEqual(self.get_list(), after_first)  # R03は消えていない
        self.assertEqual(self.decide(second["proposal_id"], "yes")[0], 404)

    def test_two_adds_from_the_same_base_apply_only_the_first_yes(self) -> None:
        _, first = self.propose()
        _, second = self.propose()  # 同じ基準の版（1）・同じ内容で作った2つ目の追加案
        self.assertEqual(first["added"]["rule_id"], second["added"]["rule_id"])
        status, applied = self.decide(first["proposal_id"], "yes")
        self.assertEqual(status, 200)
        self.assertEqual(self.ids(applied), [*INITIAL_IDS, "R04"])
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("changed", body["detail"])
        after = self.get_list()
        self.assertEqual(self.ids(after), [*INITIAL_IDS, "R04"])  # R04は1件だけ
        self.assertEqual(after["revision"], 2)
        self.assertEqual(self.decide(second["proposal_id"], "yes")[0], 404)

    def test_refuses_when_a_question_was_retired_after_the_proposal(self) -> None:
        self.add_a13()
        _, proposal = self.propose("設問A13がYesのとき問題なしとするルールを追加して")
        self.question_decision(
            "Deactivate question A13"
        )  # ルールはまだ反映前なので、廃止できる
        before = self.get_list()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("The question list changed after the proposal", body["detail"])
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)

    def test_refuses_an_add_when_any_question_change_followed_the_proposal(
        self,
    ) -> None:
        # 設問版の変更を、無関係とは見なさない（追加された別の設問でも拒否する）。
        _, proposal = self.propose()  # A09・A10だけを参照する
        self.add_a13()
        before = self.get_list()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("The question list changed after the proposal", body["detail"])
        self.assertEqual(self.get_list(), before)  # 部分反映しない
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)

    def test_refuses_a_removal_when_a_question_changed_after_the_proposal(
        self,
    ) -> None:
        _, proposal = self.propose(REMOVE_PROMPT)
        self.add_a13()
        before = self.get_list()
        status, _ = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertEqual(self.get_list(), before)  # R03は消えていない
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)

    def test_no_still_discards_a_proposal_whose_question_base_changed(self) -> None:
        _, proposal = self.propose()
        self.add_a13()
        before = self.get_list()
        status, body = self.decide(proposal["proposal_id"], "no")
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], False)
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)


class TestRemoval(RuleAdminTestCase):
    def test_proposal_shows_the_rule_to_remove_and_changes_nothing(self) -> None:
        before = self.get_list()
        status, body = self.propose(REMOVE_PROMPT)
        self.assertEqual(status, 201)
        self.assertEqual(body["kind"], "remove")
        self.assertEqual(body["removed"]["rule_id"], "R03")
        self.assertEqual([r["rule_id"] for r in body["before"]], INITIAL_IDS)
        self.assertEqual([r["rule_id"] for r in body["after"]], ["R01", "R02"])
        self.assertEqual(self.get_list(), before)

    def test_yes_removes_and_no_keeps(self) -> None:
        _, proposal = self.propose(REMOVE_PROMPT)
        self.assertEqual(
            self.decide(proposal["proposal_id"], "no")[1]["applied"], False
        )
        self.assertEqual(self.ids(self.get_list()), INITIAL_IDS)
        _, proposal = self.propose(REMOVE_PROMPT)
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 200)
        self.assertEqual(self.ids(body), ["R01", "R02"])
        self.assertEqual(self.ids(self.get_list()), ["R01", "R02"])

    def test_rejects_removing_a_rule_that_does_not_exist_or_was_removed(self) -> None:
        self.assertEqual(self.propose("ルールR09を削除して")[0], 422)
        _, proposal = self.propose(REMOVE_PROMPT)
        self.decide(proposal["proposal_id"], "yes")
        before = self.get_list()
        status, body = self.propose(REMOVE_PROMPT)
        self.assertEqual(status, 422)
        self.assertIn("Rule ID R03 does not exist", body["detail"])
        self.assertEqual(self.get_list(), before)

    def test_a_removed_id_is_not_reused_by_the_next_added_rule(self) -> None:
        _, proposal = self.propose(REMOVE_PROMPT)
        self.decide(proposal["proposal_id"], "yes")
        _, added = self.propose()
        self.assertEqual(added["added"]["rule_id"], "R04")  # R03は再利用しない


class TestUnavailable(RuleAdminTestCase):
    proposer = FakeRuleProposer(error=ProposerUnavailable("down"))

    def test_returns_502_and_changes_nothing(self) -> None:
        before = self.get_list()
        status, _ = self.propose()
        self.assertEqual(status, 502)
        self.assertEqual(self.get_list(), before)


class TestIsolationAndQuestionIntegrity(RuleAdminTestCase):
    def test_does_not_touch_the_review_screen_rules_or_cases(self) -> None:
        status, rules_before = send("GET", f"{self.base}/api/rules")
        self.assertEqual(status, 200)
        _, proposal = self.propose()
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)
        _, removal = self.propose(REMOVE_PROMPT)
        self.assertEqual(self.decide(removal["proposal_id"], "yes")[0], 200)
        self.assertEqual(send("GET", f"{self.base}/api/rules")[1], rules_before)
        self.assertEqual(self.case_store.save_calls, [])

    def test_works_without_a_case_store(self) -> None:
        server = _Server(make_app())
        with server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/rule-proposals",
                {"prompt": ADD_PROMPT},
            )
            self.assertEqual(status, 201)
            self.assertEqual(body["added"]["rule_id"], "R04")

    def test_a_question_that_an_added_rule_refers_to_cannot_be_retired(self) -> None:
        # A13（どの既存ルールも参照しない）を足し、ルールR04で参照させる。
        self.add_a13()
        _, rule_proposal = self.propose("設問A13がYesのとき注意とするルールを追加して")
        self.assertEqual(self.decide(rule_proposal["proposal_id"], "yes")[0], 200)

        status, retire = send(
            "POST",
            f"{self.base}/api/admin/question-proposals",
            {"prompt": "Deactivate question A13"},
        )
        self.assertEqual(status, 201)
        self.assertEqual([r["rule_id"] for r in retire["references"]], ["R04"])
        self.assertIs(retire["can_retire"], False)
        status, body = send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{retire['proposal_id']}/decision",
            {"decision": "yes"},
        )
        self.assertEqual(status, 409)
        self.assertIn("R04", body["detail"])
        _, questions = send("GET", f"{self.base}/api/admin/questions")
        a13 = next(q for q in questions["questions"] if q["id"] == "A13")
        self.assertNotIn("retired", a13)

    def test_a_rule_added_after_the_retirement_proposal_blocks_yes(self) -> None:
        self.add_a13()
        _, retire = send(
            "POST",
            f"{self.base}/api/admin/question-proposals",
            {"prompt": "Deactivate question A13"},
        )
        self.assertIs(retire["can_retire"], True)
        _, rule_proposal = self.propose("設問A13がYesのとき注意とするルールを追加して")
        self.decide(rule_proposal["proposal_id"], "yes")
        status, _ = send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{retire['proposal_id']}/decision",
            {"decision": "yes"},
        )
        self.assertEqual(status, 409)

    def test_removing_a_demo_rule_keeps_the_case_store_reference(self) -> None:
        # 案件の保存先のR01（A01を参照）は、デモ側の削除後も残る（保守的に数える）。
        _, removal = self.propose("ルールR01を削除して")
        self.decide(removal["proposal_id"], "yes")
        _, retire = send(
            "POST",
            f"{self.base}/api/admin/question-proposals",
            {"prompt": "設問A01を廃止して"},
        )
        self.assertIn("R01", [r["rule_id"] for r in retire["references"]])
        self.assertIs(retire["can_retire"], False)


class TestRetirementSeesBothRuleSources(RuleAdminTestCase):
    """案件側とデモ側に、同じID・版で条件の違うルールがあっても、両方を参照に数える。"""

    def retire_proposal(self, question_id: str) -> dict:
        status, body = send(
            "POST",
            f"{self.base}/api/admin/question-proposals",
            {"prompt": f"設問{question_id}を廃止して"},
        )
        self.assertEqual(status, 201)
        return body

    def keep_only_demo_r01(self) -> None:
        # 他のデモのルールがA06を参照して、結果が隠れないよう、R01だけにする。
        self.rule_store._rules = tuple(
            r for r in self.rule_store.snapshot()[1] if r.rule_id == "R01"
        )

    def test_a_demo_only_condition_blocks_retirement_despite_the_same_id(self) -> None:
        # 案件側R01はA01だけ。デモ側R01はA06も参照する（同じR01・版1）。
        self.keep_only_demo_r01()
        proposal = self.retire_proposal("A06")
        self.assertEqual([r["rule_id"] for r in proposal["references"]], ["R01"])
        self.assertIs(proposal["can_retire"], False)
        questions_before = send("GET", f"{self.base}/api/admin/questions")[1]
        status, body = send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal['proposal_id']}/decision",
            {"decision": "yes"},
        )
        self.assertEqual(status, 409)
        self.assertIn("R01", body["detail"])
        self.assertEqual(
            send("GET", f"{self.base}/api/admin/questions")[1], questions_before
        )

    def test_a_case_side_reference_still_blocks_retirement(self) -> None:
        proposal = self.retire_proposal("A01")
        self.assertIn("R01", [r["rule_id"] for r in proposal["references"]])
        self.assertIs(proposal["can_retire"], False)

    def test_an_identical_row_in_both_sources_is_counted_once(self) -> None:
        self.keep_only_demo_r01()
        self.case_store.rule_rows = self.rule_store.list_rows()
        proposal = self.retire_proposal("A06")
        self.assertEqual([r["rule_id"] for r in proposal["references"]], ["R01"])


class TestRequestWording(RuleAdminTestCase):
    """結果の名前（アカウント削除）と、削除の操作を取り違えない。"""

    def test_every_defined_result_can_be_added_even_if_its_name_contains_delete(
        self,
    ) -> None:
        names = {
            "アカウント削除": "account_deletion",
            "一時凍結": "account_suspension",
            "注意": "caution",
            "問題なし": "no_issue",
        }
        for name, code in names.items():
            prompt = f"設問A09がYesのとき{name}とするルールを追加して"
            status, body = self.propose(prompt)
            self.assertEqual(status, 201, name)
            self.assertEqual(body["kind"], "add", name)
            self.assertEqual(body["added"]["result_code"], code, name)
        self.assertEqual(self.ids(self.get_list()), INITIAL_IDS)

    def test_a_removal_with_an_id_is_a_removal_even_with_a_result_name(self) -> None:
        for prompt in (
            "Remove rule R03",
            "アカウント削除のルールR03を削除して",
        ):
            status, body = self.propose(prompt)
            self.assertEqual((status, body["kind"]), (201, "remove"), prompt)
            self.assertEqual(body["removed"]["rule_id"], "R03", prompt)

    def test_a_removal_without_an_id_or_an_add_without_conditions_changes_nothing(
        self,
    ) -> None:
        before = self.get_list()
        for prompt in (
            "ルールを削除して",
            "アカウント削除のルールを削除して",
            "アカウント削除とするルールを追加して",
            "ルールを追加して",
        ):
            status, _ = self.propose(prompt)
            self.assertEqual(status, 422, prompt)
        self.assertEqual(self.get_list(), before)

    def test_a_three_digit_id_works_in_a_removal_request(self) -> None:
        rules_before = self.rule_store.snapshot()[1]
        self.rule_store._rules = (*rules_before, rule("R100", "caution", A01="yes"))
        status, body = self.propose("ルールR100を削除して")
        self.assertEqual((status, body["removed"]["rule_id"]), (201, "R100"))


class TestPendingLimit(RuleAdminTestCase):
    def test_an_evicted_proposal_is_404_and_the_rules_do_not_change(self) -> None:
        from app.rule_store import MAX_PENDING_PROPOSALS

        _, first = self.propose()
        for _ in range(MAX_PENDING_PROPOSALS):
            self.assertEqual(self.propose()[0], 201)
        before = self.get_list()
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.ids(before), INITIAL_IDS)


class TrackingLock:
    """競合（取得待ち）を、スリープなしで知らせるロック。"""

    def __init__(self, on_contended: Any) -> None:
        self._lock = threading.Lock()
        self._on_contended = on_contended

    def __enter__(self) -> TrackingLock:
        if not self._lock.acquire(blocking=False):
            self._on_contended()
            self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


class HookedRuleStore(InMemoryRuleStore):
    """approve に入る直前に、1回だけ処理を挟む（承認の途中への割り込みを再現する）。"""

    def __init__(self) -> None:
        super().__init__()
        self.before_approve: Any = None

    def approve(self, proposal_id: str) -> Any:
        hook, self.before_approve = self.before_approve, None
        if hook is not None:
            hook()
        return super().approve(proposal_id)


class TestRuleApprovalIsSerializedWithQuestionRetirement(unittest.TestCase):
    """ルール承認の途中に設問の廃止が割り込んでも、参照切れのルールを残さない。"""

    def test_a_retirement_cannot_slip_in_between_the_check_and_the_apply(self) -> None:
        settled = threading.Event()
        rule_store = HookedRuleStore()
        lock = TrackingLock(settled.set)
        case_store = RetirementStore()
        case_store.rule_rows = []  # 案件側のルールはA13を参照しない
        app = make_app(rule_store=rule_store, case_store=case_store, admin_lock=lock)
        with _Server(app) as server:
            base = server.base_url
            q_url = f"{base}/api/admin/question-proposals"
            r_url = f"{base}/api/admin/rule-proposals"
            # A13を足す。ルールはまだ参照しないので、廃止案は「廃止できる」。
            _, added = send(
                "POST", q_url, {"prompt": "APIキーの失効履歴を確認する設問を追加して"}
            )
            send(
                "POST",
                f"{q_url}/{added['proposal_id']}/decision",
                {"decision": "yes"},
            )
            _, retire = send("POST", q_url, {"prompt": "Deactivate question A13"})
            self.assertIs(retire["can_retire"], True)
            _, rule = send(
                "POST",
                r_url,
                {"prompt": "設問A13がYesのとき注意とするルールを追加して"},
            )
            outcome: dict[str, Any] = {}

            def retire_yes() -> None:
                outcome["retire"] = send(
                    "POST",
                    f"{q_url}/{retire['proposal_id']}/decision",
                    {"decision": "yes"},
                )
                settled.set()

            retirer = threading.Thread(target=retire_yes)

            def interleave() -> None:
                # 廃止の承認を、ルール承認の途中（反映の直前）に走らせる。
                # ロックで待たされるか、先に終わるまで待つ（時間は使わない）。
                retirer.start()
                self.assertTrue(settled.wait(timeout=30))

            rule_store.before_approve = interleave
            status, _ = send(
                "POST", f"{r_url}/{rule['proposal_id']}/decision", {"decision": "yes"}
            )
            retirer.join(timeout=30)
            _, rules_now = send("GET", f"{base}/api/admin/rules")
            _, questions_now = send("GET", f"{base}/api/admin/questions")

        a13 = next(q for q in questions_now["questions"] if q["id"] == "A13")
        # 反映されたルールが参照する設問は、廃止されていない。
        self.assertEqual(status, 200)
        self.assertTrue(any("A13" in r["conditions"] for r in rules_now["rules"]))
        self.assertFalse(a13.get("retired", False), "参照されるのに廃止された")
        self.assertEqual(outcome["retire"][0], 409)


class TestMainWiring(unittest.TestCase):
    def test_main_wires_the_rule_admin_and_one_admin_lock(self) -> None:
        from app import main

        self.assertTrue(hasattr(main.app.state.admin_lock, "__enter__"))
        self.assertIsInstance(main.app.state.rule_store, InMemoryRuleStore)
        # 他のテストと状態を分けるため、このテストの間だけ新しい保存先に差し替える。
        for name, fresh in (
            ("rule_store", InMemoryRuleStore()),
            ("question_store", InMemoryQuestionStore()),
        ):
            original = getattr(main.app.state, name)
            self.addCleanup(setattr, main.app.state, name, original)
            setattr(main.app.state, name, fresh)
        with _Server(main.app) as server:
            base = f"{server.base_url}/api/admin"
            status, listed = send("GET", f"{base}/rules")
            self.assertEqual((status, len(listed["rules"])), (200, 3))
            _, proposal = send("POST", f"{base}/rule-proposals", {"prompt": ADD_PROMPT})
            status, applied = send(
                "POST",
                f"{base}/rule-proposals/{proposal['proposal_id']}/decision",
                {"decision": "yes"},
            )
            self.assertEqual((status, applied["applied"]), (200, True))
            self.assertEqual(len(send("GET", f"{base}/rules")[1]["rules"]), 4)


if __name__ == "__main__":
    unittest.main()
