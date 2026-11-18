"""公開経路（CloudFront → OAC → AWS_IAMの関数URL → Web ACL・リソースポリシー）の読み取り専用の検証。

`scripts/deploy-lambda.py` が、AWSの変更の前に呼ぶ。AWSの get-* だけを呼び、変更する操作は持たない。
1つでも期待と違えば DeployError で止まる（fail closed）。メッセージには問題の名前だけを入れ、
ARNの全文・設定の値・エッジ関数のコードは残さない。標準ライブラリのみ。
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REGION = "ap-northeast-1"
ALIAS = "demo"

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

AwsRunner = Callable[[list[str]], dict]


class DeployError(Exception):
    """検証・操作の失敗。メッセージは秘密を含まないよう作る。"""


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


@dataclass(frozen=True)
class PublicPathCheck:
    """検証の入力。配布の入力（検証済みの形式）と、関数URLの設定（get-function-url-config の応答）。"""

    function_name: str
    base_url: str
    distribution_id: str
    oac_id: str
    web_acl_arn: str
    function_url: dict = field(repr=False)
    edge_function_arn: str | None  # 承認したエッジ関数のARN。読み取り構成では None
    edge_source: bytes | None = field(repr=False)  # ZIPの中のレビュー済みエッジのソース。契約のないZIPでは None


def check_public_path(aws: AwsRunner, check: PublicPathCheck) -> bool:
    """公開経路を実APIの応答で読んで検証する（読み取りだけ）。書き込みメソッドを許可した構成なら True。

    1つでも期待と違えば DeployError で止まる。メッセージには問題の名前だけを入れる（ARNの全文・設定の値は残さない）。
    """
    if not BASE_URL_RE.fullmatch(check.base_url):
        raise DeployError("公開URLがCloudFrontのオリジンの形式ではありません")
    # オリジンは設定値ではなく、エイリアス demo の関数URLの実値から導く
    url = check.function_url
    url_match = LAMBDA_URL_RE.fullmatch(str(url.get("FunctionUrl", "")))
    arn_match = FUNCTION_ARN_RE.fullmatch(str(url.get("FunctionArn", "")))
    if (
        url.get("AuthType") != "AWS_IAM"
        or url.get("InvokeMode") != "BUFFERED"
        or url_match is None
        or arn_match is None
        or arn_match["name"] != check.function_name
    ):
        raise DeployError("関数URLの設定が期待と一致しません（AWS_IAM・BUFFERED・東京の関数ARNが必要）")
    acl_match = WEB_ACL_ARN_RE.fullmatch(check.web_acl_arn)
    if acl_match is None:
        raise DeployError("WAFのARNがus-east-1のglobal（CLOUDFRONT）scopeの形式ではありません")
    account = arn_match["account"]
    if acl_match["account"] != account:
        raise DeployError("関数とWAFのアカウントが一致しません")
    edge_arn = check.edge_function_arn
    edge_match = EDGE_FUNCTION_ARN_RE.fullmatch(edge_arn) if edge_arn else None
    if edge_arn and (edge_match is None or edge_match["account"] != account):
        raise DeployError("エッジ関数のARNが不正、または関数とアカウントが一致しません")
    path = PublicPath(
        distribution_id=check.distribution_id,
        oac_id=check.oac_id,
        web_acl_arn=check.web_acl_arn,
        public_host=check.base_url.removeprefix("https://"),
        origin_host=url_match["host"],
        account=account,
        edge_function_arn=edge_arn,
    )
    write_enabled = False
    distribution = aws(["cloudfront", "get-distribution", "--id", check.distribution_id])
    problems = distribution_problems(distribution, path)
    if not problems:
        behavior = distribution["Distribution"]["DistributionConfig"]["DefaultCacheBehavior"]
        if set(behavior["AllowedMethods"]["Items"]) == WRITE_ENABLED_METHODS:
            write_enabled = True
            # 書き込み構成では、LIVEのコードがレビュー済みのソースと一致することを、AWSの変更の前に確認する
            problems += live_edge_problems(aws, edge_match["name"] if edge_match else None, check.edge_source)
    oac = aws(["cloudfront", "get-origin-access-control", "--id", check.oac_id])
    problems += oac_problems(oac, check.oac_id)
    acl = aws(["wafv2", "get-web-acl", "--name", acl_match["name"], "--scope", "CLOUDFRONT", "--id", acl_match["id"]])
    problems += web_acl_problems(acl, check.web_acl_arn, acl_match["name"], acl_match["id"])
    policy = aws(["lambda", "get-policy", "--function-name", check.function_name, "--qualifier", ALIAS])
    resource_arn = f"arn:aws:lambda:{REGION}:{account}:function:{check.function_name}:{ALIAS}"
    problems += policy_problems(policy, path.distribution_arn, resource_arn)
    if problems:
        raise DeployError("公開経路の設定が期待と一致しません: " + ",".join(sorted(set(problems))))
    return write_enabled


def live_edge_problems(aws: AwsRunner, name: str | None, edge_source: bytes | None) -> list[str]:
    """承認したエッジ関数のLIVEのコードを読み（読み取りだけ）、ZIPの中のレビュー済みソースと照合する。

    取得の失敗・空・不一致はすべて拒否（閉じて失敗）。コードの本文は残さず、一時ファイルは成功・失敗の
    どちらでも削除する。未設定・契約のないZIPは、関連付け・版の印の検査が止めるのでここでは読まない。
    """
    if name is None or edge_source is None:
        return []
    with tempfile.TemporaryDirectory(prefix="edge-live-") as directory:
        outfile = Path(directory) / "live-function-code"
        try:
            aws(["cloudfront", "get-function", "--name", name, "--stage", "LIVE", str(outfile)])
            live = outfile.read_bytes() if outfile.is_file() and not outfile.is_symlink() else b""
        except (DeployError, OSError):
            return ["edgeFunctionRetrieval"]
    if not live:
        return ["edgeFunctionRetrieval"]
    return [] if live == edge_source else ["edgeFunctionCodeMismatch"]
