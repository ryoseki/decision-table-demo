"""共有サンプル作成のHTTP契約をDBなしで検証する。案件・値はすべて架空。

新しい操作で1件ずつ作ることと、同じ操作の再送で増えないことだけを確認する。
保存先はメモリ上のテスト用実装。SQL・制約は tests/test_db_store.py で確認する。
"""

import hashlib
import os
import unittest
import uuid
from unittest import mock

from fastapi import FastAPI

from app import sample_cases
from app.domain import OPERATOR_ID
from tests.test_api_contract import ContractStore
from tests.test_intake import _Server, make_app, send


def op_key(number: int) -> str:
    """number から決まる、小文字のUUID（決まった入力）。"""
    return str(uuid.UUID(int=(number << 64) | number))


class SampleStore(ContractStore):
    """操作IDと案件IDの対応を持つ、HTTP契約用のメモリ保存先。"""

    def __init__(self) -> None:
        super().__init__()
        self.operations: dict[str, str] = {}

    def case_id_for_operation(self, key: str) -> str:
        # HTTP契約用の決まったテスト採番。実DBのランダム採番はDB統合で確認する。
        return self.operations.get(
            key, "CASE-" + hashlib.sha256(key.encode()).hexdigest()[:8].upper()
        )

    def create_sample_operation(self, sample: dict) -> tuple[str, bool]:
        key = sample["operation_key"]
        if key in self.operations:
            return self.operations[key], False
        case_id = self.case_id_for_operation(key)
        created = self.create_sample_case({**sample, "case_id": case_id})
        self.operations[key] = case_id
        return case_id, created

    def create_sample_case(self, sample: dict) -> bool:
        case_id = sample["case_id"]
        if case_id in self.cases:
            return False
        answer = sample["a09_answer"]
        result = None
        if sample["result_code"] is not None:
            result = {
                "result_code": sample["result_code"],
                "adopted_rule": None,
                "recorded_by": OPERATOR_ID,
            }
        self.cases[case_id] = {
            "summary": sample["summary"],
            "user": sample["user"],
            "inquiry": sample["inquiry"],
            "answers": {"A09": answer or "pending"},
            "automated_answers": {} if answer is None else {"A09": answer},
            "result": result,
        }
        return True


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

    def create(self, key: str):
        return send(
            "POST",
            f"{self.server.base_url}/api/sample-cases",
            None,
            {"Idempotency-Key": key},
        )

    def listing(self) -> list[dict]:
        status, rows = send("GET", f"{self.server.base_url}/api/cases")
        self.assertEqual(status, 200)
        return rows

    def test_each_new_operation_creates_one_more_case(self):
        ids = []
        for number in (1, 2):
            key = op_key(number)
            status, body = self.create(key)
            self.assertEqual(status, 201, body)
            self.assertEqual(
                body,
                {"case_id": self.store.case_id_for_operation(key), "created": True},
            )
            self.assertRegex(body["case_id"], r"^CASE-[0-9A-F]{8}$")
            ids.append(body["case_id"])
            self.assertEqual(len(self.listing()), number)  # 1操作で1件ずつ増える
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(self.mock_calls, [])  # HTTPモックは呼ばない

    def test_resending_the_same_operation_does_not_add_or_change_a_case(self):
        key = op_key(7)
        _, first = self.create(key)
        url = f"{self.server.base_url}/api/cases/{first['case_id']}"
        before = send("GET", url)[1]
        self.assertEqual(before["result"]["result_code"], "no_issue")
        status, body = self.create(key)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"case_id": first["case_id"], "created": False})
        self.assertEqual(len(self.listing()), 1)
        self.assertEqual(send("GET", url)[1], before)  # 保存済みの判定も変わらない
