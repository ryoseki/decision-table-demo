"""scripts/deploy-lambda.py のオフラインテスト（標準ライブラリのみ）。

AWSとスモーク確認は、このファイルの偽物（FakeAws / FakeSmoke）で置き換える。
これは配布ロジックの確認であり、実際のAWS・Lambdaの動作の検証ではない。
  python3 -B -I -m unittest discover -s tests/cd
"""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("deploy_lambda", ROOT / "scripts" / "deploy-lambda.py")
dl = importlib.util.module_from_spec(_spec)
sys.modules["deploy_lambda"] = dl
_spec.loader.exec_module(dl)

NEW_SHA = "a" * 40
OLD_SHA = "b" * 40
OTHER_SHA = "c" * 40
LAYER_SHA = base64.b64encode(hashlib.sha256(b"layer").digest()).decode()
BASE_URL = "https://abcdefgh12345678.lambda-url.ap-northeast-1.on.aws"
FUNCTION = "demo-fn"
SECRET = "SUPER-SECRET-ENV-VALUE"
OLD_ZIP = hashlib.sha256(b"previous zip").hexdigest()
MUTATING = {"update-function-code", "publish-version", "update-alias"}


def code_sha(hex_digest: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_digest)).decode()


def make_artifact(directory: Path, sha: str = NEW_SHA, payload: bytes = b"new zip") -> str:
    directory.mkdir(parents=True)
    zip_path = directory / f"lambda-{sha}.zip"
    zip_path.write_bytes(payload)
    digest = hashlib.sha256(payload)
    manifest = {
        "schema": 1,
        "sourceSHA": sha,
        "dirty": False,
        "releaseEligible": True,
        "runtime": "python3.14",
        "architecture": "x86_64",
        "adapter": {
            "layerArn": dl.PINNED_LAYER_ARN,
            "execWrapper": "/opt/bootstrap",
            "handler": "run.sh",
            "port": 8000,
        },
        "zip": {
            "file": zip_path.name,
            "bytes": len(payload),
            "entries": 1,
            "sha256Hex": digest.hexdigest(),
            "codeSha256": base64.b64encode(digest.digest()).decode(),
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return digest.hexdigest()


class FakeAws:
    """`aws lambda ...` の最小の偽物。RevisionIdの条件付き更新を再現する。"""

    def __init__(self):
        self.calls = []
        self.before = {}  # 操作名 -> 呼び出し直前に実行する関数
        self.fail = {}  # 操作名 -> 投げる例外
        self.wait_fail = {}  # waiter名 -> 投げる例外
        self.pending_publish = False  # True: 新しい版は wait function-active-v2 まで Pending
        self.counter = 0
        self.alias_revisions = []  # こちらの更新が発行したエイリアスのRevisionId
        self.function = {
            "Runtime": "python3.14",
            "Architectures": ["x86_64"],
            "Handler": "run.sh",
            "Layers": [{"Arn": dl.PINNED_LAYER_ARN}],
            "Environment": {
                "Variables": {
                    "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                    "PORT": "8000",
                    "API_TOKEN": SECRET,
                }
            },
            "State": "Active",
            "LastUpdateStatus": "Successful",
            "RevisionId": "f-1",
            "CodeSha256": code_sha(OLD_ZIP),
        }
        self.dedupe_publish = False  # True: コード・設定が同じなら既存の版を返す（AWSの仕様どおり）
        self.versions = {"1": self._snapshot("1", dl.make_description(OLD_SHA, OLD_ZIP, "100"))}
        self.alias = {"FunctionVersion": "1", "RevisionId": "a-1", "RoutingConfig": {}}
        self.url = {"FunctionUrl": BASE_URL + "/", "AuthType": "NONE", "InvokeMode": "BUFFERED"}
        self.layer = {"Content": {"CodeSha256": LAYER_SHA}}

    def ops(self):
        return [call[1] for call in self.calls]

    def mutating(self):
        return [op for op in self.ops() if op in MUTATING]

    def _snapshot(self, version, description):
        """版は、発行時点の関数設定の写し（RevisionIdなし）。"""
        data = json.loads(json.dumps(self.function))
        del data["RevisionId"]
        data.update(Version=version, Description=description)
        return data

    def _rev(self, prefix):
        self.counter += 1
        return f"{prefix}-{self.counter + 1}"

    def __call__(self, args):
        self.calls.append(args)
        if args[1] == "wait":
            waiter = args[2]
            wait_opts = dict(zip(args[3::2], args[4::2]))
            if waiter in self.wait_fail:
                raise self.wait_fail[waiter]
            if waiter == "function-active-v2" and "--qualifier" in wait_opts:
                self.versions[wait_opts["--qualifier"]]["State"] = "Active"
            return {}
        op = args[1]
        opts = dict(zip(args[2::2], args[3::2]))
        if op in self.before:
            self.before[op]()
        if op in self.fail:
            raise self.fail[op]
        conflict = dl.AwsError(op, 254, conflict=True)
        if op == "get-function-url-config":
            return dict(self.url)
        if op == "get-layer-version-by-arn":
            return dict(self.layer)
        if op == "get-alias":
            return json.loads(json.dumps(self.alias))
        if op == "get-function-configuration":
            if "--qualifier" in opts:
                return dict(self.versions[opts["--qualifier"]])
            return json.loads(json.dumps(self.function))
        if op == "update-function-code":
            if opts["--revision-id"] != self.function["RevisionId"]:
                raise conflict
            data = Path(opts["--zip-file"].removeprefix("fileb://")).read_bytes()
            self.function["CodeSha256"] = base64.b64encode(hashlib.sha256(data).digest()).decode()
            self.function["RevisionId"] = self._rev("f")
            return {"CodeSha256": self.function["CodeSha256"], "RevisionId": self.function["RevisionId"]}
        if op == "publish-version":
            if (
                opts["--revision-id"] != self.function["RevisionId"]
                or opts["--code-sha256"] != self.function["CodeSha256"]
            ):
                raise conflict
            if self.dedupe_publish:
                for existing in self.versions.values():
                    if existing["CodeSha256"] == self.function["CodeSha256"]:
                        return {"Version": existing["Version"], "CodeSha256": existing["CodeSha256"]}
            version = str(len(self.versions) + 1)
            self.versions[version] = self._snapshot(version, opts["--description"])
            if self.pending_publish:
                self.versions[version]["State"] = "Pending"
            return {"Version": version, "CodeSha256": self.function["CodeSha256"]}
        if op == "update-alias":
            if opts["--revision-id"] != self.alias["RevisionId"]:
                raise conflict
            assert json.loads(opts["--routing-config"]) == {"AdditionalVersionWeights": {}}
            self.alias = {
                "FunctionVersion": opts["--function-version"],
                "RevisionId": self._rev("a"),
                "RoutingConfig": {},
            }
            self.alias_revisions.append(self.alias["RevisionId"])
            return json.loads(json.dumps(self.alias))
        raise AssertionError(f"想定外の操作: {op}")


class FakeSmoke:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}  # ラベル -> bool（既定はTrue）
        self.hook = None

    def __call__(self, base_url, expected_sha, report_id):
        label = report_id.rsplit("-", 1)[1]
        self.calls.append((base_url, expected_sha, label))
        if self.hook:
            self.hook(label)
        return self.outcomes.get(label, True)


class DeployTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.artifact = self.dir / "artifact"
        self.zip_hex = make_artifact(self.artifact)
        self.aws = FakeAws()
        self.smoke = FakeSmoke()
        self.result_file = self.dir / "out" / "result.json"

    def deploy(self, **overrides):
        params = dict(
            artifact_dir=self.artifact,
            source_sha=NEW_SHA,
            zip_hex=self.zip_hex,
            ci_run_url="https://github.com/example/repo/actions/runs/1",
            run_id="200",
            run_attempt="1",
            function_name=FUNCTION,
            base_url=BASE_URL,
            layer_code_sha256=LAYER_SHA,
            result_file=self.result_file,
            aws=self.aws,
            smoke=self.smoke,
        )
        params.update(overrides)
        return dl.Deployer(**params).run()

    def result(self):
        return json.loads(self.result_file.read_text(encoding="utf-8"))

    def assert_no_leak(self):
        text = self.result_file.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        self.assertNotIn(self.tmp.name, text)


class NormalUpdate(DeployTestCase):
    def test_updates_alias_and_records_success(self):
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(result["overall"], "success")
        self.assertEqual(result["deployment"], "completed")
        self.assertEqual(result["previousSmoke"], "passed")
        self.assertEqual(result["smoke"], "passed")
        self.assertEqual(result["rollback"], "not-needed")
        self.assertEqual(result["versions"], {"previous": "1", "new": "2", "reused": False})
        self.assertTrue(result["mutationStarted"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")
        self.assertEqual(result["lastGood"]["version"], "2")
        self.assertEqual(
            dl.parse_description(self.aws.versions["2"]["Description"]),
            {"source": NEW_SHA, "zip": self.zip_hex, "run": "200"},
        )
        self.assertEqual(
            self.aws.mutating(),
            ["update-function-code", "publish-version", "update-alias"],
        )
        # 現在版 → 新しい版の順にスモーク確認し、期待SHAを取り違えない
        self.assertEqual(
            [(sha, label) for _, sha, label in self.smoke.calls],
            [(OLD_SHA, "previous"), (NEW_SHA, "new")],
        )
        self.assert_no_leak()


class FailedSmokeRollsBack(DeployTestCase):
    def test_failed_new_smoke_restores_previous_and_stays_failed(self):
        self.smoke.outcomes["new"] = False
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(result["deployment"], "completed")  # 配布処理とスモークは別に記録
        self.assertEqual(result["smoke"], "failed")
        self.assertEqual(result["rollback"], "succeeded")
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")
        self.assertEqual(result["alias"]["state"], "rolled-back")
        self.assertEqual(result["lastGood"]["version"], "1")
        self.assertIn("2", self.aws.versions)  # 失敗した版も残す
        self.assertEqual(
            [(sha, label) for _, sha, label in self.smoke.calls][-1], (OLD_SHA, "rollback")
        )
        self.assert_no_leak()

    def test_rollback_uses_revision_returned_by_our_update(self):
        self.smoke.outcomes["new"] = False
        self.deploy()
        updates = [call for call in self.aws.calls if call[1] == "update-alias"]
        self.assertEqual(len(updates), 2)
        # 戻しには、こちらの最初の更新が返したRevisionIdを使う（読み直した値や固定値ではない）
        self.assertEqual(updates[0][updates[0].index("--revision-id") + 1], "a-1")
        self.assertEqual(
            updates[1][updates[1].index("--revision-id") + 1], self.aws.alias_revisions[0]
        )

    def test_rollback_failure_is_recorded_when_alias_changed_externally(self):
        self.smoke.outcomes["new"] = False

        def external_change(label):
            if label == "new":
                self.aws.alias["RevisionId"] = "a-external"

        self.smoke.hook = external_change
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["rollback"], "failed")
        self.assertIn("手動復旧", result["recoveryNote"])
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(self.aws.alias["RevisionId"], "a-external")  # 上書きしない
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")
        self.assertEqual(set(self.aws.versions), {"1", "2"})

    def test_rollback_smoke_failure_is_distinct(self):
        self.smoke.outcomes.update({"new": False, "rollback": False})
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["rollback"], "alias-restored-smoke-failed")
        self.assertIsNone(result["lastGood"])


class FailClosedBeforeMutation(DeployTestCase):
    def assert_untouched(self, code):
        self.assertEqual(code, 1)
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(self.result()["overall"], "failed")
        self.assertEqual(self.result()["deployment"], "not-started")
        self.assert_no_leak()

    def test_zip_tamper_blocks_all_aws_calls(self):
        (self.artifact / f"lambda-{NEW_SHA}.zip").write_bytes(b"tampered")
        self.assert_untouched(self.deploy())
        self.assertEqual(self.aws.calls, [])

    def test_manifest_tamper_blocks(self):
        path = self.artifact / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["releaseEligible"] = False
        path.write_text(json.dumps(manifest))
        self.assert_untouched(self.deploy())
        self.assertEqual(self.aws.calls, [])

    def test_manifest_with_other_layer_blocks(self):
        path = self.artifact / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["adapter"]["layerArn"] = "arn:aws:lambda:ap-northeast-1:111111111111:layer:Evil:1"
        path.write_text(json.dumps(manifest))
        self.assert_untouched(self.deploy())

    def test_zip_hash_must_match_build_output(self):
        self.assert_untouched(self.deploy(zip_hex="0" * 64))
        self.assertEqual(self.aws.calls, [])

    def test_extra_file_in_artifact_blocks(self):
        (self.artifact / "extra.txt").write_text("x")
        self.assert_untouched(self.deploy())

    def test_prior_bad_health_blocks_mutation(self):
        self.smoke.outcomes["previous"] = False
        self.assert_untouched(self.deploy())
        self.assertEqual(self.result()["previousSmoke"], "failed")
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_wrong_runtime_blocks(self):
        self.aws.function["Runtime"] = "python3.13"
        self.assert_untouched(self.deploy())

    def test_wrong_layer_blocks(self):
        self.aws.function["Layers"] = [{"Arn": "arn:aws:lambda:ap-northeast-1:1:layer:X:1"}]
        self.assert_untouched(self.deploy())

    def test_missing_exec_wrapper_blocks(self):
        del self.aws.function["Environment"]["Variables"]["AWS_LAMBDA_EXEC_WRAPPER"]
        self.assert_untouched(self.deploy())

    def test_layer_hash_mismatch_blocks(self):
        self.aws.layer = {"Content": {"CodeSha256": "B" * 43 + "="}}
        self.assert_untouched(self.deploy())

    def test_function_url_mismatch_blocks(self):
        self.aws.url["FunctionUrl"] = "https://zzzzzzzz12345678.lambda-url.ap-northeast-1.on.aws/"
        self.assert_untouched(self.deploy())

    def test_function_url_auth_must_be_none(self):
        self.aws.url["AuthType"] = "AWS_IAM"
        self.assert_untouched(self.deploy())

    def test_weighted_alias_rejected(self):
        self.aws.alias["RoutingConfig"] = {"AdditionalVersionWeights": {"1": 0.1}}
        self.assert_untouched(self.deploy())

    def test_previous_version_without_metadata_rejected(self):
        self.aws.versions["1"]["Description"] = "manual upload"
        self.assert_untouched(self.deploy())

    def test_previous_description_must_match_code_hash(self):
        self.aws.versions["1"]["CodeSha256"] = code_sha(hashlib.sha256(b"other").hexdigest())
        self.assert_untouched(self.deploy())

    def test_latest_alias_target_rejected(self):
        self.aws.alias["FunctionVersion"] = "$LATEST"
        self.assert_untouched(self.deploy())


class RacesAndErrors(DeployTestCase):
    def test_alias_race_does_not_clobber_or_roll_back(self):
        def external_change():
            self.aws.alias = {"FunctionVersion": "1", "RevisionId": "a-external", "RoutingConfig": {}}

        self.aws.before["update-alias"] = external_change
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(self.aws.ops().count("update-alias"), 1)  # 戻し処理もしない
        self.assertEqual(self.aws.alias["RevisionId"], "a-external")
        self.assertEqual(result["alias"]["state"], "conflict-not-switched")
        self.assertEqual(result["rollback"], "not-needed")
        self.assertEqual(result["smoke"], "not-run")
        self.assertEqual(result["deployment"], "failed")
        self.assertIn("2", self.aws.versions)
        self.assertEqual(self.smoke.calls[-1][2], "previous")  # 新しい版のスモークはしない

    def test_function_revision_race_stops_before_alias(self):
        self.aws.before["update-function-code"] = lambda: self.aws.function.update(RevisionId="f-ext")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_code_sha_mismatch_after_upload_stops_before_publish(self):
        original = self.aws.__call__

        def tampered(args):
            response = original(args)
            if args[1] == "update-function-code":
                response["CodeSha256"] = code_sha(hashlib.sha256(b"x").hexdigest())
            return response

        self.assertEqual(self.deploy(aws=tampered), 1)
        self.assertNotIn("publish-version", self.aws.ops())
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_ambiguous_alias_error_that_did_switch_needs_manual_recovery(self):
        """応答が失われた更新は、実際に切り替わっていても自動では戻さない（読み直した値は根拠にしない）。"""
        original = self.aws.__call__

        def flaky(args):
            response = original(args)
            if args[1] == "update-alias":
                raise dl.AwsError("update-alias", 255)  # 応答が失われた想定
            return response

        self.assertEqual(self.deploy(aws=flaky), 1)
        result = self.result()
        self.assertEqual(self.aws.ops().count("update-alias"), 1)  # 戻しの更新をしない
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")  # 上書きしない
        self.assertEqual(result["rollback"], "not-needed")
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(result["deployment"], "alias-outcome-unknown")
        self.assertEqual(result["alias"]["state"], "unknown-outcome")
        self.assertEqual(result["alias"]["version"], "2")  # 観測値（こちらの結果とは限らない）
        self.assertIn("人が", result["recoveryNote"])
        self.assertEqual(result["smoke"], "not-run")
        self.assertEqual(self.smoke.calls[-1][2], "previous")

    def test_unknown_alias_outcome_never_rolls_back_an_external_change(self):
        """読み直したRevisionIdが外部のものでも、それを使った戻しをしない。"""

        def external_change():
            self.aws.alias = {"FunctionVersion": "2", "RevisionId": "a-external", "RoutingConfig": {}}

        self.aws.before["update-alias"] = external_change
        self.aws.fail["update-alias"] = dl.AwsError("update-alias", 255)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(self.aws.ops().count("update-alias"), 1)
        self.assertEqual(self.aws.alias["RevisionId"], "a-external")
        self.assertEqual(result["alias"]["state"], "unknown-outcome")
        self.assertEqual(result["rollback"], "not-needed")

    def test_unreadable_alias_after_unknown_outcome_is_recorded_without_values(self):
        self.aws.fail["update-alias"] = dl.AwsError("update-alias", 255)
        original = self.aws.__call__

        def no_alias_read_after(args):
            if args[1] == "get-alias" and self.aws.ops().count("update-alias") >= 1:
                raise dl.AwsError("get-alias", 255)
            return original(args)

        self.assertEqual(self.deploy(aws=no_alias_read_after), 1)
        alias = self.result()["alias"]
        self.assertEqual((alias["state"], alias["version"], alias["revisionId"]), ("unknown-outcome", None, None))

    def test_malformed_alias_update_response_is_unknown_outcome(self):
        original = self.aws.__call__

        def malformed(args):
            response = original(args)
            if args[1] == "update-alias":
                response["FunctionVersion"] = "1"  # 期待と異なる応答
            return response

        self.assertEqual(self.deploy(aws=malformed), 1)
        result = self.result()
        self.assertEqual(self.aws.ops().count("update-alias"), 1)
        self.assertEqual(result["alias"]["state"], "unknown-outcome")
        self.assertEqual(result["smoke"], "not-run")

    def test_alias_conflict_does_not_claim_previous_version_as_actual(self):
        def external_change():
            self.aws.alias = {"FunctionVersion": "1", "RevisionId": "a-external", "RoutingConfig": {}}

        self.aws.before["update-alias"] = external_change
        self.deploy()
        alias = self.result()["alias"]
        self.assertIsNone(alias["version"])
        self.assertIn("不明", self.result()["recoveryNote"])

    def test_unexpected_exception_is_sanitized(self):
        self.aws.fail["publish-version"] = RuntimeError(SECRET)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["error"], "想定外のエラー: RuntimeError")
        self.assert_no_leak()

    def test_aws_error_message_has_operation_and_code_only(self):
        error = dl.AwsError("lambda publish-version", 254)
        self.assertEqual(str(error), "AWS操作に失敗: lambda publish-version (終了コード 254)")

    def test_result_written_at_each_stage_for_kill_recovery(self):
        seen = []
        original = self.aws.__call__

        def spy(args):
            if args[1] == "update-alias" and self.result_file.exists():
                seen.append(self.result())
            return original(args)

        self.deploy(aws=spy)
        self.assertEqual(seen[0]["overall"], "in-progress")
        self.assertEqual(seen[0]["stage"], "switch-alias")
        self.assertIn("版 1", seen[0]["recoveryNote"])


class RollbackResponseValidation(DeployTestCase):
    def deploy_with_bad_rollback_response(self, tamper):
        self.smoke.outcomes["new"] = False
        original = self.aws.__call__
        count = {"n": 0}

        def wrapped(args):
            response = original(args)
            if args[1] == "update-alias":
                count["n"] += 1
                if count["n"] == 2:  # 戻しの更新の応答だけを壊す
                    tamper(response)
            return response

        return self.deploy(aws=wrapped)

    def assert_manual_recovery(self):
        result = self.result()
        self.assertEqual(result["rollback"], "failed")
        self.assertEqual(result["alias"]["state"], "unknown-outcome")
        self.assertEqual(result["overall"], "failed")
        self.assertIn("手動復旧", result["recoveryNote"])
        # 戻しのスモーク確認は、応答を検証できないので行わない
        self.assertNotIn("rollback", [label for _, _, label in self.smoke.calls])

    def test_rollback_response_without_revision_is_failed(self):
        self.assertEqual(self.deploy_with_bad_rollback_response(lambda r: r.pop("RevisionId")), 1)
        self.assert_manual_recovery()

    def test_rollback_response_with_wrong_version_is_failed(self):
        self.assertEqual(
            self.deploy_with_bad_rollback_response(lambda r: r.update(FunctionVersion="2")), 1
        )
        self.assert_manual_recovery()


class AlreadyCurrent(DeployTestCase):
    def make_current(self):
        """エイリアスの版が、今回と同じソースSHA・ZIPで発行済みの状態にする。"""
        sha = code_sha(self.zip_hex)
        self.aws.function["CodeSha256"] = sha
        self.aws.versions["1"] = self.aws._snapshot(
            "1", dl.make_description(NEW_SHA, self.zip_hex, "100")
        )

    def test_same_source_and_zip_is_explicit_no_change_success(self):
        self.make_current()
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(result["overall"], "no-change-success")
        self.assertEqual(result["deployment"], "already-current")
        self.assertEqual(result["smoke"], "not-run")  # 新しい配布のスモーク確認はしていない
        self.assertEqual(result["previousSmoke"], "passed")
        self.assertIsNone(result["versions"]["new"])
        self.assertFalse(result["mutationStarted"])
        self.assertIn("新しい配布の成功ではない", result["note"])
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(set(self.aws.versions), {"1"})
        self.assertEqual([(sha, label) for _, sha, label in self.smoke.calls], [(NEW_SHA, "previous")])

    def test_failed_previous_smoke_is_not_a_no_change_success(self):
        self.make_current()
        self.smoke.outcomes["previous"] = False
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.result()["overall"], "failed")
        self.assertEqual(self.aws.mutating(), [])

    def test_same_source_with_different_zip_is_a_normal_update(self):
        self.aws.versions["1"] = self.aws._snapshot(
            "1", dl.make_description(NEW_SHA, OLD_ZIP, "100")
        )
        self.assertEqual(self.deploy(), 0)
        self.assertEqual(self.result()["overall"], "success")
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")


class ExistingPublishedVersion(DeployTestCase):
    """前回の試行が版を発行した後に止まった場合。AWSは同一内容なら既存の版を返す。"""

    def prepare(self, description=None):
        self.aws.dedupe_publish = True
        self.aws.function["CodeSha256"] = code_sha(self.zip_hex)
        self.aws.versions["2"] = self.aws._snapshot(
            "2", description or dl.make_description(NEW_SHA, self.zip_hex, "150")
        )

    def test_provable_existing_version_is_reused(self):
        self.prepare()
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(result["overall"], "success")
        self.assertEqual(result["versions"], {"previous": "1", "new": "2", "reused": True})
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")
        self.assertEqual(set(self.aws.versions), {"1", "2"})  # 新しい版は作っていない

    def test_existing_version_from_another_source_fails_closed(self):
        self.prepare(dl.make_description(OTHER_SHA, self.zip_hex, "150"))
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertIn("手動", result["error"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")
        self.assertNotIn("update-alias", self.aws.ops())

    def test_existing_version_without_provenance_fails_closed(self):
        self.prepare("manual upload")
        self.assertEqual(self.deploy(), 1)
        self.assertNotIn("update-alias", self.aws.ops())

    def test_existing_version_with_different_checked_config_fails_closed(self):
        self.prepare()
        self.aws.versions["2"]["Layers"] = [{"Arn": "arn:aws:lambda:ap-northeast-1:1:layer:X:1"}]
        self.assertEqual(self.deploy(), 1)
        self.assertIn("layers", self.result()["error"])
        self.assertNotIn("update-alias", self.aws.ops())

    def test_never_changes_configuration_to_force_a_version(self):
        self.prepare(dl.make_description(OTHER_SHA, self.zip_hex, "150"))
        self.deploy()
        self.assertEqual(
            [op for op in self.aws.ops() if op.startswith("update-") or op.startswith("put-")],
            ["update-function-code"],
        )


class RevisionCapture(DeployTestCase):
    def test_update_uses_revision_captured_at_validation(self):
        self.assertEqual(self.deploy(), 0)
        update = next(call for call in self.aws.calls if call[1] == "update-function-code")
        self.assertEqual(update[update.index("--revision-id") + 1], "f-1")
        # 設定の読み直しは、検証（1回）と更新後（1回）と版の確認（修飾子つき）だけ
        reads = [c for c in self.aws.calls if c[1] == "get-function-configuration" and "--qualifier" not in c]
        self.assertEqual(len(reads), 2)
        # 更新の直前に、設定を読み直して最新のRevisionIdを取り直してはいない
        index = next(i for i, call in enumerate(self.aws.calls) if call[1] == "update-function-code")
        before = self.aws.calls[index - 1]
        self.assertTrue(before[1] != "get-function-configuration" or "--qualifier" in before)

    def test_config_change_between_validation_and_update_stops_before_publish(self):
        # 現行版のスモーク確認の間に、別の者が設定を変えた
        self.smoke.hook = lambda label: self.aws.function.update(Runtime="python3.13", RevisionId="f-ext")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code"])  # 条件付きで拒否される
        self.assertEqual(self.aws.function["Runtime"], "python3.13")
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")
        self.assertEqual(self.result()["alias"]["state"], "untouched")

    def tamper_after_update(self, **changes):
        original = self.aws.__call__

        def wrapped(args):
            if args[1] == "wait":
                self.aws.function.update(changes)
            return original(args)

        return wrapped

    def test_runtime_change_between_update_and_publish_stops_before_publish(self):
        wrapped = self.tamper_after_update(Runtime="python3.13", RevisionId="f-ext")
        self.assertEqual(self.deploy(aws=wrapped), 1)
        self.assertNotIn("publish-version", self.aws.ops())
        self.assertNotIn("update-alias", self.aws.ops())
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_foreign_revision_between_update_and_publish_stops_before_publish(self):
        wrapped = self.tamper_after_update(RevisionId="f-ext")
        self.assertEqual(self.deploy(aws=wrapped), 1)
        self.assertIn("revisionId", self.result()["error"])
        self.assertNotIn("publish-version", self.aws.ops())
        self.assertNotIn("update-alias", self.aws.ops())


class PublishedVersionActivation(DeployTestCase):
    """発行直後の版が Pending でも、Active を待ってから確認・切り替える。"""

    def test_pending_published_version_succeeds_after_waiting(self):
        self.aws.pending_publish = True
        self.assertEqual(self.deploy(), 0)
        ops = self.aws.ops()
        waits = [c for c in self.aws.calls if c[1] == "wait" and c[2] == "function-active-v2"]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0][3:], ["--function-name", FUNCTION, "--qualifier", "2"])
        # 待機は、発行の後・版の確認の前・エイリアス切り替えの前
        publish = ops.index("publish-version")
        wait = ops.index("wait", publish)
        verify = ops.index("get-function-configuration", wait)
        self.assertLess(wait, verify)
        self.assertLess(verify, ops.index("update-alias"))
        self.assertEqual(self.aws.versions["2"]["State"], "Active")
        self.assertEqual(self.result()["overall"], "success")
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")

    def test_pending_without_waiter_would_fail_closed(self):
        # 待機が効かない（Pendingのまま）場合、検査は従来どおり閉じて止まる（緩めていない）
        self.aws.pending_publish = True
        original = self.aws.__call__

        def no_activation(args):
            if args[1] == "wait" and args[2] == "function-active-v2":
                return {}
            return original(args)

        self.assertEqual(self.deploy(aws=no_activation), 1)
        self.assertIn("state", self.result()["error"])
        self.assertNotIn("update-alias", self.aws.ops())
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_waiter_failure_leaves_alias_untouched(self):
        self.aws.pending_publish = True
        self.aws.wait_fail["function-active-v2"] = dl.AwsError("wait function-active-v2", 255)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(result["stage"], "wait-published")
        self.assertNotIn("update-alias", self.aws.ops())
        self.assertEqual(self.aws.alias, {"FunctionVersion": "1", "RevisionId": "a-1", "RoutingConfig": {}})
        self.assertEqual(result["alias"]["state"], "untouched")
        self.assertEqual(result["rollback"], "not-needed")
        self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version"])

    def test_waits_before_verifying_reused_version(self):
        self.aws.dedupe_publish = True
        self.aws.function["CodeSha256"] = code_sha(self.zip_hex)
        self.aws.versions["2"] = self.aws._snapshot(
            "2", dl.make_description(NEW_SHA, self.zip_hex, "150")
        )
        self.aws.versions["2"]["State"] = "Pending"
        self.assertEqual(self.deploy(), 0)
        self.assertTrue(self.result()["versions"]["reused"])
        self.assertEqual(self.aws.versions["2"]["State"], "Active")
        self.assertIn("wait", self.aws.ops())


class SummaryFlags(DeployTestCase):
    def test_mutation_not_started_when_validation_fails(self):
        self.aws.function["Runtime"] = "python3.13"
        self.deploy()
        self.assertFalse(self.result()["mutationStarted"])

    def test_mutation_started_is_recorded_even_when_update_fails(self):
        self.aws.fail["update-function-code"] = dl.AwsError("update-function-code", 255)
        self.deploy()
        result = self.result()
        self.assertTrue(result["mutationStarted"])
        self.assertEqual(result["overall"], "failed")


class VerifyArtifactOnly(DeployTestCase):
    def run_cli(self, *extra):
        argv = [
            "--artifact-dir", str(self.artifact),
            "--source-sha", NEW_SHA,
            "--zip-sha256", self.zip_hex,
            "--verify-artifact-only",
            *extra,
        ]
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}), \
                mock.patch.object(dl, "run_aws", side_effect=AssertionError("AWS呼び出し")), \
                mock.patch.object(dl.subprocess, "run", side_effect=AssertionError("subprocess")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return dl.main(argv), out.getvalue()

    def test_valid_artifact_passes_without_aws_or_other_arguments(self):
        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertNotIn(self.tmp.name, output)

    def test_tampered_zip_is_rejected_before_any_aws_call(self):
        (self.artifact / f"lambda-{NEW_SHA}.zip").write_bytes(b"tampered")
        code, _ = self.run_cli()
        self.assertEqual(code, 1)

    def test_tampered_manifest_is_rejected(self):
        path = self.artifact / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["dirty"] = True
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.run_cli()[0], 1)

    def test_not_release_eligible_is_rejected(self):
        path = self.artifact / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["releaseEligible"] = False
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.run_cli()[0], 1)

    def test_other_source_sha_is_rejected(self):
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}), \
                contextlib.redirect_stderr(io.StringIO()):
            code = dl.main([
                "--artifact-dir", str(self.artifact), "--source-sha", OTHER_SHA,
                "--zip-sha256", self.zip_hex, "--verify-artifact-only",
            ])
        self.assertEqual(code, 1)

    def test_deploy_mode_requires_the_deploy_arguments(self):
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}):
            with self.assertRaises(SystemExit):
                dl.main(["--artifact-dir", str(self.artifact), "--source-sha", NEW_SHA,
                         "--zip-sha256", self.zip_hex])


class InputValidation(unittest.TestCase):
    def args(self, **overrides):
        values = dict(
            verify_artifact_only=False,
            source_sha=NEW_SHA,
            zip_sha256="0" * 64,
            run_id="1",
            run_attempt="1",
            function_name=FUNCTION,
            base_url=BASE_URL,
            layer_code_sha256=LAYER_SHA,
            ci_run_url="https://github.com/example/repo/actions/runs/1",
        )
        values.update(overrides)
        return mock.Mock(**values)

    def test_valid_inputs_pass(self):
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}):
            dl.validate_inputs(self.args())

    def test_rejects_non_function_url_endpoint(self):
        for url in ("https://example.com", BASE_URL + "/path", "http://x.lambda-url.ap-northeast-1.on.aws"):
            with self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(base_url=url))

    def test_rejects_short_sha_and_blank_values(self):
        for overrides in ({"source_sha": "abc"}, {"layer_code_sha256": ""}, {"function_name": ""}):
            with self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(**overrides))

    def test_cd_allow_local_is_never_accepted(self):
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "true"}):
            with self.assertRaises(SystemExit):
                dl.validate_inputs(self.args())

    def test_smoke_environment_forces_allow_local_false_and_drops_credentials(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["env"] = kwargs["env"]
            return mock.Mock(returncode=0)

        env = {"AWS_SECRET_ACCESS_KEY": "s", "PATH": "/usr/bin", "CD_ALLOW_LOCAL": "false"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(dl.subprocess, "run", fake_run):
            self.assertTrue(dl.run_smoke(BASE_URL, NEW_SHA, "run1-1-new"))
        self.assertEqual(captured["env"]["CD_ALLOW_LOCAL"], "false")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", captured["env"])


if __name__ == "__main__":
    unittest.main()
