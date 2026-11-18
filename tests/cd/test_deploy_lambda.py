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
import zipfile
from pathlib import Path
from types import SimpleNamespace
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
EDGE_FUNCTION = "demo-viewer-ip"
EDGE_ARN = f"arn:aws:cloudfront::{ACCOUNT}:function/{EDGE_FUNCTION}"
EDGE_SOURCE = (ROOT / "infra" / "edge" / "viewer-ip.js").read_bytes()  # レビュー済みのソース
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


def make_artifact(
    directory: Path,
    sha: str = NEW_SHA,
    payload: bytes = b"new zip",
    guard: bool | None = None,
    viewer_ip: bool | None = None,
    manifest_edge_hex: str | None = None,
    edge_entry: bytes = EDGE_SOURCE,
) -> str:
    """viewer_ip が None 以外のときは、エッジのソースを含む本物のZIPを作る（True: 契約あり、False: 宣言なし）。"""
    directory.mkdir(parents=True)
    zip_path = directory / f"lambda-{sha}.zip"
    if viewer_ip is None:
        zip_path.write_bytes(payload)
    else:
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("app/main.py", payload)
            archive.writestr("edge/viewer-ip.js", edge_entry)
        payload = zip_path.read_bytes()
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
    if guard is not None:
        manifest["capabilities"] = {"publicWriteGuard": 1 if guard else 0}
    if viewer_ip is not None:
        manifest.setdefault("capabilities", {})["viewerIpContract"] = 1 if viewer_ip else 0
        manifest["edge"] = {
            "file": "edge/viewer-ip.js",
            "sha256Hex": manifest_edge_hex or hashlib.sha256(EDGE_SOURCE).hexdigest(),
        }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return digest.hexdigest()


APPROVED_ASSOCIATION = {"FunctionARN": EDGE_ARN, "EventType": "viewer-request"}


def enable_write(aws, *, methods=None, associations=None, live="source") -> None:
    """FakeAws の公開経路を、書き込み許可（7メソッド・管理ポリシーのまま・承認したエッジ関数の関連付け1件）の形にする。

    live: "source"=レビュー済みと同じコード / bytes=別のコード / None=取得に失敗する / "nofile"=ファイルを出さない
    """
    items = sorted(methods if methods is not None else dl.WRITE_ENABLED_METHODS)
    aws.behavior["AllowedMethods"].update(Quantity=len(items), Items=items)
    found = [APPROVED_ASSOCIATION] if associations is None else associations
    aws.behavior["FunctionAssociations"] = {"Quantity": len(found), "Items": found}
    aws.live_edge = EDGE_SOURCE if live == "source" else live

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
        self.on_updated = None  # 更新の完了（wait function-updated-v2）の直後に呼ぶ
        self.upload_sha = None  # update-function-code の応答のCodeSha256を差し替える
        self.publish_override = None  # publish-version の応答を差し替える
        self.alias_tamper = None  # update-alias の応答を差し替える関数
        self.fail_after = {}  # 操作名 -> 変更を反映した後に投げる例外
        self.update_revision = None  # update-function-code の応答のRevisionId
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
        self.concurrency = {"ReservedConcurrentExecutions": 3}
        self.live_edge = EDGE_SOURCE
        self.edge_outfiles = []

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
                if self.on_updated:
                    self.on_updated()
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
        if op == "get-function" and args[0] == "cloudfront":
            # 実CLIの形: --name <名前> --stage LIVE <出力ファイル>（本文はファイルへ、標準出力はメタデータ）
            assert args[2:5] == ["--name", EDGE_FUNCTION, "--stage"] and args[5] == "LIVE" and len(args) == 7
            self.edge_outfiles.append(Path(args[6]))
            if self.live_edge is None:
                raise dl.AwsError(op, 254)
            if self.live_edge != "nofile":
                Path(args[6]).write_bytes(self.live_edge)
            return {"ETag": "E1", "ContentType": "application/octet-stream"}
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
            self.in_progress_revision = self.update_revision = self._rev("fprogress")
            self.function.update(RevisionId=self.in_progress_revision, LastUpdateStatus="InProgress")
            return {"CodeSha256": self.upload_sha or self.function["CodeSha256"], "RevisionId": self.in_progress_revision}
        if op == "publish-version":
            if (
                opts["--revision-id"] != self.function["RevisionId"]
                or opts["--code-sha256"] != self.function["CodeSha256"]
            ):
                raise conflict
            if self.publish_override is not None:
                return self.publish_override
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
            if op in self.fail_after:
                raise self.fail_after[op]
            result = json.loads(json.dumps(self.alias))
            return self.alias_tamper(result) if self.alias_tamper else result
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
        self.result_file = self.dir / "out" / "result.json"
        self.use_artifact("artifact")
        self.reset()

    def reset(self):
        """偽のAWS・スモーク・結果を作り直す（subTestの間で使う）。"""
        self.aws = FakeAws()
        self.smoke = FakeSmoke()
        self.result_file.unlink(missing_ok=True)

    def use_artifact(self, name, **kwargs):
        self.artifact = self.dir / name
        shutil.rmtree(self.artifact, ignore_errors=True)
        self.zip_hex = make_artifact(self.artifact, **kwargs)

    def deploy(self, **overrides):
        args = dict(
            artifact_dir=self.artifact,
            source_sha=NEW_SHA,
            zip_sha256=self.zip_hex,
            ci_run_url="https://github.com/example/repo/actions/runs/1",
            run_id="200",
            run_attempt="1",
            function_name=FUNCTION,
            base_url=BASE_URL,
            distribution_id=DIST_ID,
            oac_id=OAC_ID,
            web_acl_arn=WAF_ARN,
            layer_code_sha256=LAYER_SHA,
            edge_function_arn=EDGE_ARN,
            result_file=self.result_file,
            verify_artifact_only=False,
        )
        args.update(overrides)
        return dl.Deployer(SimpleNamespace(**args), self.aws, self.smoke).run()

    def result(self):
        return json.loads(self.result_file.read_text(encoding="utf-8"))

    def blocked(self, expect=None, **overrides):
        """AWSの変更もスモーク確認もせず、検証で失敗して止まる。"""
        self.assertEqual(self.deploy(**overrides), 1)
        result = self.result()
        self.assertEqual(self.aws.mutating(), [])
        self.assertEqual(result["overall"], "failed")
        self.assertFalse(result["mutationStarted"])
        self.assertEqual(result["alias"], "untouched")
        self.assertEqual(self.smoke.calls, [])
        if expect is not None:
            self.assertIn(expect, result["error"])
        text = self.result_file.read_text(encoding="utf-8")
        for secret in (SECRET, self.tmp.name, ACCOUNT):  # 値・パス・アカウントを含むARNの全文は残さない
            self.assertNotIn(secret, text)
        return result

    def smoke_labels(self):
        return [(sha, label) for _, sha, label in self.smoke.calls]


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
    ("waf-window-float", lambda a: rate(a).update(EvaluationWindowSec=300.0), "webAclRules"),
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
    ("waf-observer-name-bool", with_observers(lambda r: observer_group(r).update(Name=True)), "webAclRules"),
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
    """公開経路（CloudFront → OAC → AWS_IAMの関数URL）。1つでも期待と違えば、変更もスモーク確認もせず止まる。"""

    def test_each_boundary_blocks_with_zero_mutation(self):
        for name, mutate, expect in BOUNDARY_CASES:
            with self.subTest(name):
                self.reset()
                mutate(self.aws)
                self.blocked(expect)

    def test_arguments_that_differ_from_the_real_resources_block(self):
        other_acl_id = "99999999-2222-3333-4444-555555555555"
        regional = f"arn:aws:wafv2:us-east-1:{ACCOUNT}:regional/webacl/{WAF_NAME}/{WAF_ID}"
        cases = [
            ("base-url-is-the-function-url", {"base_url": f"https://{LAMBDA_HOST}"}, "CloudFront"),
            ("base-url-trailing-slash", {"base_url": BASE_URL + "/"}, "CloudFront"),
            ("base-url-http", {"base_url": "http://" + CF_HOST}, "CloudFront"),
            ("base-url-other-host", {"base_url": "https://dzzzzzzzzzzzz.cloudfront.net"}, "domainName"),
            ("waf-regional-scope", {"web_acl_arn": regional}, "scope"),
            ("waf-tokyo-region", {"web_acl_arn": regional.replace("us-east-1", "ap-northeast-1").replace("regional", "global")}, "scope"),
            ("waf-other-account", {"web_acl_arn": WAF_ARN.replace(ACCOUNT, OTHER_ACCOUNT)}, "アカウント"),
            ("waf-other-id", {"web_acl_arn": WAF_ARN.replace(WAF_ID, other_acl_id)}, "AWS操作に失敗"),
            ("distribution-other-id", {"distribution_id": "E9ZZZZZZZZZZZZ"}, "AWS操作に失敗"),
            ("oac-other-id", {"oac_id": "E9ZZZZZZZZZZZZ"}, "AWS操作に失敗"),
        ]
        for name, overrides, expect in cases:
            with self.subTest(name):
                self.reset()
                self.blocked(expect, **overrides)
        self.reset()  # 誤ったscope・リージョンのARNでは、WAFのAPIを呼ばない
        self.blocked("scope", web_acl_arn=regional)
        self.assertNotIn("get-web-acl", self.aws.ops())

    def test_api_failures_block_with_zero_mutation(self):
        for op in ("get-distribution", "get-origin-access-control", "get-web-acl", "get-policy"):
            with self.subTest(op):
                self.reset()
                self.aws.fail[op] = dl.AwsError(op, 254)  # AccessDenied等
                self.blocked("AWS操作に失敗")

    def test_equivalent_forms_are_accepted(self):
        def deny_statement(aws):
            aws.policy_doc["Statement"].append({**other_allow("*"), "Sid": "deny", "Effect": "Deny"})

        auth = lambda a, value: statement_condition(a, 0)["StringEquals"].update({"lambda:FunctionUrlAuthType": value})  # noqa: E731
        variants = [
            ("baseline", lambda a: None),
            ("ip-rule-empty-rule-labels", lambda a: a.rules[0].update(RuleLabels=[])),
            ("ip-rule-other-name-and-priority", lambda a: a.rules[0].update(Name="rate-by-ip", Priority=7)),
            ("https-only-viewer", lambda a: a.behavior.update(ViewerProtocolPolicy="https-only")),
            ("method-order", lambda a: a.behavior["AllowedMethods"].update(Items=["OPTIONS", "HEAD", "GET"])),
            ("cached-with-options", lambda a: a.behavior["AllowedMethods"].update(CachedMethods={"Quantity": 3, "Items": ["GET", "HEAD", "OPTIONS"]})),
            ("arn-equals", lambda a: set_conditions(a, {"ArnEquals": {"AWS:SourceArn": DIST_ARN}})),
            ("string-equals-list", lambda a: set_conditions(a, {"StringEquals": {"AWS:SourceArn": [DIST_ARN]}})),
            ("auth-type-in-list", lambda a: auth(a, ["AWS_IAM"])),
            ("source-arn-and-auth-type-in-string-equals", lambda a: statement_condition(a, 0).update(StringEquals={"AWS:SourceArn": DIST_ARN, "lambda:FunctionUrlAuthType": "AWS_IAM"})),
            ("auth-type-condition-absent", lambda a: statement_condition(a, 0).pop("StringEquals")),
            ("firewall-manager-empty", lambda a: a.acl["WebACL"].update(ManagedByFirewallManager=False, PreProcessFirewallManagerRuleGroups=[], PostProcessFirewallManagerRuleGroups=[])),
            ("no-staging-or-continuous-deployment", lambda a: a.dist_config.update(ContinuousDeploymentPolicyId="", Staging=False)),
            ("deny-is-ignored", deny_statement),
            ("url-without-slash", lambda a: a.url.update(FunctionUrl=f"https://{LAMBDA_HOST}")),
            ("unqualified-function-arn", lambda a: a.url.update(FunctionArn=FUNCTION_ARN)),
            # 必須のIP規則1件＋既知の観測グループ（Count）0〜3件。優先順位は前でも後でもよい（Countは終端しない）
            ("observers-before-ip-rule", add_observers),
            ("observers-after-ip-rule-reversed", lambda a: (add_observers(a, first=False), a.rules.reverse())),
            ("observer-ip-reputation-only", lambda a: add_observers(a, OBSERVER_GROUPS[:1])),
            ("observers-two", lambda a: add_observers(a, OBSERVER_GROUPS[1:])),
            ("observer-empty-optional-lists", with_observers(lambda r: observer_group(r).update(ExcludedRules=[], RuleActionOverrides=[], ManagedRuleGroupConfigs=[]))),
        ]
        for name, mutate in variants:
            with self.subTest(name):
                self.reset()
                mutate(self.aws)
                self.assertEqual(self.deploy(), 0)
                self.assertEqual(self.result()["overall"], "success")

    def test_only_read_calls_with_real_api_shapes_and_no_region_before_any_mutation(self):
        add_observers(self.aws, first=False)  # 実際の4規則の形でも、そのまま配布できる
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
        first_mutation = self.aws.ops().index("update-function-code")
        for op in ("get-distribution", "get-origin-access-control", "get-web-acl", "get-policy"):
            self.assertLess(self.aws.ops().index(op), first_mutation)
        self.assertTrue(all("--region" not in call for call in calls))

    def test_the_current_disabled_distribution_stops_only_on_enabled(self):
        add_observers(self.aws, first=False)  # 4規則（IP規則＋観測3種）は受け入れ、無効化だけで止まる
        self.aws.dist_config.update(Enabled=False)
        result = self.blocked("distributionEnabled")
        self.assertNotIn("webAclRules", result["error"])


class WriteEdgeGate(DeployTestCase):
    """書き込みを許可した公開経路（7メソッド）の条件。AWSの変更の前に、すべて満たさなければ止める。

    条件: 管理ポリシー・承認したエッジ関数の関連付けがちょうど1件・LIVEのコードがZIPの中のレビュー済み
    ソースと一致・今回の版と戻し先の版が、ガード（guard=1）と接続元IPの契約（viewerip=1）の両方を持つ。
    """

    def setUp(self):
        super().setUp()
        self.use_artifact("g", guard=True, viewer_ip=True)
        self.arrange()

    def arrange(self, previous=(True, True), write=True, **write_options):
        self.aws = FakeAws()
        self.aws.versions["1"] = self.aws._snapshot(
            "1", dl.make_description(OLD_SHA, OLD_ZIP, "100", guard=previous[0], viewer_ip=previous[1])
        )
        if write:
            enable_write(self.aws, **write_options)

    def test_fully_prepared_write_stage_deploys_and_marks_both_capabilities(self):
        self.assertEqual(self.deploy(), 0)
        self.assertEqual({c[1] for c in self.aws.calls if c[0] == "cloudfront"}, {"get-distribution", "get-origin-access-control", "get-function"})
        self.assertLess(self.aws.ops().index("get-function"), self.aws.ops().index("update-function-code"))
        self.assertTrue(self.aws.versions["2"]["Description"].endswith(";guard=1;viewerip=1"))
        (outfile,) = self.aws.edge_outfiles  # 一時ファイルは削除される
        self.assertFalse(outfile.parent.exists())

    def test_failed_smoke_rolls_back_to_the_marked_previous_version(self):
        self.smoke.outcomes["new"] = False
        self.assertEqual(self.deploy(), 1)
        self.assertEqual((self.aws.alias["FunctionVersion"], self.result()["rollback"]), ("1", "succeeded"))

    def test_the_write_stage_is_blocked_unless_every_condition_holds(self):
        approved = APPROVED_ASSOCIATION
        other = {"FunctionARN": f"arn:aws:cloudfront::{ACCOUNT}:function/other", "EventType": "viewer-request"}
        live_other = b"function handler(e){return e.request;}"

        def artifact(**kwargs):
            def apply(t):
                t.use_artifact("variant", **kwargs)

            return apply

        def drop_associations(t):
            del t.aws.behavior["FunctionAssociations"]

        def tampered_zip(t):  # ZIPとmanifestが同じ改ざんコードを指していても、LIVEと照合するのはZIPの中の写し
            edge = EDGE_SOURCE + b"// extra\n"
            t.use_artifact("tampered", guard=True, viewer_ip=True, edge_entry=edge, manifest_edge_hex=hashlib.sha256(edge).hexdigest())

        def lambda_edge(t):
            t.aws.behavior["LambdaFunctionAssociations"] = {"Quantity": 1, "Items": [{"LambdaFunctionARN": "x"}]}

        marked = lambda guard, ip: (lambda t: t.arrange(previous=(guard, ip)))  # noqa: E731
        associations = lambda items: (lambda t: enable_write(t.aws, associations=items))  # noqa: E731
        live = lambda value: (lambda t: enable_write(t.aws, live=value))  # noqa: E731
        methods = lambda items: (lambda t: enable_write(t.aws, methods=items))  # noqa: E731
        policy = lambda value: (lambda t: t.aws.behavior.update(OriginRequestPolicyId=value))  # noqa: E731
        # (名前, 状態を作る関数, 期待するエラー, LIVEのコードを読まないはずか)
        cases = [
            ("candidate-without-guard", artifact(guard=None), "candidateWithoutWriteGuard", True),
            ("candidate-guard-zero", artifact(guard=False), "candidateWithoutWriteGuard", True),
            ("candidate-without-viewer-ip", artifact(guard=True), "candidateWithoutViewerIpContract", True),
            ("candidate-viewer-ip-zero", artifact(guard=True, viewer_ip=False), "candidateWithoutViewerIpContract", True),
            ("rollback-target-unmarked", marked(False, False), "rollbackTargetWithoutWriteGuard", False),
            ("rollback-target-guard-only", marked(True, False), "rollbackTargetWithoutViewerIpContract", False),
            ("rollback-target-viewer-ip-only", marked(False, True), "rollbackTargetWithoutWriteGuard", False),
            ("no-association", associations([]), "edgeFunctionAssociation", True),
            ("two-associations", associations([approved, other]), "edgeFunctionAssociation", True),
            ("duplicated-approved", associations([approved, dict(approved)]), "edgeFunctionAssociation", True),
            ("other-function", associations([other]), "edgeFunctionAssociation", True),
            ("other-account", associations([{**approved, "FunctionARN": EDGE_ARN.replace(ACCOUNT, OTHER_ACCOUNT)}]), "edgeFunctionAssociation", True),
            ("viewer-response", associations([{**approved, "EventType": "viewer-response"}]), "edgeFunctionAssociation", True),
            ("origin-request", associations([{**approved, "EventType": "origin-request"}]), "edgeFunctionAssociation", True),
            ("extra-field", associations([{**approved, "IncludeBody": True}]), "edgeFunctionAssociation", True),
            ("missing-event-type", associations([{"FunctionARN": EDGE_ARN}]), "edgeFunctionAssociation", True),
            ("associations-missing", drop_associations, "edgeFunctionAssociation", True),
            ("lambda-edge", lambda_edge, "lambdaEdge", False),
            ("custom-origin-request-policy", policy(WAF_ID), "originRequestPolicy", False),
            ("viewer-address-policy", policy("88a5eaf4-2fd4-4709-b370-b4c650ea3fcf"), "originRequestPolicy", False),
            ("partial-methods-with-post", methods({"GET", "HEAD", "OPTIONS", "POST"}), "allowedMethods", True),
            ("partial-methods-without-delete", methods(dl.WRITE_ENABLED_METHODS - {"DELETE"}), "allowedMethods", True),
            ("live-retrieval-fails", live(None), "edgeFunctionRetrieval", False),
            ("live-no-output-file", live("nofile"), "edgeFunctionRetrieval", False),
            ("live-empty", live(b""), "edgeFunctionRetrieval", False),
            ("live-different", live(live_other), "edgeFunctionCodeMismatch", False),
            ("live-plus-a-byte", live(EDGE_SOURCE + b"\n"), "edgeFunctionCodeMismatch", False),
            ("zip-entry-differs-from-live", tampered_zip, "edgeFunctionCodeMismatch", False),
        ]
        for name, change, expect, no_live_read in cases:
            with self.subTest(name):
                self.reset()
                self.use_artifact("g", guard=True, viewer_ip=True)
                self.arrange()
                change(self)
                self.blocked(expect)
                if no_live_read:
                    self.assertNotIn("get-function", self.aws.ops())
        # 承認したエッジ関数が設定されていなければ、書き込み構成は止まる（LIVEも読まない）
        self.arrange()
        self.blocked("edgeFunctionNotConfigured", edge_function_arn=None)
        self.assertNotIn("get-function", self.aws.ops())

    def test_malformed_edge_manifests_are_rejected_before_any_aws_call(self):
        def edit(**changes):
            def apply(t):
                path = t.artifact / "manifest.json"
                data = json.loads(path.read_text())
                for key, value in changes.items():
                    if key == "capabilities":
                        data["capabilities"].update(value)
                    else:
                        data[key] = value
                path.write_text(json.dumps(data))

            return apply

        good = {"file": "edge/viewer-ip.js", "sha256Hex": hashlib.sha256(EDGE_SOURCE).hexdigest()}
        cases = {
            "hash-differs-from-zip": lambda t: t.use_artifact("c", guard=True, viewer_ip=True, manifest_edge_hex="0" * 64),
            "edge-missing": edit(edge=None),
            "edge-not-an-object": edit(edge="edge/viewer-ip.js"),
            "other-file": edit(edge={**good, "file": "edge/other.js"}),
            "no-write-guard": edit(capabilities={"publicWriteGuard": 0}),
        }
        for name, tamper in cases.items():
            with self.subTest(name):
                self.use_artifact(f"edge-{name}", guard=True, viewer_ip=True)
                tamper(self)
                self.arrange()
                self.assertEqual(self.deploy(), 1)
                self.assertEqual(self.aws.calls, [])
                self.assertIn("edgeSource", self.result()["error"])

    def test_read_only_stage_keeps_older_guard_versions_deployable(self):
        for name, previous in (("unmarked", (False, False)), ("guard-only", (True, False))):
            with self.subTest(name):
                self.reset()
                self.arrange(previous=previous, write=False)
                self.assertEqual(self.deploy(), 0)  # 3メソッドのまま、印を付けて配布する
                self.assertTrue(self.aws.versions["2"]["Description"].endswith(";guard=1;viewerip=1"))
                self.assertNotIn("get-function", self.aws.ops())  # 読み取り構成では、エッジ関数を読まない
        self.reset()
        self.arrange(write=False)
        self.use_artifact("plain", guard=None)
        self.assertEqual(self.deploy(), 0)
        self.assertEqual(dl.parse_description(self.aws.versions["2"]["Description"]).keys(), {"source", "zip", "run"})

    def test_read_only_stage_rejects_any_edge_association(self):
        for event in ("viewer-request", "viewer-response"):
            with self.subTest(event):
                self.reset()
                self.arrange(write=False)
                items = [{**APPROVED_ASSOCIATION, "EventType": event}]
                self.aws.behavior["FunctionAssociations"] = {"Quantity": 1, "Items": items}
                self.blocked("edgeFunctions")
                self.assertNotIn("get-function", self.aws.ops())

    def test_a_published_version_is_reused_only_with_the_same_markers(self):
        for markers, ok in ((dict(guard=True, viewer_ip=True), True), (dict(guard=True), False), (dict(), False)):
            with self.subTest(markers):
                self.reset()
                self.arrange()
                self.aws.dedupe_publish = True
                self.aws.versions["2"] = self.aws._snapshot("2", dl.make_description(NEW_SHA, self.zip_hex, "150", **markers))
                self.aws.versions["2"]["CodeSha256"] = code_sha(self.zip_hex)
                self.assertEqual(self.deploy(), 0 if ok else 1)
                self.assertEqual(self.aws.alias["FunctionVersion"], "2" if ok else "1")


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

    def test_get_function_live_writes_the_code_to_a_file_argument_in_us_east_1(self):
        command = self.command(["cloudfront", "get-function", "--name", EDGE_FUNCTION, "--stage", "LIVE", "/tmp/out"])
        self.assertEqual(self.region_of(command), "us-east-1")
        self.assertEqual(
            command[:8], ["aws", "cloudfront", "get-function", "--name", EDGE_FUNCTION, "--stage", "LIVE", "/tmp/out"]
        )

    def test_lambda_uses_tokyo(self):
        command = self.command(["lambda", "get-policy", "--function-name", FUNCTION, "--qualifier", "demo"])
        self.assertEqual(self.region_of(command), "ap-northeast-1")

    def test_caller_cannot_add_a_second_region(self):
        with mock.patch.object(dl.subprocess, "run", side_effect=AssertionError("実行しない")):
            with self.assertRaises(dl.AwsError):
                dl.run_aws(["lambda", "get-alias", "--region", "us-west-2"])



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
        self.assertEqual(actions["cloudfront"], {"cloudfront:GetDistribution", "cloudfront:GetOriginAccessControl", "cloudfront:GetFunction"})
        self.assertEqual(actions["wafv2"], {"wafv2:GetWebACL"})
        self.assertNotIn("iam", actions)
        self.assertNotIn("PassRole", text)
        # リソース指定が未対応のCloudFrontの読み取りだけをワイルドカードにする
        wildcard = {sid for sid, s in by_sid.items() if s["Resource"] == "*"}
        self.assertEqual(wildcard, {"ReadOriginAccessControlsResourceScopingUnsupported"})
        edge = by_sid["ReadTheApprovedViewerRequestFunctionOnly"]
        self.assertEqual(edge["Action"], "cloudfront:GetFunction")
        self.assertEqual(edge["Resource"], "arn:aws:cloudfront::<ACCOUNT_ID>:function/<EDGE_FUNCTION_NAME>")
        self.assertNotIn("OriginRequestPolicy", text)
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

    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "infra" / "cd" / "oidc-trust-policy.json").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "infra" / "cd" / "bootstrap.md").read_text(encoding="utf-8")

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
