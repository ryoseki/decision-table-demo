"""判定ルールの条件の更新（既存ルールへの条件の追加）のAPI契約の検証（生成はスタブ・DBなし）。

見るのは、「R04にA09がYesを追加して」の依頼が、R04を対象にした更新案になること（R05を新設しない）、
Yes／Noの結果、他のルールと既存の条件・結果が変わらないこと、誤った新設案・無効な参照・重複・
版の競合を拒否して現行データを保つこと、定型依頼の再利用でも更新の対象と条件を検証すること。
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
値はすべて架空。初期のルールはR01〜R03だけなので、R04は各テストの準備（fixture）で用意する。
"""

import unittest
from typing import Any

from app import rule_admin
from app.domain import InvalidProposal
from app.rule_validation import (
    ValidatedRuleUpdate,
    read_update_intent,
    validate_rule_output,
)
from app.template_memory import InMemoryProposalTemplates
from tests.test_intake import _Server, send
from tests.test_proposal_reuse import (
    DictReuse,
    ScriptedRules,
    admin_app,
    auto_app,
    decide,
    post,
)
from tests.test_rule_admin import (
    ADD_PROMPT,
    INITIAL_IDS,
    QUESTIONS,
    FakeRuleProposer,
    RuleAdminTestCase,
    make_app,
    question,
    rule,
)

UPDATE_PROMPT = "R04にA09がYesを追加して"
R04_BEFORE = {"A10": "no"}
R04_AFTER = {"A09": "yes", "A10": "no"}


# 正式な書き方（R04・A09・Yes）ではないが、既存ルールの更新の依頼と疑われる入力。
LOWERCASE_ID_PROMPTS = (
    "r04にa09がyesを追加して",
    "R04にa09がyesを追加して",
    "r04にA09がYesを追加して",
)


def update_output(
    rule_id: Any = "R04", add_conditions: Any = None, extra: dict | None = None
) -> dict:
    return {
        "operation": "update_rule",
        "rule_id": rule_id,
        "add_conditions": {"A09": "yes"} if add_conditions is None else add_conditions,
        **(extra or {}),
    }


def update_prompt(additions: dict[str, str]) -> str:
    """追加する条件から、「R04にA09がYesとA10がNoを追加して」形式の依頼を作る。"""
    pairs = "と".join(f"{q}が{a.capitalize()}" for q, a in additions.items())
    return f"R04に{pairs}を追加して"


# R04は A10=No を持つ。(追加する条件, 拒否理由) 。「追加済み」は同じ値、
# 「書き換え」は違う値。新規の条件が先でも後でも、1つでも既存なら全体を拒否する。
PARTLY_EXISTING = (
    ({"A09": "yes", "A10": "no"}, "追加済み"),
    ({"A10": "no", "A09": "yes"}, "追加済み"),
    ({"A10": "no", "A11": "yes"}, "追加済み"),
    ({"A11": "yes", "A10": "no"}, "追加済み"),
    ({"A09": "yes", "A10": "yes"}, "書き換えは扱えません"),
    ({"A10": "yes", "A11": "no"}, "書き換えは扱えません"),
)


def add_r05_output() -> dict:
    """更新の依頼に対して、生成が新規作成として返した出力。"""
    return {
        "operation": "add_rule",
        "rule": {
            "id": "R05",
            "result_code": "caution",
            "conditions": {"A09": "yes"},
        },
    }


class TestUpdateIntent(unittest.TestCase):
    """依頼の文面から更新の指定を読む純粋な関数（サーバー側の整合の根拠）。"""

    def test_reads_the_rule_and_the_conditions(self) -> None:
        intent = read_update_intent(UPDATE_PROMPT)
        assert intent is not None
        self.assertEqual(intent.rule_ids, ("R04",))
        self.assertEqual(intent.conditions, {"A09": "yes"})
        intent = read_update_intent(
            "ルールR100に、設問A01がNoとA02がyesの条件を追加して"
        )
        assert intent is not None
        self.assertEqual(intent.rule_ids, ("R100",))
        self.assertEqual(intent.conditions, {"A01": "no", "A02": "yes"})

    def test_other_requests_are_not_updates(self) -> None:
        for prompt in (
            ADD_PROMPT,  # IDなしの新規追加
            "ルールR03を削除して",
            "アカウント削除のルールR03を削除して",
            "新しいルールとしてR04と同じ形でA09がYesを追加して",
            "R04を追加して",  # 条件なし
            "R04のA09がYesを確認して",  # 「追加」なし
        ):
            self.assertIsNone(read_update_intent(prompt), prompt)

    def test_new_or_similar_words_alone_do_not_make_a_new_rule(self) -> None:
        # 「新規」「新しい」だけでは、更新の依頼を新規作成にしない。
        for prompt in (
            "R04にA09がYesの条件を新規に追加して",
            "R04に新しい条件A09がYesを追加して",
            "R04にA09=Yesを追加して",
            "R04にA09はYesの条件を追加して",
        ):
            intent = read_update_intent(prompt)
            assert intent is not None, prompt
            self.assertEqual(intent.rule_ids, ("R04",))
            self.assertEqual(intent.conditions, {"A09": "yes"})

    def test_contradicting_conditions_in_the_request_are_rejected(self) -> None:
        with self.assertRaises(InvalidProposal) as caught:
            read_update_intent("R04にA09がYesとA09がNoを追加して")
        self.assertIn("食い違", str(caught.exception))


class TestValidation(unittest.TestCase):
    CURRENT = (
        rule("R01", "caution", A01="yes", A02="no"),
        rule("R04", "caution", A10="no"),
    )

    def validate(self, raw: Any, prompt: str | None = UPDATE_PROMPT, **kwargs: Any):
        return validate_rule_output(
            raw,
            kwargs.get("current", self.CURRENT),
            kwargs.get("questions", QUESTIONS),
            3,
            question_revision=7,
            prompt=prompt,
        )

    def rejected(self, raw: Any, message: str, **kwargs: Any) -> None:
        with self.assertRaises(InvalidProposal) as caught:
            self.validate(raw, **kwargs)
        self.assertIn(message, str(caught.exception))

    def test_valid_update_keeps_id_result_and_existing_conditions(self) -> None:
        proposal = self.validate(update_output())
        self.assertIsInstance(proposal, ValidatedRuleUpdate)
        self.assertEqual(proposal.base, self.CURRENT)
        self.assertEqual(proposal.base_revision, 3)
        self.assertEqual(proposal.base_question_revision, 7)
        self.assertEqual(proposal.target, self.CURRENT[1])
        self.assertEqual(
            proposal.updated,
            type(proposal.target)("R04", 2, "caution", (("A09", "yes"), ("A10", "no"))),
        )
        self.assertEqual(proposal.added_conditions, (("A09", "yes"),))
        # 並び・件数・他のルールは変わらない（新設しない）。
        self.assertEqual(
            [r.rule_id for r in proposal.after], [r.rule_id for r in self.CURRENT]
        )
        self.assertEqual(proposal.after[0], self.CURRENT[0])

    def test_rejects_a_new_rule_returned_for_an_update_request(self) -> None:
        self.rejected(add_r05_output(), "更新ではない案")
        self.rejected({"operation": "remove_rule", "rule_id": "R04"}, "更新ではない案")

    def test_rejects_a_target_or_conditions_that_differ_from_the_request(self) -> None:
        self.rejected(update_output("R01"), "更新の対象が依頼と違います")
        self.rejected(update_output(add_conditions={"A09": "no"}), "条件が依頼と違い")
        self.rejected(
            update_output(add_conditions={"A09": "yes", "A02": "yes"}),
            "条件が依頼と違い",
        )

    def test_rejects_a_new_rule_for_a_request_that_names_a_rule_but_is_unclear(
        self,
    ) -> None:
        # 更新と読めない言い回しの依頼が、新規作成に落ちない（曖昧として拒否）。
        for prompt in ("R04にA09のYesを追加して", "R04に条件を追加して"):
            self.rejected(add_r05_output(), "曖昧です", prompt=prompt)
        # 読み取れる言い回しなら更新の依頼で、新規作成の案は更新ではないとして拒否する。
        self.rejected(
            add_r05_output(), "更新ではない案", prompt="R04にA09をYesで追加して"
        )
        # 条件を足す兆候のない依頼（既存の定型依頼の形）は、曖昧とは見なさない。
        self.validate(add_r05_output(), prompt="R04を追加")
        # 新しいルールを作る意図が明らかなら、新規追加を拒否しない。
        for prompt in (
            "新しいルールとしてR04と同じ形でA09がYesを追加して",
            "R04を参考に、ルールを新規に追加して",
        ):
            self.validate(add_r05_output(), prompt=prompt)

    def test_rejects_a_new_rule_for_a_lowercase_id_update_request(self) -> None:
        # 小文字のID（r04・a09）は正式な入力ではないが、既存ルールを指す疑いがあるため、
        # 更新と読めない依頼が新規作成（R05）に落ちないよう、理由つきで拒否する。
        for prompt in LOWERCASE_ID_PROMPTS:
            with self.subTest(prompt):
                self.rejected(add_r05_output(), "反映できません", prompt=prompt)
                # 更新の案が返っても、依頼から更新と読めないので反映可能にしない。
                with self.assertRaises(InvalidProposal):
                    self.validate(update_output(), prompt=prompt)
        # 大文字のIDで定型の「追加」や明示的な新規追加は、これまで通り通る。
        self.validate(add_r05_output(), prompt="r04を追加")
        self.validate(
            add_r05_output(), prompt="新しいルールとしてr04と同じ形でA09がYesを追加して"
        )

    def test_rejects_an_update_when_the_request_does_not_name_one(self) -> None:
        self.rejected(
            update_output(), "更新案は反映できません", prompt="ルールを直して"
        )

    def test_rejects_an_ambiguous_request_with_several_rules(self) -> None:
        self.rejected(
            update_output(),
            "更新の対象が曖昧",
            prompt="R01とR04にA09がYesを追加して",
        )

    def test_rejects_a_missing_rule_or_question(self) -> None:
        prompt = "R09にA09がYesを追加して"
        self.rejected(
            update_output("R09"), "ルールID R09 は存在しません", prompt=prompt
        )
        prompt = "R04にA99がYesを追加して"
        self.rejected(
            update_output(add_conditions={"A99": "yes"}),
            "設問A99は存在しません",
            prompt=prompt,
        )

    def test_rejects_a_retired_question(self) -> None:
        questions = (*QUESTIONS[:8], question("A09", retired=True), *QUESTIONS[9:])
        self.rejected(update_output(), "設問A09は廃止済みです", questions=questions)

    def test_rejects_a_condition_that_is_already_there(self) -> None:
        current = (rule("R04", "caution", A09="yes", A10="no"),)
        self.rejected(update_output(), "追加済みです", current=current)

    def test_rejects_rewriting_an_existing_condition(self) -> None:
        current = (rule("R04", "caution", A09="no", A10="no"),)
        self.rejected(update_output(), "書き換えは扱えません", current=current)

    def test_rejects_a_result_that_would_duplicate_another_rule(self) -> None:
        current = (*self.CURRENT, rule("R05", "caution", A09="yes", A10="no"))
        self.rejected(
            update_output(), "ルールR05と同じになります（重複）", current=current
        )
        # 結果が違えば、同じ条件でも重複にしない（既存の方針）。
        other = (*self.CURRENT, rule("R05", "no_issue", A09="yes", A10="no"))
        self.validate(update_output(), current=other)

    def test_rejects_malformed_updates(self) -> None:
        self.rejected(update_output(extra={"result_code": "no_issue"}), "形式が不正")
        self.rejected(update_output(rule_id=4), "更新の対象が依頼と違います")
        self.rejected(
            update_output(add_conditions={}), "条件が依頼と違い", prompt=UPDATE_PROMPT
        )
        self.rejected(
            update_output(add_conditions={}),
            "追加する条件が空",
            prompt=None,  # 依頼との整合の検査を通さず、形式の検査だけを確かめる
        )

    def test_rejects_each_malformed_shape_on_its_own_without_a_request(self) -> None:
        # prompt=None で、依頼との照合に隠れず、形式の検査だけを確かめる。
        def with_conditions(value: Any) -> dict:
            return update_output(add_conditions=value)

        cases = {  # 理由に含まれる文言 → 拒否される生成の出力
            "ルールIDの形式が不正": [
                update_output(rule_id)
                for rule_id in ("r04", "R4", "", 4, None)  # 小文字・桁不足・空・数値
            ],
            "条件の設問IDの形式が不正": [
                with_conditions({q: "yes"}) for q in ("a09", "A9", 9)
            ],
            "yes か no だけ": [
                with_conditions({"A09": a}) for a in ("maybe", "Yes", True, None)
            ],
            "追加する条件が空": [
                with_conditions(["A09"]),
                with_conditions("A09=yes"),
                {**update_output(), "add_conditions": None},
            ],
            "形式が不正": [  # 欠落・余分な項目・dictでない出力そのもの
                {"operation": "update_rule", "add_conditions": {"A09": "yes"}},
                {"operation": "update_rule", "rule_id": "R04"},
                update_output(extra={"note": "x"}),
                None,
                [],
                "update_rule",
            ],
        }
        for message, outputs in cases.items():
            for raw in outputs:
                with self.subTest(message=message, raw=raw):
                    self.rejected(raw, message, prompt=None)
        # 形式が正しければ、依頼なしでも通る（上の拒否が形式だけを理由にしている確認）。
        self.assertIsInstance(
            self.validate(update_output(), prompt=None), ValidatedRuleUpdate
        )

    def test_adds_several_new_conditions_at_once(self) -> None:
        additions = {"A09": "yes", "A11": "no"}
        proposal = self.validate(
            update_output(add_conditions=additions),
            prompt="R04にA09がYesとA11がNoを追加して",
        )
        self.assertEqual(proposal.added_conditions, (("A09", "yes"), ("A11", "no")))
        self.assertEqual(
            proposal.updated.conditions,
            (("A09", "yes"), ("A10", "no"), ("A11", "no")),
        )
        self.assertEqual(proposal.updated.revision, 2)
        self.assertEqual(proposal.updated.result_code, "caution")
        self.assertEqual(proposal.after[0], self.CURRENT[0])  # 他のルールは同じ
        self.assertEqual(len(proposal.after), len(self.CURRENT))

    def test_rejects_the_whole_update_when_only_part_is_already_there(self) -> None:
        # R04は A10=No を持つ。新規の条件が先でも後でも、1つでも既存なら全体を拒否する。
        for additions, message in PARTLY_EXISTING:
            with self.subTest(additions):
                self.rejected(
                    update_output(add_conditions=additions),
                    message,
                    prompt=update_prompt(additions),
                )


class UpdateTestCase(RuleAdminTestCase):
    """R04（架空）を準備して、生成スタブの出力ごとに、更新の依頼を確かめる。"""

    def setUp(self) -> None:
        self.proposer = FakeRuleProposer()
        super().setUp()
        self.set_rules(rule("R04", "caution", **R04_BEFORE))

    def set_rules(self, *extra) -> None:
        # 初期ルールに、架空のR04などを足す（承認の経路を使わない準備）。
        self.rule_store._rules = (*self.rule_store.snapshot()[1], *extra)

    def update(self, output: Any = None, prompt: str = UPDATE_PROMPT):
        self.proposer.output = update_output() if output is None else output
        return self.propose(prompt)

    def rule_conditions(self, body: dict, rule_id: str) -> dict:
        return next(r for r in body["rules"] if r["rule_id"] == rule_id)["conditions"]


class TestUpdateProposal(UpdateTestCase):
    def test_example_request_updates_r04_and_does_not_create_r05(self) -> None:
        before = self.get_list()
        status, body = self.update()
        self.assertEqual(status, 201, body)
        self.assertEqual(body["kind"], "update")
        self.assertEqual(body["target"]["rule_id"], "R04")
        self.assertEqual(body["added_conditions"], {"A09": "yes"})
        ids = [*INITIAL_IDS, "R04"]
        self.assertEqual([r["rule_id"] for r in body["before"]], ids)
        self.assertEqual([r["rule_id"] for r in body["after"]], ids)  # R05はない
        r04_before = body["before"][-1]
        r04_after = body["after"][-1]
        self.assertEqual(r04_before["conditions"], R04_BEFORE)
        self.assertEqual(r04_after["conditions"], R04_AFTER)
        self.assertEqual(r04_after["result_code"], r04_before["result_code"])
        self.assertEqual(r04_after["revision"], r04_before["revision"] + 1)
        self.assertEqual(body["updated"], r04_after)
        self.assertEqual(body["before"][:3], body["after"][:3])  # 他のルールは同じ
        self.assertEqual(body["base_revision"], before["revision"])
        self.assertEqual(self.get_list(), before)  # 承認前は現行データを変えない

    def test_the_bundled_mock_also_returns_an_update_for_the_example(self) -> None:
        server = _Server(make_app(None, self.rule_store, self.question_store))
        with server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/rule-proposals",
                {"prompt": UPDATE_PROMPT},
            )
        self.assertEqual((status, body["kind"]), (201, "update"))
        self.assertEqual(body["target"]["rule_id"], "R04")
        self.assertEqual(len(body["after"]), len(body["before"]))

    def test_rejects_a_new_rule_for_an_update_request_and_keeps_the_data(self) -> None:
        before = self.get_list()
        for output in (
            add_r05_output(),
            {"operation": "remove_rule", "rule_id": "R04"},
            update_output("R01"),
            update_output(add_conditions={"A09": "no"}),
            update_output(add_conditions={"A09": "yes", "A02": "yes"}),
        ):
            status, body = self.update(output)
            self.assertEqual(status, 422, output)
            self.assertNotIn("proposal_id", body)
            self.assertEqual(self.get_list(), before, output)

    def test_rejects_with_a_reason_when_the_target_or_question_is_invalid(self) -> None:
        before = self.get_list()
        cases = (
            ("R09にA09がYesを追加して", update_output("R09"), "R09 は存在しません"),
            (
                "R04にA99がYesを追加して",
                update_output(add_conditions={"A99": "yes"}),
                "設問A99は存在しません",
            ),
            (
                "R01とR04にA09がYesを追加して",
                update_output(),
                "更新の対象が曖昧",
            ),
            ("ルールを直して", update_output(), "更新案は反映できません"),
        )
        for prompt, output, message in cases:
            status, body = self.update(output, prompt)
            self.assertEqual(status, 422, prompt)
            self.assertIn(message, body["detail"], prompt)
            self.assertEqual(self.get_list(), before, prompt)

    def test_rejects_a_request_whose_yes_and_no_contradict_over_http(self) -> None:
        # 同じ設問にYesとNoを両方指定した依頼（生成がどちらを返しても）は、422と理由で
        # 拒否し、提案を作らず、承認済みのルールも変えない。
        before = self.get_list()
        for prompt in (
            "R04にA09がYesとA09がNoを追加して",
            "R04にA09=YesとA09=Noを追加して",
        ):
            for output in (
                update_output(),
                update_output(add_conditions={"A09": "no"}),
                add_r05_output(),
            ):
                status, body = self.update(output, prompt)
                self.assertEqual(status, 422, (prompt, output))
                self.assertIn("食い違", body["detail"])
                self.assertIn("A09", body["detail"])
                self.assertNotIn("proposal_id", body)
                self.assertEqual(self.get_list(), before, prompt)

    def test_a_request_that_cannot_be_read_as_an_update_never_becomes_a_new_rule(
        self,
    ) -> None:
        # 言い回しが読み取れない（「のYes」）更新の依頼が、新規のR05に落ちない。
        before = self.get_list()
        for prompt in ("R04にA09のYesを追加して", "R04に条件を追加して"):
            status, body = self.update(add_r05_output(), prompt)
            self.assertEqual(status, 422, prompt)
            self.assertIn("曖昧", body["detail"])
            self.assertNotIn("proposal_id", body)
            self.assertEqual(self.get_list(), before, prompt)

    def test_a_lowercase_id_update_request_never_becomes_a_new_rule(self) -> None:
        before = self.get_list()
        for prompt in LOWERCASE_ID_PROMPTS:
            for output in (add_r05_output(), update_output()):
                status, body = self.update(output, prompt)
                self.assertEqual(status, 422, (prompt, output))
                self.assertTrue(body["detail"], prompt)  # 理由つき
                if output["operation"] == "add_rule":
                    self.assertIn("反映できません", body["detail"], prompt)
                self.assertNotIn("proposal_id", body)
                self.assertEqual(self.get_list(), before, prompt)
        # 明示的な新規追加と、既存の定型依頼（条件を足す兆候なし）は、これまで通り通る。
        for prompt in (
            "新しいルールとしてR04と同じ形でA09がYesを追加して",
            "R04を追加",
        ):
            status, body = self.update(add_r05_output(), prompt)
            self.assertEqual((status, body.get("kind")), (201, "add"), prompt)

    def test_the_word_new_alone_still_updates_over_http(self) -> None:
        # 「新規」「新しい」だけでは新規作成にせず、更新案になる（R05は作らない）。
        for prompt in (
            "R04にA09がYesの条件を新規に追加して",
            "R04に新しい条件A09がYesを追加して",
        ):
            status, body = self.update(update_output(), prompt)
            self.assertEqual((status, body["kind"]), (201, "update"), prompt)
            self.assertEqual(body["target"]["rule_id"], "R04")
            self.assertEqual(len(body["after"]), len(body["before"]))
        # その依頼に、生成が新規作成を返しても、反映可能な提案にしない。
        status, body = self.update(
            add_r05_output(), "R04に新しい条件A09がYesを追加して"
        )
        self.assertEqual(status, 422)
        self.assertIn("更新ではない案", body["detail"])

    def test_rejects_a_retired_question(self) -> None:
        self.add_a13()
        self.question_decision("設問A13を廃止して")
        before = self.get_list()
        status, body = self.update(
            update_output(add_conditions={"A13": "yes"}), "R04にA13がYesを追加して"
        )
        self.assertEqual(status, 422)
        self.assertIn("設問A13は廃止済みです", body["detail"])
        self.assertEqual(self.get_list(), before)

    def test_rejects_an_already_added_or_conflicting_condition(self) -> None:
        self.set_rules(rule("R05", "no_issue", A09="yes", A10="no"))
        before = self.get_list()
        status, body = self.update(
            prompt="R05にA09がYesを追加して", output=update_output("R05")
        )
        self.assertEqual((status, "追加済み" in body["detail"]), (422, True))
        status, body = self.update(
            update_output("R05", {"A09": "no"}), "R05にA09がNoを追加して"
        )
        self.assertEqual((status, "書き換え" in body["detail"]), (422, True))
        self.assertEqual(self.get_list(), before)

    def test_rejects_an_update_that_would_duplicate_another_rule(self) -> None:
        self.set_rules(rule("R05", "caution", A09="yes", A10="no"))
        before = self.get_list()
        status, body = self.update()
        self.assertEqual(status, 422)
        self.assertIn("ルールR05と同じになります（重複）", body["detail"])
        self.assertEqual(self.get_list(), before)


class TestUpdateSeveralConditions(UpdateTestCase):
    """複数の条件の同時追加（HTTP）。全件が新規なら成功し、1つでも既存なら全体を拒否する。"""

    PROMPT = "R04にA09がYesとA11がNoを追加して"

    def test_two_new_conditions_are_added_together_and_approved(self) -> None:
        before = self.get_list()
        status, body = self.update(
            update_output(add_conditions={"A09": "yes", "A11": "no"}), self.PROMPT
        )
        self.assertEqual((status, body["kind"]), (201, "update"), body)
        self.assertEqual(body["added_conditions"], {"A09": "yes", "A11": "no"})
        self.assertEqual(
            body["updated"]["conditions"], {"A09": "yes", "A10": "no", "A11": "no"}
        )
        self.assertEqual([r["rule_id"] for r in body["after"]], [*INITIAL_IDS, "R04"])
        self.assertEqual(self.get_list(), before)  # 承認前は変わらない
        status, decided = self.decide(body["proposal_id"], "yes")
        self.assertEqual((status, decided["applied"]), (200, True))
        self.assertEqual(self.ids(decided), [*INITIAL_IDS, "R04"])
        self.assertEqual(
            self.rule_conditions(decided, "R04"),
            {"A09": "yes", "A10": "no", "A11": "no"},
        )
        r04 = decided["rules"][-1]
        self.assertEqual((r04["revision"], r04["result_code"]), (2, "caution"))
        self.assertEqual(decided["rules"][:3], before["rules"][:3])
        self.assertEqual(decided["revision"], before["revision"] + 1)  # 1回の承認

    def test_partly_existing_conditions_reject_the_whole_in_either_order(self) -> None:
        # R04は A10=No を持つ。新規の条件が先でも後でも、全体を422で拒否する。
        before = self.get_list()
        for additions, reason in PARTLY_EXISTING:
            status, body = self.update(
                update_output(add_conditions=additions), update_prompt(additions)
            )
            self.assertEqual(status, 422, additions)
            self.assertIn(reason, body["detail"], additions)
            self.assertIn("A10", body["detail"], additions)  # 既存の条件を理由に示す
            self.assertNotIn("proposal_id", body)
            # 新規の条件も足されない
            self.assertEqual(self.get_list(), before, additions)


class TestUpdateDecision(UpdateTestCase):
    def test_sequential_updates_build_on_the_latest_version_under_one_id(self) -> None:
        before = self.get_list()
        _, first = self.update()
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 200)
        after_first = self.get_list()
        # 最新の基準（版2）から、2つ目の更新案を作る。
        status, second = self.update(
            update_output(add_conditions={"A11": "yes"}), "R04にA11がYesを追加して"
        )
        self.assertEqual((status, second["kind"]), (201, "update"), second)
        self.assertEqual(second["base_revision"], after_first["revision"])
        self.assertEqual(second["target"]["revision"], 2)
        self.assertEqual(second["target"]["conditions"], R04_AFTER)
        self.assertEqual(second["updated"]["revision"], 3)
        self.assertEqual(self.get_list(), after_first)  # 承認前は変わらない
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual((status, body["applied"]), (200, True))
        # 同じID・版3・既存の条件と結果を保ち、総数も変わらない（R05は作られない）。
        self.assertEqual(self.ids(body), [*INITIAL_IDS, "R04"])
        self.assertEqual(len(body["rules"]), len(before["rules"]))
        r04 = body["rules"][-1]
        self.assertEqual(r04["revision"], 3)
        self.assertEqual(r04["result_code"], "caution")
        self.assertEqual(r04["conditions"], {**R04_AFTER, "A11": "yes"})
        self.assertEqual(body["rules"][:3], before["rules"][:3])
        self.assertEqual(body["revision"], before["revision"] + 2)
        self.assertEqual(
            self.get_list(), {k: v for k, v in body.items() if k != "applied"}
        )
        # 使い切った1つ目の提案は、もう反映できない。
        final = self.get_list()
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.get_list(), final)

    def test_yes_updates_only_r04_and_keeps_the_rule_count(self) -> None:
        before = self.get_list()
        _, proposal = self.update()
        status, body = send(
            "POST",
            f"{self.base}/api/admin/rule-proposals/{proposal['proposal_id']}/decision",
            # 要求に更新内容を含めても無視する（サーバーが検証した案だけを反映する）。
            {"decision": "yes", "rule_id": "R01", "add_conditions": {"A02": "yes"}},
        )
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], True)
        self.assertEqual(self.ids(body), [*INITIAL_IDS, "R04"])  # R05は作られない
        self.assertEqual(len(body["rules"]), len(before["rules"]))
        self.assertEqual(body["revision"], before["revision"] + 1)
        self.assertEqual(body["rules"][:3], before["rules"][:3])  # 他のルールは同じ
        r04 = body["rules"][3]
        self.assertEqual(r04["conditions"], R04_AFTER)
        self.assertEqual(r04["result_code"], "caution")
        self.assertEqual(r04["revision"], 2)
        self.assertEqual(
            self.get_list(), {k: v for k, v in body.items() if k != "applied"}
        )

    def test_no_keeps_the_approved_rules(self) -> None:
        before = self.get_list()
        _, proposal = self.update()
        status, body = self.decide(proposal["proposal_id"], "no")
        self.assertEqual((status, body["applied"]), (200, False))
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 404)
        self.assertEqual(self.get_list(), before)

    def test_refuses_when_the_target_rule_changed_after_the_proposal(self) -> None:
        # 同じ基準で作った2つの更新案。先のYesで対象の版が進み、後のYesは反映しない。
        _, first = self.update()
        _, second = self.update(
            update_output(add_conditions={"A11": "no"}), "R04にA11がNoを追加して"
        )
        self.assertEqual(self.decide(first["proposal_id"], "yes")[0], 200)
        after_first = self.get_list()
        status, body = self.decide(second["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("変わった", body["detail"])
        self.assertEqual(self.get_list(), after_first)  # A11は足されていない
        self.assertEqual(self.rule_conditions(after_first, "R04"), R04_AFTER)
        self.assertEqual(self.decide(second["proposal_id"], "yes")[0], 404)

    def test_refuses_when_the_target_rule_was_removed_after_the_proposal(self) -> None:
        _, update = self.update()
        self.proposer.output = {"operation": "remove_rule", "rule_id": "R04"}
        _, removal = self.propose("ルールR04を削除して")
        self.assertEqual(self.decide(removal["proposal_id"], "yes")[0], 200)
        after = self.get_list()
        self.assertEqual(self.ids(after), INITIAL_IDS)
        self.assertEqual(self.decide(update["proposal_id"], "yes")[0], 409)
        self.assertEqual(self.get_list(), after)  # R04が復活も新設もされない

    def test_refuses_when_a_question_changed_after_the_proposal(self) -> None:
        _, proposal = self.update()
        self.add_a13()
        before = self.get_list()
        status, body = self.decide(proposal["proposal_id"], "yes")
        self.assertEqual(status, 409)
        self.assertIn("設問の一覧が提案の後に変わった", body["detail"])
        self.assertEqual(self.get_list(), before)
        self.assertEqual(self.rule_conditions(before, "R04"), R04_BEFORE)

    def test_an_updated_condition_blocks_retiring_its_question(self) -> None:
        # 更新で足した条件が参照する設問は、廃止できない（参照切れのルールを残さない）。
        self.add_a13()
        _, proposal = self.update(
            update_output(add_conditions={"A13": "yes"}), "R04にA13がYesを追加して"
        )
        self.assertEqual(self.decide(proposal["proposal_id"], "yes")[0], 200)
        status, retire = send(
            "POST",
            f"{self.base}/api/admin/question-proposals",
            {"prompt": "設問A13を廃止して"},
        )
        self.assertEqual(status, 201)
        self.assertIn("R04", [r["rule_id"] for r in retire["references"]])
        self.assertIs(retire["can_retire"], False)


class TestUpdateWithTemplateReuse(unittest.TestCase):
    """定型依頼の再利用が有効な構成でも、更新の対象と条件を検証し、AIを呼ばない。"""

    def app_with(self, templates, output=None):
        app = admin_app(rule_admin, templates, output)
        app.state.rule_store._rules = (
            *app.state.rule_store.snapshot()[1],
            rule("R04", "caution", **R04_BEFORE),
            # 初期のルールは全設問を持つので、更新が成り立つ別のルールも用意する。
            rule("R05", "no_issue", A01="yes"),
        )
        return app

    def test_a_template_update_is_validated_and_never_calls_the_ai(self) -> None:
        templates = DictReuse({("rule", UPDATE_PROMPT): update_output()})
        app = self.app_with(templates, add_r05_output())
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            status, body = post(base, UPDATE_PROMPT)
            self.assertEqual(status, 201, body)
            self.assertEqual(body["source"], {"kind": "template"})
            self.assertEqual(body["kind"], "update")
            self.assertEqual(body["target"]["rule_id"], "R04")
            self.assertEqual(body["updated"]["conditions"], R04_AFTER)
            self.assertEqual(decide(base, body, "yes")[0], 200)
        self.assertEqual(app.state.rule_proposer.calls, 0)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))
        rules = app.state.rule_store.snapshot()[1]
        self.assertEqual([r.rule_id for r in rules], [*INITIAL_IDS, "R04", "R05"])
        self.assertEqual(dict(rules[3].conditions), R04_AFTER)
        self.assertEqual(dict(rules[4].conditions), {"A01": "yes"})  # R05は変わらない

    def test_a_template_that_does_not_match_its_request_is_rejected(self) -> None:
        bad = {
            "新規として保存": add_r05_output(),
            "別のルール": update_output("R05"),
            "別の条件": update_output(add_conditions={"A09": "no"}),
        }
        for label, operation in bad.items():
            with self.subTest(label):
                templates = DictReuse({("rule", UPDATE_PROMPT): operation})
                app = self.app_with(templates, update_output())
                before = app.state.rule_store.snapshot()
                with _Server(app) as server:
                    status, body = post(
                        server.base_url + "/api/admin/rule-proposals", UPDATE_PROMPT
                    )
                self.assertEqual(status, 422, body)
                self.assertEqual(app.state.rule_proposer.calls, 0)  # AIへ渡さない
                self.assertEqual(app.state.rule_store.snapshot(), before)

    def test_a_template_update_for_a_missing_target_is_rejected(self) -> None:
        prompt = "R09にA09がYesを追加して"
        templates = DictReuse({("rule", prompt): update_output("R09")})
        app = self.app_with(templates)
        with _Server(app) as server:
            status, body = post(server.base_url + "/api/admin/rule-proposals", prompt)
        self.assertEqual(status, 422)
        self.assertIn("R09 は存在しません", body["detail"])
        self.assertEqual(app.state.rule_proposer.calls, 0)

    def test_yes_registers_the_update_and_reuse_checks_the_current_rules(self) -> None:
        templates = InMemoryProposalTemplates(initial=False)
        stub = ScriptedRules("bedrock", {UPDATE_PROMPT: update_output()})
        app = auto_app(templates, rule_proposer=stub)
        app.state.rule_store._rules = (
            *app.state.rule_store.snapshot()[1],
            rule("R04", "caution", **R04_BEFORE),
        )
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, first = post(base, UPDATE_PROMPT)
            self.assertEqual(first["source"], {"kind": "bedrock"})
            self.assertEqual(decide(base, first, "yes")[0], 200)
            # 反映した操作が、IDと追加した条件のまま、定型文になる。
            self.assertEqual(templates.lookup("rule", UPDATE_PROMPT), update_output())
            # 同じ依頼はAIを呼ばず、現行のルールで検証し直す（追加済みなので拒否）。
            before = app.state.rule_store.snapshot()
            status, body = post(base, UPDATE_PROMPT)
            self.assertEqual(status, 422, body)
            self.assertIn("追加済み", body["detail"])
            self.assertEqual(app.state.rule_store.snapshot(), before)
        self.assertEqual(stub.calls, 1)

    def test_a_lowercase_id_template_or_ai_add_never_becomes_a_new_rule(self) -> None:
        for prompt in LOWERCASE_ID_PROMPTS:
            for label, templates, force_ai in (
                ("template", DictReuse({("rule", prompt): add_r05_output()}), False),
                ("force_ai", DictReuse({}), True),
            ):
                with self.subTest((prompt, label)):
                    app = self.app_with(templates, add_r05_output())
                    before = app.state.rule_store.snapshot()
                    with _Server(app) as server:
                        status, body = post(
                            server.base_url + "/api/admin/rule-proposals",
                            prompt,
                            force_ai=force_ai,
                        )
                    self.assertEqual(status, 422, body)
                    self.assertIn("反映できません", body["detail"])
                    self.assertNotIn("proposal_id", body)
                    self.assertEqual(app.state.rule_store.snapshot(), before)

    def test_force_ai_returning_a_new_rule_is_still_rejected(self) -> None:
        templates = DictReuse({("rule", UPDATE_PROMPT): update_output()})
        app = self.app_with(templates, add_r05_output())
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            status, body = post(base, UPDATE_PROMPT, force_ai=True)
            self.assertEqual(status, 422, body)
            self.assertIn("更新ではない案", body["detail"])
        self.assertEqual(app.state.rule_proposer.calls, 1)
        self.assertEqual(len(app.state.rule_store.snapshot()[1]), 5)  # 増えていない


if __name__ == "__main__":
    unittest.main()
