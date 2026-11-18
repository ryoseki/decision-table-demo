"""scripts/deploy-lambda.py のオフラインテスト: 成果物の検証・版の公開・エイリアス切替・スモーク確認・戻し・CLI。

AWSとスモーク確認は、deploy_test_support の偽物（FakeAws / FakeSmoke）で置き換える。
公開経路（CloudFront / OAC / WAF / 関数URL / ポリシー / LIVEのエッジ関数）は test_public_path.py。
これは配布ロジックの確認であり、実際のAWS・Lambdaの動作の検証ではない。
  python3 -B -I -m unittest discover -s tests/cd
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from deploy_test_support import (
    ACCOUNT,
    BASE_URL,
    CF_HOST,
    DIST_ID,
    DeployTestCase,
    EDGE_ARN,
    FUNCTION,
    LAMBDA_HOST,
    LAYER_SHA,
    MUTATING,
    NEW_SHA,
    OAC_ID,
    OLD_SHA,
    OLD_ZIP,
    OTHER_ACCOUNT,
    OTHER_SHA,
    ROOT,
    SECRET,
    WAF_ARN,
    code_sha,
    dl,
    enable_write,
)


def opt(call, name):
    return call[call.index(name) + 1]


class Lifecycle(DeployTestCase):
    """配布の順序・条件付き更新・成功の記録（通常の更新）。"""

    def test_new_version_is_published_then_the_alias_switched_in_order(self):
        seen = {}
        self.smoke.hook = lambda label: seen.update({label: self.aws.mutating()})
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(
            (result["overall"], result["stage"], result["rollback"], result["alias"]),
            ("success", "done", "not-needed", "switched"),
        )
        self.assertEqual(result["versions"], {"previous": "1", "new": "2"})
        self.assertEqual(result["smoke"], {"previous": "passed", "new": "passed", "rollback": "not-run"})
        self.assertTrue(result["mutationStarted"])
        self.assertIsNone(result["recoveryNote"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")
        self.assertEqual(
            dl.parse_description(self.aws.versions["2"]["Description"]),
            {"source": NEW_SHA, "zip": self.zip_hex, "run": "200"},
        )
        steps = [c[1] if c[1] != "wait" else "wait:" + c[2] for c in self.aws.calls if c[1] in MUTATING | {"wait"}]
        self.assertEqual(
            steps,
            ["update-function-code", "wait:function-updated-v2", "publish-version", "wait:function-active-v2", "update-alias"],
        )
        ops = self.aws.ops()
        for read in ("get-function-concurrency", "get-layer-version-by-arn", "get-policy", "get-distribution"):
            self.assertLess(ops.index(read), ops.index("update-function-code"))
        # 現行版は変更の前、新しい版は切り替えの後。公開URL（CloudFront）だけで、期待SHAを取り違えない
        self.assertEqual(seen, {"previous": [], "new": ["update-function-code", "publish-version", "update-alias"]})
        self.assertEqual(self.smoke_labels(), [(OLD_SHA, "previous"), (NEW_SHA, "new")])
        self.assertEqual({url for url, _, _ in self.smoke.calls}, {BASE_URL})

    def test_every_write_is_conditional_on_the_revision_it_was_validated_against(self):
        self.assertEqual(self.deploy(), 0)
        update, publish, alias = (
            next(c for c in self.aws.calls if c[1] == op) for op in ("update-function-code", "publish-version", "update-alias")
        )
        self.assertEqual(opt(update, "--revision-id"), "f-1")  # 検証時のRevisionId
        # 発行は、更新の応答ではなく、完了後に読み直したRevisionIdとコードのハッシュが条件
        self.assertNotEqual(self.aws.update_revision, self.aws.final_revision)
        self.assertEqual(opt(publish, "--revision-id"), self.aws.final_revision)
        self.assertEqual(opt(publish, "--code-sha256"), code_sha(self.zip_hex))
        self.assertEqual(opt(alias, "--revision-id"), "a-1")  # 検証時のエイリアスのRevisionId
        self.assertEqual(opt(alias, "--function-version"), "2")
        self.assertEqual(json.loads(opt(alias, "--routing-config")), {"AdditionalVersionWeights": {}})

    def test_already_current_is_a_no_change_success_only_when_the_current_version_is_healthy(self):
        self.aws.versions["1"] = self.aws._snapshot("1", dl.make_description(NEW_SHA, self.zip_hex, "100"))
        self.aws.versions["1"]["CodeSha256"] = code_sha(self.zip_hex)
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual((result["overall"], result["stage"]), ("no-change-success", "done"))
        self.assertIn("新しい配布の成功ではない", result["note"])
        self.assertEqual((self.aws.mutating(), self.smoke_labels()), ([], [(NEW_SHA, "previous")]))
        self.assertFalse(result["mutationStarted"])
        # 同じSHA・ZIPでも、現行版のスモーク確認が失敗すれば、成功にせず止まる
        self.reset()
        self.aws.versions["1"] = self.aws._snapshot("1", dl.make_description(NEW_SHA, self.zip_hex, "100"))
        self.aws.versions["1"]["CodeSha256"] = code_sha(self.zip_hex)
        self.smoke.outcomes["previous"] = False
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.result()["overall"], "failed")
        self.assertEqual(self.aws.mutating(), [])

    def test_same_source_with_a_different_zip_is_a_normal_update(self):
        self.aws.versions["1"] = self.aws._snapshot("1", dl.make_description(NEW_SHA, OLD_ZIP, "100"))
        self.assertEqual(self.deploy(), 0)
        self.assertEqual(self.result()["overall"], "success")
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")

    def test_unhealthy_current_version_blocks_any_change(self):
        self.smoke.outcomes["previous"] = False
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(self.result()["smoke"]["previous"], "failed")

    def test_reserved_concurrency_must_be_exactly_three_and_is_never_changed(self):
        bad = {"missing": {}, "null": {"ReservedConcurrentExecutions": None}, "zero": {"ReservedConcurrentExecutions": 0}}
        bad.update({str(v): {"ReservedConcurrentExecutions": v} for v in (1, 2, 4, -1, True, "3", 3.0)})
        for name, response in bad.items():
            with self.subTest(name):
                self.reset()
                self.aws.concurrency = response
                self.blocked("3ではありません")
                self.assertEqual([c for c in self.aws.calls if c[1].endswith("function-concurrency")],
                                 [["lambda", "get-function-concurrency", "--function-name", FUNCTION]])
        self.reset()
        self.aws.fail["get-function-concurrency"] = dl.AwsError("lambda get-function-concurrency", 254)
        self.blocked("AWS操作に失敗")


class AliasSwitchAndRollback(DeployTestCase):
    def test_failed_new_smoke_restores_the_previous_version_with_our_own_revision(self):
        self.smoke.outcomes["new"] = False
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual((result["overall"], result["rollback"], result["alias"]), ("failed", "succeeded", "rolled-back"))
        self.assertEqual(result["smoke"], {"previous": "passed", "new": "failed", "rollback": "passed"})
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")
        self.assertIn("2", self.aws.versions)  # 失敗した版も消さない
        self.assertIsNone(result["recoveryNote"])
        self.assertEqual(self.smoke_labels()[-1], (OLD_SHA, "rollback"))
        first, second = [c for c in self.aws.calls if c[1] == "update-alias"]
        self.assertEqual(opt(first, "--revision-id"), "a-1")
        self.assertEqual(opt(second, "--revision-id"), self.aws.alias_revisions[0])  # 読み直した値ではない
        self.assertEqual(opt(second, "--function-version"), "1")

    def test_restored_alias_that_fails_its_smoke_is_reported_distinctly(self):
        self.smoke.outcomes.update({"new": False, "rollback": False})
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual((result["rollback"], result["alias"]), ("alias-restored-smoke-failed", "rolled-back"))
        self.assertIsNotNone(result["recoveryNote"])

    def test_alias_changed_by_someone_else_is_never_overwritten_by_the_rollback(self):
        self.smoke.outcomes["new"] = False
        self.smoke.hook = lambda label: self.aws.alias.update(RevisionId="a-external") if label == "new" else None
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual((result["rollback"], result["alias"]), ("failed", "unknown"))
        self.assertIn("get-alias", result["recoveryNote"])
        self.assertEqual((self.aws.alias["RevisionId"], self.aws.alias["FunctionVersion"]), ("a-external", "2"))
        self.assertEqual(self.smoke_labels(), [(OLD_SHA, "previous"), (NEW_SHA, "new")])  # 戻した版のスモークはしない

    def test_rollback_response_that_cannot_be_trusted_is_a_failed_rollback(self):
        for name, tamper in (
            ("wrong-version", lambda r: {**r, "FunctionVersion": "2"}),
            ("no-revision", lambda r: {k: v for k, v in r.items() if k != "RevisionId"}),
        ):
            with self.subTest(name):
                self.reset()
                self.smoke.outcomes["new"] = False
                self.aws.alias_tamper = lambda r, tamper=tamper: tamper(r) if r["FunctionVersion"] == "1" else r
                self.assertEqual(self.deploy(), 1)
                self.assertEqual(self.result()["rollback"], "failed")
                self.assertNotIn("rollback", [label for _, _, label in self.smoke.calls])

    def test_uncertain_switch_is_never_rolled_back_or_called_a_success(self):
        def conflict():
            self.aws.alias.update(RevisionId="a-external")

        cases = {
            "conflict": (lambda: setattr(self.aws, "before", {"update-alias": conflict}), "外部で変更"),
            "error-after-switching": (lambda: setattr(self.aws, "fail_after", {"update-alias": dl.AwsError("x", 255)}), "結果が不明"),
            "wrong-version-in-response": (lambda: setattr(self.aws, "alias_tamper", lambda r: {**r, "FunctionVersion": "9"}), "応答が期待と一致せず"),
            "weighted-response": (lambda: setattr(self.aws, "alias_tamper", lambda r: {**r, "RoutingConfig": {"AdditionalVersionWeights": {"1": 0.5}}}), "応答が期待と一致せず"),
            "no-revision-in-response": (lambda: setattr(self.aws, "alias_tamper", lambda r: {k: v for k, v in r.items() if k != "RevisionId"}), "応答が期待と一致せず"),
        }
        for name, (arrange, expect) in cases.items():
            with self.subTest(name):
                self.reset()
                arrange()
                self.assertEqual(self.deploy(), 1)
                result = self.result()
                self.assertEqual((result["overall"], result["alias"], result["rollback"]), ("failed", "unknown", "not-needed"))
                self.assertIn(expect, result["error"])
                self.assertIn("get-alias", result["recoveryNote"])
                self.assertEqual(len([c for c in self.aws.calls if c[1] == "update-alias"]), 1)  # 戻しの更新をしない
                self.assertEqual(result["smoke"]["new"], "not-run")
                self.assertEqual(set(self.aws.versions), {"1", "2"})
        # 外部の変更は上書きしない
        self.reset()
        self.aws.before["update-alias"] = conflict
        self.deploy()
        self.assertEqual(self.aws.alias["RevisionId"], "a-external")

    def test_progress_is_written_before_each_risky_step_for_killed_runs(self):
        seen = {}
        self.aws.before["update-alias"] = lambda: seen.update(alias=self.result())
        self.smoke.hook = lambda label: seen.update({label: self.result()})
        self.assertEqual(self.deploy(), 0)
        # エイリアスの更新を呼ぶ前に、結果不明の印と復旧メモを書く（強制終了・タイムアウトでも人が確認できる）
        self.assertEqual((seen["alias"]["overall"], seen["alias"]["stage"], seen["alias"]["alias"]), ("in-progress", "switch-alias", "unknown"))
        self.assertIn("get-alias", seen["alias"]["recoveryNote"])
        self.assertEqual((seen["new"]["alias"], seen["new"]["stage"], seen["new"]["overall"]), ("switched", "smoke-new", "in-progress"))
        self.assertTrue(seen["new"]["mutationStarted"])
        self.assertIsNotNone(seen["new"]["recoveryNote"])


# (名前, 変更前の検証で拒否されるべき偽AWSの状態を作る関数, エラーに含まれる文字列)
VALIDATION_CASES = [
    ("runtime", lambda t: t.aws.function.update(Runtime="python3.13"), "runtime"),
    ("architecture", lambda t: t.aws.function.update(Architectures=["arm64"]), "architecture"),
    ("handler", lambda t: t.aws.function.update(Handler="other"), "handler"),
    ("layer", lambda t: t.aws.function.update(Layers=[{"Arn": dl.PINNED_LAYER_ARN + "9"}]), "layers"),
    ("exec-wrapper", lambda t: t.aws.function["Environment"]["Variables"].pop("AWS_LAMBDA_EXEC_WRAPPER"), "execWrapper"),
    ("port", lambda t: t.aws.function["Environment"]["Variables"].update(PORT="1"), "port"),
    ("state", lambda t: t.aws.function.update(State="Failed"), "state"),
    ("function-revision-missing", lambda t: t.aws.function.update(RevisionId=""), "revisionId"),
    ("layer-hash", lambda t: t.aws.layer["Content"].update(CodeSha256="x"), "レイヤー"),
    ("alias-weighted", lambda t: t.aws.alias.update(RoutingConfig={"AdditionalVersionWeights": {"2": 0.1}}), "加重"),
    ("alias-latest", lambda t: t.aws.alias.update(FunctionVersion="$LATEST"), "数値の版"),
    ("alias-revision-missing", lambda t: t.aws.alias.pop("RevisionId"), "RevisionId"),
    ("previous-without-provenance", lambda t: t.aws.versions["1"].update(Description="manual"), "出所情報"),
    ("previous-code-differs", lambda t: t.aws.versions["1"].update(CodeSha256="x"), "ハッシュ"),
]


class RejectedBeforeMutation(DeployTestCase):
    def test_each_unsafe_state_is_rejected_before_any_change(self):
        for name, mutate, expect in VALIDATION_CASES:
            with self.subTest(name):
                self.reset()
                mutate(self)
                self.blocked(expect)

    def test_tampered_artifacts_are_rejected_before_any_aws_call(self):
        def zip_bytes(t):
            (t.artifact / f"lambda-{NEW_SHA}.zip").write_bytes(b"tampered")

        def manifest(**changes):
            def edit(t):
                path = t.artifact / "manifest.json"
                data = json.loads(path.read_text())
                data.update(changes)
                path.write_text(json.dumps(data))

            return edit

        def other_layer(t):
            path = t.artifact / "manifest.json"
            data = json.loads(path.read_text())
            data["adapter"]["layerArn"] = dl.PINNED_LAYER_ARN + "9"
            path.write_text(json.dumps(data))

        cases = {
            "zip": zip_bytes,
            "dirty": manifest(dirty=True),
            "not-release-eligible": manifest(releaseEligible=False),
            "other-source": manifest(sourceSHA=OTHER_SHA),
            "other-layer": other_layer,
            "extra-file": lambda t: (t.artifact / "extra.txt").write_text("x"),
        }
        for name, tamper in cases.items():
            with self.subTest(name):
                self.reset()
                self.use_artifact(f"tampered-{name}")  # 検査ごとに新しい成果物
                tamper(self)
                self.assertEqual(self.deploy(), 1)
                self.assertEqual(self.aws.calls, [])
                self.assertEqual(self.result()["overall"], "failed")
        self.reset()
        self.use_artifact("hash-mismatch")  # ビルドの出力したハッシュと違うZIPは受け付けない
        self.assertEqual(self.deploy(zip_sha256="0" * 64), 1)
        self.assertEqual(self.aws.calls, [])

    def test_failed_function_update_is_recorded_as_started(self):
        self.aws.fail["update-function-code"] = dl.AwsError("lambda update-function-code", 255)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertTrue(result["mutationStarted"])
        self.assertEqual((result["overall"], result["alias"]), ("failed", "untouched"))
        self.assertIn("終了コード 255", result["error"])
        self.assertEqual(self.aws.mutating(), ["update-function-code"])

    def test_unexpected_exceptions_and_aws_errors_do_not_leak_values(self):
        self.aws.fail["get-alias"] = RuntimeError(SECRET)
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.result()["error"], "想定外のエラー: RuntimeError")
        self.assertNotIn(SECRET, self.result_file.read_text())
        self.assertEqual(str(dl.AwsError("lambda get-alias", 254)), "AWS操作に失敗: lambda get-alias (終了コード 254)")


class RevisionRaces(DeployTestCase):
    """検証後に関数・エイリアスが変わったら、条件付き更新が拒否され、先へ進まない。"""

    def test_function_changed_after_validation_stops_the_code_update(self):
        self.aws.before["update-function-code"] = lambda: self.aws.function.update(RevisionId="f-external")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_function_changed_after_the_completed_read_stops_publishing(self):
        self.aws.before["publish-version"] = lambda: self.aws.function.update(RevisionId="f-external")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version"])
        self.assertEqual((set(self.aws.versions), self.aws.alias["FunctionVersion"]), ({"1"}, "1"))

    def test_state_after_the_update_is_checked_before_publishing(self):
        cases = {
            "update-failed": (lambda f: f.update(LastUpdateStatus="Failed"), "lastUpdateStatus"),
            "status-key-missing": (lambda f: f.pop("LastUpdateStatus"), "lastUpdateStatus"),
            "state-key-missing": (lambda f: f.pop("State"), "state"),
            "revision-empty": (lambda f: f.update(RevisionId=""), "revisionId"),
            "code-changed": (lambda f: f.update(CodeSha256=code_sha(OLD_ZIP)), "codeSha256"),
            "runtime-changed": (lambda f: f.update(Runtime="python3.13"), "runtime"),
            "layer-changed": (lambda f: f.update(Layers=[]), "layers"),
        }
        for name, (change, expect) in cases.items():
            with self.subTest(name):
                self.reset()
                self.aws.on_updated = lambda change=change: change(self.aws.function)
                self.assertEqual(self.deploy(), 1)
                self.assertIn(expect, self.result()["error"])
                self.assertIn("発行しません", self.result()["error"])
                self.assertEqual(self.aws.mutating(), ["update-function-code"])

    def test_upload_hash_mismatch_stops_before_publishing(self):
        self.aws.upload_sha = code_sha(OLD_ZIP)
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code"])
        self.assertIn("CodeSha256", self.result()["error"])


class PublishedVersion(DeployTestCase):
    """発行された版（前回の試行が残した同一内容の版を含む）を確認してから、エイリアスを動かす。"""

    def stage_existing(self, description=None, **changes):
        """前回の試行が発行して止まった版2。AWSは、同じコード・設定なら新しい版を作らず、これを返す。"""
        self.aws.dedupe_publish = True
        self.aws.versions["2"] = self.aws._snapshot(
            "2", description or dl.make_description(NEW_SHA, self.zip_hex, "150")
        )
        self.aws.versions["2"]["CodeSha256"] = code_sha(self.zip_hex)
        self.aws.versions["2"].update(changes)

    def test_provable_version_from_an_earlier_attempt_is_used_without_creating_another(self):
        self.stage_existing()  # run番号だけが今回と違う
        self.assertEqual(self.deploy(), 0)
        self.assertEqual(set(self.aws.versions), {"1", "2"})
        self.assertEqual((self.result()["versions"]["new"], self.aws.alias["FunctionVersion"]), ("2", "2"))

    def test_unprovable_or_current_version_is_never_switched_to(self):
        other = dl.make_description(OTHER_SHA, self.zip_hex, "150")
        cases = {
            "other-source": dict(description=other),
            "no-provenance": dict(description="manual"),
            "different-checked-config": dict(Runtime="python3.13"),
            "is-the-current-version": dict(),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.reset()
                self.stage_existing(**kwargs)
                if name == "is-the-current-version":  # 現在のエイリアスの版と同一内容として返る
                    self.aws.versions["1"] = self.aws._snapshot(
                        "1", dl.make_description(OLD_SHA, self.zip_hex, "100")
                    )
                    self.aws.versions["1"]["CodeSha256"] = code_sha(self.zip_hex)
                    del self.aws.versions["2"]
                self.assertEqual(self.deploy(), 1)
                self.assertIn("手動で確認", self.result()["error"])
                self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version"])
                self.assertEqual(self.aws.alias["FunctionVersion"], "1")
                self.assertEqual(self.result()["alias"], "untouched")

    def test_a_pending_version_is_waited_for_and_a_failed_wait_leaves_the_alias(self):
        self.aws.pending_publish = True
        self.assertEqual(self.deploy(), 0)
        self.reset()
        self.aws.pending_publish = True
        self.aws.wait_fail["function-active-v2"] = dl.AwsError("lambda wait", 255)
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version"])
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_malformed_publish_response_stops_before_the_alias(self):
        self.aws.publish_override = {"Version": "$LATEST"}
        self.assertEqual(self.deploy(), 1)
        self.assertNotIn("update-alias", self.aws.ops())


class DescriptionMarkers(unittest.TestCase):
    def test_markers_round_trip_in_a_fixed_order(self):
        self.assertEqual(dl.make_description(NEW_SHA, OLD_ZIP, "1"), f"source={NEW_SHA};zip={OLD_ZIP};run=1")
        both = dl.make_description(NEW_SHA, OLD_ZIP, "1", guard=True, viewer_ip=True)
        self.assertTrue(both.endswith(";guard=1;viewerip=1"))
        meta = dl.parse_description(both)
        self.assertTrue(meta["guard"] and meta["viewerip"])
        self.assertNotIn("viewerip", dl.parse_description(dl.make_description(NEW_SHA, OLD_ZIP, "1", guard=True)))

    def test_unknown_or_reordered_markers_are_not_trusted(self):
        base = f"source={NEW_SHA};zip={OLD_ZIP};run=1"
        for text in (base + ";viewerip=1;guard=1", base + ";viewerip=2", base + ";viewerip=1;viewerip=1", base + ";edge=1"):
            with self.subTest(text):
                self.assertIsNone(dl.parse_description(text))


@unittest.skipUnless(shutil.which("jq"), "jq が必要")
class WorkflowSummary(DeployTestCase):
    """ワークフローの「結果のサマリー」のjqを、実際の結果JSONに当てる（キー名のずれと欠けた値の表示）。"""

    def summary(self):
        text = (ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
        program = re.search(r"jq -r '(.*?)' cd-result/result.json", text, re.S).group(1)
        done = subprocess.run(
            ["jq", "-r", program, str(self.result_file)], capture_output=True, text=True, check=False
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_summary_renders_success_and_a_run_stopped_before_any_version(self):
        self.assertEqual(self.deploy(), 0)
        self.assertIn("直前 1 → 今回 2、エイリアス: switched", self.summary())
        self.reset()
        self.aws.versions["1"]["Description"] = "not-ours"
        self.assertEqual(self.deploy(), 1)
        text = self.summary()
        self.assertIn("直前 なし → 今回 なし、エイリアス: untouched", text)
        self.assertNotIn("null", text)


class Cli(DeployTestCase):
    """入力の検証と --verify-artifact-only。検証だけのモードは、AWS・subprocessを呼ばない。"""

    FULL = dict(
        ci_run_url="https://github.com/example/repo/actions/runs/1",
        run_id="1",
        run_attempt="1",
        function_name=FUNCTION,
        base_url=BASE_URL,
        distribution_id=DIST_ID,
        oac_id=OAC_ID,
        web_acl_arn=WAF_ARN,
        layer_code_sha256=LAYER_SHA,
        edge_function_arn=None,
        result_file=Path("result.json"),
    )

    def args(self, **overrides):
        values = dict(verify_artifact_only=False, source_sha=NEW_SHA, zip_sha256="0" * 64, **self.FULL)
        values.update(overrides)
        return SimpleNamespace(**values)

    def verify_only(self, **overrides):
        argv = ["--artifact-dir", str(self.artifact), "--source-sha", NEW_SHA, "--zip-sha256", self.zip_hex, "--verify-artifact-only"]
        for name, value in overrides.items():
            argv[argv.index(name) + 1] = value
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}), \
                mock.patch.object(dl, "run_aws", side_effect=AssertionError("AWS呼び出し")), \
                mock.patch.object(dl.subprocess, "run", side_effect=AssertionError("subprocess")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            return dl.main(argv), out.getvalue()

    def test_verify_only_accepts_a_valid_artifact_without_deploy_arguments(self):
        code, output = self.verify_only()
        self.assertEqual(code, 0)
        self.assertNotIn(self.tmp.name, output)

    def test_verify_only_rejects_tampering(self):
        def manifest(**changes):
            path = self.artifact / "manifest.json"
            data = json.loads(path.read_text())
            data.update(changes)
            path.write_text(json.dumps(data))

        self.assertEqual(self.verify_only(**{"--source-sha": OTHER_SHA})[0], 1)
        manifest(releaseEligible=False)
        self.assertEqual(self.verify_only()[0], 1)
        manifest(releaseEligible=True, dirty=True)
        self.assertEqual(self.verify_only()[0], 1)
        manifest(dirty=False)
        self.assertEqual(self.verify_only()[0], 0)
        (self.artifact / f"lambda-{NEW_SHA}.zip").write_bytes(b"tampered")
        self.assertEqual(self.verify_only()[0], 1)

    def test_deploy_mode_requires_every_argument(self):
        base = ["--artifact-dir", str(self.artifact), "--source-sha", NEW_SHA, "--zip-sha256", self.zip_hex]
        flags = {name: "--" + name.replace("_", "-") for name in self.FULL if name != "edge_function_arn"}
        values = {**self.FULL, "result_file": str(self.dir / "r.json")}
        for missing in flags:
            with self.subTest(missing):
                argv = base + [arg for name, flag in flags.items() if name != missing for arg in (flag, str(values[name]))]
                with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}), \
                        mock.patch.object(dl, "run_aws", side_effect=AssertionError("AWS呼び出し")):
                    with self.assertRaises(SystemExit) as raised:
                        dl.main(argv)
                self.assertIn(missing.replace("_", "-"), str(raised.exception))

    def test_valid_inputs_pass_and_bad_ones_are_rejected(self):
        regional = WAF_ARN.replace("global", "regional")
        bad = {
            "source_sha": ["abc", NEW_SHA.upper()],
            "zip_sha256": ["0" * 63],
            "function_name": ["", "bad name"],
            "layer_code_sha256": ["", "short"],
            # 公開URLは、CloudFrontのオリジンだけ（関数URL・パス・クエリ・ポート・ユーザー情報は不可）
            "base_url": [f"https://{LAMBDA_HOST}", BASE_URL + "/", BASE_URL + "/path", BASE_URL + "?x=1", BASE_URL + ":443", "https://user@" + CF_HOST, "http://" + CF_HOST, "https://short.cloudfront.net", CF_HOST + ".example.com", BASE_URL + "\n"],
            "distribution_id": ["e2abcdefghijkl", "E2/ABC"],
            "oac_id": [""],
            "web_acl_arn": [regional, WAF_ARN.replace("us-east-1", "ap-northeast-1"), WAF_ARN + "/extra"],
            "edge_function_arn": [EDGE_ARN + "/extra", EDGE_ARN + "\n", EDGE_ARN.replace("function/", "distribution/"), f"arn:aws:cloudfront::{ACCOUNT}:function/bad name", "arn:aws:cloudfront::123:function/x", 123],
            "result_file": [None],
        }
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}):
            dl.validate_inputs(self.args())
            dl.validate_inputs(self.args(edge_function_arn=EDGE_ARN))
            dl.validate_inputs(self.args(edge_function_arn=""))
            for name, values in bad.items():
                for value in values:
                    with self.subTest(name, value=value), self.assertRaises(SystemExit):
                        dl.validate_inputs(self.args(**{name: value}))
            for name in ("distribution_id", "oac_id", "web_acl_arn", "base_url"):  # 文字列でない値でもTypeErrorにしない
                with self.subTest(name), self.assertRaises(SystemExit):
                    dl.validate_inputs(self.args(**{name: 123}))
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "true"}), self.assertRaises(SystemExit):
            dl.validate_inputs(self.args())

    def test_edge_function_in_another_account_is_rejected_before_any_change(self):
        self.use_artifact("g", guard=True, viewer_ip=True)
        enable_write(self.aws)
        self.blocked("アカウント", edge_function_arn=EDGE_ARN.replace(ACCOUNT, OTHER_ACCOUNT))
        self.assertNotIn("get-function", self.aws.ops())

    def test_smoke_runs_with_allow_local_false_and_without_credentials(self):
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
