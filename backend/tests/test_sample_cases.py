"""共有のサンプル案件（POST /api/sample-cases）のHTTP契約の検証（DBなし）。

見るのはHTTPの入力と出力（ステータスと本文）と、再実行・一部失敗後の再試行・同時の到達で
案件が重複せず、既存の案件と保存済みの判定が変わらないこと。DBの行・SQL・トランザクションは、
`bash scripts/backend-db-test.sh` の `test_db_store.py`（DB統合）が受け持つ。
保存先は、PostgresCaseStore の契約を写したテスト用のもの（同一性は保証しない）。
案件・値はすべて架空。
"""

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi import FastAPI

from app import intake, sample_cases
from app.domain import OPERATOR_ID, StoreBusy
from app.public_guard import PublicWriteGuard, is_allowed_write
from tests.test_api_contract import ContractStore
from tests.test_intake import _Server, case_payload, make_app, send


class SampleStore(ContractStore):
    """create_sample_case と existing_case_ids を持つ、DBなしの保存先。

    PostgresCaseStore の契約を写す: 固定IDが無いときだけ、案件・回答・
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

    def existing_case_ids(self, case_ids) -> set[str]:
        with self.lock:
            return {case_id for case_id in case_ids if case_id in self.cases}

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

    def create(self, payload=None):
        return send("POST", f"{self.server.base_url}/api/sample-cases", payload)

    def listing(self) -> list[dict]:
        status, rows = send("GET", f"{self.server.base_url}/api/cases")
        self.assertEqual(status, 200)
        return rows

    def sample_rows(self) -> list[dict]:
        ids = sample_cases.SAMPLE_CASE_IDS
        return [r for r in self.listing() if r["case_id"] in ids]

    # --- 構成 -------------------------------------------------------------------

    def test_first_request_creates_the_four_fictional_cases_of_the_seed_script(self):
        status, body = self.create()
        self.assertEqual(status, 201, body)
        self.assertEqual(body["created"], list(sample_cases.SAMPLE_CASE_IDS))
        self.assertEqual(body["existing"], [])
        rows = {r["case_id"]: r for r in self.sample_rows()}
        self.assertEqual(sorted(rows), sorted(sample_cases.SAMPLE_CASE_IDS))
        first, second, third, fourth = (rows[i] for i in sample_cases.SAMPLE_CASE_IDS)
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
        user_ids = [r["user_id"] for r in rows.values()]
        self.assertEqual(user_ids, ["456", "457", "458", "460"])

    def test_creating_samples_calls_neither_the_mock_nor_the_ai(self):
        self.create()
        self.assertEqual(self.mock_calls, [])  # A09は固定値。HTTPモックは呼ばない

    def test_the_request_needs_no_body_and_ignores_one(self):
        self.assertEqual(self.create()[0], 201)
        status, body = self.create({"case_ids": ["CASE-OTHER"], "count": 99})
        self.assertEqual(status, 200)
        self.assertEqual(body["created"], [])
        self.assertEqual(len(self.sample_rows()), 4)

    # --- 再実行 -----------------------------------------------------------------

    def test_repeating_creates_nothing_and_writes_nothing(self):
        self.create()
        calls_before = list(self.store.create_calls)
        snapshot = repr(self.store.cases)
        status, body = self.create()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["created"], [])
        self.assertEqual(body["existing"], list(sample_cases.SAMPLE_CASE_IDS))
        self.assertEqual(len(self.sample_rows()), 4)
        # 作成済みには書き込まない
        self.assertEqual(self.store.create_calls, calls_before)
        self.assertEqual(repr(self.store.cases), snapshot)

    def test_a_saved_judgment_on_a_sample_is_not_overwritten_by_repeating(self):
        self.create()
        first = sample_cases.SAMPLE_CASE_IDS[0]
        status, body = send(
            "PUT",
            f"{self.server.base_url}/api/cases/{first}/judgment",
            {"result_code": "caution", "answers": {"A01": "yes"}},
        )
        self.assertEqual(status, 204, body)
        before = send("GET", f"{self.server.base_url}/api/cases/{first}")[1]
        self.assertEqual(before["result"]["result_code"], "caution")
        self.assertEqual(self.create()[0], 200)
        after = send("GET", f"{self.server.base_url}/api/cases/{first}")[1]
        self.assertEqual(after, before)
        fourth_id = sample_cases.SAMPLE_CASE_IDS[3]
        fourth = send("GET", f"{self.server.base_url}/api/cases/{fourth_id}")[1]
        self.assertEqual(fourth["result"]["result_code"], "no_issue")

    def test_existing_ordinary_cases_are_untouched_and_not_counted_as_samples(self):
        status, body = send(
            "POST", f"{self.server.base_url}/api/intake/cases", case_payload("456")
        )
        self.assertEqual(status, 201)
        url = f"{self.server.base_url}/api/cases/{body['case_id']}"
        before = send("GET", url)[1]
        self.assertEqual(self.create()[0], 201)
        self.assertEqual(len(self.listing()), 5)
        self.assertEqual(send("GET", url)[1], before)

    # --- 一部失敗後の再試行 ------------------------------------------------------

    def test_busy_store_503_keeps_the_created_part_and_retry_fills_the_rest(self):
        third = sample_cases.SAMPLE_CASE_IDS[2]
        self.store.busy_once.add(third)
        status, body = self.create()
        self.assertEqual(status, 503, body)
        self.assertIn("もう一度", body["detail"])
        self.assertEqual(
            sorted(r["case_id"] for r in self.sample_rows()),
            sorted(sample_cases.SAMPLE_CASE_IDS[:2]),  # 作成済みの分は残る
        )
        kept = {i: repr(self.store.cases[i]) for i in sample_cases.SAMPLE_CASE_IDS[:2]}
        status, body = self.create()
        self.assertEqual(status, 201, body)
        self.assertEqual(body["created"], list(sample_cases.SAMPLE_CASE_IDS[2:]))
        self.assertEqual(body["existing"], list(sample_cases.SAMPLE_CASE_IDS[:2]))
        self.assertEqual(len(self.sample_rows()), 4)  # 重複しない
        for case_id, snapshot in kept.items():
            self.assertEqual(repr(self.store.cases[case_id]), snapshot)  # 上書きしない
        # 再試行が作成を試みたのは、足りなかった案件だけ
        self.assertEqual(
            self.store.create_calls,
            [*sample_cases.SAMPLE_CASE_IDS[:3], *sample_cases.SAMPLE_CASE_IDS[2:]],
        )

    def test_unexpected_store_error_500_keeps_created_part_and_retry_fills_the_rest(
        self,
    ):
        ids = sample_cases.SAMPLE_CASE_IDS
        self.store.crash_once.add(ids[2])
        status, _ = self.create()
        self.assertEqual(status, 500)
        # 失敗した案件より前は残り、それ以降は作ろうとしていない
        self.assertEqual(self.store.create_calls, list(ids[:3]))
        self.assertEqual(sorted(self.store.cases), sorted(ids[:2]))
        kept = {i: repr(self.store.cases[i]) for i in ids[:2]}
        status, body = self.create()
        self.assertEqual(status, 201, body)
        self.assertEqual(body["created"], list(ids[2:]))
        self.assertEqual(body["existing"], list(ids[:2]))
        self.assertEqual(len(self.sample_rows()), 4)
        for case_id, snapshot in kept.items():
            self.assertEqual(repr(self.store.cases[case_id]), snapshot)

    def test_losing_a_race_is_reported_as_existing_and_not_overwritten(self):
        raced = sample_cases.SAMPLE_CASE_IDS[1]
        self.store.lose_race.add(raced)
        status, body = self.create()
        self.assertEqual(status, 201, body)
        self.assertNotIn(raced, body["created"])
        self.assertIn(raced, body["existing"])
        self.assertEqual(len(self.sample_rows()), 4)
        # 先に作られた側の内容（A09=no）のまま
        self.assertEqual(self.store.cases[raced]["answers"]["A09"], "no")

    def test_concurrent_requests_leave_four_samples_and_create_each_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.create(), range(16)))
        self.assertTrue(all(status in (200, 201) for status, _ in results))
        created = [i for _, body in results for i in body["created"]]
        self.assertEqual(sorted(created), sorted(sample_cases.SAMPLE_CASE_IDS))
        self.assertEqual(len(self.sample_rows()), 4)
        self.assertEqual(len(self.listing()), 4)

    # --- 保存先がDBでない ---------------------------------------------------------

    def test_a_store_without_database_support_returns_503_and_creates_nothing(self):
        store = intake.InMemoryCaseStore()
        with _Server(make_sample_app(store, [])) as server:
            status, body = send("POST", f"{server.base_url}/api/sample-cases")
        self.assertEqual(status, 503)
        self.assertIn("DB", body["detail"])
        self.assertEqual(store.cases, {})


class SampleDefinitions(unittest.TestCase):
    def test_the_set_matches_the_seed_script_composition(self):
        samples = sample_cases.SAMPLE_CASES
        self.assertEqual(len(samples), 4)
        self.assertEqual(len(set(sample_cases.SAMPLE_CASE_IDS)), 4)
        self.assertEqual(
            [(s["a09_answer"], s["result_code"]) for s in samples],
            [("yes", None), ("no", None), (None, None), ("yes", "no_issue")],
        )
        for sample in samples:
            number = int(sample["user"]["user_id"])
            if sample["a09_answer"] is not None:  # モックと同じ規則（偶数はyes）
                expected = "yes" if number % 2 == 0 else "no"
                self.assertEqual(sample["a09_answer"], expected)
            self.assertIn("架空", sample["inquiry"]["body"])


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
            self.assertEqual(send("POST", f"{base}/api/sample-cases")[0], 201)
            for method, path in (
                ("DELETE", "/api/sample-cases"),
                ("PUT", "/api/sample-cases"),
                ("POST", "/api/sample-cases/reset"),
                ("DELETE", "/api/cases/CASE-SAMPLE-01"),
            ):
                with self.subTest(method=method, path=path):
                    self.assertEqual(send(method, f"{base}{path}")[0], 405)
            self.assertEqual(len(store.cases), 4)  # 拒否した要求は何も変えない
