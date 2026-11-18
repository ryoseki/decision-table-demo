"""提案の生成の実接続（Amazon Bedrock）のAPI契約の検証。偽物のクライアントで試験する。

実際のBedrock・AWSには接続しない（資格情報も使わない）。確認するのは次の4つ。
- 設定: 既定はモック。実接続はリージョンとモデルの明示が必須で、不正なら起動時に失敗する
- 差し替え: 実接続の生成も、モックと同じ検証（形式・ID・重複・対象・参照）を通る
- 失敗: 接続エラー・タイムアウト・権限・不正な出力を、成功の応答にしない
  （モックにも戻さない）
- 生成元: 成功した応答に、モックか実接続か（モデル・リージョン）を付ける
  docker compose run --rm --no-deps --build backend \\
    python -m unittest discover -s tests -t .
値はすべて架空。
"""

import contextlib
import importlib.util
import json
import logging
import os
import unittest
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import uuid4

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ProfileNotFound,
    ReadTimeoutError,
)
from fastapi import FastAPI

from app import bedrock_proposer, question_admin, rule_admin
from app.bedrock_proposer import (
    BedrockConfigError,
    BedrockQuestionProposer,
    BedrockRuleProposer,
    BedrockSettings,
)
from app.domain import InvalidProposal, ProposerUnavailable
from app.question_proposer import MockQuestionProposer
from app.question_store import InMemoryQuestionStore
from app.question_validation import DemoQuestion
from app.quota_store import InMemoryAiQuota
from app.rule_proposer import MockRuleProposer
from app.rule_store import InMemoryRuleStore
from tests.test_intake import _Server, send
from tests.test_question_retirement import RetirementStore, rule_row

SETTINGS = BedrockSettings(region="region-demo-1", model_id="demo.model-v1:0")
SDK_MARKER = "sdk-internal-detail-must-not-leak"
OUTPUT_MARKER = "model-text-must-not-leak"
PROMPT_MARKER = "user-request-must-not-be-logged"
ENV = {
    "AI_PROVIDER": "bedrock",
    "BEDROCK_REGION": "region-demo-1",
    "BEDROCK_MODEL_ID": "demo.model-v1:0",
}


def reply(text: str, stop: str = "end_turn") -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
    }


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": SDK_MARKER}}, "Converse")


class FakeClient:
    """Converse の偽物。応答（または例外）を返し、呼び出しを記録する。"""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error
        self.calls: list[dict] = []

    def converse(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def add_question_json(
    id_: str = "A13", text: str = "対象期間にAPIキーが失効されたか？"
):
    return json.dumps(
        {"operation": "add_question", "question": {"id": id_, "text": text}},
        ensure_ascii=False,
    )


def add_rule_json(conditions: dict, result: str = "caution", id_: str = "R04"):
    return json.dumps(
        {
            "operation": "add_rule",
            "rule": {"id": id_, "result_code": result, "conditions": conditions},
        }
    )


class TestSettings(unittest.TestCase):
    def test_default_and_mock_choose_the_mock(self) -> None:
        self.assertIsNone(bedrock_proposer.settings_from_env({}))
        self.assertIsNone(bedrock_proposer.settings_from_env({"AI_PROVIDER": "mock"}))
        self.assertIsNone(bedrock_proposer.settings_from_env({"AI_PROVIDER": " "}))

    def test_bedrock_requires_explicit_region_and_model(self) -> None:
        for missing in ("BEDROCK_REGION", "BEDROCK_MODEL_ID"):
            env = {k: v for k, v in ENV.items() if k != missing}
            with self.assertRaises(BedrockConfigError, msg=missing):
                bedrock_proposer.settings_from_env(env)
        with self.assertRaises(BedrockConfigError):
            bedrock_proposer.settings_from_env({**ENV, "BEDROCK_REGION": " "})

    def test_unknown_provider_fails_instead_of_falling_back_to_the_mock(self) -> None:
        with self.assertRaises(BedrockConfigError):
            bedrock_proposer.settings_from_env({**ENV, "AI_PROVIDER": "bedrok"})

    def test_valid_settings_use_bounded_defaults(self) -> None:
        settings = bedrock_proposer.settings_from_env(ENV)
        assert settings is not None
        self.assertEqual(settings.region, "region-demo-1")
        self.assertEqual(settings.model_id, "demo.model-v1:0")
        self.assertEqual(settings.connect_timeout, 3.0)
        self.assertEqual(settings.read_timeout, 20.0)
        self.assertEqual(settings.max_output_tokens, 512)

    def test_limits_can_be_tuned_only_within_bounds(self) -> None:
        tuned = bedrock_proposer.settings_from_env(
            {
                **ENV,
                "BEDROCK_READ_TIMEOUT_SECONDS": "30",
                "BEDROCK_MAX_OUTPUT_TOKENS": "256",
            }
        )
        assert tuned is not None
        self.assertEqual((tuned.read_timeout, tuned.max_output_tokens), (30.0, 256))
        for name, bad in (
            ("BEDROCK_READ_TIMEOUT_SECONDS", "0"),
            ("BEDROCK_READ_TIMEOUT_SECONDS", "600"),
            ("BEDROCK_CONNECT_TIMEOUT_SECONDS", "abc"),
            ("BEDROCK_MAX_OUTPUT_TOKENS", "100000"),
        ):
            with self.assertRaises(BedrockConfigError, msg=f"{name}={bad}"):
                bedrock_proposer.settings_from_env({**ENV, name: bad})

    # 設定の上限: 名前、(下限, 上限)、設定の属性。
    LIMITS = (
        ("BEDROCK_CONNECT_TIMEOUT_SECONDS", (1, 10), "connect_timeout"),
        ("BEDROCK_READ_TIMEOUT_SECONDS", (1, 60), "read_timeout"),
        ("BEDROCK_MAX_OUTPUT_TOKENS", (64, 1024), "max_output_tokens"),
    )

    def test_limit_boundaries_are_inclusive_and_just_outside_is_refused(self) -> None:
        for name, (low, high), attr in self.LIMITS:
            for edge in (low, high):
                with self.subTest(name=name, accepted=edge):
                    settings = bedrock_proposer.settings_from_env(
                        {**ENV, name: str(edge)}
                    )
                    assert settings is not None
                    self.assertEqual(getattr(settings, attr), edge)
            for bad in (low - 0.5, low - 1, high + 0.5, high + 1):
                with self.subTest(name=name, refused=bad):
                    with self.assertRaises(BedrockConfigError):
                        bedrock_proposer.settings_from_env({**ENV, name: str(bad)})

    def test_non_numeric_or_non_finite_limits_are_refused(self) -> None:
        for name, _, _ in self.LIMITS:
            for bad in ("nan", "inf", "-inf", "-1", "1,0", "ten"):
                with self.subTest(name=name, value=bad):
                    with self.assertRaises(BedrockConfigError):
                        bedrock_proposer.settings_from_env({**ENV, name: bad})

    def test_blank_limits_use_the_defaults(self) -> None:
        settings = bedrock_proposer.settings_from_env(
            {
                **ENV,
                "BEDROCK_MAX_OUTPUT_TOKENS": "  ",
                "BEDROCK_READ_TIMEOUT_SECONDS": "",
            }
        )
        assert settings is not None
        self.assertEqual(
            (settings.read_timeout, settings.max_output_tokens), (20.0, 512)
        )

    def test_fractional_max_output_tokens_are_refused_not_truncated(self) -> None:
        for bad in ("256.5", "64.1", "1023.9"):
            with self.subTest(value=bad):
                with self.assertRaises(BedrockConfigError) as caught:
                    bedrock_proposer.settings_from_env(
                        {**ENV, "BEDROCK_MAX_OUTPUT_TOKENS": bad}
                    )
                self.assertIn("整数", str(caught.exception))
        whole = bedrock_proposer.settings_from_env(
            {**ENV, "BEDROCK_MAX_OUTPUT_TOKENS": "256.0"}
        )
        assert whole is not None
        self.assertEqual(whole.max_output_tokens, 256)  # 整数の値なら受け付ける


class TestRuntimeClient(unittest.TestCase):
    def test_client_has_bounded_timeouts_and_no_retries_without_any_call(self) -> None:
        client = bedrock_proposer.create_runtime_client(SETTINGS)  # 通信しない
        config = client.meta.config  # type: ignore[attr-defined]
        self.assertEqual(client.meta.region_name, "region-demo-1")  # type: ignore[attr-defined]
        self.assertEqual(config.connect_timeout, 3.0)
        self.assertEqual(config.read_timeout, 20.0)
        self.assertEqual(
            config.retries["total_max_attempts"], 1
        )  # 再試行しない（費用）


class TestBuildProposers(unittest.TestCase):
    def test_default_builds_mocks_without_creating_a_client(self) -> None:
        def no_client(settings: BedrockSettings) -> Any:
            raise AssertionError("モックでは、実接続のクライアントを作らない")

        questions, rules = bedrock_proposer.build_proposers({}, no_client)
        self.assertIsInstance(questions, MockQuestionProposer)
        self.assertIsInstance(rules, MockRuleProposer)

    def test_bedrock_builds_both_proposers_with_the_declared_source(self) -> None:
        client = FakeClient()
        questions, rules = bedrock_proposer.build_proposers(ENV, lambda s: client)
        self.assertIsInstance(questions, BedrockQuestionProposer)
        self.assertIsInstance(rules, BedrockRuleProposer)
        for proposer in (questions, rules):
            self.assertEqual(
                (
                    proposer.source.kind,
                    proposer.source.model_id,
                    proposer.source.region,
                ),
                ("bedrock", "demo.model-v1:0", "region-demo-1"),
            )

    def test_client_construction_failures_stop_startup_without_the_sdk_message(
        self,
    ) -> None:
        # 資格情報の解決は、通常は最初の呼び出しで行われる（その失敗は、呼び出しの
        # 失敗として分類する）。構成の誤り（存在しないプロファイルなど）は、
        # クライアントを作る時点で失敗しうる。起動時に失敗させ、SDKの文言は出さない。
        for error in (
            ProfileNotFound(profile=SDK_MARKER),
            NoRegionError(),
            NoCredentialsError(),
        ):

            def failing(settings: BedrockSettings, error: Exception = error) -> Any:
                raise error

            with self.subTest(error=type(error).__name__):
                with self.assertRaises(BedrockConfigError) as caught:
                    bedrock_proposer.build_proposers(ENV, failing)
                self.assertIn(type(error).__name__, str(caught.exception))
                self.assertNotIn(SDK_MARKER, str(caught.exception))

    def test_bad_settings_fail_at_build_time(self) -> None:
        with self.assertRaises(BedrockConfigError):
            bedrock_proposer.build_proposers({"AI_PROVIDER": "bedrock"}, lambda s: None)


CURRENT = tuple(DemoQuestion(f"A{n:02d}", f"設問{n}？") for n in range(1, 13))


class TestQuestionRequest(unittest.TestCase):
    def test_sends_one_bounded_converse_request_and_returns_the_raw_output(
        self,
    ) -> None:
        client = FakeClient(reply(add_question_json()))
        proposer = BedrockQuestionProposer(SETTINGS, client)
        raw = proposer.generate("APIキーの失効履歴の設問を追加して", CURRENT)
        self.assertEqual(raw["operation"], "add_question")
        (call,) = client.calls  # 再試行しない
        self.assertEqual(call["modelId"], "demo.model-v1:0")
        self.assertEqual(call["inferenceConfig"]["maxTokens"], 512)
        self.assertEqual(call["inferenceConfig"]["temperature"], 0)
        user_text = call["messages"][0]["content"][0]["text"]
        self.assertIn("<request>APIキーの失効履歴の設問を追加して</request>", user_text)
        self.assertIn("A12: 設問12？", user_text)
        self.assertIn('"A13"', call["system"][0]["text"])  # 追加してよいID

    def test_output_in_a_code_fence_is_accepted(self) -> None:
        fenced = f"```json\n{add_question_json()}\n```"
        proposer = BedrockQuestionProposer(SETTINGS, FakeClient(reply(fenced)))
        self.assertEqual(proposer.generate("追加", CURRENT)["question"]["id"], "A13")


class TestParseOutput(unittest.TestCase):
    def assert_invalid(self, response: Any) -> InvalidProposal:
        with self.assertRaises(InvalidProposal) as caught:
            bedrock_proposer.parse_output(response)
        self.assertNotIn(OUTPUT_MARKER, str(caught.exception))
        return caught.exception

    def test_unusable_outputs_are_invalid_and_never_echo_the_output(self) -> None:
        for response in (
            reply(OUTPUT_MARKER),  # JSONではない
            reply(f"結果は次のとおり: {add_question_json()} {OUTPUT_MARKER}"),
            reply(json.dumps([OUTPUT_MARKER])),  # オブジェクトではない
            reply(""),
            reply("x" * (bedrock_proposer.MAX_OUTPUT_CHARS + 1)),
            reply(add_question_json(), stop="max_tokens"),  # 途中で切れた
            {},
            {"output": {}},
            {"output": {"message": {"content": [{"toolUse": {}}]}}},
            None,
        ):
            self.assert_invalid(response)

    def test_only_end_turn_is_a_successful_stop(self) -> None:
        for stop in (
            "stop_sequence",  # 停止シーケンスは指定していない
            "tool_use",
            "guardrail_intervened",
            "content_filtered",
            "max_tokens",
            "",
            None,
            "unknown",
        ):
            with self.subTest(stop=stop):
                self.assert_invalid(reply(add_question_json(), stop=stop))  # type: ignore[arg-type]
        response = reply(add_question_json())
        del response["stopReason"]  # 欠落も成功とは見なさない
        self.assert_invalid(response)
        self.assertEqual(
            bedrock_proposer.parse_output(reply(add_question_json()))["operation"],
            "add_question",
        )

    def test_only_pure_text_blocks_are_accepted(self) -> None:
        text = {"text": add_question_json()}
        for blocks in (
            [text, {"toolUse": {"name": "x"}}],  # 文字以外が混ざる
            [text, {"text": "", "reasoningContent": {}}],  # 余分な項目つきのブロック
            [{"text": add_question_json(), "citations": []}],
            [{"reasoningContent": {}}, text],
            [text, "not a dict"],
            [],  # 空
            "text",  # 配列ではない
            [{"text": 1}],
        ):
            with self.subTest(blocks=blocks):
                response = reply("")
                response["output"]["message"]["content"] = blocks
                self.assert_invalid(response)
        split = reply("")  # 複数の文字ブロックは、順に連結する
        split["output"]["message"]["content"] = [{"text": "{"}, {"text": "}"}]
        self.assertEqual(bedrock_proposer.parse_output(split), {})

    def test_duplicate_json_keys_are_rejected_at_any_depth(self) -> None:
        for text in (
            '{"operation": "none", "operation": "add_question"}',
            '{"operation": "add_rule", "rule": {"id": "R04", "result_code": "caution",'
            ' "conditions": {"A09": "yes", "A09": "no"}}}',  # ルールの条件
            '{"operation": "add_question", "question": {"id": "A13", "id": "A14",'
            ' "text": "x？"}}',
        ):
            with self.subTest(text=text):
                self.assert_invalid(reply(text))

    def test_nonfinite_numbers_and_deep_nesting_are_rejected(self) -> None:
        for text in (
            '{"operation": "none", "n": NaN}',
            '{"operation": "none", "n": Infinity}',
            '{"operation": "none", "n": -Infinity}',
            '{"operation": "none", "n": 1e999}',  # 有限でない値に丸められる
            "[" * 3000 + "]" * 3000,
            '{"a":' * 1500 + "1" + "}" * 1500,
        ):
            with self.subTest(text=text[:40]):
                self.assert_invalid(reply(text))


class TestFailureClassification(unittest.TestCase):
    CASES = (
        (ReadTimeoutError(endpoint_url="https://x.invalid"), "timeout"),
        (ConnectTimeoutError(endpoint_url="https://x.invalid"), "timeout"),
        (client_error("ModelTimeoutException"), "timeout"),
        (EndpointConnectionError(endpoint_url="https://x.invalid"), "network"),
        (NoCredentialsError(), "credentials"),
        (CredentialRetrievalError(provider="p", error_msg=SDK_MARKER), "credentials"),
        (PartialCredentialsError(provider="p", cred_var="v"), "credentials"),
        (client_error("AccessDeniedException"), "access"),
        (client_error("ExpiredTokenException"), "access"),
        (client_error("UnrecognizedClientException"), "access"),
        (client_error("InvalidSignatureException"), "access"),
        (client_error("ThrottlingException"), "throttled"),
        (client_error("ServiceQuotaExceededException"), "throttled"),
        (client_error("ValidationException"), "config"),
        (client_error("ResourceNotFoundException"), "config"),
        (client_error("InternalServerException"), "service"),
        (client_error("ASurprisingNewException"), "service"),  # 未知のコード
        (ClientError({}, "Converse"), "service"),  # コードが欠落
        (ConnectionClosedError(endpoint_url="https://x.invalid"), "network"),
    )

    def test_each_sdk_failure_becomes_a_reason_after_exactly_one_call(self) -> None:
        for error, reason in self.CASES:
            client = FakeClient(error=error)
            proposer = BedrockQuestionProposer(SETTINGS, client)
            with self.assertRaises(ProposerUnavailable, msg=reason) as caught:
                proposer.generate("追加", CURRENT)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(len(client.calls), 1)  # 再試行しない
            self.assertNotIn(SDK_MARKER, str(caught.exception))


class BedrockApiTestCase(unittest.TestCase):
    """実接続の生成（偽クライアント）を、モックと同じ設問・ルールの一覧とAPIに組み込む。"""

    def make(self, client: FakeClient, case_store: object | None = None) -> None:
        self.client = client
        self.question_store = InMemoryQuestionStore()
        self.rule_store = InMemoryRuleStore()
        application = FastAPI()
        application.state.question_proposer = BedrockQuestionProposer(SETTINGS, client)
        application.state.rule_proposer = BedrockRuleProposer(SETTINGS, client)
        application.state.ai_quota = InMemoryAiQuota(b"x" * 32)
        application.state.question_store = self.question_store
        application.state.rule_store = self.rule_store
        if case_store is not None:  # 設問の廃止案は、ルールと案件の保存先を読む
            application.state.case_store = case_store
        application.include_router(question_admin.router)
        application.include_router(rule_admin.router)
        server = _Server(application)
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.base = server.base_url

    def post(self, path: str, prompt: str) -> tuple[int, dict]:
        return send(
            "POST",
            f"{self.base}/api/admin/{path}",
            {"prompt": prompt},
            {
                "X-Demo-Viewer-Ip": "192.0.2.1",
                "Idempotency-Key": str(uuid4()),
            },
        )

    def get(self, path: str) -> dict:
        status, body = send("GET", f"{self.base}/api/admin/{path}")
        self.assertEqual(status, 200)
        return body

    def decide(self, kind: str, proposal_id: str, decision: str) -> tuple[int, dict]:
        return send(
            "POST",
            f"{self.base}/api/admin/{kind}-proposals/{proposal_id}/decision",
            {"decision": decision},
        )


RETIRE_A13_JSON = '{"operation": "retire_question", "question_id": "A13"}'
REMOVE_R03_JSON = '{"operation": "remove_rule", "rule_id": "R03"}'
EXPECTED_SOURCE = {
    "kind": "bedrock",
    "model_id": "demo.model-v1:0",
    "region": "region-demo-1",
}


class TestQuestionApiWithBedrock(BedrockApiTestCase):
    def test_success_reports_the_bedrock_source_and_changes_nothing_before_approval(
        self,
    ) -> None:
        self.make(FakeClient(reply(add_question_json())))
        status, body = self.post("question-proposals", "失効の設問を追加して")
        self.assertEqual(status, 201)
        self.assertEqual(body["source"], EXPECTED_SOURCE)
        self.assertIs(body["mock"], False)
        self.assertEqual(body["added"]["id"], "A13")
        self.assertEqual(len(self.get("questions")["questions"]), 12)

    def test_lists_and_decisions_also_report_the_source(self) -> None:
        self.make(FakeClient(reply(add_question_json())))
        self.assertEqual(self.get("questions")["source"], EXPECTED_SOURCE)
        _, proposal = self.post("question-proposals", "追加して")
        status, body = send(
            "POST",
            f"{self.base}/api/admin/question-proposals/{proposal['proposal_id']}/decision",
            {"decision": "yes"},
        )
        self.assertEqual(status, 200)
        self.assertIs(body["applied"], True)  # P1の承認の流れは変わらない
        self.assertEqual(body["source"], EXPECTED_SOURCE)
        self.assertEqual(self.get("questions")["questions"][-1]["id"], "A13")

    def test_retirement_proposal_and_both_decisions_report_the_bedrock_source(
        self,
    ) -> None:
        cases = RetirementStore()
        cases.rule_rows = [
            rule_row("R01", 1, "A01", "A09")
        ]  # A13は、どのルールも参照しない
        client = FakeClient(reply(add_question_json()))
        self.make(client, cases)
        _, added = self.post("question-proposals", "追加して")
        self.assertEqual(self.decide("question", added["proposal_id"], "yes")[0], 200)

        client.response = reply(RETIRE_A13_JSON)
        status, first = self.post("question-proposals", "設問A13を廃止して")
        self.assertEqual(status, 201)
        self.assertEqual(first["kind"], "retire")
        self.assertEqual(first["source"], EXPECTED_SOURCE)
        self.assertIs(first["mock"], False)
        self.assertIs(first["can_retire"], True)
        self.assertNotIn("retired", self.get("questions")["questions"][-1])  # 承認前
        # No: 反映せず、応答は実接続の生成元のまま。
        status, declined = self.decide("question", first["proposal_id"], "no")
        self.assertEqual((status, declined["applied"]), (200, False))
        self.assertEqual(declined["source"], EXPECTED_SOURCE)
        self.assertIs(declined["mock"], False)
        self.assertNotIn("retired", declined["questions"][-1])
        # Yes: 廃止する（設問文は残る）。応答は実接続の生成元のまま。
        _, second = self.post("question-proposals", "設問A13を廃止して")
        self.assertEqual(second["source"], EXPECTED_SOURCE)
        status, applied = self.decide("question", second["proposal_id"], "yes")
        self.assertEqual((status, applied["applied"]), (200, True))
        self.assertEqual(applied["source"], EXPECTED_SOURCE)
        self.assertIs(applied["mock"], False)
        self.assertIs(applied["questions"][-1]["retired"], True)
        self.assertEqual(len(client.calls), 3)  # 追加案1回・廃止案2回。再試行しない

    def test_real_output_goes_through_the_same_validation(self) -> None:
        for output, expect in (
            (add_question_json(id_="A05"), "重複"),  # 既存のID
            (add_question_json(id_="A20"), "A13"),  # 追加できるのは次のID
            (
                add_question_json(text="問い合わせにあったログインは成功したか？"),
                "重複",
            ),
            (add_question_json(text=""), "空"),
            ('{"operation": "add_question", "question": {"id": "A13"}}', "形式"),
            ('{"operation": "retire_question", "question_id": "A99"}', "存在しません"),
            ('{"operation": "delete_everything"}', "形式"),
            ('{"operation": "none", "question": {}}', "追加だけ"),
        ):
            with self.subTest(output=output):
                self.make(FakeClient(reply(output)))
                status, body = self.post("question-proposals", "何か")
                self.assertEqual(status, 422)
                self.assertIn(expect, body["detail"])
                self.assertNotIn("source", body)  # 成功の応答ではない
                self.assertEqual(len(self.get("questions")["questions"]), 12)

    def test_unusable_output_is_422_without_leaking_the_output(self) -> None:
        for response in (
            reply(OUTPUT_MARKER),
            reply(add_question_json(), "max_tokens"),
        ):
            self.make(FakeClient(response))
            status, body = self.post("question-proposals", "追加して")
            self.assertEqual(status, 422)
            self.assertNotIn(OUTPUT_MARKER, json.dumps(body, ensure_ascii=False))
            self.assertEqual(len(self.get("questions")["questions"]), 12)

    def test_connection_failures_are_errors_not_successes_and_never_fall_back(
        self,
    ) -> None:
        expected = {
            "timeout": 504,
            "network": 502,
            "credentials": 502,
            "access": 502,
            "throttled": 502,
            "config": 502,
            "service": 502,
        }
        for error, reason in TestFailureClassification.CASES:
            with self.subTest(reason=reason, error=type(error).__name__):
                client = FakeClient(error=error)
                self.make(client)
                status, body = self.post(
                    "question-proposals", "APIキーの失効履歴を確認する設問を追加して"
                )
                self.assertEqual(status, expected[reason])
                self.assertNotIn("proposal_id", body)
                self.assertNotIn("source", body)
                self.assertNotIn(SDK_MARKER, json.dumps(body, ensure_ascii=False))
                self.assertNotIn("モック", body["detail"])  # モックの応答ではない
                self.assertEqual(len(client.calls), 1)  # 再試行しない
                self.assertEqual(len(self.get("questions")["questions"]), 12)

    def test_failure_details_differ_by_cause(self) -> None:
        details = set()
        for error, _ in TestFailureClassification.CASES:
            self.make(FakeClient(error=error))
            details.add(self.post("question-proposals", "追加して")[1]["detail"])
        self.assertEqual(len(details), 7)  # 原因の分類ごとに、別の固定の文言


class TestRuleApiWithBedrock(BedrockApiTestCase):
    def test_success_reports_the_bedrock_source(self) -> None:
        self.make(FakeClient(reply(add_rule_json({"A09": "yes", "A10": "no"}))))
        status, body = self.post("rule-proposals", "ルールを追加して")
        self.assertEqual(status, 201)
        self.assertEqual(body["source"], EXPECTED_SOURCE)
        self.assertIs(body["mock"], False)
        self.assertEqual(body["added"]["rule_id"], "R04")
        self.assertEqual(self.get("rules")["source"], EXPECTED_SOURCE)
        self.assertEqual(len(self.get("rules")["rules"]), 3)  # 承認前は変わらない

    def test_removal_proposal_and_both_decisions_report_the_bedrock_source(
        self,
    ) -> None:
        self.make(FakeClient(reply(REMOVE_R03_JSON)))
        status, first = self.post("rule-proposals", "ルールR03を削除して")
        self.assertEqual(status, 201)
        self.assertEqual(first["kind"], "remove")
        self.assertEqual(first["source"], EXPECTED_SOURCE)
        self.assertIs(first["mock"], False)
        self.assertEqual(len(self.get("rules")["rules"]), 3)  # 承認前は変わらない
        # No: 反映せず、応答は実接続の生成元のまま。
        status, declined = self.decide("rule", first["proposal_id"], "no")
        self.assertEqual((status, declined["applied"]), (200, False))
        self.assertEqual(declined["source"], EXPECTED_SOURCE)
        self.assertIs(declined["mock"], False)
        self.assertEqual(len(declined["rules"]), 3)
        # Yes: 削除する。応答は実接続の生成元のまま。
        _, second = self.post("rule-proposals", "ルールR03を削除して")
        self.assertEqual(second["source"], EXPECTED_SOURCE)
        status, applied = self.decide("rule", second["proposal_id"], "yes")
        self.assertEqual((status, applied["applied"]), (200, True))
        self.assertEqual([r["rule_id"] for r in applied["rules"]], ["R01", "R02"])
        self.assertEqual(applied["source"], EXPECTED_SOURCE)
        self.assertIs(applied["mock"], False)
        self.assertEqual(self.get("rules")["source"], EXPECTED_SOURCE)

    def test_real_output_goes_through_the_same_validation(self) -> None:
        for output, expect in (
            (add_rule_json({"A99": "yes"}), "設問A99は存在しません"),  # 参照する設問
            (add_rule_json({"A09": "maybe"}), "yes か no"),
            (add_rule_json({"A09": "yes"}, result="delete_account"), "結果コード"),
            (add_rule_json({"A09": "yes"}, id_="R09"), "R04"),  # 追加できるID
            (add_rule_json({}), "条件"),
            ('{"operation": "remove_rule", "rule_id": "R77"}', "存在しません"),
            ('{"operation": "none", "rule": {}}', "追加と削除だけ"),
        ):
            with self.subTest(output=output):
                self.make(FakeClient(reply(output)))
                status, body = self.post("rule-proposals", "何か")
                self.assertEqual(status, 422)
                self.assertIn(expect, body["detail"])
                self.assertNotIn("source", body)
                self.assertEqual(len(self.get("rules")["rules"]), 3)

    def test_unusable_output_is_422_without_leaking_the_output(self) -> None:
        for response in (
            reply(OUTPUT_MARKER),
            reply(add_rule_json({"A09": "yes"}), "max_tokens"),
        ):
            self.make(FakeClient(response))
            status, body = self.post("rule-proposals", "ルールを追加して")
            self.assertEqual(status, 422)
            self.assertNotIn(OUTPUT_MARKER, json.dumps(body, ensure_ascii=False))
            self.assertEqual(len(self.get("rules")["rules"]), 3)

    def test_duplicate_of_an_existing_rule_is_rejected(self) -> None:
        self.make(FakeClient(reply("{}")))
        _, rules = send("GET", f"{self.base}/api/admin/rules")
        first = rules["rules"][0]
        self.make(
            FakeClient(reply(add_rule_json(first["conditions"], first["result_code"])))
        )
        status, body = self.post("rule-proposals", "同じルールを追加して")
        self.assertEqual(status, 422)
        self.assertIn("重複", body["detail"])

    def test_connection_failures_are_errors_and_leave_the_list_unchanged(self) -> None:
        for error, reason in TestFailureClassification.CASES:
            with self.subTest(reason=reason):
                client = FakeClient(error=error)
                self.make(client)
                status, body = self.post("rule-proposals", "ルールを追加して")
                self.assertEqual(status, 504 if reason == "timeout" else 502)
                self.assertNotIn("proposal_id", body)
                self.assertNotIn(SDK_MARKER, json.dumps(body, ensure_ascii=False))
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(len(self.get("rules")["rules"]), 3)

    def test_rule_proposal_asks_the_model_with_current_questions_and_rules(
        self,
    ) -> None:
        client = FakeClient(reply(add_rule_json({"A09": "yes"})))
        self.make(client)
        self.post("rule-proposals", "ルールを追加して")
        (call,) = client.calls
        system = call["system"][0]["text"]
        self.assertIn('"R04"', system)
        for code in ("account_deletion", "account_suspension", "caution", "no_issue"):
            self.assertIn(code, system)
        user_text = call["messages"][0]["content"][0]["text"]
        self.assertIn("R01", user_text)
        self.assertIn("A12", user_text)


class _Records(logging.Handler):
    """全てのロガーの記録を、文面（例外の内容を含む）にして集める。"""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@contextlib.contextmanager
def captured_logs() -> Iterator[list[str]]:
    handler = _Records()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler.lines
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


class TestLogsAreSanitized(unittest.TestCase):
    """ログには、原因の分類と例外の型名だけを残す（SDKの文言・依頼・出力は残さない）。"""

    def test_a_failed_call_logs_only_the_reason_and_the_exception_type(self) -> None:
        for error, reason in TestFailureClassification.CASES:
            with self.subTest(reason=reason, error=type(error).__name__):
                proposer = BedrockQuestionProposer(SETTINGS, FakeClient(error=error))
                with captured_logs() as lines:
                    with self.assertRaises(ProposerUnavailable):
                        proposer.generate(PROMPT_MARKER, CURRENT)
                (line,) = lines  # 1件だけ
                self.assertIn(f"reason={reason}", line)
                self.assertIn(f"type={type(error).__name__}", line)
                self.assertNotIn(SDK_MARKER, line)
                self.assertNotIn(PROMPT_MARKER, line)
                self.assertNotIn("Traceback", line)  # 例外の内容（文言）を持ち込まない

    def test_unusable_model_output_is_never_logged(self) -> None:
        duplicate = '{"operation": "none", "operation": "' + OUTPUT_MARKER + '"}'
        nonfinite = '{"operation": "none", "n": NaN, "t": "' + OUTPUT_MARKER + '"}'
        for text in (
            OUTPUT_MARKER,  # JSONではない
            duplicate,  # キーの重複
            json.dumps([OUTPUT_MARKER]),  # オブジェクトではない
            "x" * (bedrock_proposer.MAX_OUTPUT_CHARS + 1) + OUTPUT_MARKER,
            nonfinite,
        ):
            questions = BedrockQuestionProposer(SETTINGS, FakeClient(reply(text)))
            rules = BedrockRuleProposer(SETTINGS, FakeClient(reply(text)))
            calls = (
                partial(questions.generate, PROMPT_MARKER, CURRENT),
                partial(rules.generate, PROMPT_MARKER, (), "R01", CURRENT),
            )
            for call in calls:
                with self.subTest(text=text[:30]):
                    with captured_logs() as lines:
                        with self.assertRaises(InvalidProposal) as caught:
                            call()
                    self.assertNotIn(OUTPUT_MARKER, str(caught.exception))
                    for line in lines:
                        self.assertNotIn(OUTPUT_MARKER, line)
                        self.assertNotIn(PROMPT_MARKER, line)


MAIN_PATH = Path(str(importlib.util.find_spec("app.main").origin))  # type: ignore[union-attr]


def load_main(env: dict[str, str], client: FakeClient | None = None) -> Any:
    """app.main を、共有の app.main とは別のモジュールとして読み込む（実際の配線）。

    他のテストが使う app.main の状態（app.state）に触れない。環境変数は、読み込みの
    間だけ差し替える（DB・静的配信は無効）。クライアントは偽物だけを渡す（実際のAWSには接続
    しない）。渡さないとき、クライアントを作ろうとしたら失敗させる。
    """
    real_build = bedrock_proposer.build_proposers

    def no_client(settings: BedrockSettings) -> Any:
        raise AssertionError("このテストでは、クライアントを作らない")

    def build(environ: Any) -> Any:
        return real_build(environ, (lambda s: client) if client else no_client)

    spec = importlib.util.spec_from_file_location("app_main_under_test", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with (
        mock.patch.dict(os.environ, env),
        mock.patch.object(bedrock_proposer, "build_proposers", build),
        mock.patch(
            "app.shared_db.build_ai_quota",
            side_effect=lambda *args, uses_bedrock, **kwargs: (
                InMemoryAiQuota(b"x" * 32) if uses_bedrock else None
            ),
        ),
    ):
        for name in (
            "DB_HOST",
            "DSQL_ENDPOINT",
            "STATIC_DIR",
            "RELEASE_FILE",
            "AI_PROVIDER",
            "BEDROCK_REGION",
            "BEDROCK_MODEL_ID",
            "BEDROCK_MAX_OUTPUT_TOKENS",
        ):
            if name not in env:
                os.environ.pop(name, None)
        spec.loader.exec_module(module)
    return module


class TestMainWiring(unittest.TestCase):
    """app.main の実際の配線（環境変数 → 生成の選択 → app.state → ルーター）。"""

    def test_bedrock_env_wires_the_real_proposers_into_the_served_app(self) -> None:
        client = FakeClient(reply(add_question_json()))
        module = load_main(ENV, client)
        self.assertIsInstance(
            module.app.state.question_proposer, BedrockQuestionProposer
        )
        self.assertIsInstance(module.app.state.rule_proposer, BedrockRuleProposer)
        with _Server(module.app) as server:
            base = f"{server.base_url}/api/admin"
            for path in ("questions", "rules"):
                _, listed = send("GET", f"{base}/{path}")
                self.assertEqual(listed["source"], EXPECTED_SOURCE)
                self.assertIs(listed["mock"], False)
            status, question = send(
                "POST",
                f"{base}/question-proposals",
                {"prompt": "追加して"},
                {
                    "X-Demo-Viewer-Ip": "192.0.2.1",
                    "Idempotency-Key": str(uuid4()),
                },
            )
            self.assertEqual((status, question["source"]), (201, EXPECTED_SOURCE))
            client.response = reply(add_rule_json({"A09": "yes"}))
            status, rule = send(
                "POST",
                f"{base}/rule-proposals",
                {"prompt": "追加して"},
                {
                    "X-Demo-Viewer-Ip": "192.0.2.1",
                    "Idempotency-Key": str(uuid4()),
                },
            )
            self.assertEqual((status, rule["source"]), (201, EXPECTED_SOURCE))
            self.assertEqual(len(client.calls), 2)  # 依頼ごとに1回

    def test_default_env_wires_the_mocks_without_creating_a_client(self) -> None:
        module = load_main({})  # クライアントを作ると失敗する
        self.assertIsInstance(module.app.state.question_proposer, MockQuestionProposer)
        self.assertIsInstance(module.app.state.rule_proposer, MockRuleProposer)
        with _Server(module.app) as server:
            for path in ("questions", "rules"):
                _, listed = send("GET", f"{server.base_url}/api/admin/{path}")
                self.assertEqual(listed["source"], {"kind": "mock"})

    def test_missing_or_bad_config_fails_at_startup_and_never_falls_back(self) -> None:
        for env in (
            {"AI_PROVIDER": "bedrock"},
            {"AI_PROVIDER": "bedrock", "BEDROCK_REGION": "region-demo-1"},
            {"AI_PROVIDER": "bedrock", "BEDROCK_MODEL_ID": "demo.model-v1:0"},
            {**ENV, "AI_PROVIDER": "bedrok"},  # 誤字
            {**ENV, "BEDROCK_MAX_OUTPUT_TOKENS": "100000"},
            {**ENV, "BEDROCK_MAX_OUTPUT_TOKENS": "256.5"},
        ):
            with self.subTest(env=env):
                with self.assertRaises(BedrockConfigError):
                    load_main(env)  # クライアントを作る前に失敗する


class TestMockSourceIsReported(unittest.TestCase):
    """既定のモックも、生成元を申告する（実接続と区別できる）。"""

    def test_mock_responses_report_kind_mock_without_model_or_region(self) -> None:
        application = FastAPI()
        application.state.question_proposer = MockQuestionProposer()
        application.state.question_store = InMemoryQuestionStore()
        application.state.rule_proposer = MockRuleProposer()
        application.state.rule_store = InMemoryRuleStore()
        application.include_router(question_admin.router)
        application.include_router(rule_admin.router)
        with _Server(application) as server:
            base = server.base_url
            for path, prompt in (
                ("question-proposals", "APIキーの失効履歴を確認する設問を追加して"),
                ("rule-proposals", "設問A09がYesのとき注意とするルールを追加して"),
            ):
                status, body = send(
                    "POST", f"{base}/api/admin/{path}", {"prompt": prompt}
                )
                self.assertEqual(status, 201)
                self.assertEqual(body["source"], {"kind": "mock"})
                self.assertIs(body["mock"], True)
            for path in ("questions", "rules"):
                _, body = send("GET", f"{base}/api/admin/{path}")
                self.assertEqual(body["source"], {"kind": "mock"})

    def test_a_mock_failure_keeps_the_mock_wording(self) -> None:
        class Down:
            def generate(self, prompt: str, current: Any) -> Any:
                raise ProposerUnavailable()

        application = FastAPI()
        application.state.question_proposer = Down()
        application.state.question_store = InMemoryQuestionStore()
        application.include_router(question_admin.router)
        with _Server(application) as server:
            status, body = send(
                "POST",
                f"{server.base_url}/api/admin/question-proposals",
                {"prompt": "追加"},
            )
        self.assertEqual(status, 502)
        self.assertIn("モック", body["detail"])


if __name__ == "__main__":
    unittest.main()
