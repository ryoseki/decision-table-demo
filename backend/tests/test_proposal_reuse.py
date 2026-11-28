import http.client
import unittest
from uuid import uuid4

from fastapi import FastAPI

from app import question_admin, rule_admin, template_admin
from app.domain import GenerationSource
from app.question_store import InMemoryQuestionStore
from app.quota_store import InMemoryAiQuota
from app.rule_store import InMemoryRuleStore
from app.template_memory import InMemoryProposalTemplates
from tests.test_api_contract import ContractStore
from tests.test_intake import _Server, send


class TestProposalReuse(unittest.TestCase):
    def test_exact_template_bypasses_generation_and_quota(self):
        class Proposer:
            source = GenerationSource("bedrock")
            calls = 0

            def generate(self, prompt, current):
                self.calls += 1
                return {}

        class Reuse:
            def lookup(self, target, prompt):
                if (target, prompt) == ("question", "設問を追加して"):
                    return {
                        "operation": "add_question",
                        "question": {"id": "A13", "text": "架空の追加設問"},
                    }
                return None

        app = FastAPI()
        app.include_router(question_admin.router)
        app.state.question_store = InMemoryQuestionStore()
        app.state.question_proposer = Proposer()
        app.state.proposal_reuse = Reuse()
        with _Server(app) as server:
            path = server.base_url + "/api/admin/question-proposals"
            status, body = send("POST", path, {"prompt": " 設問を追加して "})
            self.assertEqual(status, 201, body)
            self.assertEqual(body["source"], {"kind": "template"})
            self.assertEqual(send("POST", path, {"prompt": "\t設問を追加して"})[0], 503)
        self.assertEqual(app.state.question_proposer.calls, 0)

    def test_fixture_comparison_and_explicit_ai_retry(self):
        class Proposer:
            source = GenerationSource("bedrock")
            calls = 0

            def generate(self, prompt, current):
                self.calls += 1
                return {
                    "operation": "add_question",
                    "question": {"id": "A13", "text": "架空のAI案"},
                }

        class Questions(InMemoryQuestionStore):
            def register_for_reuse(self, proposal, prompt):
                return self.register(proposal)

        class Reuse:
            def lookup(self, target, prompt):
                if target == "question" and prompt in ("追加する", "設問を追加して"):
                    return {
                        "operation": "add_question",
                        "question": {"id": "A13", "text": "架空の定型案"},
                    }
                return None

        fixtures = [
            ("追加する", True),
            (" 設問を追加して ", True),
            ("追加しない", False),
            ("	設問を追加して", False),
        ]
        for enabled, expected_calls in ((False, 4), (True, 2)):
            app = FastAPI()
            app.include_router(question_admin.router)
            app.state.question_store = Questions()
            app.state.question_proposer = Proposer()
            app.state.ai_quota = InMemoryAiQuota(b"x" * 32)
            if enabled:
                app.state.proposal_reuse = Reuse()
            with _Server(app) as server:
                path = server.base_url + "/api/admin/question-proposals"

                def submit(prompt, *, url=path, **kwargs):
                    return send(
                        "POST",
                        url,
                        {"prompt": prompt, **kwargs},
                        {
                            "X-Demo-Viewer-Ip": "192.0.2.1",
                            "Idempotency-Key": str(uuid4()),
                        },
                    )

                wrong = 0
                last_template_id = ""
                for prompt, expected_template in fixtures:
                    status, body = submit(prompt)
                    self.assertEqual(status, 201, body)
                    if body["source"]["kind"] == "template":
                        wrong += int(not expected_template)
                        self.assertEqual(body["added"]["text"], "架空の定型案")
                        last_template_id = body["proposal_id"]
                self.assertEqual(app.state.question_proposer.calls, expected_calls)
                self.assertEqual(wrong, 0)
                if not enabled:
                    continue
                self.assertEqual(
                    send(
                        "POST",
                        path + f"/{last_template_id}/decision",
                        {"decision": "no"},
                    )[0],
                    200,
                )
                self.assertEqual(app.state.question_proposer.calls, 2)
                status, body = submit("設問を追加して", force_ai=True)
                self.assertEqual(status, 201, body)
                self.assertEqual(body["source"]["kind"], "bedrock")
                self.assertEqual(app.state.question_proposer.calls, 3)

    def test_no_and_unregistered_sources_do_not_change_templates(self):
        # Noでは定型文を変えない（旧仕様の「登録候補」も出ない）。
        templates = InMemoryProposalTemplates(initial=False)
        app = auto_app(templates, ScriptedQuestions("bedrock"))
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            _, body = post(base, "架空の依頼")
            self.assertNotIn("reusable", body)
            status, result = send(
                "POST", base + f"/{body['proposal_id']}/decision", {"decision": "no"}
            )
            self.assertEqual(status, 200)
            self.assertNotIn("reusable", result)
        self.assertEqual(templates.list_prompts("question"), [])

    def test_rule_template_with_missing_reference_is_rejected_without_ai(self):
        class Reuse:
            def lookup(self, target, prompt):
                if (target, prompt) == ("rule", "架空ルール追加"):
                    return {
                        "operation": "add_rule",
                        "rule": {
                            "id": "R04",
                            "result_code": "caution",
                            "conditions": {"A99": "yes"},
                        },
                    }
                return None

        class Proposer:
            source = GenerationSource("bedrock")

        app = FastAPI()
        app.include_router(rule_admin.router)
        app.state.question_store = InMemoryQuestionStore()
        app.state.rule_store = InMemoryRuleStore()
        app.state.rule_proposer = Proposer()
        app.state.proposal_reuse = Reuse()
        with _Server(app) as server:
            path = server.base_url + "/api/admin/rule-proposals"
            status, body = send("POST", path, {"prompt": "架空ルール追加"})
            self.assertEqual(status, 422)
            self.assertIn("A99", body["detail"])
            self.assertEqual(
                send("POST", path, {"prompt": "R01の条件からA99を外して"})[0], 503
            )

    def _reuse_app(self, router, templates):
        class Reuse:
            def lookup(self, target, prompt):
                return templates.get((target, prompt))

        class Unused:
            source = GenerationSource("bedrock")
            calls = 0

            def generate(self, *args):
                self.calls += 1
                return {}

        app = FastAPI()
        app.include_router(router)
        app.state.question_store = InMemoryQuestionStore()
        app.state.rule_store = InMemoryRuleStore()
        app.state.question_proposer = Unused()
        app.state.rule_proposer = Unused()
        app.state.proposal_reuse = Reuse()
        return app

    def test_question_add_template_uses_current_next_id_not_saved_id(self):
        templates = {
            # 承認時のIDを持つ既存形式の行（A13）と、IDを持たない新形式の行。
            ("question", "追加1"): {
                "operation": "add_question",
                "question": {"id": "A13", "text": "架空の追加設問1"},
            },
            ("question", "追加2"): {
                "operation": "add_question",
                "question": {"text": "架空の追加設問2"},
            },
        }
        app = self._reuse_app(question_admin.router, templates)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            status, first = send("POST", base, {"prompt": "追加1"})
            self.assertEqual((status, first["added"]["id"]), (201, "A13"))
            decide = base + "/" + first["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
            # 次番号はA14。保存時のID（A13）では登録しない。
            status, second = send("POST", base, {"prompt": "追加2"})
            self.assertEqual((status, second["added"]["id"]), (201, "A14"))
            self.assertEqual(second["source"], {"kind": "template"})
            # 取り込み済みの文言は、古いIDを持つ行でも重複として拒否する。
            self.assertEqual(send("POST", base, {"prompt": "追加1"})[0], 422)
            decide = base + "/" + second["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
        ids = [q.id for q in app.state.question_store.snapshot()[1]]
        self.assertEqual(ids[-2:], ["A13", "A14"])
        self.assertEqual(app.state.question_proposer.calls, 0)

    def test_rule_add_template_uses_current_next_id_not_saved_id(self):
        templates = {
            ("rule", "追加1"): {
                "operation": "add_rule",
                "rule": {
                    "id": "R04",
                    "result_code": "caution",
                    "conditions": {"A01": "yes", "A02": "no"},
                },
            },
            ("rule", "追加2"): {
                "operation": "add_rule",
                "rule": {
                    "result_code": "no_issue",
                    "conditions": {"A01": "no", "A02": "yes"},
                },
            },
        }
        app = self._reuse_app(rule_admin.router, templates)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            status, first = send("POST", base, {"prompt": "追加1"})
            self.assertEqual((status, first["added"]["rule_id"]), (201, "R04"))
            decide = base + "/" + first["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
            status, second = send("POST", base, {"prompt": "追加2"})
            self.assertEqual((status, second["added"]["rule_id"]), (201, "R05"))
            self.assertEqual(send("POST", base, {"prompt": "追加1"})[0], 422)
            decide = base + "/" + second["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
        ids = [r.rule_id for r in app.state.rule_store.snapshot()[1]]
        self.assertEqual(ids[-2:], ["R04", "R05"])
        self.assertEqual(app.state.rule_proposer.calls, 0)

    def test_stale_saved_id_is_ignored_after_that_id_is_taken(self):
        # 保存IDが、現行の次番号と食い違う行（旧形式）。保存IDで組むと重複で拒否される。
        questions = {
            ("question", "古い1"): {
                "operation": "add_question",
                "question": {"id": "A13", "text": "架空の古い設問1"},
            },
            ("question", "古い2"): {
                "operation": "add_question",
                "question": {"id": "A13", "text": "架空の古い設問2"},
            },
        }
        app = self._reuse_app(question_admin.router, questions)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            for prompt, expected in (("古い1", "A13"), ("古い2", "A14")):
                status, body = send("POST", base, {"prompt": prompt})
                self.assertEqual((status, body["added"]["id"]), (201, expected))
                decide = base + "/" + body["proposal_id"] + "/decision"
                self.assertEqual(send("POST", decide, yes)[0], 200)

        rules = {
            ("rule", "古い1"): {
                "operation": "add_rule",
                "rule": {
                    "id": "R04",
                    "result_code": "caution",
                    "conditions": {"A01": "yes", "A02": "no"},
                },
            },
            ("rule", "古い2"): {
                "operation": "add_rule",
                "rule": {
                    "id": "R04",
                    "result_code": "no_issue",
                    "conditions": {"A01": "no", "A02": "yes"},
                },
            },
        }
        app = self._reuse_app(rule_admin.router, rules)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            for prompt, expected in (("古い1", "R04"), ("古い2", "R05")):
                status, body = send("POST", base, {"prompt": prompt})
                self.assertEqual((status, body["added"]["rule_id"]), (201, expected))
                decide = base + "/" + body["proposal_id"] + "/decision"
                self.assertEqual(send("POST", decide, yes)[0], 200)

    def test_rule_next_id_skips_ids_of_removed_rules(self):
        rules = {
            ("rule", "削除"): {"operation": "remove_rule", "rule_id": "R03"},
            ("rule", "追加"): {
                "operation": "add_rule",
                "rule": {
                    "result_code": "caution",
                    "conditions": {"A01": "yes", "A02": "no"},
                },
            },
        }
        app = self._reuse_app(rule_admin.router, rules)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, removal = send("POST", base, {"prompt": "削除"})
            decide = base + "/" + removal["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
            _, added = send("POST", base, {"prompt": "追加"})
            # 削除したルールの番号は、現行の最大でなくても再利用しない。
            self.assertNotEqual(added["added"]["rule_id"], "R03")

    def test_receipt_operation_does_not_keep_assigned_id(self):
        from app.template_store import operation_from_receipt

        question = operation_from_receipt(
            "question", {"op": "add", "id": "A13", "text": "架空"}
        )
        rule = operation_from_receipt(
            "rule",
            {
                "op": "add",
                "rule": {"rule_id": "R04", "result_code": "caution", "conditions": {}},
            },
        )
        self.assertEqual(question["question"], {"text": "架空"})
        self.assertNotIn("id", rule["rule"])
        self.assertEqual(
            operation_from_receipt("question", {"op": "retire", "target_id": "A13"}),
            {"operation": "retire_question", "question_id": "A13"},
        )


class DictReuse:
    """初期定型文などの組を、完全一致で返すだけのテスト用の照合（保存先は持たない）。"""

    def __init__(self, templates):
        self.templates = dict(templates)

    def lookup(self, target, prompt):
        return self.templates.get((target, prompt))


def initial_reuse():
    from app.template_store import INITIAL_TEMPLATES

    return DictReuse({(target, prompt): op for target, prompt, op in INITIAL_TEMPLATES})


class CountingProposer:
    source = GenerationSource("bedrock")

    def __init__(self, output):
        self.output = output
        self.calls = 0

    def generate(self, *args):
        self.calls += 1
        return self.output


A13_ADD = {"operation": "add_question", "question": {"id": "A13", "text": "架空"}}
RULE_ADD = {
    "operation": "add_rule",
    "rule": {
        "id": "R04",
        "result_code": "caution",
        "conditions": {"A01": "yes", "A02": "no"},
    },
}
VIEWER = {"X-Demo-Viewer-Ip": "192.0.2.1"}


class ScriptedQuestions:
    """設問の生成のスタブ。呼び出し回数を数え、依頼文ごとの出力（関数も可）を返す。"""

    def __init__(self, kind, outputs=None):
        self.source = GenerationSource(kind)
        self.outputs = outputs or {}
        self.calls = 0

    def generate(self, prompt, current):
        self.calls += 1
        output = self.outputs.get(prompt)
        if callable(output):
            return output(current)
        if output is not None:
            return output
        number = len(current) + 1
        text = f"架空の設問 {prompt} {self.calls}"
        return {
            "operation": "add_question",
            "question": {"id": f"A{number:02}", "text": text},
        }


class ScriptedRules:
    """ルールの生成のスタブ。既定は、次のIDで、重複しない条件のルールを追加する。"""

    def __init__(self, kind, outputs=None):
        self.source = GenerationSource(kind)
        self.outputs = outputs or {}
        self.calls = 0

    def generate(self, prompt, current, next_id, questions):
        self.calls += 1
        output = self.outputs.get(prompt)
        if output is not None:
            return output
        third = "yes" if self.calls % 2 else "no"
        conditions = {"A01": "yes", "A02": "no", "A03": third}
        return {
            "operation": "add_rule",
            "rule": {"id": next_id, "result_code": "caution", "conditions": conditions},
        }


def auto_app(templates, question_proposer=None, rule_proposer=None):
    """Yesで反映したとき、定型文へ自動登録するDBなしの構成（保存先はメモリ）。"""
    app = FastAPI()
    app.include_router(question_admin.router)
    app.include_router(rule_admin.router)
    app.include_router(template_admin.router)
    app.state.question_store = InMemoryQuestionStore(templates)
    app.state.rule_store = InMemoryRuleStore(templates)
    app.state.question_proposer = question_proposer or ScriptedQuestions("mock")
    app.state.rule_proposer = rule_proposer or ScriptedRules("mock")
    app.state.proposal_reuse = templates
    app.state.ai_quota = InMemoryAiQuota(b"x" * 32)
    app.state.case_store = ContractStore()
    return app


def admin_app(router_module, templates, proposer_output):
    app = FastAPI()
    app.include_router(router_module.router)
    app.state.question_store = InMemoryQuestionStore()
    app.state.rule_store = InMemoryRuleStore()
    app.state.question_proposer = CountingProposer(proposer_output)
    app.state.rule_proposer = CountingProposer(proposer_output)
    app.state.proposal_reuse = templates
    app.state.ai_quota = InMemoryAiQuota(b"x" * 32)
    app.state.case_store = ContractStore()  # 設問の廃止は、ルールと案件を読んで確認する
    return app


def post(url, prompt, **extra):
    headers = {**VIEWER, "Idempotency-Key": str(uuid4())}
    return send("POST", url, {"prompt": prompt, **extra}, headers)


class TestInitialTemplatesAndNegatives(unittest.TestCase):
    def test_initial_rule_template_never_calls_bedrock_or_uses_quota(self):
        app = admin_app(rule_admin, initial_reuse(), RULE_ADD)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            status, body = post(base, "Remove rule R03")
            self.assertEqual(status, 201, body)
            self.assertEqual(body["source"], {"kind": "template"})
            self.assertEqual(body["removed"]["rule_id"], "R03")
            decide = base + "/" + body["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, {"decision": "yes"})[0], 200)
        self.assertEqual(app.state.rule_proposer.calls, 0)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))
        self.assertNotIn("R03", [r.rule_id for r in app.state.rule_store.snapshot()[1]])

    def test_initial_question_template_never_calls_bedrock_or_uses_quota(self):
        app = admin_app(question_admin, initial_reuse(), A13_ADD)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            # A13がない状態でも、Bedrockへ渡さず、操作できない理由を返す。
            status, body = post(base, "Deactivate question A13")
            self.assertEqual(status, 422, body)
            self.assertIn("A13", body["detail"])
            self.assertEqual(app.state.question_proposer.calls, 0)
            # AI案でA13を追加して反映した後は、定型文でも廃止案になる。
            status, added = post(base, "設問を追加して")
            self.assertEqual((status, added["source"]["kind"]), (201, "bedrock"))
            decide = base + "/" + added["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, {"decision": "yes"})[0], 200)
            status, body = post(base, "Deactivate question A13")
            self.assertEqual(status, 201, body)
            self.assertEqual(body["source"], {"kind": "template"})
            self.assertEqual(body["retired"]["id"], "A13")
        self.assertEqual(app.state.question_proposer.calls, 1)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (1, 1))

    def test_similar_but_different_requests_are_not_matched(self):
        # 廃止／削除と取り違えやすい、意味の違う依頼や、対象違い・表記違い。
        cases = {
            "question": (
                question_admin,
                "/api/admin/question-proposals",
                [
                    "設問A13を廃止しないで",
                    "設問A13の条件を編集して",
                    "ルールR03のA13の条件を外して",
                    "設問A13を廃止して。",
                    "設問a13を廃止して",
                    "　設問A13を廃止して",
                    "Remove rule R03",  # 別の対象の定型文
                ],
                A13_ADD,
            ),
            "rule": (
                rule_admin,
                "/api/admin/rule-proposals",
                [
                    "ルールR03を削除しないで",
                    "ルールR03のA04の条件を編集して",
                    "ルールR03からA04の条件を外して",
                    "ルールR03を削除して。",
                    "ルールr03を削除して",
                    "Deactivate question A13",  # 別の対象の定型文
                ],
                RULE_ADD,
            ),
        }
        for target, (module, path, prompts, output) in cases.items():
            for prompt in prompts:
                with self.subTest(target=target, prompt=prompt):
                    app = admin_app(module, initial_reuse(), output)
                    with _Server(app) as server:
                        status, body = post(server.base_url + path, prompt)
                    self.assertEqual(status, 201, body)
                    self.assertEqual(body["source"], {"kind": "bedrock"})
                    self.assertNotIn("removed", body)
                    self.assertNotIn("retired", body)
                    proposer = getattr(app.state, f"{target}_proposer")
                    self.assertEqual(proposer.calls, 1)

    def test_template_already_applied_is_rejected_with_reason_and_no_second_change(
        self,
    ):
        app = admin_app(rule_admin, initial_reuse(), RULE_ADD)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, first = post(base, "Remove rule R03")
            decide = base + "/" + first["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, yes)[0], 200)
            revision = app.state.rule_store.snapshot()[0]
            status, body = post(base, "Remove rule R03")
            self.assertEqual(status, 422, body)
            self.assertIn("R03", body["detail"])
            self.assertEqual(app.state.rule_store.snapshot()[0], revision)
            # 一度使った提案は、もう一度Yesを送っても反映しない。
            self.assertEqual(send("POST", decide, yes)[0], 404)
            self.assertEqual(app.state.rule_store.snapshot()[0], revision)
        self.assertEqual(app.state.rule_proposer.calls, 0)

    def test_rule_template_proposal_is_rejected_409_when_the_base_changed(self):
        templates = DictReuse(
            {
                ("rule", "R03を削除"): {"operation": "remove_rule", "rule_id": "R03"},
                ("rule", "R04を追加"): RULE_ADD,
            }
        )
        app = admin_app(rule_admin, templates, RULE_ADD)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, stale = post(base, "R03を削除")
            _, other = post(base, "R04を追加")
            self.assertEqual(
                send("POST", base + "/" + other["proposal_id"] + "/decision", yes)[0],
                200,
            )
            revision, rules_now = app.state.rule_store.snapshot()
            status, body = send(
                "POST", base + "/" + stale["proposal_id"] + "/decision", yes
            )
            self.assertEqual(status, 409, body)
            self.assertEqual(app.state.rule_store.snapshot(), (revision, rules_now))
            self.assertIn("R03", [r.rule_id for r in rules_now])

    def test_question_template_proposal_is_rejected_409_when_the_base_changed(self):
        templates = DictReuse(
            {
                ("question", "追加A"): {
                    "operation": "add_question",
                    "question": {"text": "架空の設問A"},
                },
                ("question", "追加B"): {
                    "operation": "add_question",
                    "question": {"text": "架空の設問B"},
                },
            }
        )
        app = admin_app(question_admin, templates, A13_ADD)
        yes = {"decision": "yes"}
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            _, first = post(base, "追加A")
            _, second = post(base, "追加B")
            self.assertEqual(
                send("POST", base + "/" + first["proposal_id"] + "/decision", yes)[0],
                200,
            )
            before = app.state.question_store.snapshot()
            status, _ = send(
                "POST", base + "/" + second["proposal_id"] + "/decision", yes
            )
            self.assertEqual(status, 409)
            self.assertEqual(app.state.question_store.snapshot(), before)
        self.assertEqual(app.state.question_proposer.calls, 0)

    def test_no_on_a_rule_template_does_not_call_ai_and_explicit_retry_does(self):
        app = admin_app(rule_admin, initial_reuse(), RULE_ADD)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, body = post(base, "Remove rule R03")
            before = app.state.rule_store.snapshot()
            decide = base + "/" + body["proposal_id"] + "/decision"
            self.assertEqual(send("POST", decide, {"decision": "no"})[0], 200)
            self.assertEqual(app.state.rule_store.snapshot(), before)
            self.assertEqual(app.state.rule_proposer.calls, 0)
            status, again = post(base, "Remove rule R03", force_ai=True)
            self.assertEqual(status, 201, again)
            self.assertEqual(again["source"], {"kind": "bedrock"})
        self.assertEqual(app.state.rule_proposer.calls, 1)


class TestTemplateAdminContract(unittest.TestCase):
    def test_list_and_delete_only_with_no_manual_registration_route(self):
        templates = InMemoryProposalTemplates(initial=False)
        templates.save("question", "依頼1", {"operation": "retire_question"})
        app = FastAPI()
        app.include_router(template_admin.router)
        app.state.proposal_reuse = templates
        with _Server(app) as server:
            path = server.base_url + "/api/admin/proposal-templates"
            self.assertEqual(send("GET", path + "/question")[1], {"prompts": ["依頼1"]})
            # 別の対象の一覧には出ない。未知の対象は422。
            self.assertEqual(send("GET", path + "/rule")[1], {"prompts": []})
            self.assertEqual(send("GET", path + "/other")[0], 422)
            # 手動の登録は、もうない（反映したときの自動登録だけ）。
            self.assertEqual(
                send("POST", path + "/question", {"proposal_id": "q1"})[0], 405
            )
            self.assertEqual(
                send("POST", path + "/question/remove", {"prompt": "依頼1", "x": 1})[1],
                {"prompts": []},
            )
            # 削除済み・未登録の文言の削除も、エラーにしない。
            status, body = send("POST", path + "/question/remove", {"prompt": "依頼1"})
            self.assertEqual((status, body), (200, {"prompts": []}))
            # 他の対象の同じ文言は、削除に巻き込まない。
            templates.save("rule", "依頼1", {"operation": "remove_rule"})
            send("POST", path + "/question/remove", {"prompt": "依頼1"})
            self.assertEqual(templates.list_prompts("rule"), ["依頼1"])


def decide(base, body, decision):
    url = f"{base}/{body['proposal_id']}/decision"
    return send("POST", url, {"decision": decision})


class TestMalformedStoredOperations(unittest.TestCase):
    def test_malformed_stored_operation_is_422_without_bedrock_or_quota(self):
        # 保存済みの操作が壊れていても、AIへは渡さず、通常の検証で422にする。
        malformed = [
            "not-an-object",
            {},
            {"operation": "unknown_operation"},
            {"operation": "retire_question"},  # 対象なし
            {"operation": "retire_question", "question_id": 13},
            {"operation": "remove_rule", "rule_id": ["R01"]},
            {"operation": "add_question", "question": "文字列"},
            {"operation": "add_question", "question": {"text": ""}},
            {"operation": "add_rule", "rule": None},
            {"operation": "add_rule", "rule": {"result_code": "nope"}},
        ]
        modules = (
            ("question", question_admin, "/api/admin/question-proposals"),
            ("rule", rule_admin, "/api/admin/rule-proposals"),
        )
        for target, module, path in modules:
            for operation in malformed:
                with self.subTest(target=target, operation=operation):
                    app = admin_app(
                        module,
                        DictReuse({(target, "壊れた定型"): operation}),
                        A13_ADD if target == "question" else RULE_ADD,
                    )
                    question_revision = app.state.question_store.snapshot()[0]
                    rule_revision = app.state.rule_store.snapshot()[0]
                    with _Server(app) as server:
                        status, body = post(server.base_url + path, "壊れた定型")
                    self.assertEqual(status, 422, body)
                    proposer = getattr(app.state, f"{target}_proposer")
                    self.assertEqual(proposer.calls, 0)
                    self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))
                    # 拒否したとき、何も変わらない。
                    self.assertEqual(
                        app.state.question_store.snapshot()[0], question_revision
                    )
                    self.assertEqual(app.state.rule_store.snapshot()[0], rule_revision)


class TestAutoRegistration(unittest.TestCase):
    def test_yes_registers_for_every_source_and_reuse_makes_zero_calls(self):
        for kind in ("mock", "bedrock"):
            with self.subTest(source=kind):
                templates = InMemoryProposalTemplates(initial=False)
                proposer = ScriptedQuestions(kind)
                app = auto_app(templates, proposer)
                with _Server(app) as server:
                    base = server.base_url + "/api/admin/question-proposals"
                    _, first = post(base, " 架空の依頼 ")  # 前後の半角スペースは除く
                    self.assertEqual(first["source"]["kind"], kind)
                    self.assertEqual(templates.list_prompts("question"), [])
                    self.assertEqual(decide(base, first, "yes")[0], 200)
                    text = first["added"]["text"]
                    # IDは保存せず、反映した文言だけを操作として保存する。
                    self.assertEqual(
                        templates.lookup("question", "架空の依頼"),
                        {"operation": "add_question", "question": {"text": text}},
                    )
                    self.assertEqual(proposer.calls, 1)
                    # 同じ依頼は、Bedrockを呼ばずアプリ内で解釈し、現行データで検証し
                    # 直す（同じ文言の設問は既にあるので、重複として拒否する）。
                    status, body = post(base, "架空の依頼")
                    self.assertEqual(status, 422, body)
                    self.assertEqual(proposer.calls, 1)

    def test_template_source_yes_keeps_one_row_and_force_ai_replaces_the_operation(
        self,
    ):
        templates = InMemoryProposalTemplates(initial=False)
        proposer = ScriptedQuestions("bedrock")
        app = auto_app(templates, proposer)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            _, first = post(base, "依頼X")
            decide(base, first, "yes")
            old = templates.lookup("question", "依頼X")
            # 別の依頼で、同じ設問は増やさず、定型文だけを確認する。
            templates.save(
                "question",
                "依頼T",
                {"operation": "add_question", "question": {"text": "架空の定型の設問"}},
            )
            status, via_template = post(base, "依頼T")
            kind = via_template["source"]["kind"]
            self.assertEqual((status, kind), (201, "template"))
            self.assertEqual(decide(base, via_template, "yes")[0], 200)
            self.assertEqual(
                templates.lookup("question", "依頼T")["question"],
                {"text": "架空の定型の設問"},
            )
            self.assertEqual(templates.list_prompts("question").count("依頼T"), 1)
            # 「AIで再提案」で同じ依頼から別の案を反映すると、操作が置き換わる。
            status, retry = post(base, "依頼X", force_ai=True)
            self.assertEqual((status, retry["source"]["kind"]), (201, "bedrock"))
            self.assertEqual(decide(base, retry, "yes")[0], 200)
            new = templates.lookup("question", "依頼X")
            self.assertNotEqual(new, old)
            self.assertEqual(new["question"]["text"], retry["added"]["text"])
            self.assertEqual(templates.list_prompts("question").count("依頼X"), 1)

    def test_no_conflict_and_blocked_apply_do_not_register(self):
        templates = InMemoryProposalTemplates(initial=False)
        retire_a01 = {"operation": "retire_question", "question_id": "A01"}
        app = auto_app(
            templates,
            ScriptedQuestions("bedrock", {"A01廃止": retire_a01}),
            ScriptedRules("bedrock"),
        )
        with _Server(app) as server:
            base = server.base_url + "/api/admin/question-proposals"
            # No: 反映しないので登録しない。
            _, declined = post(base, "Noの依頼")
            self.assertEqual(decide(base, declined, "no")[0], 200)
            # 版の競合（先に別の提案が反映された）: 反映できないので登録しない。
            _, winner = post(base, "先に反映する依頼")
            _, stale = post(base, "遅れて反映する依頼")
            self.assertEqual(decide(base, winner, "yes")[0], 200)
            self.assertEqual(decide(base, stale, "yes")[0], 409)
            # 廃止する設問を現行のルールが参照している（反映失敗）。
            _, blocked = post(base, "A01廃止")
            status, body = decide(base, blocked, "yes")
            self.assertEqual(status, 409, body)
            # 提案がない（二重のYes）は登録も変更もしない。
            self.assertEqual(decide(base, winner, "yes")[0], 404)
            self.assertEqual(templates.list_prompts("question"), ["先に反映する依頼"])
            # ルール側も同じ。Noでは登録しない。
            rules = server.base_url + "/api/admin/rule-proposals"
            _, declined_rule = post(rules, "Noのルール依頼")
            self.assertEqual(decide(rules, declined_rule, "no")[0], 200)
            self.assertEqual(templates.list_prompts("rule"), [])

    def test_rule_remove_and_add_register_then_reuse_is_validated_again(self):
        templates = InMemoryProposalTemplates(initial=False)
        remove_r01 = {"operation": "remove_rule", "rule_id": "R01"}
        rules = ScriptedRules("bedrock", {"R01を消す": remove_r01})
        app = auto_app(templates, rule_proposer=rules)
        with _Server(app) as server:
            base = server.base_url + "/api/admin/rule-proposals"
            _, removal = post(base, "R01を消す")
            self.assertEqual(decide(base, removal, "yes")[0], 200)
            self.assertEqual(templates.lookup("rule", "R01を消す"), remove_r01)
            # 再利用時点で対象が既に消えているので、Bedrockを呼ばず理由つきで拒否する。
            status, body = post(base, "R01を消す")
            self.assertEqual(status, 422, body)
            self.assertIn("R01", body["detail"])
            _, addition = post(base, "ルールを追加")
            self.assertEqual(decide(base, addition, "yes")[0], 200)
            operation = templates.lookup("rule", "ルールを追加")
            self.assertEqual(operation["operation"], "add_rule")
            self.assertNotIn("id", operation["rule"])  # 採番したIDは保存しない
            self.assertEqual(rules.calls, 2)

    def test_delete_returns_to_normal_generation_and_targets_do_not_mix(self):
        templates = InMemoryProposalTemplates(initial=False)
        proposer = ScriptedQuestions("bedrock")
        rules = ScriptedRules("bedrock")
        app = auto_app(templates, proposer, rules)
        with _Server(app) as server:
            q_base = server.base_url + "/api/admin/question-proposals"
            r_base = server.base_url + "/api/admin/rule-proposals"
            _, first = post(q_base, "共通の言い回し")
            decide(q_base, first, "yes")
            # 同じ文言でも、対象（設問／ルール）が違えば一致しない。
            status, other = post(r_base, "共通の言い回し")
            self.assertEqual((status, other["source"]["kind"]), (201, "bedrock"))
            self.assertEqual(rules.calls, 1)
            # 削除すると、次の依頼から通常の生成に戻る。
            remove = server.base_url + "/api/admin/proposal-templates/question/remove"
            self.assertEqual(send("POST", remove, {"prompt": "共通の言い回し"})[0], 200)
            status, again = post(q_base, "共通の言い回し")
            self.assertEqual((status, again["source"]["kind"]), (201, "bedrock"))
            self.assertEqual(proposer.calls, 2)

    def test_retire_template_is_not_matched_by_a_rule_condition_edit_request(self):
        # 設問の廃止（初期の定型文）と、単一ルールの条件編集は、取り違えない。
        templates = InMemoryProposalTemplates()
        app = auto_app(
            templates, ScriptedQuestions("bedrock"), ScriptedRules("bedrock")
        )
        with _Server(app) as server:
            for prompt in ("ルールR03のA13の条件を編集して", "設問A13の条件を編集して"):
                for path in ("question-proposals", "rule-proposals"):
                    with self.subTest(prompt=prompt, path=path):
                        status, body = post(
                            server.base_url + "/api/admin/" + path, prompt
                        )
                        self.assertEqual(status, 201, body)
                        self.assertEqual(body["source"], {"kind": "bedrock"})
        self.assertEqual(app.state.question_proposer.calls, 2)
        self.assertEqual(app.state.rule_proposer.calls, 2)


class TestLocalOnlyViewerFallback(unittest.TestCase):
    """専用ヘッダーなしの直接接続は、ローカル専用の旗がある構成だけで受け付ける。"""

    def _app(self, local):
        app = auto_app(InMemoryProposalTemplates(), ScriptedQuestions("bedrock"))
        if local:
            app.state.local_quota_address = True
        return app

    def _post(self, app, header_values=()):
        """専用ヘッダーを、渡した個数だけ（0件も可）同名で送る。"""
        with _Server(app) as server:
            conn = http.client.HTTPConnection(
                *server.base_url.removeprefix("http://").split(":")
            )
            body = b'{"prompt": "fallback case"}'
            conn.putrequest("POST", "/api/admin/question-proposals")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            conn.putheader("Idempotency-Key", str(uuid4()))
            for value in header_values:
                conn.putheader("X-Demo-Viewer-Ip", value)
            conn.endheaders(body)
            status = conn.getresponse().status
            conn.close()
            return status

    def test_public_default_rejects_missing_header_without_calling_ai(self):
        app = self._app(local=False)
        self.assertEqual(self._post(app), 400)
        self.assertEqual(app.state.question_proposer.calls, 0)

    def test_local_flag_uses_raw_client_host_when_no_header(self):
        app = self._app(local=True)
        self.assertEqual(self._post(app), 201)
        self.assertEqual(app.state.question_proposer.calls, 1)
        self.assertEqual(app.state.ai_quota.used("v4:127.0.0.1"), (1, 1))

    def test_local_flag_still_rejects_invalid_or_duplicate_header(self):
        for values in (
            ("192.0.2.1:4000",),
            ("192.0.2.1, 192.0.2.2",),
            ("",),
            ("192.0.2.1", "192.0.2.2"),
        ):
            app = self._app(local=True)
            self.assertEqual(self._post(app, values), 400, values)
            self.assertEqual(app.state.question_proposer.calls, 0)
