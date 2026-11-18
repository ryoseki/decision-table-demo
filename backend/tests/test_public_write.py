import json
import os
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from uuid import uuid4

from fastapi import FastAPI

from app import intake, mock_api, question_admin, rule_admin, rules
from app.domain import GenerationSource
from app.public_guard import PublicWriteGuard
from app.question_store import InMemoryQuestionStore
from app.quota_store import InMemoryAiQuota
from app.rule_store import InMemoryRuleStore
from app.usage_quota import QuotaLimits
from tests.test_intake import _Server, send


class TestPublicWrite(unittest.TestCase):
    def test_same_request_reserves_and_invokes_once(self):
        class Proposer:
            source = GenerationSource("bedrock")
            calls = 0

            def generate(self, prompt, current):
                self.calls += 1
                return {
                    "operation": "add_question",
                    "question": {"id": "A13", "text": "新しい設問"},
                }

        app = FastAPI()
        app.include_router(question_admin.router)
        app.state.question_store = InMemoryQuestionStore()
        app.state.question_proposer = Proposer()
        app.state.ai_quota = InMemoryAiQuota(b"x" * 32)
        headers = {
            "CloudFront-Viewer-Address": "192.0.2.1:5000",
            "Idempotency-Key": str(uuid4()),
        }
        with _Server(app) as server:

            def submit():
                return send(
                    "POST",
                    server.base_url + "/api/admin/question-proposals",
                    {"prompt": "追加して"},
                    headers,
                )[0]

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: submit(), range(2)))
            self.assertEqual(submit(), 409)
        self.assertEqual(sorted(responses), [201, 409])
        self.assertEqual(app.state.question_proposer.calls, 1)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (1, 1))

    def test_public_mock_is_rejected_with_or_without_viewer_header(self):
        app = FastAPI()
        app.add_middleware(PublicWriteGuard, internal_token="internal-test-token")

        @app.post("/mock/v1/auto-answers/A09")
        def mock():
            return {}

        with _Server(app) as server:
            path = "/mock/v1/auto-answers/A09"
            for headers in (
                {},
                {"CloudFront-Viewer-Address": "192.0.2.1:5000"},
                {"X-Internal-Mock-Token": "fake"},
            ):
                self.assertEqual(
                    send("POST", server.base_url + path, {}, headers)[0], 405
                )
            self.assertEqual(
                send(
                    "POST",
                    server.base_url + path,
                    {},
                    {"X-Internal-Mock-Token": "internal-test-token"},
                )[0],
                200,
            )

    def test_template_delete_is_open_only_when_reuse_is_enabled(self):
        # 登録は、Yesで反映したときの自動登録だけ。登録用の経路は、有効でも開けない。
        register = [
            "/api/admin/proposal-templates/question",
            "/api/admin/proposal-templates/rule",
        ]
        for enabled in (False, True):
            app = FastAPI()
            app.add_middleware(PublicWriteGuard, reuse_enabled=enabled)

            @app.post("/api/admin/proposal-templates/{rest:path}")
            def register_route(rest: str):
                return {}

            with _Server(app) as server:
                for path in register:
                    with self.subTest(enabled=enabled, path=path):
                        self.assertEqual(
                            send("POST", server.base_url + path, {})[0], 405
                        )
        paths = [
            "/api/admin/proposal-templates/question/remove",
            "/api/admin/proposal-templates/rule/remove",
        ]
        for enabled in (False, True):
            app = FastAPI()
            app.add_middleware(PublicWriteGuard, reuse_enabled=enabled)

            @app.post("/api/admin/proposal-templates/{rest:path}")
            def template(rest: str):
                return {}

            @app.delete("/api/admin/proposal-templates/{rest:path}")
            def template_delete(rest: str):
                return {}

            with _Server(app) as server:
                for path in paths:
                    expected = 200 if enabled else 405
                    with self.subTest(enabled=enabled, path=path):
                        self.assertEqual(
                            send("POST", server.base_url + path, {})[0], expected
                        )
                # 有効でも、公開する経路の外（他のメソッド・他の対象）は開けない。
                self.assertEqual(send("DELETE", server.base_url + paths[0])[0], 405)
                self.assertEqual(
                    send(
                        "POST",
                        server.base_url + "/api/admin/proposal-templates/other",
                        {},
                    )[0],
                    405,
                )

    def test_reuse_is_disabled_on_lambda_dsql_or_without_a_local_database(self):
        import importlib.util
        from pathlib import Path

        from app import template_store

        main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"

        class FakeTemplates:
            def __init__(self, connect):
                self.bootstrapped = False

            def bootstrap(self):
                self.bootstrapped = True

        def load(env):
            spec = importlib.util.spec_from_file_location("app_main_probe", main_path)
            module = importlib.util.module_from_spec(spec)
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(
                    template_store, "PostgresProposalTemplates", FakeTemplates
                ),
            ):
                spec.loader.exec_module(module)
            return module.app

        def guard_flag(application):
            return application.user_middleware[0].kwargs["reuse_enabled"]

        def has_template_routes(application):
            return any(
                path.startswith("/api/admin/proposal-templates")
                for path in application.openapi()["paths"]
            )

        on = {"PROPOSAL_REUSE_ENABLED": "1"}
        local = {**on, "DB_HOST": "db.invalid"}
        cases = {
            "local": (local, True),
            "flag off": ({"DB_HOST": "db.invalid"}, False),
            "flag not 1": ({"PROPOSAL_REUSE_ENABLED": "0", "DB_HOST": "db"}, False),
            "no database": (on, False),
            "lambda": ({**local, "AWS_LAMBDA_FUNCTION_NAME": "demo"}, False),
            "dsql": ({**local, "DSQL_ENDPOINT": "dsql.invalid"}, False),
            "dsql only": ({**on, "DSQL_ENDPOINT": "dsql.invalid"}, False),
        }
        for name, (env, expected) in cases.items():
            with self.subTest(name):
                application = load(env)
                self.assertEqual(guard_flag(application), expected)
                self.assertEqual(has_template_routes(application), expected)
                self.assertEqual(
                    getattr(application.state, "proposal_reuse", None) is not None,
                    expected,
                )

    def test_shared_rule_replaces_old_seed_conditions(self):
        class CaseStore:
            def list_rules(self):
                return [{"rule_id": "R01", "revision": 1, "conditions": {"A09": "yes"}}]

        class RuleStore:
            def list_rows(self):
                return [
                    {"rule_id": "R01", "revision": 2, "conditions": {}},
                    {"rule_id": "R04", "revision": 1, "conditions": {"A13": "yes"}},
                ]

            def removed_ids(self):
                return set()

        app = FastAPI()
        app.include_router(rules.router)
        app.state.case_store = CaseStore()
        app.state.rule_store = RuleStore()
        app.state.persistence = "database"
        with _Server(app) as server:
            rows = send("GET", server.base_url + "/api/rules")[1]
        self.assertEqual([row["conditions"] for row in rows], [{}, {"A13": "yes"}])


def send_full(method, url, payload=None, headers=None):
    """ステータス・ヘッダー・本文を返す（Retry-After を確かめるため）。"""
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return (
                response.status,
                response.headers,
                json.loads(response.read() or b"{}"),
            )
    except urllib.error.HTTPError as error:
        body = json.loads(error.read() or b"{}")
        error.close()
        return error.code, error.headers, body


class CountingProposer:
    """Bedrock実接続の代役（生成の回数を数える）。"""

    def __init__(self, kind):
        self.source = GenerationSource(kind)
        self.calls = 0

    def generate(self, prompt, current, *rest):
        self.calls += 1
        if rest:  # 判定ルール
            return {
                "operation": "add_rule",
                "rule": {
                    "id": rest[0],
                    "result_code": "caution",
                    "conditions": {"A01": "yes"},
                },
            }
        return {
            "operation": "add_question",
            "question": {"id": "A13", "text": "新しい設問"},
        }


def admin_app(kind="bedrock", limits=None):
    app = FastAPI()
    app.add_middleware(PublicWriteGuard)
    app.include_router(question_admin.router)
    app.include_router(rule_admin.router)
    app.state.question_store = InMemoryQuestionStore()
    app.state.rule_store = InMemoryRuleStore()
    app.state.question_proposer = CountingProposer(kind)
    app.state.rule_proposer = CountingProposer(kind)
    app.state.ai_quota = InMemoryAiQuota(b"x" * 32, limits or QuotaLimits(2, 25))
    return app


def viewer(ip="192.0.2.1", key=None):
    return {
        "CloudFront-Viewer-Address": f"{ip}:5000",
        "Idempotency-Key": key or str(uuid4()),
    }


PATHS = {
    "question": ("/api/admin/question-proposals", "question_proposer"),
    "rule": ("/api/admin/rule-proposals", "rule_proposer"),
}


class TestAiQuotaContract(unittest.TestCase):
    def post(self, server, kind, headers):
        return send_full(
            "POST", server.base_url + PATHS[kind][0], {"prompt": "追加して"}, headers
        )

    def test_per_ip_cap_returns_429_without_generation_for_both_kinds(self):
        for kind in PATHS:
            app = admin_app(limits=QuotaLimits(1, 25))
            with self.subTest(kind), _Server(app) as server:
                self.assertEqual(self.post(server, kind, viewer())[0], 201)
                status, headers, body = self.post(server, kind, viewer())
                self.assertEqual(status, 429)
                self.assertIn("接続元", body["detail"])
                self.assertIn("共有", body["detail"])
                self.assertGreaterEqual(int(headers["Retry-After"]), 1)
                self.assertEqual(getattr(app.state, PATHS[kind][1]).calls, 1)
                self.assertEqual(self.post(server, kind, viewer("192.0.2.2"))[0], 201)

    def test_global_cap_returns_429_for_another_address_without_generation(self):
        for kind in PATHS:
            with self.subTest(kind):
                app = admin_app(limits=QuotaLimits(5, 1))
                with _Server(app) as server:
                    self.assertEqual(self.post(server, kind, viewer())[0], 201)
                    status, headers, body = self.post(server, kind, viewer("192.0.2.9"))
                    self.assertEqual(status, 429)
                    self.assertIn("デモ全体", body["detail"])
                    self.assertGreaterEqual(int(headers["Retry-After"]), 1)
                proposer = getattr(app.state, PATHS[kind][1])
                self.assertEqual(proposer.calls, 1)
                self.assertEqual(app.state.ai_quota.used("v4:192.0.2.9"), (0, 1))

    def test_cap_does_not_change_shared_data(self):
        app = admin_app(limits=QuotaLimits(1, 25))
        with _Server(app) as server:
            self.post(server, "question", viewer())
            before = app.state.question_store.snapshot()
            self.assertEqual(self.post(server, "question", viewer())[0], 429)
        self.assertEqual(app.state.question_store.snapshot(), before)

    def test_input_and_viewer_boundaries_reject_before_generation_and_charge_nothing(
        self,
    ):
        good = viewer()
        cases = {
            "no viewer header": {"Idempotency-Key": good["Idempotency-Key"]},
            "malformed viewer": {**good, "CloudFront-Viewer-Address": "not-an-address"},
            "viewer without port": {**good, "CloudFront-Viewer-Address": "192.0.2.1"},
            "comma separated viewer": {
                **good,
                "CloudFront-Viewer-Address": "192.0.2.1:1, 192.0.2.2:2",
            },
            "no idempotency key": {
                "CloudFront-Viewer-Address": good["CloudFront-Viewer-Address"]
            },
            "bad idempotency key": {**good, "Idempotency-Key": "not-a-uuid"},
            "upper case idempotency key": {
                **good,
                "Idempotency-Key": good["Idempotency-Key"].upper(),
            },
        }
        for kind in PATHS:
            for name, headers in cases.items():
                with self.subTest(kind=kind, case=name):
                    app = admin_app()
                    with _Server(app) as server:
                        status = self.post(server, kind, headers)[0]
                    self.assertEqual(status, 400)
                    self.assertEqual(getattr(app.state, PATHS[kind][1]).calls, 0)
                    self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))

    def test_missing_quota_store_fails_closed_without_generation(self):
        app = admin_app()
        del app.state.ai_quota
        with _Server(app) as server:
            self.assertEqual(self.post(server, "question", viewer())[0], 503)
        self.assertEqual(app.state.question_proposer.calls, 0)

    def test_mock_generation_needs_no_headers_and_uses_no_quota(self):
        app = admin_app("mock", QuotaLimits(1, 1))
        with _Server(app) as server:
            for kind in PATHS:
                for _ in range(2):
                    self.assertEqual(self.post(server, kind, {})[0], 201)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))

    def test_yes_and_no_decisions_do_not_charge_or_generate(self):
        app = admin_app(limits=QuotaLimits(10, 50))
        with _Server(app) as server:
            for kind in PATHS:
                path = PATHS[kind][0]
                for decision in ("no", "yes"):
                    status, _, created = self.post(server, kind, viewer())
                    self.assertEqual(status, 201, created)
                    used = app.state.ai_quota.used("v4:192.0.2.1")
                    calls = getattr(app.state, PATHS[kind][1]).calls
                    status, _, _ = send_full(
                        "POST",
                        f"{server.base_url}{path}/{created['proposal_id']}/decision",
                        {"decision": decision},
                        {"CloudFront-Viewer-Address": "192.0.2.1:5000"},
                    )
                    self.assertIn(status, (200, 409), (kind, decision))
                    self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), used)
                    self.assertEqual(getattr(app.state, PATHS[kind][1]).calls, calls)


class TestInternalMockThroughGuard(unittest.TestCase):
    """受付が、自分自身のHTTPモックを、実際に呼んでガードを通る（公開環境と同じ向き）。"""

    def intake_app(self, token):
        app = FastAPI()
        app.state.internal_mock_token = token
        app.add_middleware(PublicWriteGuard, internal_token=token)
        app.state.case_store = intake.InMemoryCaseStore()
        app.include_router(mock_api.router)
        app.include_router(intake.router)
        return app

    def intake(self, app, headers):
        with (
            _Server(app) as server,
            mock.patch.dict(os.environ, {"MOCK_BASE_URL": server.base_url}),
        ):
            return send(
                "POST",
                server.base_url + "/api/intake/cases",
                {
                    "summary": "架空の問い合わせ。",
                    "user": {"user_id": "12345"},
                    "inquiry": {"subject": "架空", "body": "架空の本文。"},
                },
                headers,
            )

    def test_real_self_call_carries_the_secret_and_reaches_the_mock(self):
        status, body = self.intake(self.intake_app("internal-test-token"), viewer())
        self.assertEqual(status, 201)
        self.assertIn(body["answers"]["A09"], ("yes", "no"))

    def test_self_call_without_the_secret_stays_pending(self):
        app = self.intake_app("internal-test-token")
        app.state.internal_mock_token = ""
        status, body = self.intake(app, viewer())
        self.assertEqual(status, 201)
        self.assertEqual(body["answers"]["A09"], "pending")

    def test_viewer_header_is_not_a_bypass_for_the_mock(self):
        app = self.intake_app("internal-test-token")
        with _Server(app) as server:
            status, _ = send(
                "POST",
                server.base_url + "/mock/v1/auto-answers/A09",
                {"user_id": "12345"},
                {"CloudFront-Viewer-Address": "192.0.2.1:5000"},
            )
        self.assertEqual(status, 405)
