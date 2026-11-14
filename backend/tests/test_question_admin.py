"""設問の追加案（管理者AIの試作）のAPI契約の検証（モック・DBなし）。

見るのはHTTPの入力と出力（ステータスと本文）と、拒否・承認前・Noで一覧が変わらないこと。
案件の保存先（DB）は、アプリに置かない（設問の追加案はDBなしで動く）。
AIの出力は、テスト用の差し替え（FakeProposer）で、不正な応答や接続エラーを返させる。
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
値はすべて架空。
"""

import threading
import unittest
from typing import Any

from fastapi import FastAPI

from app import question_admin
from app.domain import (
    InvalidProposal,
    ProposalConflict,
    ProposalNotFound,
    ProposerUnavailable,
)
from app.question_proposer import MockQuestionProposer
from app.question_store import MAX_PENDING_PROPOSALS, InMemoryQuestionStore
from app.question_validation import (
    DemoQuestion,
    next_question_id,
    validate_model_output,
)
from tests.test_intake import _Server, send

EXAMPLE_PROMPT = "APIキーの失効履歴を確認する設問を追加して"
CODES = [f"A{n:02d}" for n in range(1, 13)]


class FakeProposer:
    """生の出力、または例外を返すテスト用のAI。呼び出しの回数と引数を記録する。"""

    def __init__(self, output: Any = None, error: Exception | None = None) -> None:
        self.output, self.error = output, error
        self.calls: list[str] = []

    def generate(self, prompt: str, current: tuple[DemoQuestion, ...]) -> Any:
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return self.output


def a13(text: str = "対象期間にAPIキーが失効されたか？", id_: str = "A13") -> dict:
    return {"operation": "add_question", "question": {"id": id_, "text": text}}


def make_app(proposer: Any, store: InMemoryQuestionStore | None = None) -> FastAPI:
    # case_store（DB）は置かない。設問の追加案がDBに依存しないことの確認を兼ねる。
    application = FastAPI()
    application.state.question_proposer = proposer
    application.state.question_store = store or InMemoryQuestionStore()
    application.include_router(question_admin.router)
    return application


class QuestionAdminTestCase(unittest.TestCase):
    proposer: Any = MockQuestionProposer()

    def setUp(self) -> None:
        self.store = InMemoryQuestionStore()
        server = _Server(make_app(self.proposer, self.store))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.base = server.base_url

    def get_list(self) -> dict:
        status, body = send("GET", f"{self.base}/api/admin/questions")
        self.assertEqual(status, 200)
        return body

    def propose(self, prompt: str = EXAMPLE_PROMPT) -> tuple[int, dict]:
        return send(
            "POST", f"{self.base}/api/admin/question-proposals", {"prompt": prompt}
        )

    def decide(self, proposal_id: str, decision: Any) -> tuple[int, dict]:
        return send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal_id}/decision",
            {"decision": decision},
        )

    def assert_list_unchanged(self, before: dict) -> None:
        self.assertEqual(self.get_list(), before)


class TestList(QuestionAdminTestCase):
    def test_returns_initial_demo_list_marked_as_mock_in_memory(self) -> None:
        body = self.get_list()
        self.assertEqual([q["id"] for q in body["questions"]], CODES)
        self.assertTrue(all(q["text"] for q in body["questions"]))
        self.assertIs(body["mock"], True)
        self.assertEqual(body["persistence"], "memory")
        self.assertEqual(body["revision"], 1)


class TestProposalCreation(QuestionAdminTestCase):
    def test_example_prompt_returns_before_after_and_added_without_changing_list(
        self,
    ) -> None:
        before = self.get_list()
        status, body = self.propose()
        self.assertEqual(status, 201)
        self.assertEqual([q["id"] for q in body["before"]], CODES)
        self.assertEqual([q["id"] for q in body["after"]], [*CODES, "A13"])
        self.assertEqual(body["added"]["id"], "A13")
        self.assertIn("失効", body["added"]["text"])
        self.assertEqual(body["base_revision"], 1)
        self.assertTrue(body["proposal_id"])
        self.assertIs(body["mock"], True)
        self.assert_list_unchanged(before)  # 承認前は現行データを変えない

    def test_prompt_the_mock_cannot_handle_is_rejected_422_and_list_unchanged(
        self,
    ) -> None:
        before = self.get_list()
        status, body = self.propose("何か良い感じの設問を足して")
        self.assertEqual(status, 422)
        self.assertIn("detail", body)
        self.assert_list_unchanged(before)

    def test_invalid_prompts_are_rejected_422_without_calling_the_ai(self) -> None:
        fake = FakeProposer(a13())
        server = _Server(make_app(fake))
        with server:
            url = f"{server.base_url}/api/admin/question-proposals"
            for payload in (
                {},
                {"prompt": ""},
                {"prompt": "   "},
                {"prompt": 123},
                {"prompt": "あ" * 501},
            ):
                with self.subTest(payload=payload):
                    status, _ = send("POST", url, payload)
                    self.assertEqual(status, 422)
        self.assertEqual(fake.calls, [])


class TestInvalidModelOutput(unittest.TestCase):
    """AIの出力（モック）を、アプリ側で検証して、422で拒否する。現行データは変えない。"""

    CASES = {
        "オブジェクトでない": "A13を追加",
        "操作がない": {"question": {"id": "A13", "text": "x？"}},
        "追加以外の操作": {**a13(), "operation": "delete_question"},
        "余分な項目": {**a13(), "extra": 1},
        "設問が辞書でない": {"operation": "add_question", "question": "A13"},
        "idがない": {"operation": "add_question", "question": {"text": "x？"}},
        "textがない": {"operation": "add_question", "question": {"id": "A13"}},
        "設問に余分な項目": {
            "operation": "add_question",
            "question": {"id": "A13", "text": "x？", "yes": "y"},
        },
        "IDの形式が違う": a13(id_="a13"),
        "IDが文字列でない": a13(id_=13),
        "許可された追加先でない（A14）": a13(id_="A14"),
        "許可された追加先でない（A99）": a13(id_="A99"),
        "IDの重複": a13(id_="A12"),
        "文言が空": a13(text="  "),
        "文言が文字列でない": a13(text=["x"]),
        "文言の重複": a13(text="対象期間にAPIキーが新しく発行されたか？"),
        "文言の重複（前後空白）": a13(text=" 対象期間にAPIキーが新しく発行されたか？ "),
        "文言が長すぎる": a13(text="あ" * 201),
        "文言に制御文字": a13(text="対象期間\nにAPIキーが失効されたか？"),
    }

    def test_each_invalid_output_is_rejected_422_and_nothing_changes(self) -> None:
        for name, output in self.CASES.items():
            with self.subTest(name):
                store = InMemoryQuestionStore()
                server = _Server(make_app(FakeProposer(output), store))
                with server:
                    status, body = send(
                        "POST",
                        f"{server.base_url}/api/admin/question-proposals",
                        {"prompt": EXAMPLE_PROMPT},
                    )
                    self.assertEqual(status, 422)
                    self.assertIn("detail", body)
                    revision, questions = store.snapshot()
                    self.assertEqual(revision, 1)
                    self.assertEqual([q.id for q in questions], CODES)

    def test_a_proposal_rejected_by_validation_cannot_be_approved(self) -> None:
        store = InMemoryQuestionStore()
        server = _Server(make_app(FakeProposer(a13(id_="A14")), store))
        with server:
            send(
                "POST",
                f"{server.base_url}/api/admin/question-proposals",
                {"prompt": EXAMPLE_PROMPT},
            )
            status, _ = send(
                "POST",
                f"{server.base_url}/api/admin/question-proposals/a14/decision",
                {"decision": "yes"},
            )
            self.assertEqual(status, 404)
            self.assertEqual(len(store.snapshot()[1]), 12)


class TestConnectionError(unittest.TestCase):
    def test_ai_unavailable_is_502_not_a_success_and_list_unchanged(self) -> None:
        store = InMemoryQuestionStore()
        server = _Server(make_app(FakeProposer(error=ProposerUnavailable()), store))
        with server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/question-proposals",
                {"prompt": EXAMPLE_PROMPT},
            )
            self.assertEqual(status, 502)
            self.assertNotIn("proposal_id", body)
            self.assertIn("detail", body)
            self.assertEqual(store.snapshot()[0], 1)
            self.assertEqual(len(store.snapshot()[1]), 12)


class TestDecision(QuestionAdminTestCase):
    def test_no_leaves_the_list_unchanged_and_the_proposal_is_used_up(self) -> None:
        before = self.get_list()
        _, proposal = self.propose()
        status, body = self.decide(proposal["proposal_id"], "no")
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], False)
        self.assertEqual(body["questions"], before["questions"])
        self.assert_list_unchanged(before)
        status, _ = self.decide(proposal["proposal_id"], "yes")  # Noのあとの再利用
        self.assertEqual(status, 404)
        self.assert_list_unchanged(before)

    def test_yes_applies_only_the_added_question_and_bumps_revision(self) -> None:
        _, proposal = self.propose()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], True)
        self.assertEqual(body["revision"], 2)
        self.assertEqual([q["id"] for q in body["questions"]], [*CODES, "A13"])
        self.assertEqual(body["questions"][:12], proposal["before"])
        listed = self.get_list()
        self.assertEqual(listed["questions"], proposal["after"])
        self.assertEqual(listed["revision"], 2)

    def test_replaying_yes_is_rejected_404_and_does_not_add_twice(self) -> None:
        _, proposal = self.propose()
        self.decide(proposal["proposal_id"], "yes")
        after_first = self.get_list()
        status, _ = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 404)
        self.assert_list_unchanged(after_first)

    def test_unknown_proposal_is_404_and_list_unchanged(self) -> None:
        before = self.get_list()
        for decision in ("yes", "no"):
            with self.subTest(decision):
                status, _ = self.decide("no-such-proposal", decision)
                self.assertEqual(status, 404)
        self.assert_list_unchanged(before)

    def test_invalid_decision_value_is_422_and_the_proposal_stays_pending(
        self,
    ) -> None:
        before = self.get_list()
        _, proposal = self.propose()
        for value in ("maybe", "YES", True, None):
            with self.subTest(value=value):
                status, _ = self.decide(proposal["proposal_id"], value)
                self.assertEqual(status, 422)
        self.assert_list_unchanged(before)
        status, _ = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 200)

    def test_client_cannot_supply_the_content_to_apply(self) -> None:
        _, proposal = self.propose()
        status, body = send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal['proposal_id']}/decision",
            {
                "decision": "yes",
                "added": {"id": "A13", "text": "偽の文言"},
                "after": [{"id": "A99", "text": "偽"}],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["questions"][-1], proposal["added"])
        self.assertEqual(len(body["questions"]), 13)

    def test_stale_base_is_rejected_409_and_the_applied_list_is_kept(self) -> None:
        _, first = self.propose()
        _, second = self.propose()  # 同じ基準（版1）から、もう1つ提案
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 200)
        applied = self.get_list()
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("detail", body)
        self.assert_list_unchanged(applied)
        self.assertEqual(self.decide(second["proposal_id"], "yes")[0], 404)

    def test_second_proposal_after_apply_is_rejected_as_duplicate(self) -> None:
        _, proposal = self.propose()
        self.decide(proposal["proposal_id"], "yes")
        applied = self.get_list()
        status, _ = self.propose()  # モックは再びA13を出す。既に存在するので拒否
        self.assertEqual(status, 422)
        self.assert_list_unchanged(applied)


class TestNoAndLaterProposals(QuestionAdminTestCase):
    def test_no_keeps_revision_and_another_pending_proposal_can_still_be_approved(
        self,
    ) -> None:
        _, first = self.propose()
        _, second = self.propose()  # 同じ基準（版1）の別の提案
        status, body = self.decide(first["proposal_id"], "no")
        self.assertEqual((status, body["applied"], body["revision"]), (200, False, 1))
        self.assertEqual(len(body["questions"]), 12)
        # Noは版を進めないので、別の提案は競合せず、Yesで反映できる。
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual((status, body["applied"], body["revision"]), (200, True, 2))
        self.assertEqual(len(body["questions"]), 13)

    def test_no_on_a_stale_proposal_is_200_and_reports_the_current_list(
        self,
    ) -> None:
        # 他の人の変更で一覧が進んだあとのNo。
        # 反映しないのは提案で、一覧が変わっていないとは限らない。
        _, first = self.propose()
        _, second = self.propose()
        self.decide(first["proposal_id"], "yes")
        status, body = self.decide(second["proposal_id"], "no")
        self.assertEqual((status, body["applied"], body["revision"]), (200, False, 2))
        self.assertEqual(body["questions"][-1]["id"], "A13")
        self.assertEqual(self.decide(second["proposal_id"], "no")[0], 404)

    def test_no_does_not_use_up_other_proposals_or_change_the_list(self) -> None:
        before = self.get_list()
        _, proposal = self.propose()
        self.decide(proposal["proposal_id"], "no")
        self.assert_list_unchanged(before)


class TestWiredApp(unittest.TestCase):
    """app.main の実際の配線（app.state の組み立てとルーターの登録）を通す。"""

    def test_main_serves_the_question_flow_with_mock_and_memory_store(self) -> None:
        from app import main  # 読み込み時に app.state を組み立てる。DBへは接続しない

        self.assertIsInstance(main.app.state.question_proposer, MockQuestionProposer)
        self.assertIsInstance(main.app.state.question_store, InMemoryQuestionStore)
        # 他のテストと状態を分けるため、このテストの間だけ新しい保存先に差し替える。
        original = main.app.state.question_store
        main.app.state.question_store = InMemoryQuestionStore()
        self.addCleanup(setattr, main.app.state, "question_store", original)
        with _Server(main.app) as server:
            base = f"{server.base_url}/api/admin"
            status, listed = send("GET", f"{base}/questions")
            self.assertEqual((status, len(listed["questions"])), (200, 12))
            status, proposal = send(
                "POST", f"{base}/question-proposals", {"prompt": EXAMPLE_PROMPT}
            )
            self.assertEqual(status, 201)
            self.assertEqual(len(send("GET", f"{base}/questions")[1]["questions"]), 12)
            status, applied = send(
                "POST",
                f"{base}/question-proposals/{proposal['proposal_id']}/decision",
                {"decision": "yes"},
            )
            self.assertEqual((status, applied["applied"]), (200, True))
            self.assertEqual(send("GET", f"{base}/questions")[1]["revision"], 2)


class TestIndependenceFromCaseStore(unittest.TestCase):
    def test_works_without_any_case_store_or_database(self) -> None:
        application = make_app(MockQuestionProposer())
        self.assertFalse(hasattr(application.state, "case_store"))
        with _Server(application) as server:
            status, _ = send("GET", f"{server.base_url}/api/admin/questions")
            self.assertEqual(status, 200)


class TestValidationFunction(unittest.TestCase):
    CURRENT = tuple(DemoQuestion(c, f"設問{c}？") for c in CODES)

    def test_valid_output_becomes_a_proposal_with_base_and_after(self) -> None:
        proposal = validate_model_output(a13(" 文言？ "), self.CURRENT, 7)
        self.assertEqual(proposal.added, DemoQuestion("A13", "文言？"))
        self.assertEqual(proposal.base_revision, 7)
        self.assertEqual(proposal.after, (*self.CURRENT, proposal.added))

    def test_text_length_boundary_and_id_lookalikes(self) -> None:
        self.assertEqual(
            len(validate_model_output(a13("あ" * 200), self.CURRENT, 1).added.text),
            200,
        )
        for bad_id in ("Ａ13", "A１３", "A13 ", "A013", "A1", "B13", "A-13"):
            with self.subTest(bad_id), self.assertRaises(InvalidProposal):
                validate_model_output(a13(id_=bad_id), self.CURRENT, 1)

    def test_non_boolean_and_non_string_values_are_rejected(self) -> None:
        for raw in (None, [], 1, True, {"operation": True, "question": {}}):
            with self.subTest(raw=raw), self.assertRaises(InvalidProposal):
                validate_model_output(raw, self.CURRENT, 1)

    def test_validation_does_not_mutate_the_current_list(self) -> None:
        current = self.CURRENT
        validate_model_output(a13(), current, 1)
        self.assertEqual(current, self.CURRENT)
        self.assertEqual(len(current), 12)

    def test_next_id_is_one_past_the_largest(self) -> None:
        self.assertEqual(next_question_id(self.CURRENT), "A13")
        self.assertEqual(next_question_id(()), "A01")


class TestStore(unittest.TestCase):
    def proposal(self, store: InMemoryQuestionStore, text: str = "新しい設問？"):
        revision, current = store.snapshot()
        return validate_model_output(a13(text), current, revision)

    def test_only_one_of_two_proposals_on_the_same_base_is_applied_concurrently(
        self,
    ) -> None:
        store = InMemoryQuestionStore()
        ids = [store.register(self.proposal(store, f"設問{n}？")) for n in range(8)]
        results: list[str] = []
        barrier = threading.Barrier(len(ids))

        def run(proposal_id: str) -> None:
            barrier.wait()
            try:
                store.approve(proposal_id)
                results.append("ok")
            except ProposalConflict:
                results.append("conflict")

        threads = [threading.Thread(target=run, args=(i,)) for i in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("conflict"), len(ids) - 1)
        revision, questions = store.snapshot()
        self.assertEqual((revision, len(questions)), (2, 13))

    def test_reject_and_approve_use_up_the_proposal(self) -> None:
        store = InMemoryQuestionStore()
        proposal_id = store.register(self.proposal(store))
        store.reject(proposal_id)
        with self.assertRaises(ProposalNotFound):
            store.approve(proposal_id)
        with self.assertRaises(ProposalNotFound):
            store.reject(proposal_id)
        self.assertEqual(store.snapshot()[0], 1)

    def test_pending_proposals_are_capped_dropping_the_oldest(self) -> None:
        store = InMemoryQuestionStore()
        first = store.register(self.proposal(store))
        for _ in range(MAX_PENDING_PROPOSALS):
            store.register(self.proposal(store))
        with self.assertRaises(ProposalNotFound):
            store.approve(first)

    def test_a_new_store_starts_from_the_initial_list_like_a_restart(self) -> None:
        store = InMemoryQuestionStore()
        store.approve(store.register(self.proposal(store)))
        self.assertEqual(len(store.snapshot()[1]), 13)
        restarted = InMemoryQuestionStore()
        self.assertEqual(restarted.snapshot()[0], 1)
        self.assertEqual(len(restarted.snapshot()[1]), 12)


if __name__ == "__main__":
    unittest.main()
