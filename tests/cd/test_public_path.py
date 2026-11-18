"""scripts/deploy-lambda.py のオフラインテスト: 公開経路の境界。

CloudFront / OAC / WAF / 関数URL、書き込み許可の条件（LIVEのエッジ関数）、リージョン、ポリシーのテンプレート。
偽のAWSは deploy_test_support。これは配布ロジックの確認であり、実際のAWSの動作の検証ではない。
  python3 -B -I -m unittest discover -s tests/cd
"""

import hashlib
import json
import re
import unittest
from unittest import mock

from deploy_test_support import (
    ACCOUNT,
    ALIAS_ARN,
    APPROVED_ASSOCIATION,
    BASE_URL,
    CF_HOST,
    DIST_ARN,
    DIST_ID,
    DeployTestCase,
    EDGE_ARN,
    EDGE_FUNCTION,
    EDGE_SOURCE,
    FUNCTION,
    FUNCTION_ARN,
    FakeAws,
    LAMBDA_HOST,
    NEW_SHA,
    OAC_ID,
    OLD_SHA,
    OLD_ZIP,
    OTHER_ACCOUNT,
    ROOT,
    WAF_ARN,
    WAF_ID,
    WAF_NAME,
    code_sha,
    dl,
    enable_write,
    make_global_rule,
    make_web_acl,
    visibility,
)


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


if __name__ == "__main__":
    unittest.main()
