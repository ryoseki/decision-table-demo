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
    JudgmentAlreadyRecorded,
    StoreBusy,
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
        # 不変の行。条件を変えるときは新しい revision の行を足す（旧版は残る）。
        self.rule_rows: list[dict] = []

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

    def list_rules(self) -> list[dict]:
        """rule_id ごとに最新の revision の行。並びは rule_id 順。"""
        latest: dict[str, dict] = {}
        for row in self.rule_rows:
            current = latest.get(row["rule_id"])
            if current is None or row["revision"] > current["revision"]:
                latest[row["rule_id"]] = row
        return [latest[rule_id] for rule_id in sorted(latest)]

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
        if stored.get("result") is not None:
            raise JudgmentAlreadyRecorded(case_id)  # 登録済みは何も変えない
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
        if adopted_rule is not None and not any(
            row["rule_id"] == rule_id and row["revision"] == rule_revision
            for row in self.rule_rows
        ):
            raise InvalidJudgment(
                f"採用ルールが存在しません: {rule_id} の版 {rule_revision}"
            )
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
        self.store.rule_rows = [rule_row("R01", 1, "caution", A01="yes")]
        without_rule = self.intake()
        self.judge(without_rule, {"result_code": "caution"})
        self.assertIsNone(self.get(without_rule)["result"]["adopted_rule"])
        case_id = self.intake()
        # 採用ルールは参考の記録で、選んだ結果と違っても保存できる（S10）。
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 1}
        )
        self.assertEqual(status, 204)
        self.assertEqual(
            self.get(case_id)["result"]["adopted_rule"],
            {"rule_id": "R01", "rule_revision": 1},
        )

    def test_put_rejects_an_adopted_rule_that_does_not_exist_and_saves_nothing(self):
        self.store.rule_rows = [rule_row("R01", 1, "caution", A01="yes")]
        case_id = self.intake()
        before = self.get(case_id)
        for rule_id, revision in (("NOPE", 1), ("R01", 2)):  # 別のルール、ない版
            with self.subTest(rule_id=rule_id, revision=revision):
                status, body = self.judge(
                    case_id,
                    {
                        "result_code": "no_issue",
                        "rule_id": rule_id,
                        "rule_revision": revision,
                        "answers": {"A01": "yes"},
                    },
                )
                self.assertEqual(status, 422)
                self.assertIn("採用ルールが存在しません", body["detail"])
                self.assertEqual(self.get(case_id), before)  # 結果も回答も変わらない

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

    # --- 登録済みの案件は再保存できない（2026-11-15の合意。旧「上書き」を置換） ---

    def test_put_is_allowed_once_before_registration(self):
        # 初回の保存前は、回答を直して結果を選べる。
        case_id = self.intake()
        self.assertIsNone(self.get(case_id)["result"])
        status, _ = self.judge(
            case_id, {"result_code": "caution", "answers": {"A01": "yes"}}
        )
        self.assertEqual(status, 204)
        view = self.get(case_id)
        self.assertEqual(view["result"]["result_code"], "caution")
        self.assertEqual(self.answers_of(view)["A01"], "yes")

    def test_put_rejects_a_resave_of_a_registered_case_and_keeps_stored_values(self):
        case_id = self.intake()
        self.store.rule_rows = [rule_row("R01", 1, "caution", A01="yes")]
        first = {
            "result_code": "caution",
            "rule_id": "R01",
            "rule_revision": 1,
            "answers": {"A01": "yes"},
        }
        self.assertEqual(self.judge(case_id, first)[0], 204)
        before = self.get(case_id)
        resaves = (
            first,  # 同じ内容の再送も、成功にしない
            {"result_code": "no_issue", "answers": {"A01": "no", "A02": "yes"}},
            {"result_code": "no_issue"},  # 結果だけの変更
            {"result_code": "caution", "answers": {"A02": "no"}},  # 回答だけの変更
        )
        for payload in resaves:
            with self.subTest(payload=payload):
                status, body = self.judge(case_id, payload)
                self.assertEqual(status, 409)
                self.assertIn("再保存できません", body["detail"])
                self.assertEqual(self.get(case_id), before)  # 結果も回答も採用も不変

    def test_put_rejects_adding_an_answer_to_a_case_registered_with_only_a_result(self):
        case_id = self.intake()
        self.assertEqual(self.judge(case_id, {"result_code": "no_issue"})[0], 204)
        before = self.get(case_id)
        status, _ = self.judge(
            case_id, {"result_code": "no_issue", "answers": {"A03": "yes"}}
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.get(case_id), before)
        self.assertIsNone(self.answers_of(before)["A03"])

    def test_put_rejects_resolving_a_pending_answer_after_registration(self):
        case_id = self.intake_with_pending_a09()
        self.assertEqual(self.judge(case_id, {"result_code": "caution"})[0], 204)
        before = self.get(case_id)
        status, _ = self.judge(
            case_id, {"result_code": "caution", "answers": {"A09": "no"}}
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.get(case_id), before)
        self.assertEqual(self.answers_of(before)["A09"], "pending")

    def test_a_registered_case_blocks_only_its_own_resave(self):
        registered = self.intake()
        other = self.intake()
        self.judge(registered, {"result_code": "caution"})
        self.assertEqual(self.judge(registered, {"result_code": "no_issue"})[0], 409)
        self.assertEqual(self.judge(other, {"result_code": "no_issue"})[0], 204)

    def test_get_marks_a_registered_case_as_judged_with_its_stored_result(self):
        case_id = self.intake()
        self.assertIs(self.get(case_id)["judged"], False)
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        view = self.get(case_id)
        self.assertIs(view["judged"], True)
        self.assertEqual(view["result"]["result_code"], "caution")

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
        self.store.rule_rows = [rule_row("R01", 2, "caution", A01="yes")]
        case_id = self.intake()
        other = self.intake()
        before = len(self.store.save_calls)
        self.judge(case_id, {"result_code": "caution", "answers": {"A01": "yes"}})
        self.judge(
            other, {"result_code": "no_issue", "rule_id": "R01", "rule_revision": 2}
        )
        self.assertEqual(
            self.store.save_calls[before:],
            [
                (case_id, "caution", {"A01": "yes"}, None),
                (other, "no_issue", {}, ("R01", 2)),
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


def rule_row(rule_id: str, revision: int, result_code: str, **conditions: str) -> dict:
    return {
        "rule_id": rule_id,
        "revision": revision,
        "result_code": result_code,
        "conditions": conditions,
    }


class RuleList(unittest.TestCase):
    """GET /api/rules: rule_id ごとに最新の revision の行を、rule_id 順に返す。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.store = ContractStore()
        cls.mock_calls: list[str] = []
        cls.server = _Server(make_app(cls.store, cls.mock_calls)).__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.__exit__()

    def setUp(self) -> None:
        self.store.rule_rows = []

    def get_rules(self) -> list[dict]:
        status, body = send("GET", f"{self.server.base_url}/api/rules")
        self.assertEqual(status, 200, body)
        return body

    def test_no_rules_is_an_empty_array(self):
        self.assertEqual(self.get_rules(), [])

    def test_rows_carry_id_revision_result_code_and_conditions_in_rule_id_order(self):
        self.store.rule_rows = [
            rule_row("R02", 1, "no_issue", A01="yes", A02="no"),
            rule_row("R01", 1, "caution", A01="yes", A02="yes"),
        ]
        self.assertEqual(
            self.get_rules(),
            [
                rule_row("R01", 1, "caution", A01="yes", A02="yes"),
                rule_row("R02", 1, "no_issue", A01="yes", A02="no"),
            ],
        )

    def test_only_the_latest_revision_of_each_rule_is_returned(self):
        self.store.rule_rows = [
            rule_row("R01", 1, "caution", A01="yes"),
            rule_row("R01", 2, "no_issue", A01="no"),
            rule_row("R02", 1, "caution", A01="yes"),
        ]
        self.assertEqual(
            self.get_rules(),
            [
                rule_row("R01", 2, "no_issue", A01="no"),
                rule_row("R02", 1, "caution", A01="yes"),
            ],
        )

    def test_rules_are_read_only(self):
        for method in ("POST", "PUT", "DELETE"):
            status, _ = send(method, f"{self.server.base_url}/api/rules", {})
            self.assertEqual(status, 405, method)

    def test_listing_rules_does_not_call_the_mock(self):
        self.get_rules()
        self.assertEqual(self.mock_calls, [])


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
        self.assertEqual(send("GET", f"{self.server.base_url}/api/rules")[0], 503)
        self.assertEqual(
            send("PUT", f"{base}/judgment", {"result_code": "caution"})[0], 503
        )


class BusyStore(ContractStore):
    """衝突の再試行を使い切った保存先（StoreBusy）。保存は何も変わらない。"""

    def create_case(self, case: dict) -> str:
        raise StoreBusy("検証用")

    def save_judgment(self, *args, **kwargs) -> None:
        self.save_calls.append(args)
        raise StoreBusy("検証用")


class StoreBusyContract(unittest.TestCase):
    """再試行を使い切ったとき、利用者には503（Retry-After付き）。入力は残す前提の文言。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_calls: list[str] = []
        cls.store = BusyStore()
        cls.server = _Server(make_app(cls.store, cls.mock_calls)).__enter__()
        cls.env = mock.patch.dict(os.environ, {"MOCK_BASE_URL": cls.server.base_url})
        cls.env.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env.stop()
        cls.server.__exit__()

    def put_judgment_raw(self) -> urllib.error.HTTPError:
        return self.put_judgment()

    def put_judgment(self) -> urllib.error.HTTPError:
        request = urllib.request.Request(
            f"{self.server.base_url}/api/cases/CASE-ANY/judgment",
            data=json.dumps({"result_code": "caution"}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        return caught.exception

    def test_save_judgment_busy_returns_503_with_retry_after(self):
        error = self.put_judgment()
        self.assertEqual(error.code, 503)
        self.assertEqual(error.headers["Retry-After"], "1")
        self.assertIn("混み合っています", json.loads(error.read())["detail"])
        # 再試行は保存先の中の仕事。HTTP層は1回しか呼ばず、何も保存されない。
        self.assertEqual(len(self.store.save_calls), 1)
        self.assertEqual(self.store.cases, {})

    def test_unknown_case_is_still_404_not_503(self):
        """StoreBusyは衝突の使い切りだけ。案件がない・入力が不正は従来どおり。"""
        with mock.patch.object(self.store, "save_judgment", side_effect=CaseNotFound):
            error = self.put_judgment_raw()
        self.assertEqual(error.code, 404)
        with mock.patch.object(
            self.store, "save_judgment", side_effect=InvalidJudgment("x")
        ):
            error = self.put_judgment_raw()
        self.assertEqual(error.code, 422)

    def test_intake_busy_returns_503_and_does_not_call_mock(self):
        status, body = send(
            "POST", f"{self.server.base_url}/api/intake/cases", case_payload("456")
        )
        self.assertEqual(status, 503)
        self.assertIn("混み合っています", body["detail"])
        self.assertEqual(self.mock_calls, [], "1回目の保存に失敗したらHTTPは呼ばない")
        request = urllib.request.Request(
            f"{self.server.base_url}/api/intake/cases",
            data=json.dumps(case_payload("456")).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.headers["Retry-After"], "1")
        self.assertEqual(self.store.cases, {})


if __name__ == "__main__":
    unittest.main()
