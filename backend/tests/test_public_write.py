import hmac
import http.client
import json
import os
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from unittest import mock
from uuid import uuid4

from fastapi import FastAPI

from app import intake, mock_api, question_admin, rule_admin, rules
from app.domain import GenerationSource
from app.public_guard import PublicWriteGuard
from app.question_store import InMemoryQuestionStore
from app.quota_store import InMemoryAiQuota
from app.rule_store import InMemoryRuleStore
from app.usage_quota import (
    QuotaLimits,
    ViewerAddressUnavailable,
    hash_subject,
    viewer_key,
)
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
            "X-Demo-Viewer-Ip": "192.0.2.1",
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
                {"X-Demo-Viewer-Ip": "192.0.2.1"},
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


def raw_post(server, path, header_lines):
    """同名ヘッダーを複数行で送る（urllibの辞書では送れない）。ステータスだけ返す。"""
    host, port = server.base_url.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=15)
    try:
        body = json.dumps({"prompt": "追加して"})
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body.encode())))
        for name, value in header_lines:
            connection.putheader(name, value)
        connection.endheaders(body.encode())
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


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
        "X-Demo-Viewer-Ip": ip,
        "Idempotency-Key": key or str(uuid4()),
    }


PATHS = {
    "question": ("/api/admin/question-proposals", "question_proposer"),
    "rule": ("/api/admin/rule-proposals", "rule_proposer"),
}


class TestViewerKey(unittest.TestCase):
    def test_accepted_forms_are_normalized_as_before(self):
        for value, key in (
            ("192.0.2.1", "v4:192.0.2.1"),
            ("::ffff:192.0.2.1", "v4:192.0.2.1"),
            ("2001:db8:1:2::1", "v6:2001:db8:1:2::/64"),
            ("2001:DB8:1:2:ffff:ffff:ffff:ffff", "v6:2001:db8:1:2::/64"),
            # エッジ（infra/edge/viewer-ip.test.js）が通す形と同じ値。両方のテストに同じ値を置く
            ("::ffff:c000:201", "v4:192.0.2.1"),
            ("::FFFF:C000:201", "v4:192.0.2.1"),
            ("2001:0db8:0001:0002:0000:0000:0000:0001", "v6:2001:db8:1:2::/64"),
            ("2001:db8::", "v6:2001:db8::/64"),
            ("::1", "v6:::/64"),
        ):
            with self.subTest(value):
                self.assertEqual(viewer_key(value), key)

    def test_values_the_edge_prefilter_passes_but_are_not_ips_are_still_rejected(self):
        # エッジは粗い形の確認だけ（IPの文法は解釈しない）。実際のIPなら来ない値だが、
        # 形だけ通る値が来ても、アプリが拒否して枠の予約・推論をしない
        for value in ("999.1.1.1", ":::", "1:2:3:4:5:6:7:8:9", "12345::1", "::ffff:999.1.1.1"):
            with self.subTest(value), self.assertRaises(ViewerAddressUnavailable):
                viewer_key(value)

    def test_dirty_or_missing_values_fail_closed(self):
        for value in (
            None, "", " ", " 192.0.2.1", "192.0.2.1 ", "192.0.2.1:80", "[2001:db8::1]",
            "[2001:db8::1]:80", "1.2.3.4,5.6.7.8", "2001:db8::1%eth0", "256.0.0.1",
            "01.2.3.4", "example.com", "192.0.2.1\n", "１９２.0.2.1",
        ):  # fmt: skip
            with self.subTest(value), self.assertRaises(ViewerAddressUnavailable):
                viewer_key(value)

    def test_hmac_subject_is_unchanged(self):
        secret = b"k" * 32
        self.assertEqual(
            hash_subject(secret, viewer_key("::ffff:192.0.2.1")),
            hmac.new(secret, b"v4:192.0.2.1", sha256).hexdigest(),
        )


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
            "malformed viewer": {**good, "X-Demo-Viewer-Ip": "not-an-address"},
            "viewer with port": {**good, "X-Demo-Viewer-Ip": "192.0.2.1:5000"},
            "bracketed ipv6 with port": {**good, "X-Demo-Viewer-Ip": "[2001:db8::1]:1"},
            "comma separated viewer": {
                **good,
                "X-Demo-Viewer-Ip": "192.0.2.1, 192.0.2.2",
            },
            "old cloudfront header is no fallback": {
                "Idempotency-Key": good["Idempotency-Key"],
                "CloudFront-Viewer-Address": "192.0.2.1:5000",
            },
            "no idempotency key": {"X-Demo-Viewer-Ip": good["X-Demo-Viewer-Ip"]},
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

    def test_duplicate_viewer_header_lines_reject_before_generation(self):
        for kind in PATHS:
            with self.subTest(kind):
                app = admin_app()
                with _Server(app) as server:
                    status = raw_post(
                        server,
                        PATHS[kind][0],
                        [
                            ("X-Demo-Viewer-Ip", "192.0.2.1"),
                            ("x-demo-viewer-ip", "192.0.2.2"),
                            ("Idempotency-Key", str(uuid4())),
                        ],
                    )
                self.assertEqual(status, 400)
                self.assertEqual(getattr(app.state, PATHS[kind][1]).calls, 0)
                self.assertEqual(app.state.ai_quota.used("v4:192.0.2.1"), (0, 0))
                self.assertEqual(app.state.ai_quota.used("v4:192.0.2.2"), (0, 0))

    def test_ip_forms_share_the_exact_existing_slots(self):
        app = admin_app(limits=QuotaLimits(1, 25))
        with _Server(app) as server:
            self.assertEqual(self.post(server, "question", viewer("192.0.2.7"))[0], 201)
            # IPv4射影のIPv6は、同じIPv4の枠
            mapped = viewer("::ffff:192.0.2.7")
            self.assertEqual(self.post(server, "question", mapped)[0], 429)
            # IPv6は /64 で1つの枠
            self.assertEqual(
                self.post(server, "question", viewer("2001:db8:1:2::1"))[0], 201
            )
            same64 = viewer("2001:db8:1:2:ffff:ffff:ffff:ffff")
            self.assertEqual(self.post(server, "question", same64)[0], 429)
            other64 = viewer("2001:db8:1:3::1")
            self.assertEqual(self.post(server, "question", other64)[0], 201)
        self.assertEqual(app.state.ai_quota.used("v4:192.0.2.7"), (1, 3))
        self.assertEqual(app.state.ai_quota.used("v6:2001:db8:1:2::/64"), (1, 3))

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
                        {"X-Demo-Viewer-Ip": "192.0.2.1"},
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

    def test_viewer_header_with_a_valid_token_is_still_public(self):
        """エッジの印（専用ヘッダー）がある要求は、トークンがあっても内部扱いにしない。"""
        app = self.intake_app("internal-test-token")
        with _Server(app) as server:
            status, _ = send(
                "POST",
                server.base_url + "/mock/v1/auto-answers/A09",
                {"user_id": "12345"},
                {
                    "X-Demo-Viewer-Ip": "192.0.2.1",
                    "X-Internal-Mock-Token": "internal-test-token",
                },
            )
        self.assertEqual(status, 405)

    def test_duplicate_internal_token_is_rejected(self):
        app = self.intake_app("internal-test-token")
        with _Server(app) as server:
            host, port = server.base_url.removeprefix("http://").split(":")
            connection = http.client.HTTPConnection(host, int(port), timeout=15)
            try:
                connection.putrequest("POST", "/mock/v1/auto-answers/A09")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", "2")
                connection.putheader("X-Internal-Mock-Token", "internal-test-token")
                connection.putheader("X-Internal-Mock-Token", "internal-test-token")
                connection.endheaders(b"{}")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 405)
            finally:
                connection.close()

    def test_public_non_listed_methods_and_paths_stay_denied_with_the_edge_header(self):
        app = self.intake_app("internal-test-token")
        edge = {"X-Demo-Viewer-Ip": "192.0.2.1"}
        with _Server(app) as server:
            for method, path in (
                ("DELETE", "/api/intake/cases"),
                ("PATCH", "/api/cases/x/judgment"),
                ("POST", "/api/cases/x/judgment"),
                ("PUT", "/api/intake/cases"),
                ("POST", "/mock/v1/auto-answers/A09/../A10"),
                ("POST", "/api/admin/other"),
            ):
                with self.subTest(method=method, path=path):
                    status, _ = send(method, server.base_url + path, {}, edge)
                    self.assertEqual(status, 405)

    def test_viewer_header_is_not_a_bypass_for_the_mock(self):
        app = self.intake_app("internal-test-token")
        with _Server(app) as server:
            status, _ = send(
                "POST",
                server.base_url + "/mock/v1/auto-answers/A09",
                {"user_id": "12345"},
                {"X-Demo-Viewer-Ip": "192.0.2.1"},
            )
        self.assertEqual(status, 405)
