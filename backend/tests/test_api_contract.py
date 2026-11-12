"""案件の再表示と［判定する］のHTTP契約の検証（DBなし）。

見るのはHTTPの入力と出力（ステータスと本文）で、DBの行ではない。
保存先は `InMemoryCaseStore` を継承したテスト用の保存先で、
DB・docs・環境変数は要らない。コンテナ内では次で素早く回せる。
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
（--build は、ソースがイメージにコピーされる構成のため。付けないと古いソースで走る）

ここで確認しないこと: トランザクション、ロールバック、同時保存の直列化、
SQL、DDLとの整合。これらは実DBが必要で、`bash scripts/backend-db-test.sh` の
`test_db_store.py` が受け持つ。
テスト用の保存先は、PostgreSQLの実装と同じ振る舞いを手で写したもので、
同一性は保証しない（同じ契約テストを両方に流す形にはしていない）。
案件・値はすべて架空。
"""

import json
import os
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime
from unittest import mock

from app import intake
from app.domain import (
    HUMAN_ANSWERS,
    OPERATOR_ID,
    RESULT_CODES,
    CaseNotFound,
    InvalidJudgment,
)
from tests.test_intake import _Server, case_payload, make_app, send

QUESTION_CODES = [f"A{n:02d}" for n in range(1, 13)]


class ContractStore(intake.InMemoryCaseStore):
    """get_case と save_judgment を持つ、DBなしの保存先。

    PostgresCaseStore の契約を写す。

    返す項目は、契約テストが確認するものだけ（設問の文面などは持たない）。
    検査はすべて更新の前に行うので、失敗した保存は何も変えない。
    save_judgment の呼び出しを save_calls に記録し、HTTP層が保存先を呼んだ回数と
    引数を確認できるようにする。
    """

    def __init__(self) -> None:
        super().__init__()
        self.save_calls: list[tuple] = []

    def get_case(self, case_id: str) -> dict:
        stored = self.cases.get(case_id)
        if stored is None:
            raise CaseNotFound(case_id)
        return {
            "case_id": case_id,
            "summary": stored["summary"],
            "user": stored["user"],
            "inquiry": stored["inquiry"],
            "period_start_at": stored.get("period_start_at"),
            "period_end_at": stored.get("period_end_at"),
            # None は未回答（回答の行がない）。pending は確認待ち。
            "questions": [
                {"question_code": code, "answer": stored["answers"].get(code)}
                for code in QUESTION_CODES
            ],
            "result": stored.get("result"),
        }

    def list_cases(self) -> list[dict]:
        """一覧の各行。並びは案件ID順（受付日時の列がないため）。"""
        rows = []
        for case_id in sorted(self.cases):
            stored = self.cases[case_id]
            counts = {"yes": 0, "no": 0, "pending": 0}
            for answer in stored["answers"].values():
                counts[answer] += 1
            counts["unanswered"] = len(QUESTION_CODES) - len(stored["answers"])
            result = stored.get("result")
            rows.append(
                {
                    "case_id": case_id,
                    "summary": stored["summary"],
                    "user_id": stored["user"]["user_id"],
                    "inquiry": stored["inquiry"],
                    "result_code": result["result_code"] if result else None,
                    "counts": counts,
                }
            )
        return rows

    def save_judgment(self, case_id, result_code, answers, adopted_rule) -> None:
        self.save_calls.append((case_id, result_code, dict(answers), adopted_rule))
        stored = self.cases.get(case_id)
        if stored is None:
            raise CaseNotFound(case_id)
        if result_code not in RESULT_CODES:
            raise InvalidJudgment(
                f"result_code が定義済みのコードではありません: {result_code}"
            )
        for code, answer in answers.items():
            if code not in QUESTION_CODES:
                raise InvalidJudgment(f"案件にない設問です: {code}")
            if answer == "pending" and stored["answers"].get(code) != "pending":
                raise InvalidJudgment(f"{code}: pending にはできません")
            if answer not in (*HUMAN_ANSWERS, "pending"):
                raise InvalidJudgment(f"{code}: 回答が不正です")
        rule_id, rule_revision = adopted_rule if adopted_rule else (None, None)
        stored["result"] = {
            "result_code": result_code,
            "adopted_rule": (
                {"rule_id": rule_id, "rule_revision": rule_revision}
                if rule_id is not None
                else None
            ),
            "recorded_by": OPERATOR_ID,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        stored["answers"].update(answers)


class CasesApiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_calls: list[str] = []
        cls.store = ContractStore()
        cls.server = _Server(make_app(cls.store, cls.mock_calls)).__enter__()
        cls.env = mock.patch.dict(os.environ, {"MOCK_BASE_URL": cls.server.base_url})
        cls.env.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env.stop()
        cls.server.__exit__()

    # --- ヘルパー ---------------------------------------------------------------

    def intake(self, user_id: str = "456", **extra) -> str:
        """偶数の利用者IDならA09はyes、奇数ならno（モックの契約）。"""
        status, body = send(
            "POST",
            f"{self.server.base_url}/api/intake/cases",
            case_payload(user_id, **extra),
        )
        self.assertEqual(status, 201, body)
        return body["case_id"]

    def intake_with_pending_a09(self) -> str:
        with (
            mock.patch("app.intake.MOCK_TIMEOUT_SECONDS", 0.5),
            mock.patch("app.mock_api.FAULT_DELAY_SECONDS", 2.0),
        ):
            return self.intake("458", demo_controls={"fault_questions": ["A09"]})

    def get(self, case_id: str) -> dict:
        status, body = send("GET", f"{self.server.base_url}/api/cases/{case_id}")
        self.assertEqual(status, 200, body)
        return body

    def judge(self, case_id: str, payload) -> tuple[int, dict]:
        return send(
            "PUT", f"{self.server.base_url}/api/cases/{case_id}/judgment", payload
        )

    @staticmethod
    def answers_of(view: dict) -> dict:
        return {q["question_code"]: q["answer"] for q in view["questions"]}

    # --- 再表示（GET） ----------------------------------------------------------

    def test_get_unknown_case_is_404(self):
        status, body = send("GET", f"{self.server.base_url}/api/cases/CASE-NOPE")
        self.assertEqual(status, 404)
        self.assertEqual(body["detail"], "案件がありません")

    def test_get_after_intake_is_unjudged_and_does_not_call_the_mock(self):
        case_id = self.intake("456")
        calls_after_intake = len(self.mock_calls)
        view = self.get(case_id)
        self.get(case_id)
        self.assertEqual(len(self.mock_calls), calls_after_intake)
        self.assertIsNone(view["result"])
        self.assertEqual(len(view["questions"]), 12)
        answers = self.answers_of(view)
        self.assertEqual(answers["A09"], "yes")
        self.assertTrue(all(v is None for k, v in answers.items() if k != "A09"))

    def test_get_shows_pending_when_the_fetch_failed(self):
        case_id = self.intake_with_pending_a09()
        self.assertEqual(self.answers_of(self.get(case_id))["A09"], "pending")

    # --- ［判定する］（PUT） ----------------------------------------------------

    # --- 一覧（GET /api/cases） --------------------------------------------------

    def list_cases(self) -> list[dict]:
        status, body = send("GET", f"{self.server.base_url}/api/cases")
        self.assertEqual(status, 200, body)
        return body

    def test_list_returns_summary_rows_in_case_id_order(self):
        yes_case = self.intake("456")  # A09 は yes
        no_case = self.intake("457")  # A09 は no
        pending_case = self.intake_with_pending_a09()  # A09 は確認待ち
        rows = self.list_cases()
        ids = [row["case_id"] for row in rows]
        self.assertEqual(ids, sorted(ids))  # 案件ID順（仮）
        by_id = {row["case_id"]: row for row in rows}
        row = by_id[yes_case]
        self.assertEqual(
            set(row),
            {"case_id", "summary", "user_id", "inquiry", "result_code", "counts"},
        )
        self.assertEqual(row["user_id"], "456")
        self.assertEqual(set(row["inquiry"]), {"subject", "body"})
        self.assertIsNone(row["result_code"])  # 未判定は null
        self.assertEqual(
            row["counts"], {"yes": 1, "no": 0, "pending": 0, "unanswered": 11}
        )
        self.assertEqual(by_id[no_case]["counts"]["no"], 1)
        self.assertEqual(
            by_id[pending_case]["counts"],
            {"yes": 0, "no": 0, "pending": 1, "unanswered": 11},
        )

    def test_list_reflects_a_saved_judgment(self):
        case_id = self.intake()
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        row = {r["case_id"]: r for r in self.list_cases()}[case_id]
        self.assertEqual(row["result_code"], "caution")
        self.assertEqual(
            row["counts"], {"yes": 2, "no": 0, "pending": 0, "unanswered": 10}
        )

    def test_list_does_not_call_the_mock(self):
        self.intake()
        calls = len(self.mock_calls)
        self.list_cases()
        self.assertEqual(len(self.mock_calls), calls)

    def test_put_unknown_case_is_404(self):
        status, body = self.judge("CASE-NOPE", {"result_code": "caution"})
        self.assertEqual(status, 404)
        self.assertEqual(body["detail"], "案件がありません")

    def test_put_saves_the_result_and_keeps_the_answers(self):
        case_id = self.intake_with_pending_a09()
        before = self.answers_of(self.get(case_id))
        calls = len(self.mock_calls)
        status, _ = self.judge(case_id, {"result_code": "caution"})
        self.assertEqual(status, 204)
        view = self.get(case_id)
        self.assertEqual(view["result"]["result_code"], "caution")
        self.assertEqual(view["result"]["recorded_by"], "OP-1")
        self.assertIsNone(view["result"]["adopted_rule"])
        self.assertEqual(self.answers_of(view), before)
        self.assertEqual(len(self.mock_calls), calls, "保存でHTTPを呼ばない")

    def test_put_records_the_adopted_rule_only_when_both_fields_are_sent(self):
        case_id = self.intake()
        self.judge(case_id, {"result_code": "caution"})
        self.assertIsNone(self.get(case_id)["result"]["adopted_rule"])
        # 採用ルールは参考の記録で、選んだ結果と違っても保存できる（S10）。
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 1}
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            self.get(case_id)["result"]["adopted_rule"],
            {"rule_id": "R01", "rule_revision": 1},
        )

    def test_put_updates_only_the_given_answers(self):
        case_id = self.intake()
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "answers": {"A01": "yes", "A02": "no"}}
        )
        self.assertEqual(status, 204)
        answers = self.answers_of(self.get(case_id))
        self.assertEqual((answers["A01"], answers["A02"]), ("yes", "no"))
        self.assertIsNone(answers["A03"])  # 渡さない設問は未回答のまま
        self.assertEqual(answers["A09"], "yes")

    def test_put_keeps_only_the_latest_judgment(self):
        case_id = self.intake()
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        self.judge(case_id, {"result_code": "no_issue", "answers": {"A01": "no"}})
        view = self.get(case_id)
        self.assertEqual(view["result"]["result_code"], "no_issue")
        self.assertEqual(self.answers_of(view)["A01"], "no")

    def test_put_is_idempotent_when_the_same_judgment_is_resent(self):
        case_id = self.intake()
        payload = {"result_code": "caution", "answers": {"A01": "yes"}}
        self.assertEqual(self.judge(case_id, payload)[0], 204)
        first = self.get(case_id)
        self.assertEqual(self.judge(case_id, payload)[0], 204)
        second = self.get(case_id)
        # 登録日時を除いて、同じ状態になる（結果・回答・採用ルールは変わらない）。
        for view in (first, second):
            view["result"].pop("recorded_at")
        self.assertEqual(first, second)

    def test_put_may_replace_pending_with_a_human_answer(self):
        case_id = self.intake_with_pending_a09()
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "answers": {"A09": "no"}}
        )
        self.assertEqual(status, 204)
        view = self.get(case_id)
        self.assertEqual(self.answers_of(view)["A09"], "no")
        self.assertEqual(
            view["result"]["result_code"], "no_issue"
        )  # 結果も、同時に保存される

    def test_put_may_replace_pending_with_yes_and_keeps_the_rest(self):
        case_id = self.intake_with_pending_a09()
        status, _ = self.judge(
            case_id, {"result_code": "caution", "answers": {"A09": "yes"}}
        )
        self.assertEqual(status, 204)
        view = self.get(case_id)
        answers = self.answers_of(view)
        self.assertEqual(answers["A09"], "yes")
        self.assertEqual(view["result"]["result_code"], "caution")
        # 送っていない設問は、未回答のまま変わらない
        self.assertTrue(all(v is None for k, v in answers.items() if k != "A09"))

    # --- 入力の拒否（422）。拒否した保存は何も変えない ---------------------------

    def assert_rejected_and_unchanged(
        self, case_id: str, payload, reason: str | None = None
    ) -> None:
        """422 で拒否し、何も変えない。reason があれば、拒否の理由（detail）に含まれる。

        ステータスだけでは、誤った理由での422でも通ってしまうため、理由も確認する。
        """
        before = self.get(case_id)
        status, body = self.judge(case_id, payload)
        self.assertEqual(status, 422, payload)
        if reason is not None:
            self.assertIn(reason, json.dumps(body.get("detail"), ensure_ascii=False))
        self.assertEqual(self.get(case_id), before)

    def test_put_rejects_an_undefined_or_missing_result_code(self):
        case_id = self.intake()
        # 定義にない結果コードは、保存先の検査で拒否される（理由に、そのコードが入る）。
        self.assert_rejected_and_unchanged(
            case_id, {"result_code": "unknown"}, reason="unknown"
        )
        # 欠落・型の違いは、HTTP層の検査で拒否される（理由に、項目名が入る）。
        self.assert_rejected_and_unchanged(case_id, {}, reason="result_code")
        self.assert_rejected_and_unchanged(
            case_id, {"result_code": 1}, reason="result_code"
        )

    def test_put_rejects_a_rule_that_is_not_both_or_neither(self):
        case_id = self.intake()
        for rule in (
            {"rule_id": "R01"},
            {"rule_revision": 1},
            {"rule_id": "R01", "rule_revision": 0},
            {"rule_id": "R01", "rule_revision": "1"},
        ):
            with self.subTest(rule=rule):
                self.assert_rejected_and_unchanged(
                    case_id, {"result_code": "caution", **rule}
                )

    def test_put_rejects_bad_answers_and_saves_nothing(self):
        case_id = self.intake("456")  # A09 はyes
        for answers, reason in (
            ({"A01": "maybe"}, "answers"),  # 回答の値が不正（HTTP層）
            ({"A01": None}, "answers"),  # 回答を空に戻す操作はない（HTTP層）
            ({"A09": "pending"}, "A09"),  # yes から確認待ちには戻せない（保存先）
            ({"A01": "pending"}, "A01"),  # 未回答から確認待ちは作れない（保存先）
            ({"Z99": "yes"}, "Z99"),  # 案件にない設問（保存先）
            ({"A01": "yes", "Z99": "yes"}, "Z99"),  # 一部が正しくても全体を拒否する
        ):
            with self.subTest(answers=answers):
                self.assert_rejected_and_unchanged(
                    case_id,
                    {"result_code": "caution", "answers": answers},
                    reason=reason,
                )

    def put_raw(self, case_id: str, body: bytes) -> int:
        """JSONとして不正な本文など、send() では作れない本文を送る。"""
        request = urllib.request.Request(
            f"{self.server.base_url}/api/cases/{case_id}/judgment",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    def test_put_rejects_a_body_that_is_not_a_json_object(self):
        case_id = self.intake()
        before = self.get(case_id)
        for body in (b"{not json", b"", b"[]", b'"caution"', b"null", b"1"):
            with self.subTest(body=body):
                self.assertEqual(self.put_raw(case_id, body), 422)
        self.assertEqual(self.get(case_id), before)

    def test_put_rejects_answers_that_are_not_an_object(self):
        case_id = self.intake()
        for answers in ("x", ["A01"], [], 1, True):
            with self.subTest(answers=answers):
                self.assert_rejected_and_unchanged(
                    case_id, {"result_code": "caution", "answers": answers}
                )

    def test_put_checks_the_body_before_looking_up_the_case(self):
        # 不正な本文は、案件があるかどうかにかかわらず422（404より先に検査する）。
        status, _ = self.judge("CASE-NOPE", {"result_code": 1})
        self.assertEqual(status, 422)
        self.assertEqual(self.put_raw("CASE-NOPE", b"{not json"), 422)

    # --- HTTP層が保存先を呼ぶ回数と引数 ---------------------------------------------

    def test_put_calls_the_store_once_with_the_given_arguments(self):
        case_id = self.intake()
        before = len(self.store.save_calls)
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        self.judge(
            case_id, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 2}
        )
        self.assertEqual(
            self.store.save_calls[before:],
            [
                (case_id, "caution", {"A01": "yes"}, None),
                (case_id, "no_issue", {}, ("R01", 2)),
            ],
        )

    def test_bodies_rejected_by_the_http_layer_never_reach_the_store(self):
        case_id = self.intake()
        before = len(self.store.save_calls)
        for body in (
            {},
            {"result_code": 1},
            {"result_code": "caution", "rule_id": "R01"},
            {"result_code": "caution", "rule_revision": 1},
            {"result_code": "caution", "answers": "x"},
            {"result_code": "caution", "answers": {"A01": "maybe"}},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.judge(case_id, body)[0], 422)
        self.assertEqual(self.put_raw(case_id, b"{not json"), 422)
        self.assertEqual(len(self.store.save_calls), before)

    def test_redisplay_never_calls_save(self):
        case_id = self.intake()
        before = len(self.store.save_calls)
        self.get(case_id)
        self.get(case_id)
        self.assertEqual(len(self.store.save_calls), before)


class EmptyList(unittest.TestCase):
    """案件が1件もないときの一覧は、空の配列（200）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server(make_app(ContractStore(), [])).__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__()

    def test_list_is_an_empty_array(self):
        self.assertEqual(send("GET", f"{self.server.base_url}/api/cases"), (200, []))


class WithoutDatabase(unittest.TestCase):
    """保存先がDBでない（開発用のメモリ保存）ときの契約: 503。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server(make_app(intake.InMemoryCaseStore(), [])).__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__()

    def test_get_and_put_return_503(self):
        base = f"{self.server.base_url}/api/cases/CASE-ANY"
        self.assertEqual(send("GET", base)[0], 503)
        self.assertEqual(send("GET", f"{self.server.base_url}/api/cases")[0], 503)
        self.assertEqual(
            send("PUT", f"{base}/judgment", {"result_code": "caution"})[0], 503
        )


if __name__ == "__main__":
    unittest.main()
