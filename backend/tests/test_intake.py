"""受付とHTTPモックの検証。

docs/intake-mock-contract-design.md 4.1・4.2のうち、DBを使わないもの。

uvicornを実際に起動し、受付処理が実HTTPでモックを呼ぶ。案件・利用者・問い合わせは架空。
DB保存（S5〜S15の保存部分）は対象外。
"""

import json
import os
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import uvicorn
from fastapi import FastAPI

from app import cases, intake, mock_api


class _Server:
    """uvicornをスレッドで起動する。起動後のURLを base_url で返す。"""

    def __init__(self, application: FastAPI) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        config = uvicorn.Config(application, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True
        )

    def __enter__(self) -> _Server:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicornが起動しません")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()


def send(method: str, url: str, payload=None, headers=None) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        error.close()
        try:
            return error.code, json.loads(raw)
        except ValueError:  # 未処理の例外は本文がJSONでない500になる
            return error.code, {"raw": raw.decode(errors="replace")}


def post(url: str, payload, headers=None) -> tuple[int, dict]:
    return send("POST", url, payload, headers)


class FailingStore(intake.InMemoryCaseStore):
    def __init__(self, fail_on: str) -> None:
        super().__init__()
        self.fail_on = fail_on

    def create_case(self, case):
        if self.fail_on == "create":
            raise RuntimeError("1回目の保存の失敗（検証用）")
        return super().create_case(case)

    def set_auto_answer(self, case_id, question_code, answer):
        if self.fail_on == "set":
            raise RuntimeError("2回目の保存の失敗（検証用）")
        super().set_auto_answer(case_id, question_code, answer)


def make_app(store: intake.InMemoryCaseStore, mock_calls: list[str]) -> FastAPI:
    """受付とモックを同一アプリに置き、モックへの呼び出しを数える。"""
    application = FastAPI()
    application.state.case_store = store
    application.include_router(mock_api.router)
    application.include_router(intake.router)
    application.include_router(cases.router)

    @application.middleware("http")
    async def count_mock_calls(request, call_next):
        if request.url.path.startswith("/mock/"):
            mock_calls.append(request.url.path)
        return await call_next(request)

    return application


def case_payload(user_id: str, **extra) -> dict:
    return {
        "summary": "心当たりのないログインについて問い合わせが届いた。（架空）",
        "user": {"user_id": user_id},
        "inquiry": {"subject": "心当たりのないログイン", "body": "架空の問い合わせ。"},
        **extra,
    }


class MockContract(unittest.TestCase):
    """M1〜M3。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server(make_app(intake.InMemoryCaseStore(), [])).__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__()

    def call(self, code, payload):
        return post(f"{self.server.base_url}/mock/v1/auto-answers/{code}", payload)

    def test_m1_even_is_yes_odd_is_no(self):
        self.assertEqual(
            self.call("A09", {"user_id": "456"}),
            (200, {"question_code": "A09", "answer": "yes", "sample_number": 456}),
        )
        status, body = self.call("A09", {"user_id": "457"})
        self.assertEqual((status, body["answer"]), (200, "no"))

    def test_sample_number_takes_precedence(self):
        status, body = self.call("A09", {"user_id": "abc", "sample_number": 2})
        self.assertEqual((status, body["answer"]), (200, "yes"))

    def test_m2_question_without_auto_answer_is_404(self):
        self.assertEqual(self.call("A01", {"user_id": "456"})[0], 404)

    def test_m3_non_integer_user_id_without_sample_number_is_422(self):
        self.assertEqual(self.call("A09", {"user_id": "abc"})[0], 422)
        self.assertEqual(self.call("A09", {"user_id": "-4"})[0], 422)


class Intake(unittest.TestCase):
    """S1・S3・S4・S12・S13・S16と、受付が再取得しないこと。"""

    def run_intake(self, store=None):
        store = store or intake.InMemoryCaseStore()
        calls: list[str] = []
        server = _Server(make_app(store, calls))
        env = mock.patch.dict(os.environ, {"MOCK_BASE_URL": server.base_url})
        server.__enter__()
        env.start()
        self.addCleanup(server.__exit__)
        self.addCleanup(env.stop)
        return server, store, calls

    def test_s1_success_calls_mock_once_and_stores_answer(self):
        server, store, calls = self.run_intake()
        for user_id, expected in (("456", "yes"), ("457", "no")):
            calls.clear()
            status, body = post(
                f"{server.base_url}/api/intake/cases", case_payload(user_id)
            )
            self.assertEqual(status, 201)
            self.assertEqual(body["answers"], {"A09": expected})
            self.assertEqual(calls, ["/mock/v1/auto-answers/A09"])
            stored = store.cases[body["case_id"]]
            self.assertEqual(stored["answers"], {"A09": expected})
            self.assertEqual(stored["automated_answers"], {"A09": expected})

    def test_s4_failure_injected_by_demo_controls_is_pending(self):
        server, store, calls = self.run_intake()
        with (
            mock.patch.object(intake, "MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch.object(mock_api, "FAULT_DELAY_SECONDS", 2.0),
        ):
            status, body = post(
                f"{server.base_url}/api/intake/cases",
                case_payload("458", demo_controls={"fault_questions": ["A09"]}),
            )
        self.assertEqual(status, 201)
        self.assertEqual(body["answers"], {"A09": "pending"})
        self.assertEqual(len(calls), 1, "失敗しても再試行しない")
        stored = store.cases[body["case_id"]]
        self.assertEqual(stored["answers"], {"A09": "pending"})
        self.assertEqual(stored["automated_answers"], {})

    def test_demo_controls_for_other_question_does_not_fail_a09(self):
        server, _, _ = self.run_intake()
        _, body = post(
            f"{server.base_url}/api/intake/cases",
            case_payload("456", demo_controls={"fault_questions": ["A01"]}),
        )
        self.assertEqual(body["answers"], {"A09": "yes"})

    def test_m3_through_intake_non_integer_user_id_is_pending(self):
        server, _, calls = self.run_intake()
        status, body = post(f"{server.base_url}/api/intake/cases", case_payload("abc"))
        self.assertEqual(
            (status, body["answers"], len(calls)), (201, {"A09": "pending"}, 1)
        )

    def test_s3_missing_or_blank_required_fields_are_422_and_no_http_no_case(self):
        server, store, calls = self.run_intake()
        bad_payloads = [
            {**case_payload("456"), "user": {}},
            {**case_payload("456"), "user": {"user_id": "   "}},
            {**case_payload("456"), "inquiry": {"body": "本文だけ"}},
            {**case_payload("456"), "inquiry": {"subject": "件名だけ"}},
            {**case_payload("456"), "inquiry": {"subject": " ", "body": "本文"}},
            {**case_payload("456"), "inquiry": {"subject": "件名", "body": "\n"}},
            {**case_payload("456"), "inquiry": {"subject": "件名", "body": 1}},
            {**case_payload("456"), "user": {"user_id": 456}},
            {**case_payload("456"), "period_start_at": "2026-10-20T09:00:00+09:00"},
        ]
        for payload in bad_payloads:
            status, _ = post(f"{server.base_url}/api/intake/cases", payload)
            self.assertEqual(status, 422, payload)
        self.assertEqual((calls, store.cases), ([], {}))

    def test_s16_unknown_fields_are_ignored_and_not_stored(self):
        server, store, _ = self.run_intake()
        payload = case_payload("456", source_case_id="X-1")
        payload["user"]["identity_verified"] = True
        payload["inquiry"]["received_at"] = "2026-10-20T09:00:00+09:00"
        status, body = post(f"{server.base_url}/api/intake/cases", payload)
        self.assertEqual(status, 201)
        stored = store.cases[body["case_id"]]
        self.assertEqual(stored["user"], {"user_id": "456"})
        self.assertEqual(set(stored["inquiry"]), {"subject", "body"})
        self.assertNotIn("source_case_id", stored)
        self.assertNotIn("demo_controls", stored)

    def test_s12_second_save_failure_leaves_pending_without_refetch(self):
        server, _, calls = self.run_intake(FailingStore("set"))
        status, body = post(f"{server.base_url}/api/intake/cases", case_payload("456"))
        self.assertEqual(
            (status, body["answers"], len(calls)), (201, {"A09": "pending"}, 1)
        )

    def test_s13_first_save_failure_does_not_call_mock(self):
        server, _, calls = self.run_intake(FailingStore("create"))
        status, _ = post(f"{server.base_url}/api/intake/cases", case_payload("456"))
        self.assertEqual((status, calls), (500, []))

    def test_missing_mock_base_url_fails_before_saving(self):
        server, store, calls = self.run_intake()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOCK_BASE_URL")
            status, _ = post(f"{server.base_url}/api/intake/cases", case_payload("456"))
        self.assertEqual((status, calls, store.cases), (500, [], {}))


class _StubHandler(BaseHTTPRequestHandler):
    """モックの失敗応答を実HTTPで再現する（M4・M6）。"""

    def do_POST(self):  # noqa: N802
        self.server.calls += 1
        status, body, headers = self.server.responder(self)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def j(obj) -> bytes:
    return json.dumps(obj).encode()


class FailureResponses(unittest.TestCase):
    """M4（5xx）・M6（壊れた応答）。いずれも確認待ちで再試行しない。"""

    CASES = {
        "500": lambda h: (500, j({"detail": "error"}), {}),
        "503": lambda h: (503, b"", {}),
        "404": lambda h: (404, j({"detail": "x"}), {}),
        "422": lambda h: (422, j({"detail": "x"}), {}),
        "redirect": lambda h: (302, b"", {"Location": "/elsewhere"}),
        "truncated": lambda h: (
            200,
            j({"question_code": "A09"}),
            {"Content-Length": "999"},
        ),
        "204": lambda h: (204, b"", {}),
        "not-json": lambda h: (200, b"<html>", {}),
        "not-object": lambda h: (200, j(["yes"]), {}),
        "no-answer": lambda h: (200, j({"question_code": "A09"}), {}),
        "null-answer": lambda h: (200, j({"question_code": "A09", "answer": None}), {}),
        "other-answer": lambda h: (
            200,
            j({"question_code": "A09", "answer": "maybe"}),
            {},
        ),
        "upper-answer": lambda h: (
            200,
            j({"question_code": "A09", "answer": "YES"}),
            {},
        ),
        "bool-answer": lambda h: (200, j({"question_code": "A09", "answer": True}), {}),
        "other-question": lambda h: (
            200,
            j({"question_code": "A01", "answer": "yes"}),
            {},
        ),
    }

    def test_each_failure_becomes_pending_with_one_call(self):
        for name, responder in self.CASES.items():
            with self.subTest(name):
                stub = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
                stub.calls, stub.responder = 0, responder
                thread = threading.Thread(target=stub.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(stub.server_close)
                self.addCleanup(stub.shutdown)
                with mock.patch.dict(
                    os.environ,
                    {"MOCK_BASE_URL": f"http://127.0.0.1:{stub.server_port}"},
                ):
                    store = intake.InMemoryCaseStore()
                    with _Server(make_app(store, [])) as app_server:
                        status, body = post(
                            f"{app_server.base_url}/api/intake/cases",
                            case_payload("456"),
                        )
                self.assertEqual((status, body["answers"]), (201, {"A09": "pending"}))
                self.assertEqual(stub.calls, 1)

    def test_connection_refused_is_pending(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with mock.patch.dict(os.environ, {"MOCK_BASE_URL": f"http://127.0.0.1:{port}"}):
            with _Server(make_app(intake.InMemoryCaseStore(), [])) as app_server:
                status, body = post(
                    f"{app_server.base_url}/api/intake/cases", case_payload("456")
                )
        self.assertEqual((status, body["answers"]), (201, {"A09": "pending"}))


if __name__ == "__main__":
    unittest.main()
