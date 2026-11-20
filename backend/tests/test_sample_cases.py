"""共有のサンプル案件（POST /api/sample-cases）のHTTP契約の検証（DBなし）。

1回の操作（依頼ID＝Idempotency-Key）につき、共有の架空案件を1件追加する。見るのはHTTPの入力と
出力（ステータスと本文）と、別の操作で増えること、同じ操作の再送・失敗後の再試行・同時の到達で
重複しないこと、入力の拒否で何も変わらないこと、既存の案件と保存済みの判定が変わらないこと。
DBの行・SQL・トランザクションは、`bash scripts/backend-db-test.sh` の `test_db_store.py`
（DB統合）が受け持つ。保存先は、PostgresCaseStore の契約を写したテスト用のもの
（同一性は保証しない）。案件・値はすべて架空。
"""

import http.client
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi import FastAPI

from app import intake, sample_cases
from app.domain import OPERATOR_ID, StoreBusy
from app.public_guard import PublicWriteGuard, is_allowed_write
from tests.test_api_contract import ContractStore
from tests.test_intake import _Server, case_payload, make_app, send


def op_key(number: int) -> str:
    """number を4で割った余りの型になる、小文字のUUID（決まった入力）。"""
    return str(uuid.UUID(int=(number << 64) | number))


class SampleStore(ContractStore):
    """create_sample_case を持つ、DBなしの保存先。

    PostgresCaseStore の契約を写す: 案件IDが無いときだけ、案件・回答・
    （登録済みなら）結果を一度に入れる。あれば何も変えずFalse。
    """

    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.create_calls: list[str] = []
        self.busy_once: set[str] = set()  # 1回だけ StoreBusy にする案件ID
        self.crash_once: set[str] = set()  # 1回だけ StoreBusy以外の例外にする案件ID
        # 確認のあとに、別の要求が先に作った扱いにする案件ID
        self.lose_race: set[str] = set()

    def create_sample_case(self, sample: dict) -> bool:
        case_id = sample["case_id"]
        self.create_calls.append(case_id)
        if case_id in self.busy_once:
            self.busy_once.discard(case_id)
            raise StoreBusy("衝突")
        if case_id in self.crash_once:
            self.crash_once.discard(case_id)
            raise RuntimeError("想定外の保存先の失敗")
        with self.lock:
            if case_id in self.lose_race and case_id not in self.cases:
                self.lose_race.discard(case_id)
                self._insert(sample, answer="no")  # 先に作った別の要求
                return False
            if case_id in self.cases:
                return False
            self._insert(sample, answer=sample["a09_answer"])
            return True

    def _insert(self, sample: dict, answer: str | None) -> None:
        result = None
        if sample["result_code"] is not None:
            result = {
                "result_code": sample["result_code"],
                "adopted_rule": None,
                "recorded_by": OPERATOR_ID,
            }
        self.cases[sample["case_id"]] = {
            "summary": sample["summary"],
            "user": sample["user"],
            "inquiry": sample["inquiry"],
            "answers": {"A09": answer or "pending"},
            "automated_answers": {} if answer is None else {"A09": answer},
            "result": result,
        }


def make_sample_app(store, mock_calls: list[str]) -> FastAPI:
    application = make_app(store, mock_calls)
    application.include_router(sample_cases.router)
    return application


class SampleCasesContract(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_calls: list[str] = []
        self.store = SampleStore()
        self.server = _Server(make_sample_app(self.store, self.mock_calls)).__enter__()
        self.addCleanup(self.server.__exit__)
        env = mock.patch.dict(os.environ, {"MOCK_BASE_URL": self.server.base_url})
        env.start()
        self.addCleanup(env.stop)

    def create(self, key: str | None = None, payload=None, headers=None):
        sent = dict(headers or {})
        if key is not None:
            sent["Idempotency-Key"] = key
        return send("POST", f"{self.server.base_url}/api/sample-cases", payload, sent)

    def listing(self) -> list[dict]:
        status, rows = send("GET", f"{self.server.base_url}/api/cases")
        self.assertEqual(status, 200)
        return rows

    def case_id_of(self, key: str) -> str:
        return sample_cases.sample_for_operation(key)["case_id"]

    # --- 1操作1件 ---------------------------------------------------------------

    def test_one_operation_creates_exactly_one_new_case(self):
        key = op_key(0)
        status, body = self.create(key)
        self.assertEqual(status, 201, body)
        self.assertEqual(body, {"case_id": self.case_id_of(key), "created": True})
        self.assertEqual([r["case_id"] for r in self.listing()], [body["case_id"]])

    def test_each_new_operation_adds_one_more_case_without_any_upper_limit(self):
        ids = []
        for number in range(1, 11):  # 4件を超えても、1回ごとに1件増える
            status, body = self.create(op_key(number))
            self.assertEqual((status, body["created"]), (201, True))
            ids.append(body["case_id"])
            self.assertEqual(len(self.listing()), number)
        self.assertEqual(len(set(ids)), 10)  # 別の操作は別の案件
        self.assertEqual(sorted(r["case_id"] for r in self.listing()), sorted(ids))

    def test_the_four_approved_shapes_are_used_with_the_seed_composition(self):
        for number in range(4):  # 型は依頼IDだけで決まる（余り0〜3）
            status, body = self.create(op_key(number))
            self.assertEqual(status, 201, body)
        rows = {r["case_id"]: r for r in self.listing()}
        first, second, third, fourth = (
            rows[self.case_id_of(op_key(number))] for number in range(4)
        )
        # 未審査2件: 結果なし、確認待ちなし（A09はyes／no）
        for row, answer in ((first, "yes"), (second, "no")):
            self.assertIsNone(row["result_code"])
            self.assertEqual(row["counts"][answer], 1)
            self.assertEqual(row["counts"]["pending"], 0)
        # A09の取得エラー1件: 確認待ち1、結果なし
        self.assertIsNone(third["result_code"])
        self.assertEqual(third["counts"]["pending"], 1)
        # 登録済み（問題なし）1件
        self.assertEqual(fourth["result_code"], "no_issue")
        self.assertEqual(fourth["counts"]["pending"], 0)
        # すべて架空の利用者ID（モックと同じ規則: 偶数はyes）
        self.assertEqual(
            [r["user_id"] for r in (first, second, third, fourth)],
            ["456", "457", "458", "460"],
        )

    def test_creating_a_sample_calls_neither_the_mock_nor_the_ai(self):
        self.create(op_key(2))
        self.assertEqual(self.mock_calls, [])  # A09は固定値。HTTPモックは呼ばない

    def test_the_body_is_not_needed_and_is_ignored(self):
        status, body = self.create(op_key(5), {"case_id": "CASE-OTHER", "count": 99})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["case_id"], self.case_id_of(op_key(5)))
        self.assertEqual(len(self.listing()), 1)

    def test_keys_sharing_the_first_half_still_create_separate_cases(self):
        # 前半が同じで後ろだけが違う正しいUUID。別の操作は別の案件になる。
        first = "12345678-1234-4234-8234-000000000001"
        second = "12345678-1234-4234-8234-000000000002"
        saved = {}
        for key in (first, second):
            status, body = self.create(key)
            self.assertEqual((status, body["created"]), (201, True), body)
            saved[key] = body["case_id"]
        self.assertNotEqual(saved[first], saved[second])
        self.assertEqual(len(self.listing()), 2)
        url = f"{self.server.base_url}/api/cases/{saved[first]}"
        judged = send(
            "PUT",
            f"{url}/judgment",
            {"result_code": "caution", "answers": {"A01": "yes"}},
        )
        self.assertEqual(judged[0], 204)
        before = send("GET", url)[1]
        status, body = self.create(first)  # 同じ依頼IDの再送は、同じ案件で200
        self.assertEqual(
            (status, body), (200, {"case_id": saved[first], "created": False})
        )
        self.assertEqual(send("GET", url)[1], before)  # 保存済みの判定は変わらない
        self.assertEqual(len(self.listing()), 2)

    # --- 同じ操作の再送 -----------------------------------------------------------

    def test_resending_the_same_operation_returns_the_same_case_and_writes_nothing(
        self,
    ):
        key = op_key(7)
        _, first = self.create(key)
        snapshot = repr(self.store.cases)
        status, body = self.create(key)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"case_id": first["case_id"], "created": False})
        self.assertEqual(len(self.listing()), 1)
        self.assertEqual(repr(self.store.cases), snapshot)  # 何も書き換えない

    def test_a_saved_judgment_is_not_overwritten_by_a_resend_or_a_new_operation(self):
        key = op_key(8)  # 未審査の型
        _, created = self.create(key)
        case_id = created["case_id"]
        status, body = send(
            "PUT",
            f"{self.server.base_url}/api/cases/{case_id}/judgment",
            {"result_code": "caution", "answers": {"A01": "yes"}},
        )
        self.assertEqual(status, 204, body)
        url = f"{self.server.base_url}/api/cases/{case_id}"
        before = send("GET", url)[1]
        self.assertEqual(before["result"]["result_code"], "caution")
        self.assertEqual(self.create(key)[0], 200)  # 同じ操作の再送
        self.assertEqual(self.create(op_key(9))[0], 201)  # 別の操作
        self.assertEqual(send("GET", url)[1], before)
        # 登録済みの型の案件も、再送で変わらない
        key4 = op_key(11)
        self.create(key4)
        registered = f"{self.server.base_url}/api/cases/{self.case_id_of(key4)}"
        stored = send("GET", registered)[1]
        self.assertEqual(stored["result"]["result_code"], "no_issue")
        self.assertEqual(self.create(key4)[0], 200)
        self.assertEqual(send("GET", registered)[1], stored)

    def test_existing_cases_including_the_earlier_fixed_samples_stay_untouched(self):
        status, body = send(
            "POST", f"{self.server.base_url}/api/intake/cases", case_payload("456")
        )
        self.assertEqual(status, 201)
        ordinary = f"{self.server.base_url}/api/cases/{body['case_id']}"
        # 以前の固定の共有サンプルが、すでにある状態
        legacy = {"case_id": "CASE-SAMPLE-01", **sample_cases.SAMPLE_TEMPLATES[0]}
        self.assertTrue(self.store.create_sample_case(legacy))
        legacy_url = f"{self.server.base_url}/api/cases/CASE-SAMPLE-01"
        before = (send("GET", ordinary)[1], send("GET", legacy_url)[1])
        for number in range(3):
            self.assertEqual(self.create(op_key(number))[0], 201)
        self.assertEqual(len(self.listing()), 5)  # 既存の2件 + 3件
        self.assertEqual((send("GET", ordinary)[1], send("GET", legacy_url)[1]), before)

    # --- 入力の拒否 ---------------------------------------------------------------

    def test_a_missing_or_invalid_operation_key_is_422_and_changes_nothing(self):
        good = op_key(11)  # 英字を含む（大文字にすると変わる）
        for label, headers in {
            "missing": {},
            "empty": {"Idempotency-Key": ""},
            "not a uuid": {"Idempotency-Key": "not-a-uuid"},
            "too long": {"Idempotency-Key": good + "0"},
            "upper case": {"Idempotency-Key": good.upper()},
            "no hyphens": {"Idempotency-Key": good.replace("-", "")},
            "case id": {"Idempotency-Key": "CASE-SAMPLE-01"},
        }.items():
            with self.subTest(label):
                status, body = self.create(None, None, headers)
                self.assertEqual(status, 422, body)
                self.assertIn("依頼ID", body["detail"])
        self.assertEqual(self.store.cases, {})
        self.assertEqual(self.store.create_calls, [])

    def test_two_operation_keys_in_one_request_are_422_and_change_nothing(self):
        host, _, port = self.server.base_url.removeprefix("http://").partition(":")
        connection = http.client.HTTPConnection(host, int(port), timeout=15)
        try:
            connection.putrequest("POST", "/api/sample-cases")
            connection.putheader("Idempotency-Key", op_key(1))
            connection.putheader("Idempotency-Key", op_key(2))
            connection.putheader("Content-Length", "0")
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 422)
        finally:
            connection.close()
        self.assertEqual((self.store.cases, self.store.create_calls), ({}, []))

    # --- 失敗後の再試行 ------------------------------------------------------------

    def test_busy_store_503_creates_nothing_and_retry_with_the_same_key_creates_one(
        self,
    ):
        key = op_key(6)
        case_id = self.case_id_of(key)
        self.store.busy_once.add(case_id)
        status, body = self.create(key)
        self.assertEqual(status, 503, body)
        self.assertIn("もう一度", body["detail"])
        self.assertEqual(self.store.cases, {})  # 不正な部分保存はない
        status, body = self.create(key)  # 同じ操作の再試行
        self.assertEqual((status, body), (201, {"case_id": case_id, "created": True}))
        self.assertEqual(list(self.store.cases), [case_id])
        self.assertEqual(self.create(key)[0], 200)  # さらに再送しても重複しない
        self.assertEqual(len(self.listing()), 1)

    def test_unexpected_store_error_500_creates_nothing_and_retry_creates_one(self):
        key = op_key(13)
        case_id = self.case_id_of(key)
        self.store.crash_once.add(case_id)
        status, _ = self.create(key)
        self.assertEqual(status, 500)
        self.assertEqual(self.store.cases, {})
        status, body = self.create(key)
        self.assertEqual((status, body["created"]), (201, True))
        self.assertEqual(list(self.store.cases), [case_id])

    def test_a_failure_does_not_affect_other_operations_or_stored_cases(self):
        self.create(op_key(21))
        kept = repr(self.store.cases)
        key = op_key(22)
        self.store.busy_once.add(self.case_id_of(key))
        self.assertEqual(self.create(key)[0], 503)
        self.assertEqual(repr(self.store.cases), kept)

    def test_losing_a_race_on_the_same_operation_is_reported_as_existing(self):
        key = op_key(14)
        case_id = self.case_id_of(key)
        self.store.lose_race.add(case_id)
        status, body = self.create(key)
        self.assertEqual((status, body), (200, {"case_id": case_id, "created": False}))
        self.assertEqual(len(self.listing()), 1)
        # 先に作られた側の内容のまま（上書きしない）
        self.assertEqual(self.store.cases[case_id]["answers"]["A09"], "no")

    # --- 同時の到達 ------------------------------------------------------------------

    def test_concurrent_requests_with_the_same_key_create_one_case(self):
        key = op_key(15)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.create(key), range(16)))
        self.assertTrue(all(status in (200, 201) for status, _ in results))
        self.assertEqual(sum(body["created"] for _, body in results), 1)
        self.assertEqual(
            {body["case_id"] for _, body in results}, {self.case_id_of(key)}
        )
        self.assertEqual(len(self.listing()), 1)

    def test_concurrent_requests_with_distinct_keys_create_one_case_each(self):
        keys = [op_key(100 + n) for n in range(16)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.create, keys))
        self.assertEqual([status for status, _ in results], [201] * 16)
        self.assertEqual(len({body["case_id"] for _, body in results}), 16)
        self.assertEqual(len(self.listing()), 16)

    # --- 保存先がDBでない ---------------------------------------------------

    def test_a_store_without_database_support_returns_503_and_creates_nothing(self):
        store = intake.InMemoryCaseStore()
        with _Server(make_sample_app(store, [])) as server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/sample-cases",
                None,
                {"Idempotency-Key": op_key(1)},
            )
        self.assertEqual(status, 503)
        self.assertIn("DB", body["detail"])
        self.assertEqual(store.cases, {})


class SampleDefinitions(unittest.TestCase):
    def test_the_templates_match_the_seed_script_composition(self):
        templates = sample_cases.SAMPLE_TEMPLATES
        self.assertEqual(
            [(t["a09_answer"], t["result_code"]) for t in templates],
            [("yes", None), ("no", None), (None, None), ("yes", "no_issue")],
        )
        for template in templates:
            number = int(template["user"]["user_id"])
            if template["a09_answer"] is not None:  # モックと同じ規則（偶数はyes）
                expected = "yes" if number % 2 == 0 else "no"
                self.assertEqual(template["a09_answer"], expected)
            self.assertIn("架空", template["inquiry"]["body"])

    def test_the_case_is_decided_by_the_operation_key_alone(self):
        key = op_key(40)
        first = sample_cases.sample_for_operation(key)
        self.assertEqual(first, sample_cases.sample_for_operation(key))
        self.assertRegex(first["case_id"], r"^CASE-SAMPLE-[0-9A-F]{32}$")
        # 依頼IDの32桁すべてが案件IDに残る（切り詰めると、別の依頼IDが同じ案件になる）
        base = "12345678-1234-4234-8234-0000000000"
        keys = [f"{base}{n:02x}" for n in range(256)]
        self.assertEqual(
            len({sample_cases.sample_for_operation(k)["case_id"] for k in keys}), 256
        )
        # 以前の固定ID（CASE-SAMPLE-01〜04）とは重ならない
        legacy = {f"CASE-SAMPLE-{n:02d}" for n in range(1, 5)}
        ids = {
            sample_cases.sample_for_operation(str(uuid.uuid4()))["case_id"]
            for _ in range(200)
        }
        self.assertEqual(len(ids), 200)
        self.assertFalse(ids & legacy)
        shapes = {
            sample_cases.sample_for_operation(op_key(n))["summary"] for n in range(8)
        }
        self.assertEqual(len(shapes), 4)  # 4つの型がそろう


class PublicGuardForSampleCases(unittest.TestCase):
    def test_only_post_to_the_fixed_route_is_allowed(self):
        self.assertTrue(is_allowed_write("POST", "/api/sample-cases", False))
        for method, path in (
            ("PUT", "/api/sample-cases"),
            ("PATCH", "/api/sample-cases"),
            ("DELETE", "/api/sample-cases"),
            ("POST", "/api/sample-cases/"),
            ("POST", "/api/sample-cases/CASE-SAMPLE-01"),
            ("POST", "/api/sample-cases/reset"),
            ("DELETE", "/api/cases/CASE-SAMPLE-01"),
            ("POST", "/api/cases/CASE-SAMPLE-01"),
        ):
            with self.subTest(method=method, path=path):
                self.assertFalse(is_allowed_write(method, path, False))

    def test_guarded_app_serves_the_route_and_rejects_the_rest_before_the_app(self):
        store = SampleStore()
        application = make_sample_app(store, [])
        application.add_middleware(PublicWriteGuard)
        with _Server(application) as server:
            base = server.base_url
            status, _ = send(
                "POST", f"{base}/api/sample-cases", None, {"Idempotency-Key": op_key(1)}
            )
            self.assertEqual(status, 201)
            for method, path in (
                ("DELETE", "/api/sample-cases"),
                ("PUT", "/api/sample-cases"),
                ("POST", "/api/sample-cases/reset"),
                ("DELETE", "/api/cases/CASE-SAMPLE-01"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertEqual(send(method, f"{base}{path}")[0], 405)
            self.assertEqual(len(store.cases), 1)  # 拒否した要求は何も変えない
