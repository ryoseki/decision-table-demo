"""scripts/deploy-lambda.py のオフラインテスト用の共有部品（標準ライブラリのみ）。

偽のAWS（FakeAws）・スモーク確認（FakeSmoke）・成果物の生成・配布実行の土台（DeployTestCase）だけを置く。
このファイルはテストを含まず、`unittest discover` の対象（test*.py）にならない。
これは配布ロジックの確認であり、実際のAWS・Lambdaの動作の検証ではない。
"""

import base64
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
