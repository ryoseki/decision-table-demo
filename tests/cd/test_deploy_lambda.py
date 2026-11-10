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
import re
import shutil
import subprocess
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
FUNCTION = "demo-fn"
ACCOUNT = "123456789012"  # 架空のアカウントID
OTHER_ACCOUNT = "210987654321"
BASE_URL = "https://d1234abcd5678.cloudfront.net"  # 公開URL（CloudFront）
CF_HOST = "d1234abcd5678.cloudfront.net"
LAMBDA_HOST = "abcdefgh12345678.lambda-url.ap-northeast-1.on.aws"
FUNCTION_ARN = f"arn:aws:lambda:ap-northeast-1:{ACCOUNT}:function:{FUNCTION}"
ALIAS_ARN = FUNCTION_ARN + ":demo"
DIST_ID = "E2ABCDEFGHIJKL"
DIST_ARN = f"arn:aws:cloudfront::{ACCOUNT}:distribution/{DIST_ID}"
OAC_ID = "E3ZYXWVUTSRQPO"
WAF_NAME = "demo-acl"
WAF_ID = "11111111-2222-3333-4444-555555555555"
WAF_ARN = f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/{WAF_NAME}/{WAF_ID}"
SECRET = "SUPER-SECRET-ENV-VALUE"
OLD_ZIP = hashlib.sha256(b"previous zip").hexdigest()
MUTATING = {
    "update-function-code",
    "publish-version",
    "update-alias",
    # 同時実行数は読み取りだけ。書き込みが呼ばれたら変更として数える（偽物も受け付けない）
    "put-function-concurrency",
    "delete-function-concurrency",
}


def code_sha(hex_digest: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_digest)).decode()


def make_distribution() -> dict:
    """CloudFront GetDistribution の応答の形（正常な基準）。"""
    return {
        "ETag": "E1",
        "Distribution": {
            "Id": DIST_ID,
            "ARN": DIST_ARN,
            "Status": "Deployed",
            "DomainName": CF_HOST,
            "DistributionConfig": {
                "Enabled": True,
                "DefaultRootObject": "",
                "WebACLId": WAF_ARN,
                "Origins": {
                    "Quantity": 1,
                    "Items": [
                        {
                            "Id": "lambda-origin",
                            "DomainName": LAMBDA_HOST,
                            "OriginPath": "",
                            "CustomHeaders": {"Quantity": 0},
                            "OriginAccessControlId": OAC_ID,
                            "CustomOriginConfig": {
                                "HTTPPort": 80,
                                "HTTPSPort": 443,
                                "OriginProtocolPolicy": "https-only",
                                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                            },
                        }
                    ],
                },
                "OriginGroups": {"Quantity": 0},
                "DefaultCacheBehavior": {
                    "TargetOriginId": "lambda-origin",
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "AllowedMethods": {
                        "Quantity": 3,
                        "Items": ["GET", "HEAD", "OPTIONS"],
                        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                    },
                    "CachePolicyId": dl.CACHING_DISABLED_POLICY_ID,
                    "OriginRequestPolicyId": dl.ALL_VIEWER_EXCEPT_HOST_POLICY_ID,
                    "LambdaFunctionAssociations": {"Quantity": 0},
                    "FunctionAssociations": {"Quantity": 0},
                    "Compress": True,
                },
                "CacheBehaviors": {"Quantity": 0},
                "CustomErrorResponses": {"Quantity": 0},
            },
        },
    }


def make_oac() -> dict:
    return {
        "ETag": "E2",
        "OriginAccessControl": {
            "Id": OAC_ID,
            "OriginAccessControlConfig": {
                "Name": "demo-oac",
                "SigningProtocol": "sigv4",
                "SigningBehavior": "always",
                "OriginAccessControlOriginType": "lambda",
            },
        },
    }


def visibility(name: str) -> dict:
    return {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": name}


def make_ip_rule(priority: int = 0) -> dict:
    """必須の規則: 実際の接続元IPごと 300件/300秒、超過はBlock（get-web-aclの形）。"""
    return {
        "Name": "per-ip",
        "Priority": priority,
        "Statement": {
            "RateBasedStatement": {
                "Limit": 300,
                "EvaluationWindowSec": 300,
                "AggregateKeyType": "IP",
            }
        },
        "Action": {"Block": {}},
        "VisibilityConfig": visibility("per-ip"),
    }


def make_global_rule(priority: int = 1) -> dict:
    """要件から外した全体（CONSTANT）の規則。受け入れてはいけない未対応の規則として使う。"""
    return {
        "Name": "global",
        "Priority": priority,
        "Statement": {
            "RateBasedStatement": {
                "Limit": 1500,
                "EvaluationWindowSec": 300,
                "AggregateKeyType": "CONSTANT",
                "ScopeDownStatement": {
                    "ByteMatchStatement": {
                        "SearchString": "Lw==",
                        "FieldToMatch": {"UriPath": {}},
                        "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                        "PositionalConstraint": "STARTS_WITH",
                    }
                },
            }
        },
        "Action": {"Block": {}},
        "VisibilityConfig": visibility("global"),
    }


def make_web_acl() -> dict:
    """基準のWeb ACL: 必須のIP規則1件だけ（観測グループは各テストで足す）。"""
    return {
        "LockToken": "token",
        "WebACL": {
            "Name": WAF_NAME,
            "Id": WAF_ID,
            "ARN": WAF_ARN,
            "DefaultAction": {"Allow": {}},
            "VisibilityConfig": visibility("demo-acl"),
            "Rules": [make_ip_rule()],
        },
    }


def make_policy_document() -> dict:
    """`aws lambda add-permission` が生成する条件の形（実CLIの出力に合わせた偽物）。

    --source-arn → ArnLike の AWS:SourceArn、--function-url-auth-type AWS_IAM → StringEquals の
    lambda:FunctionUrlAuthType、--invoked-via-function-url → Bool の lambda:InvokedViaFunctionUrl。
    実際のAWSの出力そのものの確認ではなく、公式CLIの説明に基づく想定（未確認）。
    """
    principal = {"Service": "cloudfront.amazonaws.com"}
    return {
        "Version": "2012-10-17",
        "Id": "default",
        "Statement": [
            {
                "Sid": "cf-url",
                "Effect": "Allow",
                "Principal": principal,
                "Action": "lambda:InvokeFunctionUrl",
                "Resource": ALIAS_ARN,
                "Condition": {
                    "StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"},
                    "ArnLike": {"AWS:SourceArn": DIST_ARN},
                },
            },
            {
                "Sid": "cf-invoke",
                "Effect": "Allow",
                "Principal": principal,
                "Action": "lambda:InvokeFunction",
                "Resource": ALIAS_ARN,
                "Condition": {
                    "ArnLike": {"AWS:SourceArn": DIST_ARN},
                    "Bool": {"lambda:InvokedViaFunctionUrl": "true"},
                },
            },
        ],
    }


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
            # 版に固定される設定（比較の対象）と、コード更新で変わるメタデータ
            "Role": f"arn:aws:iam::{ACCOUNT}:role/demo-fn-exec",
            "MemorySize": 512,
            "Timeout": 10,
            "Description": "",
            "PackageType": "Zip",
            "TracingConfig": {"Mode": "PassThrough"},
            "EphemeralStorage": {"Size": 512},
            "State": "Active",
            "LastUpdateStatus": "Successful",
            "LastModified": "2026-01-01T00:00:00.000+0000",
            "CodeSize": 100,
            "RevisionId": "f-1",
            "CodeSha256": code_sha(OLD_ZIP),
        }
        # 実際のCDで、update-function-code の応答のRevisionIdと完了後の値が不一致だった観測を再現する仮定
        # （AWSの通常の遷移か、同時期の手動変更かは未確定）。wait function-updated-v2 で別の値になる
        self.in_progress_revision = None
        self.final_revision = None
        self.dedupe_publish = False  # True: コード・設定が同じなら既存の版を返す（AWSの仕様どおり）
        self.versions = {"1": self._snapshot("1", dl.make_description(OLD_SHA, OLD_ZIP, "100"))}
        self.alias = {"FunctionVersion": "1", "RevisionId": "a-1", "RoutingConfig": {}}
        self.url = {
            "FunctionUrl": f"https://{LAMBDA_HOST}/",
            "FunctionArn": ALIAS_ARN,
            "AuthType": "AWS_IAM",
            "InvokeMode": "BUFFERED",
        }
        self.layer = {"Content": {"CodeSha256": LAYER_SHA}}
        self.dist = make_distribution()
        self.oac = make_oac()
        self.acl = make_web_acl()
        self.policy_doc = make_policy_document()
        # get-function-concurrency の応答（未設定なら実APIはキーを返さない想定）
        self.concurrency = {"ReservedConcurrentExecutions": 1}

    # テストから設定を書き換えやすくする入口
    @property
    def dist_config(self):
        return self.dist["Distribution"]["DistributionConfig"]

    @property
    def behavior(self):
        return self.dist_config["DefaultCacheBehavior"]

    @property
    def origin(self):
        return self.dist_config["Origins"]["Items"][0]

    @property
    def rules(self):
        return self.acl["WebACL"]["Rules"]

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
            if waiter == "function-updated-v2" and self.in_progress_revision is not None:
                # 完了: 別のRevisionId・更新日時・コードサイズ・状態の遷移（内容の設定は同じ）
                self.final_revision = self._rev("ffinal")
                self.function.update(
                    RevisionId=self.final_revision,
                    LastUpdateStatus="Successful",
                    LastModified="2026-01-02T00:00:00.000+0000",
                    CodeSize=200,
                )
                self.in_progress_revision = None
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
        if op == "get-policy":
            return {"Policy": json.dumps(self.policy_doc), "RevisionId": "p-1"}
        # 実APIの形の引数だけを受け付ける（CloudFront・WAFは読み取りのみ）
        if op == "get-distribution":
            assert args[0] == "cloudfront" and set(opts) == {"--id"}
            if opts["--id"] != DIST_ID:
                raise dl.AwsError(op, 254)  # 存在しないID
            return json.loads(json.dumps(self.dist))
        if op == "get-origin-access-control":
            assert args[0] == "cloudfront" and set(opts) == {"--id"}
            if opts["--id"] != OAC_ID:
                raise dl.AwsError(op, 254)
            return json.loads(json.dumps(self.oac))
        if op == "get-web-acl":
            assert args[0] == "wafv2"
            if opts != {"--name": WAF_NAME, "--scope": "CLOUDFRONT", "--id": WAF_ID}:
                raise dl.AwsError(op, 254)  # 名前・scope・IDが違えば実際のAWSでも取得に失敗する想定
            return json.loads(json.dumps(self.acl))
        if op == "get-layer-version-by-arn":
            return dict(self.layer)
        if op == "get-function-concurrency":
            # 修飾子なしの対象関数だけ（版・エイリアスの同時実行数は読まない）
            assert args[0] == "lambda" and opts == {"--function-name": FUNCTION}
            return json.loads(json.dumps(self.concurrency))
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
            self.in_progress_revision = self._rev("fprogress")
            self.function.update(RevisionId=self.in_progress_revision, LastUpdateStatus="InProgress")
            return {"CodeSha256": self.function["CodeSha256"], "RevisionId": self.in_progress_revision}
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
            distribution_id=DIST_ID,
            oac_id=OAC_ID,
            web_acl_arn=WAF_ARN,
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
        self.assertNotIn(ACCOUNT, text)  # アカウントを含むARNの全文・IDは結果に残さない


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
        # スモーク確認は公開URL（CloudFront）だけで行う。関数URLへのフォールバックはない
        self.assertEqual({url for url, _, _ in self.smoke.calls}, {BASE_URL})
        self.assertEqual(result["publicPath"], "passed")
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

    def test_function_url_not_matching_the_cloudfront_origin_blocks(self):
        # 実際の関数URLが、CloudFrontのオリジンのホストと違う
        self.aws.url["FunctionUrl"] = "https://zzzzzzzz12345678.lambda-url.ap-northeast-1.on.aws/"
        self.assert_untouched(self.deploy())

    def test_function_url_auth_must_be_aws_iam(self):
        self.aws.url["AuthType"] = "NONE"  # 直接公開（旧方式）は許さない
        self.assert_untouched(self.deploy())
        self.assertNotIn("get-distribution", self.aws.ops())

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
            response = original(args)
            if args[1] == "wait" and args[2] == "function-updated-v2":
                # 更新の完了の直後（完了後の読み直しの前）に、別の者が変更した
                self.aws.function.update(changes)
            return response

        return wrapped

    def test_runtime_change_between_update_and_publish_stops_before_publish(self):
        wrapped = self.tamper_after_update(Runtime="python3.13", RevisionId="f-ext")
        self.assertEqual(self.deploy(aws=wrapped), 1)
        self.assertNotIn("publish-version", self.aws.ops())
        self.assertNotIn("update-alias", self.aws.ops())
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")

    def test_revision_only_change_before_the_completed_read_is_indistinguishable(self):
        """完了後に読んだRevisionIdが何であれ、設定・コードが検証時と同一なら、それを条件に発行する。

        読む前に別の操作があっても、版に固定される設定・コードが同一（元に戻された変更を含む）なら
        区別できず、他者の操作が無かったとは証明できない。異なる設定・コードは比較して発行しない
        （EveryVersionableSettingIsCompared などのテスト）。
        """
        wrapped = self.tamper_after_update(RevisionId="f-ext")
        self.assertEqual(self.deploy(aws=wrapped), 0)
        publish = next(call for call in self.aws.calls if call[1] == "publish-version")
        self.assertEqual(publish[publish.index("--revision-id") + 1], "f-ext")

    def test_change_after_the_completed_read_is_rejected_by_publish(self):
        # 完了後の読み直しの後・発行の前に関数が変わると、発行のRevisionIdの条件が拒否する
        self.aws.before["publish-version"] = lambda: self.aws.function.update(RevisionId="f-late")
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version"])
        self.assertEqual(self.aws.versions.keys(), {"1"})
        self.assertEqual(self.aws.alias["FunctionVersion"], "1")
        self.assertEqual(self.result()["alias"]["state"], "untouched")


class InProgressAndCompletedRevisions(DeployTestCase):
    """更新の応答のRevisionIdと完了後の値が不一致でも、完了後に読み直した値で発行する。

    実際のCDで不一致を観測した（原因が通常の遷移か同時期の手動変更かは未確定）。ここは偽のAWSでの
    再現で、AWSの通常の遷移の証明ではない。
    """

    def test_publishes_with_the_completed_revision_not_the_update_response(self):
        self.assertEqual(self.deploy(), 0)
        self.assertIsNotNone(self.aws.final_revision)
        update = next(call for call in self.aws.calls if call[1] == "update-function-code")
        publish = next(call for call in self.aws.calls if call[1] == "publish-version")
        # 更新は検証時に捕捉したRevisionId、発行は完了後に読み直したRevisionId（更新の応答の値ではない）
        self.assertEqual(update[update.index("--revision-id") + 1], "f-1")
        revision = publish[publish.index("--revision-id") + 1]
        self.assertEqual(revision, self.aws.final_revision)
        self.assertTrue(self.aws.final_revision.startswith("ffinal-"))
        self.assertEqual(publish[publish.index("--code-sha256") + 1], code_sha(self.zip_hex))
        result = self.result()
        self.assertEqual((result["overall"], result["deployment"]), ("success", "completed"))
        self.assertEqual(self.aws.alias["FunctionVersion"], "2")
        self.assert_no_leak()

    def test_metadata_may_change_but_content_must_not(self):
        self.assertEqual(self.deploy(), 0)
        # 揮発性のメタデータ（更新日時・コードサイズ）は完了で変わった
        self.assertEqual(self.aws.function["CodeSize"], 200)
        self.assertEqual(self.aws.function["LastModified"], "2026-01-02T00:00:00.000+0000")

    def changed_case(self, name, change, expect):
        self.setUp()
        original = self.aws.__call__

        def wrapped(args):
            response = original(args)
            if args[1] == "wait" and args[2] == "function-updated-v2":
                change(self.aws.function)
            return response

        self.assertEqual(self.deploy(aws=wrapped), 1, name)
        self.assertNotIn("publish-version", self.aws.ops(), name)
        self.assertNotIn("update-alias", self.aws.ops(), name)
        self.assertEqual(self.aws.mutating(), ["update-function-code"], name)
        self.assertEqual(self.aws.alias["FunctionVersion"], "1", name)
        result = self.result()
        self.assertEqual(result["alias"]["state"], "untouched", name)
        self.assertEqual(result["rollback"], "not-needed", name)
        self.assertTrue(result["mutationStarted"], name)
        self.assertIn(expect, result["error"], name)
        self.assertNotIn("SECRET-2", self.result_file.read_text(encoding="utf-8"), name)
        self.assertNotIn(SECRET, self.result_file.read_text(encoding="utf-8"), name)
        self.assert_no_leak()

    def test_changed_code_hash_fails_before_publish(self):
        self.changed_case(
            "code",
            lambda f: f.update(CodeSha256=code_sha(hashlib.sha256(b"other").hexdigest())),
            "codeSha256",
        )

    def test_empty_or_missing_completed_revision_fails_before_publish(self):
        self.changed_case("empty", lambda f: f.update(RevisionId=""), "revisionId")
        self.changed_case("missing", lambda f: f.pop("RevisionId"), "revisionId")

    def test_failed_update_status_fails_before_publish(self):
        self.changed_case("status", lambda f: f.update(LastUpdateStatus="Failed"), "lastUpdateStatus")

    def test_final_check_requires_explicit_active_and_successful(self):
        """完了後の最終確認は、State・LastUpdateStatus の欠落・null・別の値を許さない（発行しない）。"""
        cases = {
            "state-missing": (lambda f: f.pop("State"), "state"),
            "state-null": (lambda f: f.update(State=None), "state"),
            "state-pending": (lambda f: f.update(State="Pending"), "state"),
            "state-failed": (lambda f: f.update(State="Failed"), "state"),
            "status-missing": (lambda f: f.pop("LastUpdateStatus"), "lastUpdateStatus"),
            "status-null": (lambda f: f.update(LastUpdateStatus=None), "lastUpdateStatus"),
            "status-in-progress": (lambda f: f.update(LastUpdateStatus="InProgress"), "lastUpdateStatus"),
        }
        for name, (change, expect) in cases.items():
            with self.subTest(name):
                self.changed_case(name, change, expect)

    def test_status_keys_are_tolerated_only_before_the_update(self):
        """検証時（更新の前）は従来どおり、キーがなければ許す。最終確認だけが厳密。"""
        config = dict(FakeAws().function)
        del config["State"], config["LastUpdateStatus"]
        self.assertEqual(dl.config_problems(config), [])
        self.assertEqual(sorted(dl.config_problems(config, require_status=True)), ["lastUpdateStatus", "state"])
        self.assertEqual(dl.config_problems(FakeAws().function, require_status=True), [])

    def test_every_versionable_setting_is_compared(self):
        """検査対象外の設定（追加の環境変数・ロール・メモリなど）の変更も、発行の前に止める。"""

        def env(f):
            f["Environment"]["Variables"]["EXTRA"] = "x"

        def secret_value(f):
            f["Environment"]["Variables"]["API_TOKEN"] = "SECRET-2"

        def drop_env(f):
            del f["Environment"]["Variables"]["API_TOKEN"]

        cases = {
            "extra-env": (env, "setting:Environment"),
            "secret-env-value": (secret_value, "setting:Environment"),
            "removed-env": (drop_env, "setting:Environment"),
            "role": (lambda f: f.update(Role="arn:aws:iam::1:role/other"), "setting:Role"),
            "memory": (lambda f: f.update(MemorySize=1024), "setting:MemorySize"),
            "timeout": (lambda f: f.update(Timeout=900), "setting:Timeout"),
            "description": (lambda f: f.update(Description="changed"), "setting:Description"),
            "vpc": (lambda f: f.update(VpcConfig={"SubnetIds": ["s-1"]}), "setting:VpcConfig"),
            "kms": (lambda f: f.update(KMSKeyArn="arn:aws:kms:ap-northeast-1:1:key/k"), "setting:KMSKeyArn"),
            "tracing": (lambda f: f.update(TracingConfig={"Mode": "Active"}), "setting:TracingConfig"),
            "storage": (lambda f: f.update(EphemeralStorage={"Size": 1024}), "setting:EphemeralStorage"),
            "filesystem": (
                lambda f: f.update(FileSystemConfigs=[{"Arn": "arn:aws:elasticfilesystem:x", "LocalMountPath": "/mnt/x"}]),
                "setting:FileSystemConfigs",
            ),
            "layer-order": (
                lambda f: f.update(Layers=[{"Arn": dl.PINNED_LAYER_ARN}, {"Arn": "arn:aws:lambda:ap-northeast-1:1:layer:X:1"}]),
                "layers",
            ),
            "package-type": (lambda f: f.update(PackageType="Image"), "setting:PackageType"),
            "unknown-new-setting": (lambda f: f.update(FutureSetting={"On": True}), "setting:FutureSetting"),
        }
        for name, (change, expect) in cases.items():
            with self.subTest(name):
                self.changed_case(name, change, expect)

    def test_runtime_architecture_handler_changes_are_still_named(self):
        self.changed_case("runtime", lambda f: f.update(Runtime="python3.13"), "runtime")
        self.changed_case("arch", lambda f: f.update(Architectures=["arm64"]), "architecture")
        self.changed_case("handler", lambda f: f.update(Handler="other.sh"), "handler")

    def test_only_setting_names_are_reported(self):
        self.changed_case(
            "secret", lambda f: f["Environment"]["Variables"].update(API_TOKEN="SECRET-2"), "setting:Environment"
        )
        self.assertNotIn("EXTRA", self.result()["error"])

    def test_fingerprint_helpers_hold_no_raw_values(self):
        key = b"k" * 32
        config = dict(FakeAws().function)
        digests = dl.settings_fingerprint(config, key)
        self.assertNotIn("RevisionId", digests)
        self.assertNotIn("CodeSha256", digests)
        self.assertNotIn(SECRET, json.dumps(digests))
        # 鍵が違えば指紋も違う（値の推測の手掛かりにしない）
        self.assertNotEqual(digests, dl.settings_fingerprint(config, b"j" * 32))
        # 揮発性のメタデータだけの違いは同じ指紋
        other = dict(config, RevisionId="x", LastModified="y", CodeSize=1, LastUpdateStatus="InProgress")
        self.assertEqual(dl.setting_differences(digests, dl.settings_fingerprint(other, key)), [])
        self.assertEqual(
            dl.setting_differences(digests, dl.settings_fingerprint(dict(config, MemorySize=1), key)),
            ["setting:MemorySize"],
        )

    def test_every_excluded_metadata_key_is_ignored_and_nothing_else(self):
        key = b"k" * 32
        config = dict(FakeAws().function, SnapStart={"ApplyOn": "None", "OptimizationStatus": "Off"})
        base = dl.settings_fingerprint(config, key)
        for name in sorted(dl.VOLATILE_CONFIG_KEYS):
            with self.subTest(name):
                changed = dict(config, **{name: "changed-value"})
                self.assertEqual(dl.setting_differences(base, dl.settings_fingerprint(changed, key)), [])
        snap = dict(config, SnapStart={"ApplyOn": "None", "OptimizationStatus": "On"})
        self.assertEqual(dl.setting_differences(base, dl.settings_fingerprint(snap, key)), [])
        snap_apply = dict(config, SnapStart={"ApplyOn": "PublishedVersions", "OptimizationStatus": "Off"})
        self.assertEqual(dl.setting_differences(base, dl.settings_fingerprint(snap_apply, key)), ["setting:SnapStart"])
        # 値がnullの項目は、キーがないのと同じ。除外一覧にない未知の項目は比較する
        self.assertEqual(dl.setting_differences(base, dl.settings_fingerprint(dict(config, Extra=None), key)), [])
        self.assertEqual(
            dl.setting_differences(base, dl.settings_fingerprint(dict(config, RuntimeVersionConfig={"A": 1}), key)),
            ["setting:RuntimeVersionConfig"],
        )

    def test_unusual_setting_names_are_reported_without_the_name(self):
        self.assertEqual(dl.setting_differences({"Bad Name!": "a"}, {}), ["setting:unknown"])
        self.assertEqual(dl.setting_differences({}, {"x" * 65: "a"}), ["setting:unknown"])


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


def rate(aws, index=0):
    return aws.rules[index]["Statement"]["RateBasedStatement"]


def oac_config(aws):
    return aws.oac["OriginAccessControl"]["OriginAccessControlConfig"]


def set_conditions(aws, condition):
    for statement in aws.policy_doc["Statement"]:
        statement["Condition"] = json.loads(json.dumps(condition))


def statement_condition(aws, index):
    return aws.policy_doc["Statement"][index]["Condition"]


def duplicate_ip_rule(aws):
    aws.rules.append({**json.loads(json.dumps(aws.rules[0])), "Name": "per-ip-2", "Priority": 1})


def unknown_count_rule(priority: int = 1) -> dict:
    """未知の独自規則（Countで観測だけ）。既知のAWSマネージド規則グループではないので拒否する。"""
    return {
        "Name": "custom-count",
        "Priority": priority,
        "Statement": {
            "ByteMatchStatement": {
                "SearchString": "/admin",
                "FieldToMatch": {"UriPath": {}},
                "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                "PositionalConstraint": "STARTS_WITH",
            }
        },
        "Action": {"Count": {}},
        "VisibilityConfig": visibility("custom-count"),
    }


OBSERVER_GROUPS = (
    "AWSManagedRulesAmazonIpReputationList",
    "AWSManagedRulesCommonRuleSet",
    "AWSManagedRulesKnownBadInputsRuleSet",
)


def observer(group: str, priority: int, vendor: str = "AWS") -> dict:
    """コンソールの自動作成で付き得る、Countで観測だけするAWSのマネージド規則グループ（get-web-aclの形）。"""
    return {
        "Name": f"{vendor}-{group}",
        "Priority": priority,
        "Statement": {"ManagedRuleGroupStatement": {"VendorName": vendor, "Name": group}},
        "OverrideAction": {"Count": {}},
        "VisibilityConfig": visibility(f"{vendor}-{group}"),
    }


def add_observers(aws, groups=OBSERVER_GROUPS, first=True):
    """観測グループを足す。first=True なら必須のIP規則より前の優先順位（Countは終端しないので可）。"""
    if first:
        for rule in aws.rules:
            rule["Priority"] += len(groups)
        aws.rules[:0] = [observer(g, i) for i, g in enumerate(groups)]
    else:
        start = len(aws.rules)
        aws.rules.extend(observer(g, start + i) for i, g in enumerate(groups))


def with_observers(change):
    """3つの観測グループを足したうえで、そのうち1つ（先頭）を change で変える。"""

    def mutate(aws):
        add_observers(aws)
        change(aws.rules[0])

    return mutate


def observer_group(rule):
    return rule["Statement"]["ManagedRuleGroupStatement"]


def second_origin(aws):
    aws.dist_config["Origins"].update(
        Quantity=2, Items=[aws.origin, {**aws.origin, "Id": "second"}]
    )


OTHER_DIST_ARN = f"arn:aws:cloudfront::{ACCOUNT}:distribution/E9ZZZZZZZZZZZZ"
OTHER_LAMBDA_HOST = "zzzzzzzz12345678.lambda-url.ap-northeast-1.on.aws"
EDGE_ITEM = {"Quantity": 1, "Items": [{"EventType": "viewer-request"}]}


def other_allow(principal):
    return {
        "Sid": "other",
        "Effect": "Allow",
        "Principal": principal,
        "Action": "lambda:InvokeFunctionUrl",
        "Resource": ALIAS_ARN,
        "Condition": {"ArnLike": {"AWS:SourceArn": DIST_ARN}},
    }


# (名前, 基準の偽AWSを壊す関数, エラーメッセージに含まれるべき文字列)
BOUNDARY_CASES = [
    # 関数URL（実値からオリジンを導く）
    ("url-auth-none", lambda a: a.url.update(AuthType="NONE"), "AWS_IAM"),
    ("url-streaming", lambda a: a.url.update(InvokeMode="RESPONSE_STREAM"), "BUFFERED"),
    ("url-not-lambda-domain", lambda a: a.url.update(FunctionUrl="https://example.com/"), "AWS_IAM"),
    ("url-no-function-arn", lambda a: a.url.pop("FunctionArn"), "AWS_IAM"),
    (
        "url-other-function-arn",
        lambda a: a.url.update(FunctionArn=f"arn:aws:lambda:ap-northeast-1:{ACCOUNT}:function:another:demo"),
        "AWS_IAM",
    ),
    (
        "url-other-account",
        lambda a: a.url.update(FunctionArn=f"arn:aws:lambda:ap-northeast-1:{OTHER_ACCOUNT}:function:{FUNCTION}:demo"),
        "アカウント",
    ),
    ("url-host-differs-from-origin", lambda a: a.url.update(FunctionUrl=f"https://{OTHER_LAMBDA_HOST}/"), "originDomain"),
    # CloudFront: 識別・状態
    ("wrong-domain", lambda a: a.dist["Distribution"].update(DomainName="dzzzzzzzzzzzz.cloudfront.net"), "domainName"),
    ("distribution-id", lambda a: a.dist["Distribution"].update(Id="E9ZZZZZZZZZZZZ"), "distributionIdentity"),
    (
        "distribution-other-account",
        lambda a: a.dist["Distribution"].update(ARN=f"arn:aws:cloudfront::{OTHER_ACCOUNT}:distribution/{DIST_ID}"),
        "distributionIdentity",
    ),
    ("undeployed", lambda a: a.dist["Distribution"].update(Status="InProgress"), "distributionStatus"),
    ("disabled", lambda a: a.dist_config.update(Enabled=False), "distributionEnabled"),
    ("waf-association-other", lambda a: a.dist_config.update(WebACLId=WAF_ARN + "x"), "webAclAssociation"),
    ("waf-association-missing", lambda a: a.dist_config.pop("WebACLId"), "webAclAssociation"),
    # CloudFront: オリジン
    ("origin-host", lambda a: a.origin.update(DomainName=OTHER_LAMBDA_HOST), "originDomain"),
    ("origin-path", lambda a: a.origin.update(OriginPath="/app"), "originPath"),
    ("origin-http", lambda a: a.origin["CustomOriginConfig"].update(OriginProtocolPolicy="http-only"), "originProtocol"),
    ("origin-match-viewer", lambda a: a.origin["CustomOriginConfig"].update(OriginProtocolPolicy="match-viewer"), "originProtocol"),
    ("origin-oac-other", lambda a: a.origin.update(OriginAccessControlId="E9ZZZZZZZZZZZZ"), "originAccessControl"),
    ("origin-oac-empty", lambda a: a.origin.update(OriginAccessControlId=""), "originAccessControl"),
    ("two-origins", second_origin, "origins"),
    ("origin-count-mismatch", lambda a: a.dist_config["Origins"].update(Quantity=2), "origins"),
    ("target-origin", lambda a: a.behavior.update(TargetOriginId="other"), "targetOrigin"),
    # OAC
    ("oac-never", lambda a: oac_config(a).update(SigningBehavior="never"), "oacSigningBehavior"),
    ("oac-no-override", lambda a: oac_config(a).update(SigningBehavior="no-override"), "oacSigningBehavior"),
    ("oac-protocol", lambda a: oac_config(a).update(SigningProtocol="other"), "oacSigningProtocol"),
    ("oac-s3", lambda a: oac_config(a).update(OriginAccessControlOriginType="s3"), "oacOriginType"),
    ("oac-id", lambda a: a.oac["OriginAccessControl"].update(Id="E9ZZZZZZZZZZZZ"), "oacIdentity"),
    # キャッシュ・ビヘイビア（初期版はキャッシュ無効のManagedポリシーだけ）
    ("cache-policy-optimized", lambda a: a.behavior.update(CachePolicyId="658327ea-f89d-4fab-a63d-7e88639e58f6"), "cachePolicy"),
    ("cache-policy-missing", lambda a: a.behavior.pop("CachePolicyId"), "cachePolicy"),
    ("origin-request-policy", lambda a: a.behavior.update(OriginRequestPolicyId="216adef6-5c7f-47e4-b989-5492eafa07d3"), "originRequestPolicy"),
    (
        "extra-cache-behavior",
        lambda a: a.dist_config.update(CacheBehaviors={"Quantity": 1, "Items": [{"PathPattern": "/assets/*"}]}),
        "cacheBehaviors",
    ),
    ("viewer-allow-all", lambda a: a.behavior.update(ViewerProtocolPolicy="allow-all"), "viewerProtocol"),
    (
        "methods-with-post",
        lambda a: a.behavior["AllowedMethods"].update(Quantity=4, Items=["GET", "HEAD", "OPTIONS", "POST"]),
        "allowedMethods",
    ),
    ("methods-get-head-only", lambda a: a.behavior["AllowedMethods"].update(Quantity=2, Items=["GET", "HEAD"]), "allowedMethods"),
    (
        "cached-methods-post",
        lambda a: a.behavior["AllowedMethods"].update(CachedMethods={"Quantity": 1, "Items": ["POST"]}),
        "cachedMethods",
    ),
    ("lambda-edge", lambda a: a.behavior.update(LambdaFunctionAssociations=EDGE_ITEM), "lambdaEdge"),
    ("edge-function", lambda a: a.behavior.update(FunctionAssociations=EDGE_ITEM), "edgeFunctions"),
    (
        "error-response-rewrite",
        lambda a: a.dist_config.update(
            CustomErrorResponses={
                "Quantity": 1,
                "Items": [{"ErrorCode": 404, "ResponsePagePath": "/index.html", "ResponseCode": "200"}],
            }
        ),
        "errorResponses",
    ),
    ("origin-group", lambda a: a.dist_config.update(OriginGroups={"Quantity": 1, "Items": [{"Id": "g"}]}), "originGroups"),
    ("default-root-object", lambda a: a.dist_config.update(DefaultRootObject="index.html"), "defaultRootObject"),
    # WAF: 必須のIP規則（接続元IP・300件/300秒・Block）はちょうど1件。値・形が少しでも違えば拒否
    ("waf-weak-ip-limit", lambda a: rate(a).update(Limit=3000), "webAclRules"),
    ("waf-ip-limit-301", lambda a: rate(a).update(Limit=301), "webAclRules"),
    ("waf-ip-limit-299", lambda a: rate(a).update(Limit=299), "webAclRules"),
    ("waf-ip-limit-string", lambda a: rate(a).update(Limit="300"), "webAclRules"),
    ("waf-ip-limit-float", lambda a: rate(a).update(Limit=300.0), "webAclRules"),
    ("waf-ip-limit-missing", lambda a: rate(a).pop("Limit"), "webAclRules"),
    ("waf-window", lambda a: rate(a).update(EvaluationWindowSec=600), "webAclRules"),
    ("waf-window-60", lambda a: rate(a).update(EvaluationWindowSec=60), "webAclRules"),
    ("waf-window-missing", lambda a: rate(a).pop("EvaluationWindowSec"), "webAclRules"),
    ("waf-ip-key-constant", lambda a: rate(a).update(AggregateKeyType="CONSTANT"), "webAclRules"),
    ("waf-ip-key-custom", lambda a: rate(a).update(AggregateKeyType="CUSTOM_KEYS", CustomKeys=[{"IP": {}}]), "webAclRules"),
    ("waf-count-action", lambda a: a.rules[0].update(Action={"Count": {}}), "webAclRules"),
    ("waf-allow-action", lambda a: a.rules[0].update(Action={"Allow": {}}), "webAclRules"),
    ("waf-challenge-action", lambda a: a.rules[0].update(Action={"Challenge": {}}), "webAclRules"),
    ("waf-captcha-action", lambda a: a.rules[0].update(Action={"Captcha": {}}), "webAclRules"),
    ("waf-action-missing", lambda a: a.rules[0].pop("Action"), "webAclRules"),
    ("waf-custom-response", lambda a: a.rules[0].update(Action={"Block": {"CustomResponse": {"ResponseCode": 429}}}), "webAclRules"),
    ("waf-override-action", lambda a: a.rules[0].update(OverrideAction={"Count": {}}), "webAclRules"),
    ("waf-ip-rule-labels", lambda a: a.rules[0].update(RuleLabels=[{"Name": "demo:label"}]), "webAclRules"),
    ("waf-ip-rule-unknown-key", lambda a: a.rules[0].update(CaptchaConfig={}), "webAclRules"),
    ("waf-ip-scope-down", lambda a: rate(a).update(ScopeDownStatement={"NotStatement": {}}), "webAclRules"),
    (
        "waf-forwarded-ip",
        lambda a: rate(a).update(AggregateKeyType="FORWARDED_IP", ForwardedIPConfig={"HeaderName": "X-Forwarded-For"}),
        "webAclRules",
    ),
    ("waf-ip-forwarded-config-with-ip-key", lambda a: rate(a).update(ForwardedIPConfig={"HeaderName": "X-Forwarded-For"}), "webAclRules"),
    ("waf-ip-statement-wrapped", lambda a: a.rules[0]["Statement"].update(NotStatement={}), "webAclRules"),
    ("waf-no-rules", lambda a: a.rules.clear(), "webAclRules"),
    ("waf-rules-not-a-list", lambda a: a.acl["WebACL"].update(Rules={"per-ip": a.rules[0]}), "webAclRules"),
    ("waf-duplicate-ip-rule", duplicate_ip_rule, "webAclRules"),
    # 要件から外した全体（CONSTANT）の規則は、IP規則があっても・件数が上限内でも受け入れない
    ("waf-unsupported-global-rule-added", lambda a: a.rules.append(make_global_rule()), "webAclRules"),
    ("waf-unsupported-global-rule-instead-of-ip", lambda a: a.rules.__setitem__(0, make_global_rule(0)), "webAclRules"),
    (
        "waf-unsupported-global-rule-with-two-observers",
        lambda a: (add_observers(a, OBSERVER_GROUPS[1:], first=False), a.rules.append(make_global_rule(3))),
        "webAclRules",
    ),
    (
        "waf-unsupported-global-rule-with-three-observers",
        lambda a: (add_observers(a, first=False), a.rules.append(make_global_rule(4))),
        "webAclRules",
    ),
    ("waf-unknown-count-rule", lambda a: a.rules.append(unknown_count_rule()), "webAclRules"),
    # 観測専用のマネージド規則グループ（Count）。既知の3つ以外・Count以外・絞り込みや上書きは拒否
    ("waf-observer-unknown-group", with_observers(lambda r: observer_group(r).update(Name="AWSManagedRulesBotControlRuleSet")), "webAclRules"),
    ("waf-observer-unknown-group-alone", lambda a: a.rules.append(observer("AWSManagedRulesAdminProtectionRuleSet", 2)), "webAclRules"),
    ("waf-observer-other-vendor", lambda a: a.rules.append(observer("AWSManagedRulesCommonRuleSet", 2, vendor="ExampleVendor")), "webAclRules"),
    ("waf-observer-vendor-lowercase", with_observers(lambda r: observer_group(r).update(VendorName="aws")), "webAclRules"),
    # 名前が文字列でない（unhashable）でもTypeErrorにせず、問題名 webAclRules を出して閉じる
    ("waf-observer-name-list", with_observers(lambda r: observer_group(r).update(Name=["AWSManagedRulesCommonRuleSet"])), "webAclRules"),
    ("waf-observer-name-dict", with_observers(lambda r: observer_group(r).update(Name={"x": 1})), "webAclRules"),
    ("waf-observer-name-null", with_observers(lambda r: observer_group(r).update(Name=None)), "webAclRules"),
    ("waf-observer-name-number", with_observers(lambda r: observer_group(r).update(Name=1)), "webAclRules"),
    ("waf-observer-vendor-list", with_observers(lambda r: observer_group(r).update(VendorName=["AWS"])), "webAclRules"),
    ("waf-observer-duplicate", lambda a: add_observers(a, (OBSERVER_GROUPS[1], OBSERVER_GROUPS[1])), "webAclRules"),
    ("waf-more-than-four-rules", lambda a: add_observers(a, (*OBSERVER_GROUPS, OBSERVER_GROUPS[0])), "webAclRules"),
    ("waf-observer-override-none", with_observers(lambda r: r.update(OverrideAction={"None": {}})), "webAclRules"),
    ("waf-observer-override-missing", with_observers(lambda r: r.pop("OverrideAction")), "webAclRules"),
    (
        "waf-observer-count-with-custom-request",
        with_observers(lambda r: r.update(OverrideAction={"Count": {"CustomRequestHandling": {"InsertHeaders": [{"Name": "x", "Value": "y"}]}}})),
        "webAclRules",
    ),
    ("waf-observer-action-and-override", with_observers(lambda r: r.update(Action={"Block": {}})), "webAclRules"),
    ("waf-observer-action-instead-of-override", with_observers(lambda r: (r.pop("OverrideAction"), r.update(Action={"Count": {}}))), "webAclRules"),
    ("waf-observer-scope-down", with_observers(lambda r: observer_group(r).update(ScopeDownStatement={"NotStatement": {}})), "webAclRules"),
    (
        "waf-observer-rule-action-override",
        with_observers(lambda r: observer_group(r).update(RuleActionOverrides=[{"Name": "NoUserAgent_HEADER", "ActionToUse": {"Allow": {}}}])),
        "webAclRules",
    ),
    ("waf-observer-excluded-rules", with_observers(lambda r: observer_group(r).update(ExcludedRules=[{"Name": "SizeRestrictions_BODY"}])), "webAclRules"),
    ("waf-observer-optional-list-null", with_observers(lambda r: observer_group(r).update(ExcludedRules=None)), "webAclRules"),
    ("waf-observer-version", with_observers(lambda r: observer_group(r).update(Version="Version_1.0")), "webAclRules"),
    ("waf-observer-rule-labels", with_observers(lambda r: r.update(RuleLabels=[{"Name": "demo:label"}])), "webAclRules"),
    ("waf-observer-other-statement", with_observers(lambda r: r.update(Statement={"RuleGroupReferenceStatement": {"ARN": "arn:example"}})), "webAclRules"),
    # 観測グループがあっても、必須のIP規則が欠けた・変わったら拒否（観測グループで代わりにはならない）
    ("waf-observers-without-ip-rate", lambda a: (add_observers(a, first=False), a.rules.pop(0)), "webAclRules"),
    ("waf-observers-only", lambda a: (add_observers(a), a.rules.__delitem__(slice(3, None))), "webAclRules"),
    ("waf-observers-with-weak-ip-limit", lambda a: (add_observers(a, first=False), rate(a).update(Limit=3000)), "webAclRules"),
    ("waf-observers-with-ip-window", lambda a: (add_observers(a, first=False), rate(a).update(EvaluationWindowSec=600)), "webAclRules"),
    ("waf-observers-with-count-ip", lambda a: (add_observers(a, first=False), a.rules[0].update(Action={"Count": {}})), "webAclRules"),
    ("waf-observers-with-forwarded-ip", lambda a: (add_observers(a, first=False), rate(a).update(AggregateKeyType="FORWARDED_IP")), "webAclRules"),
    ("waf-observers-with-duplicate-ip", lambda a: (add_observers(a, OBSERVER_GROUPS[:2], first=False), duplicate_ip_rule(a)), "webAclRules"),
    (
        "waf-rate-rule-as-count-override",
        lambda a: (add_observers(a, first=False), a.rules[0].pop("Action"), a.rules[0].update(OverrideAction={"Count": {}})),
        "webAclRules",
    ),
    # Firewall Managerの前処理・後処理の規則グループ、管理下のACLは、必須のIP規則より前に迂回し得るため拒否
    ("waf-firewall-manager-managed", lambda a: a.acl["WebACL"].update(ManagedByFirewallManager=True), "webAclFirewallManager"),
    (
        "waf-firewall-manager-pre-process",
        lambda a: a.acl["WebACL"].update(PreProcessFirewallManagerRuleGroups=[{"Name": "fm-group", "FirewallManagerStatement": {}}]),
        "webAclFirewallManager",
    ),
    (
        "waf-firewall-manager-post-process",
        lambda a: a.acl["WebACL"].update(PostProcessFirewallManagerRuleGroups=[{"Name": "fm-group", "FirewallManagerStatement": {}}]),
        "webAclFirewallManager",
    ),
    ("waf-firewall-manager-groups-not-a-list", lambda a: a.acl["WebACL"].update(PreProcessFirewallManagerRuleGroups="unexpected"), "webAclFirewallManager"),
    # CloudFrontの継続的デプロイ（ステージング）の経路は初期構成では使わない
    ("cf-continuous-deployment-policy", lambda a: a.dist_config.update(ContinuousDeploymentPolicyId="policy-id"), "continuousDeployment"),
    ("cf-staging-distribution", lambda a: a.dist_config.update(Staging=True), "stagingDistribution"),
    ("waf-default-block", lambda a: a.acl["WebACL"].update(DefaultAction={"Block": {}}), "webAclDefaultAction"),
    ("waf-response-other-arn", lambda a: a.acl["WebACL"].update(ARN=WAF_ARN + "x"), "webAclIdentity"),
    # 関数のリソースポリシー（エイリアス demo）
    ("policy-public-principal", lambda a: a.policy_doc["Statement"][0].update(Principal="*"), "policyPrincipal"),
    ("policy-aws-wildcard", lambda a: a.policy_doc["Statement"][0].update(Principal={"AWS": "*"}), "policyPrincipal"),
    ("policy-other-service", lambda a: a.policy_doc["Statement"][1].update(Principal={"Service": "events.amazonaws.com"}), "policyPrincipal"),
    (
        "policy-other-allow",
        lambda a: a.policy_doc["Statement"].append(other_allow({"AWS": f"arn:aws:iam::{OTHER_ACCOUNT}:root"})),
        "policyPrincipal",
    ),
    ("policy-wildcard-source", lambda a: set_conditions(a, {"ArnLike": {"AWS:SourceArn": f"arn:aws:cloudfront::{ACCOUNT}:distribution/*"}}), "policyCondition"),
    ("policy-other-distribution", lambda a: set_conditions(a, {"ArnLike": {"AWS:SourceArn": OTHER_DIST_ARN}}), "policyCondition"),
    ("policy-string-like", lambda a: set_conditions(a, {"StringLike": {"AWS:SourceArn": DIST_ARN}}), "policyCondition"),
    ("policy-no-condition", lambda a: a.policy_doc["Statement"][0].pop("Condition"), "policyCondition"),
    ("policy-missing-invoke-function", lambda a: a.policy_doc["Statement"].pop(), "policyActions"),
    ("policy-wildcard-action", lambda a: a.policy_doc["Statement"][0].update(Action=["lambda:InvokeFunctionUrl", "lambda:*"]), "policyAction"),
    ("policy-unqualified-resource", lambda a: a.policy_doc["Statement"][0].update(Resource=FUNCTION_ARN), "policyResource"),
    ("policy-not-principal", lambda a: a.policy_doc["Statement"][0].update(NotPrincipal={"AWS": "*"}), "policyNegation"),
    # 認証方式の限定は AWS_IAM だけ。NONE・複数値・ワイルドカード・別の演算子・未知の条件は拒否
    ("policy-auth-type-none", lambda a: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": "NONE"}), "policyCondition"),
    ("policy-auth-type-list-with-none", lambda a: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": ["AWS_IAM", "NONE"]}), "policyCondition"),
    ("policy-auth-type-wildcard", lambda a: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": "*"}), "policyCondition"),
    ("policy-auth-type-lowercase", lambda a: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": "aws_iam"}), "policyCondition"),
    (
        "policy-auth-type-string-like",
        lambda a: statement_condition(a, 0).update(
            StringEquals={}, StringLike={"lambda:FunctionUrlAuthType": "AWS_IAM"}
        ),
        "policyCondition",
    ),
    (
        "policy-auth-type-without-source-arn",
        lambda a: a.policy_doc["Statement"][0].update(
            Condition={"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}}
        ),
        "policyCondition",
    ),
    ("policy-unknown-key-in-string-equals", lambda a: statement_condition(a, 0)["StringEquals"].update({"aws:PrincipalOrgID": "o-example"}), "policyCondition"),
    ("policy-unknown-operator", lambda a: statement_condition(a, 0).update(IpAddress={"aws:SourceIp": "0.0.0.0/0"}), "policyCondition"),
    ("policy-unknown-key-in-bool", lambda a: statement_condition(a, 1)["Bool"].update({"aws:SecureTransport": "true"}), "policyCondition"),
    ("policy-bool-false", lambda a: statement_condition(a, 1).update(Bool={"lambda:InvokedViaFunctionUrl": "false"}), "policyCondition"),
    ("policy-source-arn-extra-value", lambda a: statement_condition(a, 0).update(ArnLike={"AWS:SourceArn": [DIST_ARN, OTHER_DIST_ARN]}), "policyCondition"),
    (
        "policy-source-arn-in-string-equals-wildcard",
        lambda a: statement_condition(a, 0).update(
            ArnLike={"AWS:SourceArn": DIST_ARN},
            StringEquals={"AWS:SourceArn": f"arn:aws:cloudfront::{ACCOUNT}:distribution/*"},
        ),
        "policyCondition",
    ),
    ("policy-empty-operator-body", lambda a: statement_condition(a, 0).update(StringEquals={}), "policyCondition"),
    ("policy-not-a-document", lambda a: setattr(a, "policy_doc", "not-a-policy"), "policyStatements"),
    ("policy-empty", lambda a: a.policy_doc.update(Statement=[]), "policyStatements"),
]


class PublicPathBoundaries(DeployTestCase):
    """公開経路（CloudFront → OAC → AWS_IAMの関数URL）の検証。

    どれか1つでも期待と違えば、AWSの変更を1件もせず、スモーク確認もせず、失敗で止まる。
    """

    def fresh(self):
        self.aws = FakeAws()
        self.smoke = FakeSmoke()
        self.result_file.unlink(missing_ok=True)

    def blocked(self, expect=None, **overrides):
        self.assertEqual(self.deploy(**overrides), 1)
        self.assertEqual(self.aws.mutating(), [])
        result = self.result()
        self.assertEqual(result["overall"], "failed")
        self.assertEqual(result["validation"], "failed")
        self.assertEqual(result["publicPath"], "failed")
        self.assertEqual(result["deployment"], "not-started")
        self.assertFalse(result["mutationStarted"])
        self.assertEqual(self.smoke.calls, [])  # 公開経路を確認できない間は、スモーク確認もしない
        self.assert_no_leak()
        if expect is not None:
            self.assertIn(expect, result["error"])

    def test_each_boundary_blocks_with_zero_mutation(self):
        for name, mutate, expect in BOUNDARY_CASES:
            with self.subTest(name):
                self.fresh()
                mutate(self.aws)
                self.blocked(expect)

    def test_arguments_that_differ_from_the_real_resources_block(self):
        other_acl_id = "99999999-2222-3333-4444-555555555555"
        cases = [
            ("base-url-is-the-function-url", {"base_url": f"https://{LAMBDA_HOST}"}, "CloudFront"),
            ("base-url-trailing-slash", {"base_url": BASE_URL + "/"}, "CloudFront"),
            ("base-url-http", {"base_url": "http://" + CF_HOST}, "CloudFront"),
            ("base-url-other-host", {"base_url": "https://dzzzzzzzzzzzz.cloudfront.net"}, "domainName"),
            ("waf-regional-scope", {"web_acl_arn": f"arn:aws:wafv2:us-east-1:{ACCOUNT}:regional/webacl/{WAF_NAME}/{WAF_ID}"}, "scope"),
            ("waf-tokyo-region", {"web_acl_arn": f"arn:aws:wafv2:ap-northeast-1:{ACCOUNT}:global/webacl/{WAF_NAME}/{WAF_ID}"}, "scope"),
            ("waf-other-account", {"web_acl_arn": f"arn:aws:wafv2:us-east-1:{OTHER_ACCOUNT}:global/webacl/{WAF_NAME}/{WAF_ID}"}, "アカウント"),
            ("waf-other-id", {"web_acl_arn": f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/{WAF_NAME}/{other_acl_id}"}, "AWS操作に失敗"),
            ("distribution-other-id", {"distribution_id": "E9ZZZZZZZZZZZZ"}, "AWS操作に失敗"),
            ("oac-other-id", {"oac_id": "E9ZZZZZZZZZZZZ"}, "AWS操作に失敗"),
        ]
        for name, overrides, expect in cases:
            with self.subTest(name):
                self.fresh()
                self.blocked(expect, **overrides)
        # 誤ったscope・リージョンのARNでは、WAFのAPIを呼ばない
        self.fresh()
        self.blocked("scope", web_acl_arn=cases[4][1]["web_acl_arn"])
        self.assertNotIn("get-web-acl", self.aws.ops())

    def test_api_access_denied_blocks_with_zero_mutation(self):
        for op in ("get-distribution", "get-origin-access-control", "get-web-acl", "get-policy"):
            with self.subTest(op):
                self.fresh()
                self.aws.fail[op] = dl.AwsError(op, 254)  # AccessDenied等
                self.blocked("AWS操作に失敗")

    def test_equivalent_forms_are_accepted(self):
        def arn_equals(aws):
            set_conditions(aws, {"ArnEquals": {"AWS:SourceArn": DIST_ARN}})

        def deny_statement(aws):
            aws.policy_doc["Statement"].append(
                {**other_allow("*"), "Sid": "deny", "Effect": "Deny"}
            )

        variants = [
            ("ip-rule-only-is-the-baseline", lambda a: None),
            ("ip-rule-empty-rule-labels", lambda a: a.rules[0].update(RuleLabels=[])),
            ("ip-rule-other-name-and-priority", lambda a: a.rules[0].update(Name="rate-by-ip", Priority=7)),
            ("https-only-viewer", lambda a: a.behavior.update(ViewerProtocolPolicy="https-only")),
            ("method-order", lambda a: a.behavior["AllowedMethods"].update(Items=["OPTIONS", "HEAD", "GET"])),
            (
                "cached-with-options",
                lambda a: a.behavior["AllowedMethods"].update(
                    CachedMethods={"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"]}
                ),
            ),
            ("arn-equals", arn_equals),
            ("string-equals-list", lambda a: set_conditions(a, {"StringEquals": {"AWS:SourceArn": [DIST_ARN]}})),
            # 実CLIの形（ArnLike + StringEquals の認証方式）。認証方式は AWS_IAM だけ許す
            ("real-cli-shape-is-the-baseline", lambda a: None),
            ("auth-type-in-list", lambda a: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": ["AWS_IAM"]})),
            (
                "auth-type-and-source-arn-both-in-string-equals",
                lambda a: statement_condition(a, 0).update(
                    StringEquals={"AWS:SourceArn": DIST_ARN, "lambda:FunctionUrlAuthType": "AWS_IAM"}
                ),
            ),
            ("auth-type-condition-absent", lambda a: statement_condition(a, 0).pop("StringEquals")),
            # 実APIの空・偽の値（キーがない場合と同じ意味）は正常
            (
                "firewall-manager-absent-or-empty",
                lambda a: a.acl["WebACL"].update(
                    ManagedByFirewallManager=False,
                    PreProcessFirewallManagerRuleGroups=[],
                    PostProcessFirewallManagerRuleGroups=[],
                ),
            ),
            (
                "no-staging-or-continuous-deployment",
                lambda a: a.dist_config.update(ContinuousDeploymentPolicyId="", Staging=False),
            ),
            ("deny-is-ignored", deny_statement),
            ("rule-order", lambda a: (add_observers(a, first=False), a.rules.reverse())),
            # 必須のIP規則1件＋既知の観測グループ（Count）0〜3件（合計1〜4件）。
            # 優先順位は前でも後でもよい（Countは終端しない）
            ("observers-all-three-before-ip-rule", add_observers),
            ("observers-all-three-after-ip-rule", lambda a: add_observers(a, first=False)),
            ("observer-ip-reputation-only", lambda a: add_observers(a, OBSERVER_GROUPS[:1])),
            ("observer-common-only", lambda a: add_observers(a, OBSERVER_GROUPS[1:2])),
            ("observer-known-bad-inputs-only", lambda a: add_observers(a, OBSERVER_GROUPS[2:])),
            ("observers-two", lambda a: add_observers(a, OBSERVER_GROUPS[1:])),
            (
                "observers-shuffled-with-ip-rule",
                lambda a: (add_observers(a), a.rules.insert(1, a.rules.pop(3))),
            ),
            (
                "observer-empty-optional-lists",
                with_observers(
                    lambda r: observer_group(r).update(
                        ExcludedRules=[], RuleActionOverrides=[], ManagedRuleGroupConfigs=[]
                    )
                ),
            ),
            ("observer-empty-rule-labels", with_observers(lambda r: r.update(RuleLabels=[]))),
            ("url-without-slash", lambda a: a.url.update(FunctionUrl=f"https://{LAMBDA_HOST}")),
            ("unqualified-function-arn", lambda a: a.url.update(FunctionArn=FUNCTION_ARN)),
        ]
        for name, mutate in variants:
            with self.subTest(name):
                self.fresh()
                mutate(self.aws)
                self.assertEqual(self.deploy(), 0)
                self.assertEqual(self.result()["overall"], "success")

    def test_reads_use_real_api_shapes_and_happen_before_any_mutation(self):
        self.assertEqual(self.deploy(), 0)
        calls = self.aws.calls
        self.assertEqual(
            [c for c in calls if c[0] in ("cloudfront", "wafv2")],
            [
                ["cloudfront", "get-distribution", "--id", DIST_ID],
                ["cloudfront", "get-origin-access-control", "--id", OAC_ID],
                ["wafv2", "get-web-acl", "--name", WAF_NAME, "--scope", "CLOUDFRONT", "--id", WAF_ID],
            ],
        )
        self.assertEqual(
            [c for c in calls if c[1] == "get-policy"],
            [["lambda", "get-policy", "--function-name", FUNCTION, "--qualifier", "demo"]],
        )
        ops = self.aws.ops()
        first_mutation = ops.index("update-function-code")
        for op in ("get-distribution", "get-origin-access-control", "get-web-acl", "get-policy"):
            self.assertLess(ops.index(op), first_mutation)
        # CloudFront・WAFには書き込みの呼び出しがない。リージョンの指定はrun_awsだけで行う
        for call in calls:
            if call[0] in ("cloudfront", "wafv2"):
                self.assertTrue(call[1].startswith("get-"))
            self.assertNotIn("--region", call)
        self.assertEqual(self.result()["publicPath"], "passed")

    def test_failed_previous_smoke_still_checked_through_cloudfront_url(self):
        self.smoke.outcomes["previous"] = False
        self.assertEqual(self.deploy(), 1)
        self.assertEqual({url for url, _, _ in self.smoke.calls}, {BASE_URL})
        self.assertEqual(self.aws.mutating(), [])


class RunAwsRegions(unittest.TestCase):
    """実コマンドのリージョン。CloudFront・WAFはus-east-1、Lambdaは東京で、--regionは1回だけ。"""

    def command(self, args):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        with mock.patch.object(dl.subprocess, "run", fake_run):
            dl.run_aws(args)
        return captured["command"]

    def region_of(self, command):
        self.assertEqual(command.count("--region"), 1)
        return command[command.index("--region") + 1]

    def test_cloudfront_and_wafv2_use_us_east_1(self):
        for args in (
            ["cloudfront", "get-distribution", "--id", DIST_ID],
            ["cloudfront", "get-origin-access-control", "--id", OAC_ID],
            ["wafv2", "get-web-acl", "--name", WAF_NAME, "--scope", "CLOUDFRONT", "--id", WAF_ID],
        ):
            with self.subTest(args[1]):
                command = self.command(args)
                self.assertEqual(self.region_of(command), "us-east-1")
                self.assertEqual(command[:2], ["aws", args[0]])

    def test_lambda_uses_tokyo(self):
        command = self.command(["lambda", "get-policy", "--function-name", FUNCTION, "--qualifier", "demo"])
        self.assertEqual(self.region_of(command), "ap-northeast-1")

    def test_caller_cannot_add_a_second_region(self):
        with mock.patch.object(dl.subprocess, "run", side_effect=AssertionError("実行しない")):
            with self.assertRaises(dl.AwsError):
                dl.run_aws(["lambda", "get-alias", "--region", "us-west-2"])


class WebAclRuleClassification(unittest.TestCase):
    """必須のIP規則（ip_rate_rule_ok）と観測グループ（classify_observer）は互いの形を受け入れない。"""

    def setUp(self):
        (self.ip,) = make_web_acl()["WebACL"]["Rules"]
        self.global_rule = make_global_rule()

    def test_ip_rule_is_the_only_accepted_rate_rule(self):
        self.assertTrue(dl.ip_rate_rule_ok(self.ip))
        self.assertIsNone(dl.classify_observer(self.ip))
        # 要件から外した全体（CONSTANT）の規則は、どちらの判定でも受け入れない
        self.assertFalse(dl.ip_rate_rule_ok(self.global_rule))
        self.assertIsNone(dl.classify_observer(self.global_rule))

    def test_obsolete_global_rule_helpers_are_removed(self):
        for name in ("GLOBAL_RATE_LIMIT", "scope_down_ok", "classify_rule"):
            with self.subTest(name):
                self.assertFalse(hasattr(dl, name))

    def test_bool_is_not_accepted_as_a_number(self):
        rule = json.loads(json.dumps(self.ip))
        rule["Statement"]["RateBasedStatement"]["EvaluationWindowSec"] = True
        self.assertFalse(dl.ip_rate_rule_ok(rule))

    def test_known_observers_are_not_rate_rules(self):
        for group in OBSERVER_GROUPS:
            with self.subTest(group):
                rule = observer(group, 2)
                self.assertEqual(dl.classify_observer(rule), group)
                self.assertFalse(dl.ip_rate_rule_ok(rule))

    def test_allowed_observers_are_exactly_the_three_known_groups(self):
        self.assertEqual(dl.OBSERVER_GROUPS, frozenset(OBSERVER_GROUPS))
        self.assertEqual(dl.WEB_ACL_MAX_RULES, 1 + len(OBSERVER_GROUPS))
        self.assertEqual(dl.WEB_ACL_MAX_RULES, 4)

    def test_malformed_group_names_are_rejected_without_raising(self):
        for bad in (["AWSManagedRulesCommonRuleSet"], {"a": 1}, None, 1, True):
            with self.subTest(repr(bad)):
                rule = observer(OBSERVER_GROUPS[1], 2)
                rule["Statement"]["ManagedRuleGroupStatement"]["Name"] = bad
                self.assertIsNone(dl.classify_observer(rule))
                self.assertFalse(dl.web_acl_rules_ok([self.ip, rule]))

    def test_rule_list_bounds(self):
        observers = [observer(g, 1 + i) for i, g in enumerate(OBSERVER_GROUPS)]
        self.assertTrue(dl.web_acl_rules_ok([self.ip]))
        self.assertTrue(dl.web_acl_rules_ok([self.ip, *observers]))  # 合計4件（実際の構成と同じ形）
        self.assertTrue(dl.web_acl_rules_ok([*observers, self.ip]))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, *observers, observers[0]]))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, *observers, self.global_rule]))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, self.global_rule]))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, *observers[:2], self.global_rule]))
        self.assertFalse(dl.web_acl_rules_ok([self.global_rule]))
        self.assertFalse(dl.web_acl_rules_ok(observers))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, self.ip]))
        self.assertFalse(dl.web_acl_rules_ok([self.ip, unknown_count_rule()]))
        self.assertFalse(dl.web_acl_rules_ok([]))
        self.assertFalse(dl.web_acl_rules_ok(None))


class TemplatesMatchTheCode(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / "infra" / "cd" / name).read_text(encoding="utf-8"))

    def test_waf_template_satisfies_the_validator(self):
        response = make_web_acl()
        response["WebACL"]["Rules"] = self.load("waf-rules.json")["Rules"]
        self.assertEqual(dl.web_acl_problems(response, WAF_ARN, WAF_NAME, WAF_ID), [])
        # テンプレートの1件に既知の観測グループ3種（Count）を足した4件も受け入れる
        response["WebACL"]["Rules"] += [observer(g, 1 + i) for i, g in enumerate(OBSERVER_GROUPS)]
        self.assertEqual(dl.web_acl_problems(response, WAF_ARN, WAF_NAME, WAF_ID), [])

    def test_waf_template_has_only_the_ip_rule(self):
        text = (ROOT / "infra" / "cd" / "waf-rules.json").read_text(encoding="utf-8")
        rules = json.loads(text)["Rules"]
        self.assertEqual(len(rules), 1)
        self.assertTrue(dl.ip_rate_rule_ok(rules[0]))
        rate_statement = rules[0]["Statement"]["RateBasedStatement"]
        self.assertEqual(
            rate_statement,
            {"Limit": dl.IP_RATE_LIMIT, "EvaluationWindowSec": dl.RATE_WINDOW_SEC, "AggregateKeyType": "IP"},
        )
        self.assertEqual((dl.IP_RATE_LIMIT, dl.RATE_WINDOW_SEC), (300, 300))
        for removed in ("CONSTANT", "1500", "ScopeDownStatement"):
            self.assertNotIn(removed, text)

    def test_deploy_policy_adds_only_the_read_permissions(self):
        text = (ROOT / "infra" / "cd" / "deploy-policy.json").read_text(encoding="utf-8")
        statements = json.loads(text)["Statement"]
        by_sid = {s["Sid"]: s for s in statements}
        actions = {}
        for statement in statements:
            value = statement["Action"]
            for action in [value] if isinstance(value, str) else value:
                actions.setdefault(action.split(":")[0], set()).add(action)
        self.assertEqual(actions["cloudfront"], {"cloudfront:GetDistribution", "cloudfront:GetOriginAccessControl"})
        self.assertEqual(actions["wafv2"], {"wafv2:GetWebACL"})
        self.assertNotIn("iam", actions)
        self.assertNotIn("PassRole", text)
        # ResourceがワイルドカードなのはOAC（リソース指定が未対応の読み取り）だけ
        wildcard = {sid for sid, s in by_sid.items() if s["Resource"] == "*"}
        self.assertEqual(wildcard, {"ReadOriginAccessControlsResourceScopingUnsupported"})
        self.assertEqual(by_sid["ReadTheTargetDistributionOnly"]["Resource"].count("<DISTRIBUTION_ID>"), 1)
        self.assertEqual(by_sid["ReadTheTargetWebAclOnly"]["Resource"], "<WEB_ACL_ARN>")
        policy = by_sid["ReadTheDemoAliasResourcePolicyOnly"]
        self.assertEqual(policy["Action"], "lambda:GetPolicy")
        self.assertTrue(policy["Resource"].endswith(":function:<FUNCTION_NAME>:demo"))
        # 従来のLambda更新権限と固定レイヤーの読み取りは維持
        deploy = set(by_sid["DeployToTheDemoFunctionOnly"]["Action"])
        for action in ("lambda:UpdateFunctionCode", "lambda:PublishVersion", "lambda:UpdateAlias", "lambda:GetFunction"):
            self.assertIn(action, deploy)
        self.assertEqual(by_sid["ReadThePinnedPublicLayerVersionOnly"]["Resource"], dl.PINNED_LAYER_ARN)
        # 予約済み同時実行数は読み取りだけ。別の文で、修飾子なしの対象関数だけを対象にする
        concurrency = by_sid["ReadTheDemoFunctionReservedConcurrencyOnly"]
        self.assertEqual(concurrency["Effect"], "Allow")
        self.assertEqual(concurrency["Action"], "lambda:GetFunctionConcurrency")
        self.assertEqual(
            concurrency["Resource"],
            "arn:aws:lambda:ap-northeast-1:<ACCOUNT_ID>:function:<FUNCTION_NAME>",
        )
        self.assertNotIn("Condition", concurrency)
        holders = [
            s["Sid"] for s in statements
            if "lambda:GetFunctionConcurrency" in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
        ]
        self.assertEqual(holders, ["ReadTheDemoFunctionReservedConcurrencyOnly"])
        for forbidden in ("PutFunctionConcurrency", "DeleteFunctionConcurrency", "lambda:*", "Put", "Delete"):
            self.assertNotIn(forbidden, text)


class OidcTrustPolicyTemplate(unittest.TestCase):
    """配布ロールの信頼ポリシーの sub は、認証済みの端末で確認した正確な接頭辞＋ :environment:demo。

    新しいリポジトリのsubは、所有者とリポジトリの数値IDを含む不変の形式になり得る。ワイルドカード・
    推測・旧形式への黙った戻しはしない。以下の識別子はすべて架空。
    """

    PREFIX = "repo:example-owner@1000001/example-repo@2000002"  # 架空
    LEGACY = "repo:example-owner/example-repo"
    SLUG = "example-owner/example-repo"

    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "infra" / "cd" / "oidc-trust-policy.json").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "infra" / "cd" / "bootstrap.md").read_text(encoding="utf-8")

    def pattern(self, name):
        match = re.search(rf"^{name}='(.+)'$", self.bootstrap, re.MULTILINE)
        self.assertIsNotNone(match, f"{name} が手順書にない")
        return re.compile(match.group(1))

    def test_template_uses_exactly_the_selected_prefix_and_demo_environment(self):
        document = json.loads(self.template)
        condition = document["Statement"][0]["Condition"]
        self.assertEqual(set(condition), {"StringEquals"})  # StringLike・ワイルドカードの条件は持たない
        self.assertEqual(
            condition["StringEquals"]["token.actions.githubusercontent.com:sub"],
            "<OIDC_SUB_PREFIX>:environment:demo",
        )
        self.assertEqual(condition["StringEquals"]["token.actions.githubusercontent.com:aud"], "sts.amazonaws.com")
        self.assertNotIn("*", self.template)
        # 古い前提（所有者/リポジトリ名から組み立てる形）に戻らない
        for old in ("<OWNER>", "<REPO>", "repo:<"):
            self.assertNotIn(old, self.template)

    def test_substitution_yields_the_exact_subject(self):
        for prefix in (self.PREFIX, self.LEGACY):
            substituted = json.loads(self.template.replace("<OIDC_SUB_PREFIX>", prefix))
            sub = substituted["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"]
            self.assertEqual(sub, f"{prefix}:environment:demo")

    def test_runbook_validation_accepts_only_the_two_exact_forms(self):
        immutable = self.pattern("OIDC_IMMUTABLE_RE")
        legacy = self.pattern("OIDC_LEGACY_RE")
        self.assertIsNotNone(immutable.fullmatch(self.PREFIX))
        self.assertIsNone(immutable.fullmatch(self.LEGACY))
        self.assertIsNotNone(legacy.fullmatch(self.LEGACY))
        self.assertIsNone(legacy.fullmatch(self.PREFIX))
        for bad in (
            "repo:*",
            "repo:example-owner/*",
            "repo:example-owner@1000001/*",
            "repo:example-owner@1000001/example-repo",  # リポジトリのIDなし
            "repo:example-owner/example-repo@2000002",  # 所有者のIDなし
            "repo:example-owner@x/example-repo@2000002",
            self.PREFIX + ":environment:demo",  # 接尾辞は含めない
            self.PREFIX + ":*",
            "REPLACE_OIDC_SUB_PREFIX",
            "",
            self.PREFIX + "\n",
        ):
            with self.subTest(bad):
                self.assertIsNone(immutable.fullmatch(bad))
                self.assertIsNone(legacy.fullmatch(bad))
        # 手順書の検証は、接頭辞の所有者・リポジトリ名が REPO_SLUG と一致することも要求する
        groups = immutable.fullmatch(self.PREFIX).groups()
        self.assertEqual(f"{groups[0]}/{groups[2]}", self.SLUG)

    def test_runbook_documents_the_authenticated_local_lookup_and_forbids_fallbacks(self):
        text = self.bootstrap
        self.assertIn("repos/${REPO_SLUG}/actions/oidc/customization/sub", text)
        self.assertIn("OIDC_SUB_PREFIX", text)
        self.assertIn("<OIDC_SUB_PREFIX>", text)
        self.assertNotIn("<OWNER>/<REPO>", text)
        self.assertNotIn("repo:<リポジトリ>:environment:demo", text)
        self.assertNotIn("repo:${REPO_SLUG}:environment:demo", text)  # 旧前提の組み立て
        self.assertIn("ワイルドカード", text)
        self.assertIn("CloudShell", text)
        # 実際のGitHubの応答（Codexの実測）で確認した項目名。未確認という古い記述に戻らない
        for key in ("use_immutable_subject", "sub_claim_prefix"):
            self.assertIn(key, text)
        self.assertNotIn("接頭辞の項目名は未確認", text)

    def runbook_block(self, marker):
        blocks = [b for b in re.findall(r"```bash\n(.*?)```", self.bootstrap, re.DOTALL) if marker in b]
        self.assertEqual(len(blocks), 1, marker)
        return blocks[0]

    def run_prefix_check(self, prefix, slug):
        """手順書の検証ブロックを、実際のbashで実行する（AWS・ネットワークは使わない）。"""
        if shutil.which("bash") is None:
            self.skipTest("bash がない")
        block = re.sub(r"^OIDC_SUB_PREFIX=.*\n", "", self.runbook_block("OIDC_IMMUTABLE_RE="), count=1, flags=re.M)
        script = 'set -euo pipefail\ndie() { echo "停止: $*" >&2; exit 1; }\n' + block
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OIDC_SUB_PREFIX": prefix, "REPO_SLUG": slug}
        done = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, encoding="utf-8", errors="replace", check=False
        )
        return done.returncode

    def test_runbook_bash_check_accepts_only_exact_forms_matching_the_repo_slug(self):
        self.assertEqual(self.run_prefix_check(self.PREFIX, self.SLUG), 0)
        self.assertEqual(self.run_prefix_check(self.LEGACY, self.SLUG), 0)
        bad = {
            "other-repo-immutable": ("repo:example-owner@1000001/other-repo@2000002", self.SLUG),
            "other-owner-immutable": ("repo:other-owner@1000001/example-repo@2000002", self.SLUG),
            "other-repo-legacy": ("repo:example-owner/other-repo", self.SLUG),
            "other-owner-legacy": ("repo:other-owner/example-repo", self.SLUG),
            "wildcard": ("repo:example-owner/*", self.SLUG),
            "wildcard-all": ("repo:*", self.SLUG),
            "suffix": (self.PREFIX + ":environment:demo", self.SLUG),
            "no-owner-id": ("repo:example-owner/example-repo@2000002", self.SLUG),
            "placeholder": ("REPLACE_OIDC_SUB_PREFIX", self.SLUG),
            "empty": ("", self.SLUG),
        }
        for name, (prefix, slug) in bad.items():
            with self.subTest(name):
                self.assertNotEqual(self.run_prefix_check(prefix, slug), 0)

    def run_jq_check(self, document, sub):
        """手順書のjqの検査を、実際のjqで実行する。終了コード0だけが合格（jq -e）。"""
        jq = shutil.which("jq")
        if jq is None:
            self.skipTest("jq がない")
        match = re.search(
            r"jq -e --arg sub \"\$\{OIDC_SUB_PREFIX\}:environment:demo\" \\\n  '([^']+)'", self.bootstrap
        )
        self.assertIsNotNone(match, "手順書のjqの検査が見つからない")
        done = subprocess.run(
            [jq, "-e", "--arg", "sub", sub, match.group(1)],
            input=json.dumps(document), capture_output=True, encoding="utf-8", check=False,
        )
        return done.returncode

    def test_runbook_jq_check_requires_one_statement_with_the_exact_sub(self):
        sub = f"{self.PREFIX}:environment:demo"
        good = json.loads(self.template.replace("<OIDC_SUB_PREFIX>", self.PREFIX))
        self.assertEqual(self.run_jq_check(good, sub), 0)
        # 確認した接頭辞と違う・接尾辞が違う場合は不合格
        self.assertNotEqual(self.run_jq_check(good, f"{self.LEGACY}:environment:demo"), 0)
        self.assertNotEqual(self.run_jq_check(good, f"{self.PREFIX}:environment:demo2"), 0)
        self.assertNotEqual(self.run_jq_check(good, f"{self.PREFIX}:*"), 0)
        # 文が2件（広い許可の追加）は不合格。lengthとsubの照合が別々に効く
        two = json.loads(json.dumps(good))
        two["Statement"].append(json.loads(json.dumps(good["Statement"][0])))
        self.assertNotEqual(self.run_jq_check(two, sub), 0)
        # StringEquals以外の条件演算子の追加（StringLikeなど）は不合格
        like = json.loads(json.dumps(good))
        like["Statement"][0]["Condition"]["StringLike"] = {"token.actions.githubusercontent.com:sub": "repo:*"}
        self.assertNotEqual(self.run_jq_check(like, sub), 0)
        # subが違う文書は不合格
        wrong = json.loads(json.dumps(good))
        wrong["Statement"][0]["Condition"]["StringEquals"]["token.actions.githubusercontent.com:sub"] = "repo:x:environment:demo"
        self.assertNotEqual(self.run_jq_check(wrong, sub), 0)

    def test_public_docs_hold_no_real_account_ids(self):
        # 12桁の数字は、公開されているアダプタのレイヤーの所有アカウントだけ許す（実在の利用者のアカウントIDは書かない）
        allowed = {"753240598075"}
        for name in ("infra/cd/bootstrap.md", "infra/cd/oidc-trust-policy.json", "docs/development-workflow.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            found = set(re.findall(r"(?<![0-9A-Za-z])[0-9]{12}(?![0-9A-Za-z])", text))
            with self.subTest(name):
                self.assertLessEqual(found, allowed)

    def test_public_files_hold_only_the_fictional_example(self):
        # 実在の識別子は書かない。例は架空の所有者・リポジトリ・IDだけ
        for text in (self.bootstrap, self.template):
            for match in re.finditer(r"repo:[A-Za-z0-9._-]+@[0-9]+/[A-Za-z0-9._-]+@[0-9]+", text):
                self.assertEqual(match.group(0), self.PREFIX)


class ReservedConcurrency(DeployTestCase):
    """予約済み同時実行数は、変更の前に読み取りでちょうど1を確認する。自動では設定しない。"""

    def concurrency_calls(self):
        return [c for c in self.aws.calls if c[1].endswith("function-concurrency")]

    def test_exactly_one_passes_and_is_read_before_any_mutation_or_smoke(self):
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(result["reservedConcurrency"], "passed")
        self.assertEqual(
            self.concurrency_calls(),
            [["lambda", "get-function-concurrency", "--function-name", FUNCTION]],
        )
        ops = self.aws.ops()
        self.assertLess(ops.index("get-function-concurrency"), ops.index("update-function-code"))
        self.assertEqual(self.smoke.calls[0][2], "previous")
        self.assertNotIn("put-function-concurrency", ops)
        self.assertNotIn("delete-function-concurrency", ops)

    def test_wrong_concurrency_blocks_with_zero_mutation_and_no_smoke(self):
        cases = [
            ("missing", {}, "missing", "設定されていません"),
            ("null", {"ReservedConcurrentExecutions": None}, "missing", "設定されていません"),
            ("zero", {"ReservedConcurrentExecutions": 0}, "zero", "0です"),
            ("two", {"ReservedConcurrentExecutions": 2}, "not-one", "1ではありません"),
            ("large", {"ReservedConcurrentExecutions": 1000}, "not-one", "1ではありません"),
            ("negative", {"ReservedConcurrentExecutions": -1}, "not-one", "1ではありません"),
            ("bool-true", {"ReservedConcurrentExecutions": True}, "not-one", "1ではありません"),
            ("string", {"ReservedConcurrentExecutions": "1"}, "not-one", "1ではありません"),
            ("float", {"ReservedConcurrentExecutions": 1.0}, "not-one", "1ではありません"),
        ]
        for name, response, state, message in cases:
            with self.subTest(name):
                self.aws = FakeAws()
                self.smoke = FakeSmoke()
                self.result_file.unlink(missing_ok=True)
                self.aws.concurrency = response
                self.assertEqual(self.deploy(), 1)
                result = self.result()
                self.assertEqual(result["reservedConcurrency"], state)
                self.assertEqual(result["overall"], "failed")
                self.assertEqual(result["validation"], "failed")
                self.assertEqual(result["stage"], "validate-concurrency")
                self.assertEqual(result["deployment"], "not-started")
                self.assertFalse(result["mutationStarted"])
                self.assertIn(message, result["error"])
                self.assertIn("変更しません", result["error"])
                self.assertEqual(self.aws.mutating(), [])
                self.assertEqual(self.smoke.calls, [])
                # 自動で直さない（読み取り1回だけ）
                self.assertEqual(
                    self.concurrency_calls(),
                    [["lambda", "get-function-concurrency", "--function-name", FUNCTION]],
                )
                self.assertEqual(self.aws.alias["FunctionVersion"], "1")
                self.assert_no_leak()

    def test_read_failure_blocks_with_zero_mutation(self):
        self.aws.fail["get-function-concurrency"] = dl.AwsError("lambda get-function-concurrency", 254)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertEqual(result["reservedConcurrency"], "read-failed")
        self.assertIn("AWS操作に失敗", result["error"])
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(self.smoke.calls, [])

    def test_public_path_failure_stops_before_reading_concurrency(self):
        rate(self.aws).update(Limit=3000)
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.result()["reservedConcurrency"], "not-run")
        self.assertEqual(self.concurrency_calls(), [])

    def test_problem_classifier(self):
        self.assertIsNone(dl.reserved_concurrency_problem({"ReservedConcurrentExecutions": 1}))
        self.assertEqual(dl.reserved_concurrency_problem({}), "missing")
        self.assertEqual(dl.reserved_concurrency_problem(None), "missing")
        self.assertEqual(dl.reserved_concurrency_problem({"ReservedConcurrentExecutions": 0}), "zero")
        self.assertEqual(dl.reserved_concurrency_problem({"ReservedConcurrentExecutions": 2}), "not-one")
        self.assertEqual(dl.REQUIRED_RESERVED_CONCURRENCY, 1)


class ActualFourRuleWebAcl(DeployTestCase):
    """利用者が確認した実際の形（2026-11-10）: IP規則のBlock 1件＋既知の観測グループ3種（Count）、全体の規則なし。

    偽のAWSでの形の再現で、実際のWeb ACLの応答そのものではない。
    """

    def setUp(self):
        super().setUp()
        add_observers(self.aws, first=False)  # 優先順位: IP規則 0、観測グループ 1〜3

    def test_four_rule_acl_is_reused_and_deploys(self):
        self.assertEqual(len(self.aws.rules), 4)
        self.assertEqual(self.deploy(), 0)
        result = self.result()
        self.assertEqual(result["overall"], "success")
        self.assertEqual(result["publicPath"], "passed")
        self.assertEqual(result["reservedConcurrency"], "passed")
        self.assertEqual(self.aws.mutating(), ["update-function-code", "publish-version", "update-alias"])
        # Web ACL・CloudFrontは読み取りだけ（作り直し・更新をしない）
        for call in self.aws.calls:
            if call[0] in ("cloudfront", "wafv2"):
                self.assertTrue(call[1].startswith("get-"))
        self.assertEqual(len(self.aws.rules), 4)

    def test_four_rules_with_observers_first_also_deploy(self):
        self.aws = FakeAws()
        add_observers(self.aws, first=True)
        self.assertEqual(self.deploy(), 0)

    def test_current_disabled_distribution_fails_closed_only_on_enabled(self):
        # 現在はディストリビューションが無効のまま。WAFの4件は受け入れ、無効化だけで止まる
        self.aws.dist_config.update(Enabled=False)
        self.assertEqual(self.deploy(), 1)
        result = self.result()
        self.assertIn("distributionEnabled", result["error"])
        self.assertNotIn("webAclRules", result["error"])
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(self.smoke.calls, [])

    def test_four_rules_with_wrong_concurrency_still_block(self):
        self.aws.concurrency = {"ReservedConcurrentExecutions": 0}
        self.assertEqual(self.deploy(), 1)
        self.assertEqual(self.result()["reservedConcurrency"], "zero")
        self.assertEqual(self.aws.mutating(), [])

    def test_four_rules_with_bad_ip_setting_still_block(self):
        rate(self.aws).update(EvaluationWindowSec=600)
        self.assertEqual(self.deploy(), 1)
        self.assertIn("webAclRules", self.result()["error"])
        self.assertEqual(self.aws.mutating(), [])

    def test_adding_the_removed_global_rule_makes_five_and_blocks(self):
        self.aws.rules.append(make_global_rule(4))
        self.assertEqual(self.deploy(), 1)
        self.assertIn("webAclRules", self.result()["error"])
        self.assertEqual(self.aws.mutating(), [])


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

    def test_deploy_mode_requires_each_cloudfront_argument(self):
        full = {
            "--ci-run-url": "https://github.com/example/repo/actions/runs/1",
            "--run-id": "1",
            "--run-attempt": "1",
            "--function-name": FUNCTION,
            "--base-url": BASE_URL,
            "--distribution-id": DIST_ID,
            "--oac-id": OAC_ID,
            "--web-acl-arn": WAF_ARN,
            "--layer-code-sha256": LAYER_SHA,
            "--result-file": str(self.dir / "r.json"),
        }
        for missing in ("--distribution-id", "--oac-id", "--web-acl-arn"):
            with self.subTest(missing):
                argv = ["--artifact-dir", str(self.artifact), "--source-sha", NEW_SHA,
                        "--zip-sha256", self.zip_hex]
                for name, value in full.items():
                    if name != missing:
                        argv += [name, value]
                with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}), \
                        mock.patch.object(dl, "run_aws", side_effect=AssertionError("AWS呼び出し")):
                    with self.assertRaises(SystemExit) as raised:
                        dl.main(argv)
                self.assertIn(missing.removeprefix("--").replace("-", "_"), str(raised.exception))

    def test_verify_only_ignores_cloudfront_arguments_and_never_calls_aws(self):
        code, _ = self.run_cli()  # 新しい引数なし
        self.assertEqual(code, 0)


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
            distribution_id=DIST_ID,
            oac_id=OAC_ID,
            web_acl_arn=WAF_ARN,
            layer_code_sha256=LAYER_SHA,
            ci_run_url="https://github.com/example/repo/actions/runs/1",
        )
        values.update(overrides)
        return mock.Mock(**values)

    def test_valid_inputs_pass(self):
        with mock.patch.dict(os.environ, {"CD_ALLOW_LOCAL": "false"}):
            dl.validate_inputs(self.args())

    def test_rejects_anything_but_a_bare_cloudfront_origin(self):
        urls = (
            "https://example.com",
            f"https://{LAMBDA_HOST}",  # 関数URLは公開URLにできない（フォールバック禁止）
            BASE_URL + "/",
            BASE_URL + "/path",
            BASE_URL + "?x=1",
            BASE_URL + ":443",
            "https://user@" + CF_HOST,
            "http://" + CF_HOST,
            "https://D1234ABCD5678.cloudfront.net",
            "https://d1234abcd5678.cloudfront.net.example.com",
            "https://short.cloudfront.net",
            BASE_URL + "\n",
        )
        for url in urls:
            with self.subTest(url), self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(base_url=url))

    def test_rejects_bad_distribution_oac_and_web_acl(self):
        bad = (
            {"distribution_id": "e2abcdefghijkl"},
            {"distribution_id": "E2/ABC"},
            {"oac_id": ""},
            {"web_acl_arn": f"arn:aws:wafv2:us-east-1:{ACCOUNT}:regional/webacl/{WAF_NAME}/{WAF_ID}"},
            {"web_acl_arn": f"arn:aws:wafv2:ap-northeast-1:{ACCOUNT}:global/webacl/{WAF_NAME}/{WAF_ID}"},
            {"web_acl_arn": WAF_ARN + "/extra"},
        )
        for overrides in bad:
            with self.subTest(overrides), self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(**overrides))

    def test_rejects_short_sha_and_blank_values(self):
        for overrides in ({"source_sha": "abc"}, {"layer_code_sha256": ""}, {"function_name": ""}):
            with self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(**overrides))

    def test_non_string_values_exit_safely_instead_of_type_error(self):
        for name in ("distribution_id", "oac_id", "web_acl_arn", "base_url"):
            with self.subTest(name), self.assertRaises(SystemExit):
                dl.validate_inputs(self.args(**{name: 123}))

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
