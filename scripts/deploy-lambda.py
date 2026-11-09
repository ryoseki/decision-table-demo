#!/usr/bin/env python3
"""承認済みのLambda ZIPを、既存のエイリアス demo へ配布する（標準ライブラリのみ）。

AWS CLI（`aws lambda ...`）をsubprocessで呼ぶ。初回構築・IAM・関数URL・同時実行数・CloudFront・WAFの
操作はしない（infra/cd/bootstrap.md と infra/cd/cloudfront-plan.md の人の操作）。CloudFront・WAFは
読み取り（get-*）だけで、公開経路（CloudFront → OAC → AWS_IAMの関数URL）が期待どおりか、AWSの変更の前に
検証する。公開URLはCloudFrontだけで、関数URLへの直接の公開はフォールバックにもしない。
変更の前にすべて検証し（fail closed）、変更後は
スモーク確認に失敗したら、こちらが切り替えたエイリアスだけを記録した直前の正常版へ戻す。
結果は常に --result-file のJSONへ書く。Lambdaの設定全体や資格情報は出力・保存しない。
プロセスが強制終了・タイムアウトした場合は戻し処理が走らないことがある。その場合は結果JSONの
recoveryNote と infra/cd/bootstrap.md の手順で人が確認する。
エイリアスが既に同じソースSHA・ZIPの版を指し、既存のスモーク確認が合格した場合は、変更しない
成功（overall=no-change-success）として終え、新しい配布の成功とは扱わない。
`--verify-artifact-only` は成果物の検証だけを行い、AWS CLI・資格情報・通信を使わない。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REGION = "ap-northeast-1"
# CloudFrontとWAF（CLOUDFRONT scope）のAPIはus-east-1。Lambdaは東京。
CF_REGION = "us-east-1"
GLOBAL_SERVICES = ("cloudfront", "wafv2")
ALIAS = "demo"
RUNTIME = "python3.14"
ARCHITECTURE = "x86_64"
HANDLER = "run.sh"
EXEC_WRAPPER = "/opt/bootstrap"
ADAPTER_PORT = "8000"
# レイヤーはこの固定ARNだけを許す（マニフェストが改ざんされても別のレイヤーにしない）。
PINNED_LAYER_ARN = "arn:aws:lambda:ap-northeast-1:753240598075:layer:LambdaAdapterLayerX86:28"

# 初期版は、キャッシュ無効のManagedポリシーだけを許す（アセットのキャッシュは将来案）。
CACHING_DISABLED_POLICY_ID = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_EXCEPT_HOST_POLICY_ID = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
CLOUDFRONT_SERVICE = "cloudfront.amazonaws.com"
INVOKE_ACTIONS = {"lambda:InvokeFunctionUrl", "lambda:InvokeFunction"}
ALLOWED_METHODS = {"GET", "HEAD", "OPTIONS"}
# WAFのレートベース規則の閾値（infra/cd/waf-rules.json と同じ値。近似で厳密な保証ではない）
RATE_WINDOW_SEC = 300
IP_RATE_LIMIT = 300
GLOBAL_RATE_LIMIT = 1500

SHA_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
CODE_SHA_RE = re.compile(r"[A-Za-z0-9+/]{43}=")
VERSION_RE = re.compile(r"[1-9][0-9]{0,17}")
RUN_RE = re.compile(r"[0-9]{1,20}")
FUNCTION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# 公開URL（CD_BASE_URL）はCloudFrontのオリジンだけ。パス・クエリ・ユーザー情報・ポートは不可。
BASE_URL_RE = re.compile(r"https://[a-z0-9]{8,64}\.cloudfront\.net")
# LambdaのオリジンURLは設定せず、get-function-url-config（修飾子 demo）の実値から導く。
LAMBDA_URL_RE = re.compile(
    r"https://(?P<host>[a-z0-9]{8,64}\.lambda-url\.ap-northeast-1\.on\.aws)/?"
)
FUNCTION_ARN_RE = re.compile(
    r"arn:aws:lambda:ap-northeast-1:(?P<account>[0-9]{12}):function:"
    r"(?P<name>[A-Za-z0-9_-]{1,64})(?::demo)?"
)
CLOUDFRONT_ID_RE = re.compile(r"[A-Z0-9]{8,32}")
WEB_ACL_ARN_RE = re.compile(
    r"arn:aws:wafv2:us-east-1:(?P<account>[0-9]{12}):global/webacl/"
    r"(?P<name>[A-Za-z0-9_-]{1,128})/"
    r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
DESCRIPTION_RE = re.compile(
    r"source=(?P<source>[0-9a-f]{40});zip=(?P<zip>[0-9a-f]{64});run=(?P<run>[0-9]{1,20})"
)

AwsRunner = Callable[[list[str]], dict]
SmokeRunner = Callable[[str, str, str], bool]


class DeployError(Exception):
    """検証・操作の失敗。メッセージは秘密を含まないよう作る。"""


class AwsError(DeployError):
    def __init__(self, operation: str, returncode: int, conflict: bool = False):
        super().__init__(f"AWS操作に失敗: {operation} (終了コード {returncode})")
        self.operation = operation
        self.conflict = conflict


class Interrupted(Exception):
    pass


def make_description(source_sha: str, zip_hex: str, run_id: str) -> str:
    return f"source={source_sha};zip={zip_hex};run={run_id}"


def parse_description(text: object) -> dict[str, str] | None:
    if not isinstance(text, str):
        return None
    match = DESCRIPTION_RE.fullmatch(text)
    return match.groupdict() if match else None


def config_problems(config: dict) -> list[str]:
    """検査対象の設定（関数・版の共通）。環境変数の値は保持せず、問題の名前だけ返す。"""
    variables = (config.get("Environment") or {}).get("Variables") or {}
    layers = [layer.get("Arn") for layer in config.get("Layers") or []]
    problems = []
    if config.get("Runtime") != RUNTIME:
        problems.append("runtime")
    if config.get("Architectures") != [ARCHITECTURE]:
        problems.append("architecture")
    if config.get("Handler") != HANDLER:
        problems.append("handler")
    if layers != [PINNED_LAYER_ARN]:
        problems.append("layers")
    if variables.get("AWS_LAMBDA_EXEC_WRAPPER") != EXEC_WRAPPER:
        problems.append("execWrapper")
    if variables.get("PORT") != ADAPTER_PORT:
        problems.append("port")
    if config.get("State") not in (None, "Active"):
        problems.append("state")
    if config.get("LastUpdateStatus") not in (None, "Successful"):
        problems.append("lastUpdateStatus")
    return problems


@dataclass(frozen=True)
class PublicPath:
    """公開経路の期待値。オリジンのホストとアカウントは、実際の関数URLの設定から導く。"""

    distribution_id: str
    oac_id: str
    web_acl_arn: str
    public_host: str  # CD_BASE_URL のホスト（<id>.cloudfront.net）
    origin_host: str  # 関数URLのホスト
    account: str

    @property
    def distribution_arn(self) -> str:
        return f"arn:aws:cloudfront::{self.account}:distribution/{self.distribution_id}"


def strings(value: object) -> list[str] | None:
    """文字列、または文字列だけのリストをリストにする。それ以外は None。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def block_items(block: object) -> list | None:
    """CloudFrontの {Quantity, Items} 形式の項目。形式や個数が不整合なら None（問題として扱う）。"""
    if block is None:
        return []
    if not isinstance(block, dict):
        return None
    items = block.get("Items") or []
    if not isinstance(items, list) or block.get("Quantity", len(items)) != len(items):
        return None
    return items


def origin_problems(origin: object, path: PublicPath) -> list[str]:
    if not isinstance(origin, dict):
        return ["origins"]
    problems = []
    custom = origin.get("CustomOriginConfig")
    if origin.get("DomainName") != path.origin_host:
        problems.append("originDomain")
    if origin.get("OriginPath", "") != "":
        problems.append("originPath")
    if (
        not isinstance(custom, dict)
        or custom.get("OriginProtocolPolicy") != "https-only"
        or custom.get("HTTPSPort", 443) != 443
        or origin.get("S3OriginConfig")
    ):
        problems.append("originProtocol")
    if origin.get("OriginAccessControlId") != path.oac_id:
        problems.append("originAccessControl")
    return problems


def behavior_problems(behavior: object, origin: object) -> list[str]:
    if not isinstance(behavior, dict):
        return ["defaultCacheBehavior"]
    problems = []
    if not isinstance(origin, dict) or behavior.get("TargetOriginId") != origin.get("Id"):
        problems.append("targetOrigin")
    if behavior.get("ViewerProtocolPolicy") not in ("redirect-to-https", "https-only"):
        problems.append("viewerProtocol")
    methods = behavior.get("AllowedMethods")
    items = block_items(methods) if isinstance(methods, dict) else None
    if (
        items is None
        or not all(isinstance(item, str) for item in items)
        or len(items) != len(set(items))
        or set(items) != ALLOWED_METHODS
    ):
        problems.append("allowedMethods")
    else:
        cached = block_items(methods.get("CachedMethods"))
        if cached is None or not all(isinstance(item, str) for item in cached):
            problems.append("cachedMethods")
        elif not set(cached) <= ALLOWED_METHODS:
            problems.append("cachedMethods")
    if behavior.get("CachePolicyId") != CACHING_DISABLED_POLICY_ID:
        problems.append("cachePolicy")
    if behavior.get("OriginRequestPolicyId") != ALL_VIEWER_EXCEPT_HOST_POLICY_ID:
        problems.append("originRequestPolicy")
    # エッジでの書き換え（検証の回避）を許さない
    if block_items(behavior.get("LambdaFunctionAssociations")) != []:
        problems.append("lambdaEdge")
    if block_items(behavior.get("FunctionAssociations")) != []:
        problems.append("edgeFunctions")
    return problems


def distribution_problems(response: dict, path: PublicPath) -> list[str]:
    """CloudFront GetDistribution の応答を検査する。問題の名前だけ返す（値は含めない）。"""
    distribution = response.get("Distribution")
    config = distribution.get("DistributionConfig") if isinstance(distribution, dict) else None
    if not isinstance(config, dict):
        return ["distribution"]
    problems = []
    if (
        distribution.get("Id") != path.distribution_id
        or distribution.get("ARN") != path.distribution_arn
    ):
        problems.append("distributionIdentity")
    if distribution.get("Status") != "Deployed":
        problems.append("distributionStatus")
    if config.get("Enabled") is not True:
        problems.append("distributionEnabled")
    if distribution.get("DomainName") != path.public_host:
        problems.append("domainName")
    if config.get("WebACLId") != path.web_acl_arn:
        problems.append("webAclAssociation")
    # ルートはアプリ（HTML）のまま。ルートオブジェクトの差し替え・404のSPA変換はしない
    if config.get("DefaultRootObject", "") != "":
        problems.append("defaultRootObject")
    if block_items(config.get("CacheBehaviors")) != []:
        problems.append("cacheBehaviors")
    if block_items(config.get("CustomErrorResponses")) != []:
        problems.append("errorResponses")
    if block_items(config.get("OriginGroups")) != []:
        problems.append("originGroups")
    # 継続的デプロイ（ステージング）の経路は、本番と別の設定でトラフィックが流れ得る。初期構成では使わない
    if config.get("ContinuousDeploymentPolicyId") not in (None, ""):
        problems.append("continuousDeployment")
    if config.get("Staging") not in (None, False):
        problems.append("stagingDistribution")
    origins = block_items(config.get("Origins"))
    origin = origins[0] if origins is not None and len(origins) == 1 else None
    problems += origin_problems(origin, path)
    problems += behavior_problems(config.get("DefaultCacheBehavior"), origin)
    return problems


def oac_problems(response: dict, oac_id: str) -> list[str]:
    oac = response.get("OriginAccessControl")
    config = oac.get("OriginAccessControlConfig") if isinstance(oac, dict) else None
    if not isinstance(config, dict):
        return ["originAccessControl"]
    problems = []
    if oac.get("Id") != oac_id:
        problems.append("oacIdentity")
    if config.get("OriginAccessControlOriginType") != "lambda":
        problems.append("oacOriginType")
    if config.get("SigningBehavior") != "always":
        problems.append("oacSigningBehavior")
    if config.get("SigningProtocol") != "sigv4":
        problems.append("oacSigningProtocol")
    return problems


RULE_KEYS = {"Name", "Priority", "Statement", "Action", "VisibilityConfig", "RuleLabels"}
RATE_KEYS = {"Limit", "EvaluationWindowSec", "AggregateKeyType"}


def scope_down_ok(statement: object) -> bool:
    """全体制限の対象は、URIパスが "/" で始まるもの（ほぼすべてのリクエスト）だけ。変換なし。"""
    if not isinstance(statement, dict) or set(statement) != {"ByteMatchStatement"}:
        return False
    inner = statement["ByteMatchStatement"]
    return (
        isinstance(inner, dict)
        and set(inner) == {"SearchString", "FieldToMatch", "TextTransformations", "PositionalConstraint"}
        # CLIのJSON出力ではblobがbase64（"/" は "Lw=="）になることがある。どちらも同じ "/" の意味
        and inner["SearchString"] in ("/", "Lw==")
        and inner["FieldToMatch"] == {"UriPath": {}}
        and inner["TextTransformations"] == [{"Priority": 0, "Type": "NONE"}]
        and inner["PositionalConstraint"] == "STARTS_WITH"
    )


def classify_rule(rule: object) -> str | None:
    """期待する2規則のどちらか（"ip" / "global"）。少しでも違えば None（迂回の余地を残さない）。"""
    if not isinstance(rule, dict) or not set(rule) <= RULE_KEYS:
        return None
    # ActionはBlockだけ（通常の403。Count・Allow・Captcha・Challenge・独自応答は不可）。OverrideActionも不可
    if rule.get("Action") != {"Block": {}} or rule.get("RuleLabels"):
        return None
    statement = rule.get("Statement")
    if not isinstance(statement, dict) or set(statement) != {"RateBasedStatement"}:
        return None
    rate = statement["RateBasedStatement"]
    if not isinstance(rate, dict) or rate.get("EvaluationWindowSec") != RATE_WINDOW_SEC:
        return None
    key = rate.get("AggregateKeyType")
    if key == "IP" and rate.get("Limit") == IP_RATE_LIMIT and set(rate) == RATE_KEYS:
        return "ip"  # 実際の接続元IP。ForwardedIPConfigも、対象を絞るScopeDownもなし
    if (
        key == "CONSTANT"
        and rate.get("Limit") == GLOBAL_RATE_LIMIT
        and set(rate) == RATE_KEYS | {"ScopeDownStatement"}
        and scope_down_ok(rate["ScopeDownStatement"])
    ):
        return "global"
    return None


def web_acl_problems(response: dict, arn: str, name: str, acl_id: str) -> list[str]:
    acl = response.get("WebACL")
    if not isinstance(acl, dict):
        return ["webAcl"]
    problems = []
    if acl.get("ARN") != arn or acl.get("Name") != name or acl.get("Id") != acl_id:
        problems.append("webAclIdentity")
    if acl.get("DefaultAction") != {"Allow": {}}:
        problems.append("webAclDefaultAction")
    # Firewall Managerが管理・前後に差し込む規則グループは、期待の2規則より先に評価されてAllowなどで
    # 迂回し得る。初期構成では使わないので、有効・非空なら拒否する（キーがない・空・falseは正常）
    if acl.get("ManagedByFirewallManager") not in (None, False) or any(
        acl.get(key) not in (None, [])
        for key in ("PreProcessFirewallManagerRuleGroups", "PostProcessFirewallManagerRuleGroups")
    ):
        problems.append("webAclFirewallManager")
    rules = acl.get("Rules")
    if not isinstance(rules, list) or sorted(map(str, (classify_rule(r) for r in rules))) != [
        "global",
        "ip",
    ]:
        problems.append("webAclRules")
    return problems


def condition_ok(condition: object, source_arn: str) -> bool:
    """AWS:SourceArn が対象ディストリビューションと完全一致し、ほかの条件は次の2つの限定だけ。

    `aws lambda add-permission` が生成する形（--source-arn は ArnLike の AWS:SourceArn、
    --function-url-auth-type AWS_IAM は StringEquals の lambda:FunctionUrlAuthType、
    --invoked-via-function-url は Bool の lambda:InvokedViaFunctionUrl）に合わせる。
    SourceArn は常に完全一致（ワイルドカード・別のARN・複数値は不可）。認証方式は AWS_IAM だけ
    （NONE・ワイルドカード・複数値は不可）。未知の演算子・キーはすべて拒否する。
    """
    if not isinstance(condition, dict):
        return False
    found = False
    for operator, body in condition.items():
        if not isinstance(body, dict) or not body:
            return False
        if operator in ("ArnLike", "ArnEquals"):
            if set(body) != {"AWS:SourceArn"} or strings(body["AWS:SourceArn"]) != [source_arn]:
                return False
            found = True
        elif operator == "StringEquals":
            for key, value in body.items():
                if key == "AWS:SourceArn" and strings(value) == [source_arn]:
                    found = True
                elif key == "lambda:FunctionUrlAuthType" and strings(value) == ["AWS_IAM"]:
                    continue  # 関数URLの認証方式の限定（AWS_IAMだけ）
                else:
                    return False
        elif operator != "Bool" or body != {"lambda:InvokedViaFunctionUrl": "true"}:
            return False
    return found


def policy_problems(response: dict, source_arn: str, resource_arn: str) -> list[str]:
    """エイリアス demo のリソースポリシー。特定のCloudFrontだけに、2つの呼び出し権限を許す。"""
    try:
        document = json.loads(response.get("Policy"))
    except (TypeError, ValueError):
        return ["policyFormat"]
    statements = document.get("Statement") if isinstance(document, dict) else None
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list) or not statements:
        return ["policyStatements"]
    problems: set[str] = set()
    seen: set[str] = set()
    for statement in statements:
        if not isinstance(statement, dict):
            problems.add("policyFormat")
            continue
        effect = statement.get("Effect")
        if effect == "Deny":
            continue  # 拒否は許可を狭めるだけ
        if effect != "Allow":
            problems.add("policyEffect")
            continue
        if any(key in statement for key in ("NotPrincipal", "NotAction", "NotResource")):
            problems.add("policyNegation")
        principal = statement.get("Principal")
        if (
            not isinstance(principal, dict)
            or set(principal) != {"Service"}
            or strings(principal["Service"]) != [CLOUDFRONT_SERVICE]
        ):
            problems.add("policyPrincipal")  # ワイルドカード・他のサービス・他者のAllowを拒否
        actions = strings(statement.get("Action"))
        if not actions or not set(actions) <= INVOKE_ACTIONS:
            problems.add("policyAction")
        else:
            seen.update(actions)
        if strings(statement.get("Resource")) != [resource_arn]:
            problems.add("policyResource")
        if not condition_ok(statement.get("Condition"), source_arn):
            problems.add("policyCondition")
    if seen != INVOKE_ACTIONS:
        problems.add("policyActions")
    return sorted(problems)


def code_sha_from_hex(zip_hex: str) -> str:
    return base64.b64encode(bytes.fromhex(zip_hex)).decode("ascii")


def sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.digest()


def load_manifest(artifact_dir: Path, source_sha: str, zip_hex: str) -> tuple[Path, dict]:
    """成果物のmanifestとZIPを検証する。ビルド結果の出力（期待値）とも照合する。"""
    zip_name = f"lambda-{source_sha}.zip"
    if not artifact_dir.is_dir():
        raise DeployError("成果物ディレクトリがありません")
    names = {entry.name for entry in artifact_dir.iterdir()}
    if names != {"manifest.json", zip_name}:
        raise DeployError("成果物の構成が想定と異なります")
    manifest_path = artifact_dir / "manifest.json"
    zip_path = artifact_dir / zip_name
    for path in (manifest_path, zip_path):
        if path.is_symlink() or not path.is_file():
            raise DeployError("成果物は通常のファイルである必要があります")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeployError("manifest.json を読み込めません") from error
    if not isinstance(manifest, dict):
        raise DeployError("manifest.json の形式が不正です")
    zip_info = manifest.get("zip")
    adapter = manifest.get("adapter")
    if not isinstance(zip_info, dict) or not isinstance(adapter, dict):
        raise DeployError("manifest.json の zip/adapter が不正です")

    digest = sha256_file(zip_path)
    checks = {
        "schema": manifest.get("schema") == 1,
        "sourceSHA": manifest.get("sourceSHA") == source_sha,
        "dirty": manifest.get("dirty") is False,
        "releaseEligible": manifest.get("releaseEligible") is True,
        "runtime": manifest.get("runtime") == RUNTIME,
        "architecture": manifest.get("architecture") == ARCHITECTURE,
        "layerArn": adapter.get("layerArn") == PINNED_LAYER_ARN,
        "execWrapper": adapter.get("execWrapper") == EXEC_WRAPPER,
        "handler": adapter.get("handler") == HANDLER,
        "port": adapter.get("port") == int(ADAPTER_PORT),
        "zipFile": zip_info.get("file") == zip_name,
        "zipBytes": zip_info.get("bytes") == zip_path.stat().st_size,
        "zipHex": zip_info.get("sha256Hex") == digest.hex() == zip_hex,
        "zipCodeSha": zip_info.get("codeSha256") == base64.b64encode(digest).decode("ascii"),
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    if failed:
        raise DeployError("manifest検証に失敗: " + ",".join(failed))
    return zip_path, manifest


class Result:
    """結果JSON。段階ごとにアトミックに書き直す（強制終了でも直前までの記録が残る）。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    def update(self, **fields: object) -> None:
        self.data.update(fields)
        self.data["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.flush()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".result-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


class Deployer:
    def __init__(
        self,
        *,
        artifact_dir: Path,
        source_sha: str,
        zip_hex: str,
        ci_run_url: str,
        run_id: str,
        run_attempt: str,
        function_name: str,
        base_url: str,
        distribution_id: str,
        oac_id: str,
        web_acl_arn: str,
        layer_code_sha256: str,
        result_file: Path,
        aws: AwsRunner,
        smoke: SmokeRunner,
    ):
        self.artifact_dir = artifact_dir
        self.source_sha = source_sha
        self.zip_hex = zip_hex
        self.run_id = run_id
        self.run_attempt = run_attempt
        self.function = function_name
        self.base_url = base_url  # 公開URL（CloudFront）。スモーク確認もここで行う
        self.distribution_id = distribution_id
        self.oac_id = oac_id
        self.web_acl_arn = web_acl_arn
        self.layer_code_sha256 = layer_code_sha256
        self.aws = aws
        self.smoke = smoke
        self.code_sha = code_sha_from_hex(zip_hex)
        self.prev: dict | None = None  # version/source/zip/aliasRevision
        self.new_version: str | None = None
        self.alias_rev: str | None = None  # こちらの最新の更新が返したRevisionId
        self.switched = False
        self.function_revision: str | None = None  # 検証時の設定のRevisionId
        self.already_current = False
        self.result = Result(
            result_file,
            {
                "schema": 1,
                "sourceSHA": source_sha,
                "zipSha256": zip_hex,
                "ciRunUrl": ci_run_url,
                "runId": run_id,
                "runAttempt": run_attempt,
                "overall": "in-progress",
                "stage": "start",
                "validation": "not-run",
                # CloudFront・OAC・WAF・関数のリソースポリシーの読み取り検証（変更なし）の結果
                "publicPath": "not-run",
                "deployment": "not-started",
                # AWSの変更操作を始める直前に true にする。成功を意味せず、結果の書き込み自体が
                # 失敗した場合は記録されない（その場合の有無は未確認）。
                "mutationStarted": False,
                "previousSmoke": "not-run",
                "smoke": "not-run",
                "rollback": "not-needed",
                "versions": {"previous": None, "new": None, "reused": False},
                "note": None,
                "alias": {"name": ALIAS, "version": None, "revisionId": None, "state": "untouched"},
                "lastGood": None,
                "error": None,
                "recoveryNote": None,
            },
        )

    # --- AWS呼び出し（結果は必要な項目だけ取り出す） ---
    def _lambda(self, *args: str) -> dict:
        return self.aws(["lambda", *args])

    # CloudFront・WAFは読み取り（get-*）だけ。書き込みの呼び出しは作らない。
    def _cloudfront(self, *args: str) -> dict:
        return self.aws(["cloudfront", *args])

    def _wafv2(self, *args: str) -> dict:
        return self.aws(["wafv2", *args])

    def _versions(self, new: str | None = None, reused: bool = False) -> dict:
        previous = self.prev["version"] if self.prev else None
        return {"previous": previous, "new": new, "reused": reused}

    def _report_id(self, label: str) -> str:
        return f"run{self.run_id}-{self.run_attempt}-{label}"

    def _alias_state(self, response: dict) -> tuple[str, str]:
        version = response.get("FunctionVersion")
        revision = response.get("RevisionId")
        weights = (response.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
        if weights:
            raise DeployError("エイリアスが加重ルーティングです。人が確認してください")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise DeployError("エイリアスの指す版が数値の版ではありません")
        if not isinstance(revision, str) or not revision:
            raise DeployError("エイリアスのRevisionIdを取得できません")
        return version, revision

    # --- 検証（変更なし） ---
    def _validate_public_path(self, url: dict) -> None:
        """公開経路（CloudFront → OAC → AWS_IAMの関数URL）を、実APIの応答で読んで検証する。

        読み取りだけで、AWSの変更の前に呼ぶ。1つでも期待と違えば例外で止まる。問題は名前だけを
        メッセージに入れる（アカウントを含むARNの全文・設定の値は結果に残さない）。
        """
        self.result.update(stage="validate-public-path")
        try:
            self._check_public_path(url)
        except DeployError:
            self.result.update(publicPath="failed")
            raise
        self.result.update(publicPath="passed")

    def _check_public_path(self, url: dict) -> None:
        # 公開URLはCloudFrontだけ。関数URL（lambda-url）を公開URLにするフォールバックはない
        if not BASE_URL_RE.fullmatch(self.base_url):
            raise DeployError("公開URLがCloudFrontのオリジンの形式ではありません")
        # オリジンは設定値ではなく、エイリアス demo の関数URLの実値から導く
        url_match = LAMBDA_URL_RE.fullmatch(str(url.get("FunctionUrl", "")))
        arn_match = FUNCTION_ARN_RE.fullmatch(str(url.get("FunctionArn", "")))
        if (
            url.get("AuthType") != "AWS_IAM"
            or url.get("InvokeMode") != "BUFFERED"
            or url_match is None
            or arn_match is None
            or arn_match["name"] != self.function
        ):
            raise DeployError(
                "関数URLの設定が期待と一致しません（AWS_IAM・BUFFERED・東京の関数ARNが必要）"
            )
        acl_match = WEB_ACL_ARN_RE.fullmatch(self.web_acl_arn)
        if acl_match is None:
            raise DeployError("WAFのARNがus-east-1のglobal（CLOUDFRONT）scopeの形式ではありません")
        account = arn_match["account"]
        if acl_match["account"] != account:
            raise DeployError("関数とWAFのアカウントが一致しません")
        path = PublicPath(
            distribution_id=self.distribution_id,
            oac_id=self.oac_id,
            web_acl_arn=self.web_acl_arn,
            public_host=self.base_url.removeprefix("https://"),
            origin_host=url_match["host"],
            account=account,
        )
        problems: list[str] = []
        distribution = self._cloudfront("get-distribution", "--id", self.distribution_id)
        problems += distribution_problems(distribution, path)
        oac = self._cloudfront("get-origin-access-control", "--id", self.oac_id)
        problems += oac_problems(oac, self.oac_id)
        acl = self._wafv2(
            "get-web-acl",
            "--name",
            acl_match["name"],
            "--scope",
            "CLOUDFRONT",
            "--id",
            acl_match["id"],
        )
        problems += web_acl_problems(acl, self.web_acl_arn, acl_match["name"], acl_match["id"])
        policy = self._lambda("get-policy", "--function-name", self.function, "--qualifier", ALIAS)
        resource_arn = f"arn:aws:lambda:{REGION}:{account}:function:{self.function}:{ALIAS}"
        problems += policy_problems(policy, path.distribution_arn, resource_arn)
        if problems:
            raise DeployError("公開経路の設定が期待と一致しません: " + ",".join(sorted(set(problems))))

    def validate(self) -> None:
        self.result.update(stage="validate-manifest")
        self.zip_path, self.manifest = load_manifest(
            self.artifact_dir, self.source_sha, self.zip_hex
        )
        self.result.update(stage="validate-aws")

        url = self._lambda(
            "get-function-url-config", "--function-name", self.function, "--qualifier", ALIAS
        )
        self._validate_public_path(url)

        config = self._lambda("get-function-configuration", "--function-name", self.function)
        problems = config_problems(config)
        revision = config.get("RevisionId")
        if not isinstance(revision, str) or not revision:
            problems.append("revisionId")
        del config  # 環境変数を保持しない
        if problems:
            raise DeployError("関数設定が期待と一致しません: " + ",".join(problems))
        # 検証した設定のRevisionId。コード更新はこの版を条件にする（検証後に変わっていたら更新は失敗する）
        self.function_revision = revision

        layer = self._lambda("get-layer-version-by-arn", "--arn", PINNED_LAYER_ARN)
        if (layer.get("Content") or {}).get("CodeSha256") != self.layer_code_sha256:
            raise DeployError("レイヤーのCodeSha256が記録した期待値と一致しません")

        alias = self._lambda("get-alias", "--function-name", self.function, "--name", ALIAS)
        version, alias_revision = self._alias_state(alias)
        old = self._lambda(
            "get-function-configuration", "--function-name", self.function, "--qualifier", version
        )
        meta = parse_description(old.get("Description"))
        if meta is None:
            raise DeployError("直前の版に利用できる出所情報（description）がありません")
        if old.get("CodeSha256") != code_sha_from_hex(meta["zip"]):
            raise DeployError("直前の版のコードと出所情報のハッシュが一致しません")
        self.prev = {
            "version": version,
            "sourceSHA": meta["source"],
            "zipSha256": meta["zip"],
            "aliasRevision": alias_revision,
        }
        self.alias_rev = alias_revision
        self.result.update(
            validation="passed",
            versions=self._versions(),
            alias={
                "name": ALIAS,
                "version": version,
                "revisionId": alias_revision,
                "state": "untouched",
            },
        )

    def check_previous(self) -> None:
        assert self.prev is not None
        self.result.update(stage="previous-smoke")
        ok = self.smoke(self.base_url, self.prev["sourceSHA"], self._report_id("previous"))
        self.result.update(previousSmoke="passed" if ok else "failed")
        if not ok:
            raise DeployError("現在のエイリアスの版が正常ではないため、変更しませんでした")
        self.result.update(
            lastGood={
                "version": self.prev["version"],
                "sourceSHA": self.prev["sourceSHA"],
                "zipSha256": self.prev["zipSha256"],
                "smoke": "passed",
            }
        )

    # --- 変更 ---
    def already_current_check(self) -> bool:
        """直前の版が今回と同じソース・ZIPで、全項目のスモーク確認も合格済みなら、変更しない。"""
        assert self.prev is not None
        if (
            self.prev["sourceSHA"] != self.source_sha
            or self.prev["zipSha256"] != self.zip_hex
            or self.result.data["previousSmoke"] != "passed"
        ):
            return False
        self.already_current = True
        self.result.update(
            stage="done",
            deployment="already-current",
            smoke="not-run",
            versions=self._versions(),
            note=(
                "エイリアスは既に同じソースSHA・ZIPの版を指し、その版の既存スモーク確認は合格した。"
                "変更なしの成功で、新しい版は発行しておらず、新しい配布の成功ではない。"
            ),
        )
        return True

    # --- 変更 ---
    def mutate(self) -> None:
        assert self.prev is not None and self.function_revision is not None
        prev = self.prev
        self.result.update(
            deployment="in-progress",
            recoveryNote=(
                "強制終了・タイムアウトの場合は戻し処理が走らない。エイリアス demo の指す版を確認し、"
                f"直前の正常版（版 {prev['version']}）へ戻すか、infra/cd/bootstrap.md の公開停止を使う。"
            ),
        )
        self.result.update(stage="update-code", mutationStarted=True)
        # 検証時に取得したRevisionIdを条件にする。以後に設定が変わっていれば更新は失敗する
        updated = self._lambda(
            "update-function-code",
            "--function-name",
            self.function,
            "--zip-file",
            f"fileb://{self.zip_path}",
            "--revision-id",
            self.function_revision,
        )
        own_revision = updated.get("RevisionId")
        if updated.get("CodeSha256") != self.code_sha:
            raise DeployError("アップロード後のCodeSha256がmanifestと一致しません")
        if not isinstance(own_revision, str) or not own_revision:
            raise DeployError("コード更新の応答にRevisionIdがありません")

        self.result.update(stage="wait-update")
        self._lambda("wait", "function-updated-v2", "--function-name", self.function)
        current = self._lambda("get-function-configuration", "--function-name", self.function)
        problems = config_problems(current)
        if current.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        # こちらの更新のRevisionIdのまま（他者の変更が入っていない）ことを、発行の条件にする
        if current.get("RevisionId") != own_revision:
            problems.append("revisionId")
        del current
        if problems:
            raise DeployError(
                "コード更新後の関数の状態が期待と一致しません（発行しません）: " + ",".join(problems)
            )

        self.result.update(stage="publish")
        published = self._lambda(
            "publish-version",
            "--function-name",
            self.function,
            "--code-sha256",
            self.code_sha,
            "--revision-id",
            own_revision,
            "--description",
            make_description(self.source_sha, self.zip_hex, self.run_id),
        )
        version = published.get("Version")
        if (
            not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
            or published.get("CodeSha256") != self.code_sha
        ):
            raise DeployError("発行された版の内容が期待と一致しません")
        # 発行直後の版は Pending のことがある（既存の版を再利用する場合も同じ手順）。Active になるまで
        # 待ってから確認する。待機はAWS CLIの上限つき。失敗・タイムアウトはエイリアスを動かさず止まる。
        self.result.update(stage="wait-published")
        self._lambda(
            "wait",
            "function-active-v2",
            "--function-name",
            self.function,
            "--qualifier",
            version,
        )
        reused = self._verify_published(version)
        self.new_version = version
        self.result.update(versions=self._versions(version, reused))

        self.result.update(stage="switch-alias")
        try:
            switched = self._lambda(
                "update-alias",
                "--function-name",
                self.function,
                "--name",
                ALIAS,
                "--function-version",
                version,
                "--revision-id",
                prev["aliasRevision"],
                "--routing-config",
                '{"AdditionalVersionWeights":{}}',
            )
        except AwsError as error:
            self._unknown_alias_outcome(error)
            raise
        revision = switched.get("RevisionId")
        if (
            switched.get("FunctionVersion") != version
            or (switched.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            self._unknown_alias_outcome(None)
        self._mark_switched(version, revision)

        self.result.update(stage="new-smoke")
        ok = self.smoke(self.base_url, self.source_sha, self._report_id("new"))
        self.result.update(smoke="passed" if ok else "failed")
        if not ok:
            raise DeployError("新しい版のスモーク確認に失敗しました")
        self.result.update(
            lastGood={
                "version": version,
                "sourceSHA": self.source_sha,
                "zipSha256": self.zip_hex,
                "smoke": "passed",
            }
        )

    def _verify_published(self, version: str) -> bool:
        """発行（または既存の同一内容）の版を読み、今回のソース・ZIP・検査済みの設定と証明できるか確認する。

        AWSは、コードも設定も前回の版から変わらないと新しい版を作らず既存の版を返す。再利用は、
        descriptionのソースSHA・ZIPのハッシュ、コードのハッシュ、検査対象の設定がすべて一致すると
        確認できたときだけ。それ以外は閉じて止まり、手動確認とする（設定の変更で版を作ることはしない）。
        戻り値: 既存の版を再利用したか。
        """
        assert self.prev is not None
        manual = "。手動で確認してください（infra/cd/bootstrap.md の「発行済みの版が既にある場合」）"
        if version == self.prev["version"]:
            raise DeployError("発行された版がエイリアスの現在の版と同じです" + manual)
        config = self._lambda(
            "get-function-configuration", "--function-name", self.function, "--qualifier", version
        )
        meta = parse_description(config.get("Description"))
        problems = config_problems(config)
        if config.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        del config
        if meta is None or meta["source"] != self.source_sha or meta["zip"] != self.zip_hex:
            problems.append("description")
        if problems:
            raise DeployError(
                f"発行された版 {version} を今回のソース・設定と証明できません: "
                + ",".join(problems)
                + manual
            )
        assert meta is not None
        return meta["run"] != self.run_id

    def _mark_switched(self, version: str, revision: str) -> None:
        self.switched = True
        self.alias_rev = revision
        self.result.update(
            deployment="completed",
            alias={"name": ALIAS, "version": version, "revisionId": revision, "state": "switched"},
        )

    def _unknown_alias_outcome(self, error: AwsError | None) -> None:
        """エイリアス更新の結果を、こちらの成功応答で確認できないとき。常に例外で終える。

        読み直して得たRevisionIdは、外部の変更かもしれず、戻しの根拠にしない（自動では戻さず、
        新しい版を指していても上書きしない）。観測した状態は記録だけして、人が確認する。
        """
        assert self.prev is not None
        if error is not None and error.conflict:
            # 条件付き更新が拒否された。こちらは切り替えていないが、実際の版は不明。
            self.result.update(
                alias={
                    "name": ALIAS,
                    "version": None,
                    "revisionId": None,
                    "state": "conflict-not-switched",
                },
                recoveryNote=(
                    "エイリアスが外部で変更されたため切り替えなかった。エイリアスの現在の版は不明"
                    f"（確認前に直前の版 {self.prev['version']} とは仮定しない）。"
                    f"発行済みの版 {self.new_version} は残す。戻し処理はしない。人が確認する。"
                ),
            )
            raise DeployError("エイリアスが外部で変更されたため切り替えませんでした")
        observed = {"version": None, "revisionId": None}
        try:
            current = self._lambda("get-alias", "--function-name", self.function, "--name", ALIAS)
            version, revision = self._alias_state(current)
            observed = {"version": version, "revisionId": revision}
        except DeployError:
            pass
        self.result.update(
            deployment="alias-outcome-unknown",
            alias={"name": ALIAS, **observed, "state": "unknown-outcome"},
            recoveryNote=(
                "エイリアス更新の結果を確認できない。自動の戻しはしていない。"
                "alias の version / revisionId は読み直しの観測値で、こちらの更新の結果とは限らない"
                f"（取得できなければ null）。直前の正常版は版 {self.prev['version']}、"
                f"発行済みの版は {self.new_version}（どちらも削除しない）。"
                "人がエイリアス demo の指す版を確認し、必要なら infra/cd/bootstrap.md の手順で戻す。"
            ),
        )
        raise DeployError("エイリアス更新の結果が不明です。観測した状態を記録しました。人が確認してください")

    def rollback(self) -> None:
        assert self.prev is not None
        prev = self.prev
        self.result.update(stage="rollback", rollback="in-progress")
        if self.alias_rev is None:
            self.result.update(rollback="failed")
            return
        try:
            restored = self._lambda(
                "update-alias",
                "--function-name",
                self.function,
                "--name",
                ALIAS,
                "--function-version",
                prev["version"],
                "--revision-id",
                self.alias_rev,
                "--routing-config",
                '{"AdditionalVersionWeights":{}}',
            )
        except AwsError as error:
            self.result.update(
                rollback="failed",
                recoveryNote=(
                    "戻し処理に失敗"
                    + ("（エイリアスが外部で変更された）" if error.conflict else "")
                    + f"。手動復旧が必要。直前の正常版は版 {prev['version']}、"
                    f"今回の版は {self.new_version}。どちらの版も削除しない。"
                ),
            )
            return
        revision = restored.get("RevisionId")
        if (
            restored.get("FunctionVersion") != prev["version"]
            or (restored.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            self.alias_rev = None
            self.result.update(
                rollback="failed",
                alias={"name": ALIAS, "version": None, "revisionId": None, "state": "unknown-outcome"},
                recoveryNote=(
                    "戻しの応答が期待と一致しない。エイリアスの状態は不明で、スモーク確認はしていない。"
                    f"手動復旧が必要。直前の正常版は版 {prev['version']}、今回の版は {self.new_version}。"
                    "どちらの版も削除しない。"
                ),
            )
            return
        self.alias_rev = revision
        self.result.update(
            alias={
                "name": ALIAS,
                "version": prev["version"],
                "revisionId": self.alias_rev,
                "state": "rolled-back",
            }
        )
        ok = self.smoke(self.base_url, prev["sourceSHA"], self._report_id("rollback"))
        self.result.update(
            rollback="succeeded" if ok else "alias-restored-smoke-failed",
            lastGood=(
                {
                    "version": prev["version"],
                    "sourceSHA": prev["sourceSHA"],
                    "zipSha256": prev["zipSha256"],
                    "smoke": "passed",
                }
                if ok
                else None
            ),
        )

    def run(self) -> int:
        failure: str | None = None
        try:
            self.validate()
            self.check_previous()
            if not self.already_current_check():
                self.mutate()
        except Interrupted:
            failure = "処理が中断されました"
        except DeployError as error:
            failure = str(error)
        except Exception as error:  # 想定外の例外も秘密を出さずに記録する
            failure = f"想定外のエラー: {type(error).__name__}"
        try:
            if failure is not None:
                if self.switched:
                    self.rollback()
                if self.result.data["validation"] != "passed":
                    self.result.data["validation"] = "failed"
                if self.result.data["deployment"] == "in-progress":
                    self.result.data["deployment"] = "failed"
            data = self.result.data
            fresh = (
                failure is None and data["deployment"] == "completed" and data["smoke"] == "passed"
            )
            unchanged = failure is None and self.already_current
            ok = fresh or unchanged
            # 変更なしの成功は、新しい配布の成功と区別する（overall を別の値にする）
            overall = "success" if fresh else "no-change-success" if unchanged else "failed"
            self.result.update(
                overall=overall,
                stage="done" if ok else data["stage"],
                error=failure,
            )
        except Exception:  # 結果の書き込みに失敗しても、終了コードは失敗にする
            return 1
        return 0 if ok else 1


def run_aws(args: list[str]) -> dict:
    operation = " ".join(args[:2])
    if "--region" in args:  # リージョンはここだけで決める（重複させない）
        raise AwsError(operation, -1)
    region = CF_REGION if args and args[0] in GLOBAL_SERVICES else REGION
    command = ["aws", *args, "--region", region, "--output", "json", "--no-cli-pager"]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AwsError(operation, -1) from None
    if completed.returncode != 0:
        conflict = "PreconditionFailed" in completed.stderr
        raise AwsError(operation, completed.returncode, conflict)
    if not completed.stdout.strip():
        return {}
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        raise AwsError(operation, 0) from None
    if not isinstance(data, dict):
        raise AwsError(operation, 0)
    return data


def run_smoke(base_url: str, expected_sha: str, report_id: str) -> bool:
    root = Path(__file__).resolve().parent.parent
    # 資格情報を渡さない最小の環境。CD_ALLOW_LOCAL は常に false。
    keep = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env.update(
        CD_BASE_URL=base_url,
        CD_EXPECTED_SHA=expected_sha,
        CD_ALLOW_LOCAL="false",
        CD_REPORT_ID=report_id,
    )
    try:
        completed = subprocess.run(
            ["bash", "scripts/cd-smoke.sh"], cwd=root, env=env, timeout=600, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--zip-sha256", required=True, help="ビルドジョブの出力したZIPのSHA-256(16進)")
    parser.add_argument(
        "--verify-artifact-only",
        action="store_true",
        help="成果物（manifestとZIP）だけを検証して終える。AWS CLI・資格情報・通信は使わない",
    )
    # 以下は配布には必須（--verify-artifact-only のときは不要）。不足は validate_inputs で拒否する。
    parser.add_argument("--ci-run-url")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--function-name")
    parser.add_argument("--base-url", help="公開URL（https://<id>.cloudfront.net のオリジンのみ）")
    parser.add_argument("--distribution-id", help="CloudFrontのディストリビューションID")
    parser.add_argument("--oac-id", help="Origin Access ControlのID")
    parser.add_argument("--web-acl-arn", help="WAF（CLOUDFRONT scope、us-east-1）のWeb ACLのARN")
    parser.add_argument("--layer-code-sha256")
    parser.add_argument("--result-file", type=Path)
    return parser.parse_args(argv)


DEPLOY_ONLY_ARGS = (
    "ci_run_url",
    "run_id",
    "run_attempt",
    "function_name",
    "base_url",
    "distribution_id",
    "oac_id",
    "web_acl_arn",
    "layer_code_sha256",
    "result_file",
)


def validate_inputs(args: argparse.Namespace) -> None:
    names = ("source_sha", "zip_sha256")
    if getattr(args, "verify_artifact_only", False) is not True:
        names += DEPLOY_ONLY_ARGS
    # 型が不正（文字列でない）な値は、正規表現でTypeErrorにせず、安全に拒否する。
    # 不足（None）は下の「引数が不足」で扱う。result_file は Path のため除く。
    wrong = [
        name.replace("_", "-")
        for name in names
        if name != "result_file"
        and getattr(args, name, None) is not None
        and not isinstance(getattr(args, name), str)
    ]
    if wrong:
        raise SystemExit("入力が不正です: " + ",".join(wrong))
    rules = [
        (SHA_RE.fullmatch(args.source_sha), "source-sha"),
        (HEX64_RE.fullmatch(args.zip_sha256), "zip-sha256"),
    ]
    if getattr(args, "verify_artifact_only", False) is not True:
        missing = [name for name in DEPLOY_ONLY_ARGS if getattr(args, name, None) is None]
        if missing:
            raise SystemExit("引数が不足しています: " + ",".join(missing))
        rules += [
            (RUN_RE.fullmatch(args.run_id), "run-id"),
            (RUN_RE.fullmatch(args.run_attempt), "run-attempt"),
            (FUNCTION_RE.fullmatch(args.function_name), "function-name"),
            (BASE_URL_RE.fullmatch(args.base_url), "base-url"),
            (CLOUDFRONT_ID_RE.fullmatch(args.distribution_id), "distribution-id"),
            (CLOUDFRONT_ID_RE.fullmatch(args.oac_id), "oac-id"),
            (WEB_ACL_ARN_RE.fullmatch(args.web_acl_arn), "web-acl-arn"),
            (CODE_SHA_RE.fullmatch(args.layer_code_sha256), "layer-code-sha256"),
            (args.ci_run_url.startswith("https://github.com/"), "ci-run-url"),
        ]
    bad = [name for ok, name in rules if not ok]
    if bad:
        raise SystemExit("入力が不正です: " + ",".join(bad))
    if os.environ.get("CD_ALLOW_LOCAL", "") not in ("", "false"):
        raise SystemExit("CD_ALLOW_LOCAL は配布では使えません")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    validate_inputs(args)
    if args.verify_artifact_only:
        # AWS・GitHub・通信を使わない。承認後・資格情報の前に、取得した成果物の改ざんを検出する。
        try:
            load_manifest(args.artifact_dir, args.source_sha, args.zip_sha256)
        except DeployError as error:
            print(f"成果物の検証に失敗: {error}", file=sys.stderr)
            return 1
        print("成果物の検証: 成功（manifest・ZIP・ソースSHA・ビルドのハッシュが一致）")
        return 0

    def on_term(signum, frame):  # noqa: ARG001
        raise Interrupted

    signal.signal(signal.SIGTERM, on_term)
    deployer = Deployer(
        artifact_dir=args.artifact_dir,
        source_sha=args.source_sha,
        zip_hex=args.zip_sha256,
        ci_run_url=args.ci_run_url,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        function_name=args.function_name,
        base_url=args.base_url,
        distribution_id=args.distribution_id,
        oac_id=args.oac_id,
        web_acl_arn=args.web_acl_arn,
        layer_code_sha256=args.layer_code_sha256,
        result_file=args.result_file,
        aws=run_aws,
        smoke=run_smoke,
    )
    return deployer.run()


if __name__ == "__main__":
    sys.exit(main())
