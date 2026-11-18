#!/usr/bin/env python3
"""承認済みのLambda ZIPを、既存のエイリアス demo へ配布する（標準ライブラリのみ）。

AWS CLI（`aws lambda ...`）をsubprocessで呼ぶ。初回構築・IAM・関数URL・同時実行数・CloudFront・WAFの
変更はしない（infra/cd/bootstrap.md と infra/cd/cloudfront-plan.md の人の操作）。CloudFront・WAF・
予約済み同時実行数（ちょうど3）は読み取り（get-*）だけで、期待と違えばAWSの変更の前に止まる（fail closed）。

流れ: 検証（成果物・公開経路・関数設定・直前の版）→ 現行版のスモーク確認 → コード更新（検証時の
RevisionIdが条件）→ 完了待ち → 設定とコードのハッシュの確認 → 版の発行（完了後のRevisionIdとコードの
ハッシュが条件）→ 発行した版の確認 → エイリアスの切り替え（検証時のRevisionIdが条件・加重なし）→
スモーク確認。新しい版のスモーク確認が失敗したら、こちらの切り替えが返したRevisionIdを条件に、直前の
正常版へ戻して、戻した版のスモーク確認を行う（戻しが成功しても全体は失敗）。

エイリアスの更新結果が確認できない場合（エラー・想定外の応答・外部の変更）と、強制終了・タイムアウトは、
自動では戻さず、失敗として終え、結果JSONの recoveryNote に人の復旧手順を残す（新しい版を指していても
上書きしない）。エイリアスが既に同じソースSHA・ZIPの版を指し、スモーク確認が合格した場合は、変更しない
成功（overall=no-change-success）で、新しい配布の成功とは扱わない。結果は常に --result-file のJSONへ
段階ごとに書き、Lambdaの設定全体や資格情報は出力・保存しない。
`--verify-artifact-only` は成果物の検証だけを行い、AWS CLI・資格情報・通信を使わない。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
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
WRITE_ENABLED_METHODS = ALLOWED_METHODS | {"PUT", "POST", "PATCH", "DELETE"}
# 書き込みを許可した構成（7メソッド）でも、オリジンリクエストポリシーは読み取り構成と同じManagedのまま。
# 接続元IPは、承認したCloudFront Functions（viewer-request）が専用ヘッダー（x-demo-viewer-ip）へ入れる。
# 7メソッドの条件は、承認したエッジ関数の関連付けがちょうど1件（viewer-request）で、そのLIVEのコードが
# ZIPの中のレビュー済みソースと一致すること。3メソッドの読み取り構成は、エッジ関数の関連付けなし。
EDGE_EVENT_TYPE = "viewer-request"
EDGE_ARTIFACT_PATH = "edge/viewer-ip.js"
EDGE_FUNCTION_ARN_RE = re.compile(
    r"arn:aws:cloudfront::(?P<account>[0-9]{12}):function/(?P<name>[A-Za-z0-9_-]{1,64})"
)
# WAFの必須のレートベース規則（実際の接続元IPごと）の閾値。infra/cd/waf-rules.json と同じ値。
# 近似で厳密な保証ではない。全体（CONSTANT）の規則は2026-11-10に要件から外した（受け入れない）。
RATE_WINDOW_SEC = 300
IP_RATE_LIMIT = 300
# 予約済み同時実行数はちょうど3（読み取りで確認するだけ。自動では設定しない）。
# 1から3へ上げたのは、画面のHTML・JS・CSSの並行した読み込みが、コールドスタートで
# 429（TooManyRequests）になり、配布後のスモーク確認が失敗したため（2026-11-15）。
# 関数URLの上限は予約済み同時実行数の10倍のRPS（3なら30RPS、超過は429）で、
# WAFの件数制限や月額の上限の代わりではない（infra/cd/cloudfront-plan.md）。
REQUIRED_RESERVED_CONCURRENCY = 3

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
    r"(?P<guard>;guard=1)?(?P<viewerip>;viewerip=1)?"
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



def make_description(
    source_sha: str, zip_hex: str, run_id: str, guard: bool = False, viewer_ip: bool = False
) -> str:
    """版のdescription。

    guard=True: 公開書き込みのガード・利用枠を含むZIPを、この配布が検証して発行した印。
    viewer_ip=True: 接続元IPの契約（専用ヘッダー x-demo-viewer-ip・ポートなしのIP1つ。
    エッジのソースがZIPに入り、manifestとZIPが一致する）を満たすZIPを、この配布が検証して発行した印。
    """
    text = f"source={source_sha};zip={zip_hex};run={run_id}"
    if guard:
        text += ";guard=1"
    if viewer_ip:
        text += ";viewerip=1"
    return text


def parse_description(text: object) -> dict[str, str] | None:
    if not isinstance(text, str):
        return None
    match = DESCRIPTION_RE.fullmatch(text)
    if not match:
        return None
    return {key: value for key, value in match.groupdict().items() if value is not None}


def config_problems(config: dict, *, require_status: bool = False) -> list[str]:
    """検査対象の設定（関数・版の共通）。環境変数の値は保持せず、問題の名前だけ返す。

    require_status=True なら、State=Active・LastUpdateStatus=Successful の明示的な値を必須にする
    （キーがない・null は問題）。更新の完了後の最終確認で使う。既定は、キーがなければ許す。
    """
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
    allowed_state = ("Active",) if require_status else (None, "Active")
    allowed_update = ("Successful",) if require_status else (None, "Successful")
    if config.get("State") not in allowed_state:
        problems.append("state")
    if config.get("LastUpdateStatus") not in allowed_update:
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
    edge_function_arn: str | None = None  # 承認したエッジ関数（CloudFront Functions）のARN。未設定ならNone

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


def behavior_problems(behavior: object, origin: object, edge_function_arn: str | None = None) -> list[str]:
    if not isinstance(behavior, dict):
        return ["defaultCacheBehavior"]
    problems = []
    if not isinstance(origin, dict) or behavior.get("TargetOriginId") != origin.get("Id"):
        problems.append("targetOrigin")
    if behavior.get("ViewerProtocolPolicy") not in ("redirect-to-https", "https-only"):
        problems.append("viewerProtocol")
    methods = behavior.get("AllowedMethods")
    items = block_items(methods) if isinstance(methods, dict) else None
    method_set = None
    if (
        items is None
        or not all(isinstance(item, str) for item in items)
        or len(items) != len(set(items))
        or set(items) not in (ALLOWED_METHODS, WRITE_ENABLED_METHODS)
    ):
        problems.append("allowedMethods")
    else:
        method_set = set(items)
        cached = block_items(methods.get("CachedMethods"))
        if cached is None or not all(isinstance(item, str) for item in cached):
            problems.append("cachedMethods")
        elif not set(cached) <= ALLOWED_METHODS:
            problems.append("cachedMethods")
    if behavior.get("CachePolicyId") != CACHING_DISABLED_POLICY_ID:
        problems.append("cachePolicy")
    # 両方の構成で、Managedの AllViewerExceptHostHeader のまま（専用のポリシーは使わない）
    if behavior.get("OriginRequestPolicyId") != ALL_VIEWER_EXCEPT_HOST_POLICY_ID:
        problems.append("originRequestPolicy")
    # Lambda@Edgeは使わない
    if block_items(behavior.get("LambdaFunctionAssociations")) != []:
        problems.append("lambdaEdge")
    associations = block_items(behavior.get("FunctionAssociations"))
    if method_set == WRITE_ENABLED_METHODS:
        # 書き込み構成: 承認した viewer-request の関連付けがちょうど1件（他は不可）
        if edge_function_arn is None:
            problems.append("edgeFunctionNotConfigured")
        elif not (
            associations is not None
            and len(associations) == 1
            and isinstance(associations[0], dict)
            and set(associations[0]) == {"FunctionARN", "EventType"}
            and associations[0]["FunctionARN"] == edge_function_arn
            and associations[0]["EventType"] == EDGE_EVENT_TYPE
        ):
            problems.append("edgeFunctionAssociation")
    elif associations != []:
        # 読み取り構成（3メソッド）: エッジ関数の関連付けなし。書き換えによる検証の回避も許さない
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
    problems += behavior_problems(config.get("DefaultCacheBehavior"), origin, path.edge_function_arn)
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


def ip_rate_rule_ok(rule: object) -> bool:
    """必須のIPごとのレート制限（接続元IP・300件/300秒・Block）とちょうど一致するか。

    少しでも違えば False（迂回の余地を残さない）。ActionはBlockだけ（通常の403。Count・Allow・
    Captcha・Challenge・独自応答は不可）。OverrideAction・RuleLabelsも不可。集計キーは実際の接続元IP
    （AggregateKeyType=IP）だけで、ForwardedIPConfig・ScopeDownStatement・独自キーは持たない。
    全体（CONSTANT）などほかのレートベース規則は、この判定でも観測グループの判定でも受け入れない。
    """
    if not isinstance(rule, dict) or not set(rule) <= RULE_KEYS:
        return False
    if rule.get("Action") != {"Block": {}} or rule.get("RuleLabels"):
        return False
    statement = rule.get("Statement")
    if not isinstance(statement, dict) or set(statement) != {"RateBasedStatement"}:
        return False
    rate = statement["RateBasedStatement"]
    if not isinstance(rate, dict) or set(rate) != RATE_KEYS:
        return False
    limit = rate["Limit"]
    window = rate["EvaluationWindowSec"]
    # 300.0 == 300 は真になるため、型も整数に限る
    return (
        rate["AggregateKeyType"] == "IP"
        and type(limit) is int
        and limit == IP_RATE_LIMIT
        and type(window) is int
        and window == RATE_WINDOW_SEC
    )


# 必須のIP規則に加えて、観測専用として残してよいAWSのマネージド規則グループ（プロジェクト独自の判断）。
# コンソールがWeb ACLの自動作成時に付け得るもので、OverrideActionのCountだけで残す。
# Countは終端しない（一致しても評価が続く）ため、優先順位に関係なく必須のIP規則のBlockを迂回しない
# （https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-rule-group-override-options.html）
OBSERVER_VENDOR = "AWS"
OBSERVER_GROUPS = frozenset(
    {
        "AWSManagedRulesAmazonIpReputationList",
        "AWSManagedRulesCommonRuleSet",
        "AWSManagedRulesKnownBadInputsRuleSet",
    }
)
OBSERVER_RULE_KEYS = {"Name", "Priority", "Statement", "OverrideAction", "VisibilityConfig", "RuleLabels"}
# 規則グループ内の除外・動作の上書き・設定。APIが空の配列で返すことがあるため、空なら許す
OBSERVER_OPTIONAL_LISTS = ("ExcludedRules", "RuleActionOverrides", "ManagedRuleGroupConfigs")
# Web ACLの規則の上限（必須のIP規則1件＋観測3件。プロジェクト独自の上限）
WEB_ACL_MAX_RULES = 1 + len(OBSERVER_GROUPS)


def classify_observer(rule: object) -> str | None:
    """観測専用のマネージド規則グループならその名前。少しでも違えば None。

    VendorNameはAWS、名前は OBSERVER_GROUPS のどれか、OverrideActionはちょうど {"Count": {}}。
    Action・別のOverrideAction（None＝グループ内の動作のまま）・ScopeDownStatement・Version・
    空でない除外や動作の上書きは不可（BlockやAllowを持ち込まない・対象を絞らない）。
    """
    if not isinstance(rule, dict) or not set(rule) <= OBSERVER_RULE_KEYS:
        return None
    if rule.get("OverrideAction") != {"Count": {}} or rule.get("RuleLabels"):
        return None
    statement = rule.get("Statement")
    if not isinstance(statement, dict) or set(statement) != {"ManagedRuleGroupStatement"}:
        return None
    group = statement["ManagedRuleGroupStatement"]
    if not isinstance(group, dict) or not set(group) <= {"VendorName", "Name", *OBSERVER_OPTIONAL_LISTS}:
        return None
    if any(group.get(key, []) != [] for key in OBSERVER_OPTIONAL_LISTS):
        return None
    name = group.get("Name")
    # 名前が文字列でないと、frozensetの所属確認がTypeError（unhashable）になり、問題名を出せない
    if group.get("VendorName") != OBSERVER_VENDOR or not isinstance(name, str) or name not in OBSERVER_GROUPS:
        return None
    return name


def web_acl_rules_ok(rules: object) -> bool:
    """必須のIP規則（Block）がちょうど1件、ほかは重複のない観測専用グループ0〜3件（合計1〜4件）。"""
    if not isinstance(rules, list) or not 1 <= len(rules) <= WEB_ACL_MAX_RULES:
        return False
    ip_rules = 0
    observers: list[str] = []
    for rule in rules:
        if ip_rate_rule_ok(rule):
            ip_rules += 1
            continue
        group = classify_observer(rule)
        if group is None:
            # 未知の規則（全体のCONSTANTなど）・未知のベンダー・Count以外のグループは拒否
            return False
        observers.append(group)
    return ip_rules == 1 and len(set(observers)) == len(observers)


def reserved_concurrency_ok(response: object) -> bool:
    """get-function-concurrency の応答が、ちょうど整数の3か（未設定・0・他の値・bool・文字列・小数は不可）。"""
    value = response.get("ReservedConcurrentExecutions") if isinstance(response, dict) else None
    return type(value) is int and value == REQUIRED_RESERVED_CONCURRENCY


def web_acl_problems(response: dict, arn: str, name: str, acl_id: str) -> list[str]:
    acl = response.get("WebACL")
    if not isinstance(acl, dict):
        return ["webAcl"]
    problems = []
    if acl.get("ARN") != arn or acl.get("Name") != name or acl.get("Id") != acl_id:
        problems.append("webAclIdentity")
    if acl.get("DefaultAction") != {"Allow": {}}:
        problems.append("webAclDefaultAction")
    # Firewall Managerが管理・前後に差し込む規則グループは、必須のIP規則より先に評価されてAllowなどで
    # 迂回し得る。初期構成では使わないので、有効・非空なら拒否する（キーがない・空・falseは正常）
    if acl.get("ManagedByFirewallManager") not in (None, False) or any(
        acl.get(key) not in (None, [])
        for key in ("PreProcessFirewallManagerRuleGroups", "PostProcessFirewallManagerRuleGroups")
    ):
        problems.append("webAclFirewallManager")
    if not web_acl_rules_ok(acl.get("Rules")):
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


def manifest_has_write_guard(manifest: dict) -> bool:
    """manifest（ZIPのハッシュと照合済み）が、公開書き込みのガードと利用枠を含むと宣言しているか。"""
    capabilities = manifest.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get("publicWriteGuard") == 1


def manifest_has_viewer_ip_contract(manifest: dict) -> bool:
    """manifest（ZIPのハッシュ・中のエッジのソースと照合済み）が、接続元IPの契約を宣言しているか。"""
    capabilities = manifest.get("capabilities")
    return (
        isinstance(capabilities, dict)
        and capabilities.get("viewerIpContract") == 1
        and capabilities.get("publicWriteGuard") == 1
    )


def zip_edge_source(zip_path: Path) -> bytes:
    """ZIPの中のエッジ関数のソース（レビュー済みのものを、ビルドが入れた写し）。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return archive.read(EDGE_ARTIFACT_PATH)
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        raise DeployError("ZIPにエッジ関数のソースがありません") from None


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
    # 接続元IPの契約を宣言するmanifestは、ZIPの中のエッジのソースのSHA-256をmanifestと照合する。
    # 以後、LIVEのコードと照合するのは、manifestの値ではなく、このZIPの中の写し。
    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("viewerIpContract") == 1:
        edge = manifest.get("edge")
        recorded = edge.get("sha256Hex") if isinstance(edge, dict) else None
        embedded = hashlib.sha256(zip_edge_source(zip_path)).hexdigest()
        if (
            not isinstance(edge, dict)
            or edge.get("file") != EDGE_ARTIFACT_PATH
            or recorded != embedded
            or capabilities.get("publicWriteGuard") != 1
        ):
            raise DeployError("manifest検証に失敗: edgeSource")
    return zip_path, manifest


class Result:
    """結果JSON。段階ごとにアトミックに書き直す（強制終了でも直前までの記録が残る）。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    def update(self, **fields: object) -> None:
        self.data.update(fields)
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
    """`args` は parse_args の結果（入力は validate_inputs で検証済み）。aws・smoke は差し替え可能。"""

    def __init__(self, args: argparse.Namespace, aws: AwsRunner, smoke: SmokeRunner):
        self.a = args
        self.aws = aws
        self.smoke = smoke
        self.code_sha = code_sha_from_hex(args.zip_sha256)
        self.edge_arn = args.edge_function_arn or None
        self.prev: dict[str, str] = {}  # version / source / zip / aliasRevision（検証で確定）
        self.switched_revision: str | None = None  # こちらの切り替えが返したRevisionId（戻しの根拠）
        self.no_change = False
        self.write_enabled = False  # 公開経路が書き込みメソッドを許可しているか（検証で確定）
        self.guard = self.viewer_ip = False  # 今回のZIPが持つ印（manifestで確認）
        self.edge_source: bytes | None = None  # ZIPの中のレビュー済みエッジのソース（契約があるときだけ）
        self.result = Result(
            args.result_file,
            {
                "schema": 2,
                "sourceSHA": args.source_sha,
                "zipSha256": args.zip_sha256,
                "ciRunUrl": args.ci_run_url,
                "runId": args.run_id,
                "runAttempt": args.run_attempt,
                "overall": "in-progress",
                "stage": "start",
                # AWSの変更操作を始める直前に true にする。成功を意味しない。
                "mutationStarted": False,
                "versions": {"previous": None, "new": None},
                # untouched / unknown（切り替え中・結果不明・外部の変更）/ switched / rolled-back
                "alias": "untouched",
                "smoke": {"previous": "not-run", "new": "not-run", "rollback": "not-run"},
                "rollback": "not-needed",
                "note": None,
                "error": None,
                "recoveryNote": None,
            },
        )

    def lam(self, *args: str) -> dict:
        return self.aws(["lambda", *args])

    def stage(self, name: str) -> None:
        self.result.update(stage=name)

    def run_smoke(self, label: str, expected_sha: str) -> bool:
        self.stage(f"smoke-{label}")
        a = self.a
        ok = self.smoke(a.base_url, expected_sha, f"run{a.run_id}-{a.run_attempt}-{label}")
        self.result.data["smoke"][label] = "passed" if ok else "failed"
        self.result.update()
        return ok

    # --- 検証（変更なし） ---
    def validate(self) -> None:
        a = self.a
        self.stage("validate")
        self.zip_path, manifest = load_manifest(a.artifact_dir, a.source_sha, a.zip_sha256)
        self.guard = manifest_has_write_guard(manifest)
        self.viewer_ip = manifest_has_viewer_ip_contract(manifest)
        # LIVEのコードと照合する「レビュー済みのソース」は、ハッシュ検証済みのZIPの中の写し
        self.edge_source = zip_edge_source(self.zip_path) if self.viewer_ip else None

        url = self.lam("get-function-url-config", "--function-name", a.function_name, "--qualifier", ALIAS)
        self._check_public_path(url)

        config = self.lam("get-function-configuration", "--function-name", a.function_name)
        problems = config_problems(config)
        self.function_revision = config.get("RevisionId")
        if not isinstance(self.function_revision, str) or not self.function_revision:
            problems.append("revisionId")
        if problems:
            raise DeployError("関数設定が期待と一致しません: " + ",".join(problems))

        # 予約済み同時実行数は読み取りだけ。自動では設定・修正しない
        if not reserved_concurrency_ok(self.lam("get-function-concurrency", "--function-name", a.function_name)):
            raise DeployError(
                "予約済み同時実行数がちょうど3ではありません（未設定・0を含む）。"
                "配布ヘルパーは同時実行数を変更しません（人が確認してください）"
            )
        layer = self.lam("get-layer-version-by-arn", "--arn", PINNED_LAYER_ARN)
        if (layer.get("Content") or {}).get("CodeSha256") != a.layer_code_sha256:
            raise DeployError("レイヤーのCodeSha256が記録した期待値と一致しません")

        alias = self.lam("get-alias", "--function-name", a.function_name, "--name", ALIAS)
        version, revision = alias.get("FunctionVersion"), alias.get("RevisionId")
        if (alias.get("RoutingConfig") or {}).get("AdditionalVersionWeights"):
            raise DeployError("エイリアスが加重ルーティングです。人が確認してください")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise DeployError("エイリアスの指す版が数値の版ではありません")
        if not isinstance(revision, str) or not revision:
            raise DeployError("エイリアスのRevisionIdを取得できません")
        old = self.lam("get-function-configuration", "--function-name", a.function_name, "--qualifier", version)
        meta = parse_description(old.get("Description"))
        if meta is None:
            raise DeployError("直前の版に利用できる出所情報（description）がありません")
        if old.get("CodeSha256") != code_sha_from_hex(meta["zip"]):
            raise DeployError("直前の版のコードと出所情報のハッシュが一致しません")
        if self.write_enabled:
            # 書き込み可能な公開経路では、今回の版も戻し先の版も、ガード・利用枠（guard=1）と接続元IPの
            # 契約（viewerip=1）の両方の印を持つ版だけを許す。古い版は、読み取り構成（許可メソッド3種）で
            # 配布して印を付けてから書き込みへ切り替える。
            missing = [
                name
                for name, present in (
                    ("candidateWithoutWriteGuard", self.guard),
                    ("candidateWithoutViewerIpContract", self.viewer_ip),
                    ("rollbackTargetWithoutWriteGuard", meta.get("guard")),
                    ("rollbackTargetWithoutViewerIpContract", meta.get("viewerip")),
                )
                if not present
            ]
            if missing:
                raise DeployError(
                    "書き込みを許可した公開経路では、ガード・利用枠と接続元IPの契約を含まない版を"
                    "配布・戻し先にできません: " + ",".join(missing)
                )
        self.prev = {"version": version, "source": meta["source"], "zip": meta["zip"], "aliasRevision": revision}
        self.result.update(versions={"previous": version, "new": None})

    def _check_public_path(self, url: dict) -> None:
        """公開経路（CloudFront → OAC → AWS_IAMの関数URL）を、実APIの応答で読んで検証する（読み取りだけ）。

        1つでも期待と違えば例外で止まる。メッセージには問題の名前だけを入れる（ARNの全文・設定の値は残さない）。
        """
        a = self.a
        if not BASE_URL_RE.fullmatch(a.base_url):
            raise DeployError("公開URLがCloudFrontのオリジンの形式ではありません")
        # オリジンは設定値ではなく、エイリアス demo の関数URLの実値から導く
        url_match = LAMBDA_URL_RE.fullmatch(str(url.get("FunctionUrl", "")))
        arn_match = FUNCTION_ARN_RE.fullmatch(str(url.get("FunctionArn", "")))
        if (
            url.get("AuthType") != "AWS_IAM"
            or url.get("InvokeMode") != "BUFFERED"
            or url_match is None
            or arn_match is None
            or arn_match["name"] != a.function_name
        ):
            raise DeployError("関数URLの設定が期待と一致しません（AWS_IAM・BUFFERED・東京の関数ARNが必要）")
        acl_match = WEB_ACL_ARN_RE.fullmatch(a.web_acl_arn)
        if acl_match is None:
            raise DeployError("WAFのARNがus-east-1のglobal（CLOUDFRONT）scopeの形式ではありません")
        account = arn_match["account"]
        if acl_match["account"] != account:
            raise DeployError("関数とWAFのアカウントが一致しません")
        edge_match = EDGE_FUNCTION_ARN_RE.fullmatch(self.edge_arn) if self.edge_arn else None
        if self.edge_arn and (edge_match is None or edge_match["account"] != account):
            raise DeployError("エッジ関数のARNが不正、または関数とアカウントが一致しません")
        path = PublicPath(
            distribution_id=a.distribution_id,
            oac_id=a.oac_id,
            web_acl_arn=a.web_acl_arn,
            public_host=a.base_url.removeprefix("https://"),
            origin_host=url_match["host"],
            account=account,
            edge_function_arn=self.edge_arn,
        )
        distribution = self.aws(["cloudfront", "get-distribution", "--id", a.distribution_id])
        problems = distribution_problems(distribution, path)
        if not problems:
            behavior = distribution["Distribution"]["DistributionConfig"]["DefaultCacheBehavior"]
            if set(behavior["AllowedMethods"]["Items"]) == WRITE_ENABLED_METHODS:
                self.write_enabled = True
                # 書き込み構成では、LIVEのコードがレビュー済みのソースと一致することを、AWSの変更の前に確認する
                problems += self._live_edge_problems(edge_match["name"] if edge_match else None)
        oac = self.aws(["cloudfront", "get-origin-access-control", "--id", a.oac_id])
        problems += oac_problems(oac, a.oac_id)
        acl = self.aws(
            ["wafv2", "get-web-acl", "--name", acl_match["name"], "--scope", "CLOUDFRONT", "--id", acl_match["id"]]
        )
        problems += web_acl_problems(acl, a.web_acl_arn, acl_match["name"], acl_match["id"])
        policy = self.lam("get-policy", "--function-name", a.function_name, "--qualifier", ALIAS)
        resource_arn = f"arn:aws:lambda:{REGION}:{account}:function:{a.function_name}:{ALIAS}"
        problems += policy_problems(policy, path.distribution_arn, resource_arn)
        if problems:
            raise DeployError("公開経路の設定が期待と一致しません: " + ",".join(sorted(set(problems))))

    def _live_edge_problems(self, name: str | None) -> list[str]:
        """承認したエッジ関数のLIVEのコードを読み（読み取りだけ）、ZIPの中のレビュー済みソースと照合する。

        取得の失敗・空・不一致はすべて拒否（閉じて失敗）。コードの本文は残さず、一時ファイルは成功・失敗の
        どちらでも削除する。未設定・契約のないZIPは、関連付け・版の印の検査が止めるのでここでは読まない。
        """
        if name is None or self.edge_source is None:
            return []
        with tempfile.TemporaryDirectory(prefix="edge-live-") as directory:
            outfile = Path(directory) / "live-function-code"
            try:
                self.aws(["cloudfront", "get-function", "--name", name, "--stage", "LIVE", str(outfile)])
                live = outfile.read_bytes() if outfile.is_file() and not outfile.is_symlink() else b""
            except (DeployError, OSError):
                return ["edgeFunctionRetrieval"]
        if not live:
            return ["edgeFunctionRetrieval"]
        return [] if live == self.edge_source else ["edgeFunctionCodeMismatch"]

    # --- 変更 ---
    def deploy(self) -> None:
        a, prev, r = self.a, self.prev, self.result
        if not self.run_smoke("previous", prev["source"]):
            raise DeployError("現在のエイリアスの版が正常ではないため、変更しませんでした")
        if prev["source"] == a.source_sha and prev["zip"] == a.zip_sha256:
            self.no_change = True
            r.update(
                stage="done",
                note="エイリアスは既に同じソースSHA・ZIPの版を指し、その版のスモーク確認は合格した。"
                "変更なしの成功で、新しい版は発行しておらず、新しい配布の成功ではない。",
            )
            return

        # 検証時に取得したRevisionIdを条件にする。以後に関数が変わっていれば更新は失敗する
        r.update(stage="update-code", mutationStarted=True)
        updated = self.lam(
            "update-function-code", "--function-name", a.function_name,
            "--zip-file", f"fileb://{self.zip_path}", "--revision-id", self.function_revision,
        )  # fmt: skip
        if updated.get("CodeSha256") != self.code_sha:
            raise DeployError("アップロード後のCodeSha256がmanifestと一致しません")
        # この応答のRevisionIdは、完了後と同じとは限らない（実際のCDで不一致を観測）。比較にも発行の条件にも使わない。
        self.stage("wait-update")
        self.lam("wait", "function-updated-v2", "--function-name", a.function_name)
        current = self.lam("get-function-configuration", "--function-name", a.function_name)
        problems = config_problems(current, require_status=True)
        if current.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        completed = current.get("RevisionId")  # 完了後に読み直した値。発行の条件にする
        if not isinstance(completed, str) or not completed:
            problems.append("revisionId")
        if problems:
            raise DeployError("コード更新後の関数の状態が期待と一致しません（発行しません）: " + ",".join(problems))

        self.stage("publish")
        description = make_description(a.source_sha, a.zip_sha256, a.run_id, self.guard, self.viewer_ip)
        published = self.lam(
            "publish-version", "--function-name", a.function_name,
            "--code-sha256", self.code_sha, "--revision-id", completed, "--description", description,
        )  # fmt: skip
        version = published.get("Version")
        if (
            not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
            or published.get("CodeSha256") != self.code_sha
        ):
            raise DeployError("発行された版の内容が期待と一致しません")
        # 発行直後の版は Pending のことがある。Active になるまで待ってから確認する（待機の失敗はエイリアスを動かさず止まる）
        self.stage("wait-published")
        self.lam("wait", "function-active-v2", "--function-name", a.function_name, "--qualifier", version)
        self._verify_published(version)
        r.update(versions={"previous": prev["version"], "new": version})

        # 切り替え。結果を確認できなければ戻さず、失敗として終える（人が復旧する）
        self.stage("switch-alias")
        self.switched_revision = self._set_alias(version, prev["aliasRevision"])
        r.update(alias="switched")
        if not self.run_smoke("new", a.source_sha):
            raise DeployError("新しい版のスモーク確認に失敗しました")

    def _verify_published(self, version: str) -> None:
        """発行された版が、今回のソース・ZIP・検査済みの設定の版と確認できるか（確認できなければ止まる）。

        AWSの公式資料は、コードも設定も変わらないと新しい版を発行しないと述べるが、既存の版を返すとは
        明記していない（未確認）。返された版は、description（run番号以外）・コードのハッシュ・検査対象の
        設定がすべて今回と一致すると確認できたときだけ使う。エイリアスの現在の版や、確認できない版は、
        止まって人が確認する（設定を変えて版を作らせない）。
        """
        a = self.a
        config = self.lam("get-function-configuration", "--function-name", a.function_name, "--qualifier", version)
        meta = parse_description(config.get("Description")) or {}
        problems = config_problems(config)
        if config.get("CodeSha256") != self.code_sha:
            problems.append("codeSha256")
        expected = (a.source_sha, a.zip_sha256, self.guard, self.viewer_ip)
        if (meta.get("source"), meta.get("zip"), bool(meta.get("guard")), bool(meta.get("viewerip"))) != expected:
            problems.append("description")
        if version == self.prev["version"]:
            problems.append("sameAsCurrentVersion")
        if problems:
            raise DeployError(
                f"発行された版 {version} を今回のソース・設定と証明できません: {','.join(problems)}。"
                "手動で確認してください（infra/cd/bootstrap.md の「発行済みの版が既にある場合」）"
            )

    def _set_alias(self, version: str, expected_revision: str) -> str:
        """エイリアス demo を、RevisionIdを条件に、加重なしで指定の版へ向ける。応答を確認できたときだけ新しいRevisionIdを返す。

        呼ぶ前に alias を unknown にして復旧メモを残す（強制終了・タイムアウトでも、結果不明として残る）。
        失敗・想定外の応答・条件の不一致（外部の変更）はすべて例外で、戻しの根拠にはならない。
        """
        a, prev = self.a, self.prev
        self.result.update(
            alias="unknown",
            recoveryNote=(
                "エイリアス demo の更新結果を確認できていない。自動では戻していない。"
                f"`aws lambda get-alias` で指す版を確認する（直前の正常版は版 {prev['version']}、"
                f"今回の版は {self.result.data['versions']['new']}。どちらも削除しない）。"
                "必要なら infra/cd/bootstrap.md の手順で、現在のRevisionIdを条件に `update-alias` で戻す。"
            ),
        )
        try:
            response = self.lam(
                "update-alias", "--function-name", a.function_name, "--name", ALIAS,
                "--function-version", version, "--revision-id", expected_revision,
                "--routing-config", '{"AdditionalVersionWeights":{}}',
            )  # fmt: skip
        except AwsError as error:
            raise DeployError(
                "エイリアスが外部で変更されたため更新されませんでした（実際の版は不明）"
                if error.conflict
                else "エイリアス更新の結果が不明です。人が確認してください"
            ) from None
        revision = response.get("RevisionId")
        if (
            response.get("FunctionVersion") != version
            or (response.get("RoutingConfig") or {}).get("AdditionalVersionWeights")
            or not isinstance(revision, str)
            or not revision
        ):
            raise DeployError("エイリアス更新の応答が期待と一致せず、結果が不明です。人が確認してください")
        return revision

    def rollback(self) -> None:
        """こちらの切り替えが返したRevisionIdを条件に、直前の正常版へ戻し、戻した版のスモーク確認をする。"""
        prev, r = self.prev, self.result
        r.update(stage="rollback", rollback="in-progress")
        try:
            self._set_alias(prev["version"], self.switched_revision)
        except DeployError:
            r.update(rollback="failed")  # alias=unknown と recoveryNote は _set_alias が残している
            return
        r.update(alias="rolled-back")
        if self.run_smoke("rollback", prev["source"]):
            r.update(rollback="succeeded", recoveryNote=None)
        else:
            r.update(rollback="alias-restored-smoke-failed")

    def run(self) -> int:
        failure: str | None = None
        try:
            self.validate()
            self.deploy()
        except DeployError as error:
            failure = str(error)
        except Exception as error:  # 想定外の例外も秘密を出さずに記録する
            failure = f"想定外のエラー: {type(error).__name__}"
        try:
            if failure is not None and self.switched_revision is not None:
                self.rollback()
            if failure is None:
                self.result.update(overall="no-change-success" if self.no_change else "success", stage="done", recoveryNote=None)
            else:
                self.result.update(overall="failed", error=failure)
        except Exception:  # 結果の書き込みに失敗しても、終了コードは失敗にする
            return 1
        return 0 if failure is None else 1


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
    parser.add_argument(
        "--edge-function-arn",
        help="承認したCloudFront Functions（viewer-request）のARN。書き込み構成（7メソッド）では必須、"
        "読み取り構成では未指定でよい",
    )
    parser.add_argument("--result-file", type=Path)
    return parser.parse_args(argv)


# 入力名 -> 完全一致する形式（--verify-artifact-only では先頭の2つだけ検証する）
INPUT_FORMATS = {
    "source_sha": SHA_RE,
    "zip_sha256": HEX64_RE,
    "ci_run_url": re.compile(r"https://github\.com/.+"),
    "run_id": RUN_RE,
    "run_attempt": RUN_RE,
    "function_name": FUNCTION_RE,
    "base_url": BASE_URL_RE,
    "distribution_id": CLOUDFRONT_ID_RE,
    "oac_id": CLOUDFRONT_ID_RE,
    "web_acl_arn": WEB_ACL_ARN_RE,
    "layer_code_sha256": CODE_SHA_RE,
}


def validate_inputs(args: argparse.Namespace) -> None:
    verify_only = args.verify_artifact_only is True
    bad = [
        name.replace("_", "-")
        for name in list(INPUT_FORMATS)[: 2 if verify_only else None]
        if not isinstance(getattr(args, name, None), str) or not INPUT_FORMATS[name].fullmatch(getattr(args, name))
    ]
    if not verify_only:
        edge = args.edge_function_arn
        if edge not in (None, "") and not (isinstance(edge, str) and EDGE_FUNCTION_ARN_RE.fullmatch(edge)):
            bad.append("edge-function-arn")
        if args.result_file is None:
            bad.append("result-file")
    if bad:
        raise SystemExit("入力が不正または不足しています: " + ",".join(bad))
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
    return Deployer(args, run_aws, run_smoke).run()


if __name__ == "__main__":
    sys.exit(main())
