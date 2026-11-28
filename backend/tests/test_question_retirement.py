"""設問の廃止案（管理者AIの試作）のAPI契約の検証（モック・DBなし）。

見るのはHTTPの入力と出力（ステータスと本文）と、拒否・承認前・Noで一覧・ルール・案件が
変わらないこと。ルールと案件の保存先は、`ContractStore`（メモリ）を拡張したテスト用の保存先。
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
架空の設問A13（APIキーの失効履歴。どの判定ルールも参照しない）で、廃止の挙動を確認する。
A09は、架空のルールR01が参照している設問。値はすべて架空。
"""

import unittest

from fastapi import FastAPI

from app import cases, question_admin, rules
from app.domain import InvalidProposal
from app.question_proposer import MockQuestionProposer
from app.question_store import InMemoryQuestionStore
from app.question_validation import (
    DemoQuestion,
    ValidatedRetirement,
    find_case_impact,
    find_rule_references,
    validate_model_output,
)
from tests.test_api_contract import ContractStore
from tests.test_intake import _Server, case_payload, send

ADD_PROMPT = "APIキーの失効履歴を確認する設問を追加して"
RETIRE_A13 = "Deactivate question A13"
A13_TEXT = (
    "Was an API key revoked during the target period? / "
    "対象期間にAPIキーが失効されたか？"
)


def rule_row(rule_id: str, revision: int, *question_ids: str) -> dict:
    return {
        "rule_id": rule_id,
        "revision": revision,
        "result_code": "caution",
        "conditions": {q: "yes" for q in question_ids},
    }


class RetirementStore(ContractStore):
    """A13を持つ案件を返せる、DBなしの保存先。

    A13は、answers に A13 を入れた案件にだけ出す（受付時のコピーの代わり）。
    保存済みの設問文は prompt に持つ。
    """

    def get_case(self, case_id: str) -> dict:
        case = super().get_case(case_id)
        answers = self.cases[case_id]["answers"]
        if "A13" in answers:
            case["questions"].append(
                {"question_code": "A13", "prompt": A13_TEXT, "answer": answers["A13"]}
            )
        return case


def make_app(case_store: object | None, store: InMemoryQuestionStore) -> FastAPI:
    application = FastAPI()
    if case_store is not None:
        application.state.case_store = case_store
    application.state.question_proposer = MockQuestionProposer()
    application.state.question_store = store
    application.include_router(question_admin.router)
    application.include_router(cases.router)
    application.include_router(rules.router)
    return application


class RetirementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = RetirementStore()
        # R01はA09を参照する。どのルールもA13は参照しない。
        self.cases.rule_rows = [rule_row("R01", 1, "A01", "A09")]
        self.questions = InMemoryQuestionStore()
        server = _Server(make_app(self.cases, self.questions))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.base = server.base_url
        # 設問A13を、既存の追加案のフローで反映しておく。
        _, proposal = self.propose(ADD_PROMPT)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)
        self.unjudged = self.open_case("456", "yes")  # 判定未登録・A13=Yes
        self.judged = self.open_case("123", "no")  # 判定登録済み・A13=No
        self.no_a13 = self.cases.create_case(case_payload("789"))  # A13を持たない
        status, _ = send(
            "PUT",
            f"{self.base}/api/cases/{self.judged}/judgment",
            {"result_code": "no_issue"},
        )
        self.assertEqual(status, 204)
        self.cases.save_calls.clear()

    def open_case(self, user_id: str, a13: str) -> str:
        case_id = self.cases.create_case(case_payload(user_id))
        self.cases.cases[case_id]["answers"]["A13"] = a13
        return case_id

    def propose(self, prompt: str) -> tuple[int, dict]:
        return send(
            "POST", f"{self.base}/api/admin/question-proposals", {"prompt": prompt}
        )

    def decide(self, proposal_id: str, decision: str) -> tuple[int, dict]:
        return send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal_id}/decision",
            {"decision": decision},
        )

    def get_list(self) -> dict:
        return send("GET", f"{self.base}/api/admin/questions")[1]

    def get_case(self, case_id: str) -> dict:
        status, body = send("GET", f"{self.base}/api/cases/{case_id}")
        self.assertEqual(status, 200)
        return body

    def snapshot(self) -> tuple:
        """一覧・ルール・案件の全体（変わっていないことの確認用）。"""
        return (
            self.get_list(),
            [dict(r) for r in self.cases.rule_rows],
            {k: dict(v["answers"]) for k, v in self.cases.cases.items()},
            {k: v.get("result") for k, v in self.cases.cases.items()},
        )

    def q(self, case: dict, code: str) -> dict:
        return next(x for x in case["questions"] if x["question_code"] == code)


class TestRetirementProposal(RetirementTestCase):
    def test_unreferenced_question_gives_diff_with_no_references_and_changes_nothing(
        self,
    ) -> None:
        before = self.snapshot()
        status, body = self.propose(RETIRE_A13)
        self.assertEqual(status, 201)
        self.assertEqual(body["kind"], "retire")
        self.assertEqual(body["retired"], {"id": "A13", "text": A13_TEXT})
        self.assertEqual(body["references"], [])
        self.assertIs(body["can_retire"], True)
        self.assertEqual(body["cascade"], [])  # ルール・案件を連鎖して変えない
        # 差分: 一覧は残し、A13だけが廃止になる。
        self.assertNotIn("retired", body["before"][-1])
        self.assertEqual(
            body["after"][-1], {"id": "A13", "text": A13_TEXT, "retired": True}
        )
        self.assertEqual(
            [q["id"] for q in body["before"]], [q["id"] for q in body["after"]]
        )
        self.assertEqual(self.snapshot(), before)  # 承認前は何も変わらない

    def test_referenced_question_lists_the_referencing_rules_and_cannot_retire(
        self,
    ) -> None:
        status, body = self.propose("設問A09を廃止して")
        self.assertEqual(status, 201)
        self.assertEqual(
            body["references"],
            [{"rule_id": "R01", "revision": 1, "result_code": "caution"}],
        )
        self.assertIs(body["can_retire"], False)

    def test_referenced_question_shows_both_references_and_case_impact(self) -> None:
        # 参照があって廃止できない案でも、影響を受ける案件の一覧は同時に出す。
        before = self.snapshot()
        _, body = self.propose("設問A09を廃止して")
        self.assertIs(body["can_retire"], False)
        self.assertEqual([r["rule_id"] for r in body["references"]], ["R01"])
        impact = body["impact"]
        # A09は全案件が持つ設問。判定の登録の有無で分かれる。
        self.assertEqual(
            sorted(c["case_id"] for c in impact["unjudged"]),
            sorted([self.unjudged, self.no_a13]),
        )
        self.assertEqual(
            [(c["case_id"], c["result_code"]) for c in impact["judged"]],
            [(self.judged, "no_issue")],
        )
        self.assertEqual(body["cascade"], [])
        self.assertEqual(self.snapshot(), before)  # 提案だけでは何も変わらない

    def test_impact_separates_unjudged_and_judged_cases_by_result_not_by_answer(
        self,
    ) -> None:
        _, body = self.propose(RETIRE_A13)
        impact = body["impact"]
        self.assertEqual(
            [(c["case_id"], c["answer"]) for c in impact["unjudged"]],
            [(self.unjudged, "yes")],
        )
        self.assertEqual(
            [(c["case_id"], c["answer"], c["result_code"]) for c in impact["judged"]],
            [(self.judged, "no", "no_issue")],
        )
        listed = {c["case_id"] for c in (*impact["unjudged"], *impact["judged"])}
        self.assertNotIn(self.no_a13, listed)  # 設問を持たない案件は影響なし

    def test_unjudged_case_whose_answer_is_pending_is_still_unjudged(self) -> None:
        self.cases.cases[self.unjudged]["answers"]["A13"] = "pending"
        _, body = self.propose(RETIRE_A13)
        self.assertEqual(
            [(c["case_id"], c["answer"]) for c in body["impact"]["unjudged"]],
            [(self.unjudged, "pending")],
        )

    def test_only_latest_rule_revisions_count_as_references(self) -> None:
        # 旧版はA13を参照するが、最新の版は参照しない -> 参照なし。
        self.cases.rule_rows = [
            rule_row("R01", 1, "A13"),
            rule_row("R01", 2, "A01"),
        ]
        _, body = self.propose(RETIRE_A13)
        self.assertEqual(body["references"], [])
        # 旧版は参照せず、最新の版が参照する -> 参照あり。
        self.cases.rule_rows = [rule_row("R01", 1, "A01"), rule_row("R01", 2, "A13")]
        _, body = self.propose(RETIRE_A13)
        self.assertEqual([r["revision"] for r in body["references"]], [2])

    def test_unknown_or_already_retired_or_missing_id_is_rejected_422(self) -> None:
        for prompt in ("設問A99を廃止して", "設問を廃止して"):
            with self.subTest(prompt):
                before = self.snapshot()
                status, body = self.propose(prompt)
                self.assertEqual(status, 422)
                self.assertIn("detail", body)
                self.assertEqual(self.snapshot(), before)
        _, proposal = self.propose(RETIRE_A13)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)
        status, body = self.propose(RETIRE_A13)
        self.assertEqual(status, 422)
        self.assertIn("deactivated", body["detail"])

    def test_retirement_needs_a_rule_and_case_store_503_and_changes_nothing(
        self,
    ) -> None:
        questions = InMemoryQuestionStore()
        with _Server(make_app(None, questions)) as server:
            base = server.base_url
            _, added = send(
                "POST", f"{base}/api/admin/question-proposals", {"prompt": ADD_PROMPT}
            )
            send(
                "POST",
                f"{base}/api/admin/question-proposals/{added['proposal_id']}/decision",
                {"decision": "yes"},
            )
            status, _ = send(
                "POST",
                f"{base}/api/admin/question-proposals",
                {"prompt": RETIRE_A13},
            )
            self.assertEqual(status, 503)
            self.assertEqual(questions.retired_ids(), frozenset())


class TestRetirementDecision(RetirementTestCase):
    def test_yes_on_unreferenced_question_retires_it_and_keeps_the_text(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], True)
        self.assertEqual(body["revision"], 3)  # 初期1 -> 追加2 -> 廃止3
        self.assertEqual(
            body["questions"][-1], {"id": "A13", "text": A13_TEXT, "retired": True}
        )
        self.assertEqual(len(body["questions"]), 13)  # 一覧から消さない
        self.assertEqual(self.get_list()["questions"], body["questions"])

    def test_yes_does_not_change_rules_or_cases_no_cascade(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        before = self.snapshot()[1:]
        self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(self.snapshot()[1:], before)
        self.assertEqual(self.cases.save_calls, [])

    def test_no_keeps_everything_and_the_proposal_cannot_be_reused(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        before = self.snapshot()
        status, body = self.decide(proposal["proposal_id"], "no")
        self.assertEqual((status, body["applied"]), (200, False))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.snapshot(), before)

    def test_yes_on_referenced_question_is_refused_409_and_nothing_changes(
        self,
    ) -> None:
        _, proposal = self.propose("設問A09を廃止して")
        before = self.snapshot()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("R01", body["detail"])
        self.assertIn("cannot be deactivated", body["detail"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)

    def test_yes_keeps_the_proposal_when_references_cannot_be_checked(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        before = self.snapshot()
        working = self.cases.list_rules

        def unavailable() -> list[dict]:
            raise RuntimeError("ルールの保存先に接続できません")

        self.cases.list_rules = unavailable  # type: ignore[method-assign]
        status, _ = self.decide(proposal["proposal_id"], "yes")
        self.assertGreaterEqual(status, 500)  # 一時障害。404（使い切り）ではない
        self.assertEqual(self.snapshot(), before)  # 一覧・版・案件・ルールは不変

        self.cases.list_rules = working  # type: ignore[method-assign]
        status, body = self.decide(proposal["proposal_id"], "yes")  # 同じ提案で再試行
        self.assertEqual((status, body["applied"]), (200, True))
        self.assertEqual(body["revision"], 3)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)

    def test_rule_that_started_referencing_after_the_proposal_blocks_yes(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        self.assertEqual(proposal["references"], [])
        self.cases.rule_rows.append(rule_row("R02", 1, "A13"))
        before = self.snapshot()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("R02", body["detail"])
        self.assertEqual(self.snapshot(), before)

    def test_retirement_is_refused_409_when_an_add_was_applied_meanwhile(self) -> None:
        _, retire = self.propose(RETIRE_A13)
        self.assertEqual(retire["base_revision"], 2)
        # 提案の後に、同じ基準（版2）の追加案が先に反映される。
        revision, current = self.questions.snapshot()
        added = validate_model_output(
            {
                "operation": "add_question",
                "question": {"id": "A14", "text": "別の設問？"},
            },
            current,
            revision,
        )
        add_id = self.questions.register(added)
        self.assertEqual(self.decide(add_id, "yes")[0], 200)
        before = self.snapshot()
        status, body = self.decide(retire["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("detail", body)
        self.assertEqual(self.snapshot(), before)  # 一覧・ルール・案件・結果は不変
        listed = self.get_list()
        self.assertEqual(listed["revision"], 3)
        a13 = next(q for q in listed["questions"] if q["id"] == "A13")
        self.assertNotIn("retired", a13)  # A13は廃止されない
        self.assertEqual(self.cases.save_calls, [])
        self.assertEqual(self.decide(retire["proposal_id"], "yes")[0], 404)  # 使い切り
        self.assertEqual(self.snapshot(), before)
        # 提案し直せば、新しい基準（版3）で廃止できる。
        _, again = self.propose(RETIRE_A13)
        status, body = self.decide(again["proposal_id"], "yes")
        self.assertEqual((status, body["revision"]), (200, 4))

    def test_add_proposal_is_marked_kind_add_and_has_no_retirement_fields(self) -> None:
        with _Server(make_app(None, InMemoryQuestionStore())) as server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/question-proposals",
                {"prompt": ADD_PROMPT},
            )
        self.assertEqual(status, 201)
        self.assertEqual(body["kind"], "add")
        for key in ("retired", "references", "impact", "can_retire"):
            self.assertNotIn(key, body)


class TestCaseViewAfterRetirement(RetirementTestCase):
    def retire_a13(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)

    def test_before_retirement_nothing_is_marked_retired(self) -> None:
        case = self.get_case(self.unjudged)
        self.assertIs(self.q(case, "A13")["retired"], False)
        self.assertIs(case["judged"], False)

    def test_unjudged_case_keeps_saved_text_and_answer_but_marks_retired(self) -> None:
        self.retire_a13()
        case = self.get_case(self.unjudged)
        a13 = self.q(case, "A13")
        self.assertEqual(a13["retired"], True)
        self.assertEqual(a13["prompt"], A13_TEXT)  # 保存済みの設問文
        self.assertEqual(a13["answer"], "yes")  # 保存済みの回答
        self.assertIs(case["judged"], False)
        self.assertIsNone(case["result"])
        # ほかの設問は回答の対象のまま。
        self.assertTrue(
            all(
                not q["retired"]
                for q in case["questions"]
                if q["question_code"] != "A13"
            )
        )

    def test_rules_endpoint_returns_only_latest_revisions_without_the_retired_question(
        self,
    ) -> None:
        self.retire_a13()
        # GET /api/rules が返すのは、各ルールの最新の版だけ。
        # 案件の照合そのものは画面側の役割で、ここでは確認しない。
        self.cases.rule_rows.append(rule_row("R01", 2, "A01", "A05"))
        status, latest = send("GET", f"{self.base}/api/rules")
        self.assertEqual(status, 200)
        self.assertEqual([(r["rule_id"], r["revision"]) for r in latest], [("R01", 2)])
        self.assertNotIn("A13", latest[0]["conditions"])  # 廃止した設問は照合に使わない

    def test_judged_case_shows_the_registered_result_and_saved_values(self) -> None:
        self.retire_a13()
        case = self.get_case(self.judged)
        self.assertIs(case["judged"], True)
        self.assertEqual(case["result"]["result_code"], "no_issue")
        a13 = self.q(case, "A13")
        self.assertEqual(
            (a13["prompt"], a13["answer"], a13["retired"]), (A13_TEXT, "no", True)
        )

    def test_new_answer_to_a_retired_question_is_refused_422_and_nothing_changes(
        self,
    ) -> None:
        self.retire_a13()
        for case_id in (self.unjudged, self.judged):
            with self.subTest(case_id):
                before = self.snapshot()
                status, body = send(
                    "PUT",
                    f"{self.base}/api/cases/{case_id}/judgment",
                    {"result_code": "caution", "answers": {"A13": "no", "A09": "yes"}},
                )
                self.assertEqual(status, 422)
                self.assertIn("A13", body["detail"])
                self.assertEqual(self.snapshot(), before)  # A09も保存されない
                self.assertEqual(self.cases.save_calls, [])  # 保存先を呼ばない

    def test_judgment_without_the_retired_question_still_saves(self) -> None:
        self.retire_a13()
        status, _ = send(
            "PUT",
            f"{self.base}/api/cases/{self.unjudged}/judgment",
            {"result_code": "caution", "answers": {"A09": "no"}},
        )
        self.assertEqual(status, 204)
        case = self.get_case(self.unjudged)
        self.assertIs(case["judged"], True)
        self.assertEqual(self.q(case, "A13")["answer"], "yes")  # 保存済みの回答は残る

    def test_no_decision_leaves_the_case_view_unchanged(self) -> None:
        _, proposal = self.propose(RETIRE_A13)
        self.decide(proposal["proposal_id"], "no")
        self.assertIs(self.q(self.get_case(self.unjudged), "A13")["retired"], False)

    def test_case_view_without_a_question_store_has_nothing_retired(self) -> None:
        # 設問の保存先を置かないアプリ（既存の構成）でも、案件は開ける。
        application = FastAPI()
        application.state.case_store = self.cases
        application.include_router(cases.router)
        with _Server(application) as server:
            status, case = send("GET", f"{server.base_url}/api/cases/{self.unjudged}")
        self.assertEqual(status, 200)
        self.assertIs(self.q(case, "A13")["retired"], False)


class TestPureFunctions(unittest.TestCase):
    CURRENT = (DemoQuestion("A01", "x？"), DemoQuestion("A13", "y？"))

    def test_retire_is_validated_against_the_current_list(self) -> None:
        proposal = validate_model_output(
            {"operation": "retire_question", "question_id": "A13"}, self.CURRENT, 5
        )
        self.assertIsInstance(proposal, ValidatedRetirement)
        self.assertEqual(
            proposal.after,
            (DemoQuestion("A01", "x？"), DemoQuestion("A13", "y？", True)),
        )
        self.assertEqual(proposal.base_revision, 5)

    def test_invalid_retirement_outputs_are_rejected(self) -> None:
        retired = (DemoQuestion("A13", "y？", True),)
        cases = {
            "存在しない": (
                {"operation": "retire_question", "question_id": "A99"},
                self.CURRENT,
            ),
            "format": (
                {"operation": "retire_question", "question_id": "13"},
                self.CURRENT,
            ),
            "型": ({"operation": "retire_question", "question_id": 13}, self.CURRENT),
            "余分な項目": (
                {"operation": "retire_question", "question_id": "A13", "x": 1},
                self.CURRENT,
            ),
            "retired": (
                {"operation": "retire_question", "question_id": "A13"},
                retired,
            ),
        }
        for name, (raw, current) in cases.items():
            with self.subTest(name), self.assertRaises(InvalidProposal):
                validate_model_output(raw, current, 1)

    def test_references_count_both_yes_and_no_conditions(self) -> None:
        rules_ = [
            {
                "rule_id": "R01",
                "revision": 1,
                "result_code": "caution",
                "conditions": {"A13": "no"},
            },
            {
                "rule_id": "R02",
                "revision": 1,
                "result_code": "caution",
                "conditions": {"A01": "yes"},
            },
        ]
        self.assertEqual(
            [r["rule_id"] for r in find_rule_references("A13", rules_)], ["R01"]
        )
        self.assertEqual(find_rule_references("A05", rules_), [])

    def test_impact_ignores_cases_without_the_question(self) -> None:
        cases_ = [
            {
                "case_id": "C1",
                "summary": "s",
                "result": None,
                "questions": [{"question_code": "A13", "answer": None}],
            },
            {
                "case_id": "C2",
                "summary": "s",
                "result": {"result_code": "caution"},
                "questions": [{"question_code": "A13", "answer": "yes"}],
            },
            {
                "case_id": "C3",
                "summary": "s",
                "result": None,
                "questions": [{"question_code": "A01", "answer": "yes"}],
            },
        ]
        impact = find_case_impact("A13", cases_)
        self.assertEqual([c["case_id"] for c in impact["unjudged"]], ["C1"])
        self.assertIsNone(impact["unjudged"][0]["answer"])
        self.assertEqual([c["case_id"] for c in impact["judged"]], ["C2"])


if __name__ == "__main__":
    unittest.main()
