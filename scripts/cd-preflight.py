#!/usr/bin/env python3
"""CDの事前確認（標準ライブラリのみ。AWSには接続しない）。fail closed。

  python3 -I scripts/cd-preflight.py source --sha <40桁>        # mainの祖先・CIの成功
  python3 -I scripts/cd-preflight.py environment --approver <ログイン名>
  python3 -I scripts/cd-preflight.py approval --approver <ログイン名> --run-id <ID>

環境変数: GH_TOKEN, GH_REPO（owner/repo）。source は GITHUB_REF も使う。
GitHub REST API は固定の https://api.github.com だけを使う。入力はシェルへ渡さず、
gitはargv配列で呼ぶ。出力に資格情報は含めない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

API_ROOT = "https://api.github.com"
CI_WORKFLOW_FILE = "ci.yml"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
REQUIRED_JOBS = ("frontend", "backend", "e2e")
DEFAULT_ENVIRONMENT = "demo"

SHA_RE = re.compile(r"[0-9a-f]{40}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
ENV_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
RUN_RE = re.compile(r"[0-9]{1,20}")

Api = Callable[[str], object]
Git = Callable[[list[str]], int]


class PreflightError(Exception):
    pass


def github_api(token: str) -> Api:
    def get(path: str) -> object:
        request = urllib.request.Request(
            API_ROOT + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "decision-table-demo-cd-preflight",
            },
        )
        label = path.split("?", 1)[0]
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise PreflightError(f"GitHub APIを取得できません: {label} (HTTP {error.code})") from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise PreflightError(f"GitHub APIを取得できません: {label}") from None

    return get


def run_git(args: list[str]) -> int:
    try:
        return subprocess.run(
            ["git", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        ).returncode
    except OSError:
        return 127


def _dict(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise PreflightError(f"{what} の形式が想定と異なります")
    return value


def check_environment(api: Api, repo: str, environment: str, approver: str) -> list[str]:
    """既存のEnvironmentが合意した保護設定か確認する。自動作成には頼らない。"""
    base = f"/repos/{repo}/environments/{urllib.parse.quote(environment, safe='')}"
    env = _dict(api(base), "Environment")
    notes: list[str] = []

    rules = [
        rule
        for rule in env.get("protection_rules") or []
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    if len(rules) != 1:
        raise PreflightError("required_reviewers の保護ルールがちょうど1つ必要です")
    rule = rules[0]
    reviewers = rule.get("reviewers") or []
    if len(reviewers) != 1:
        raise PreflightError("必須レビュー担当者はちょうど1人（User）である必要があります")
    entry = _dict(reviewers[0], "reviewer")
    person = entry.get("reviewer") if isinstance(entry.get("reviewer"), dict) else {}
    login = person.get("login")
    if entry.get("type") != "User" or not isinstance(login, str):
        raise PreflightError("必須レビュー担当者がUserではありません")
    if login.lower() != approver.lower():
        raise PreflightError("必須レビュー担当者が CD_APPROVER_LOGIN と一致しません")
    # 合意: 唯一の人が起動し承認する。自己承認の禁止は意図して無効（false）にする。
    if rule.get("prevent_self_review") is not False:
        raise PreflightError("prevent_self_review が合意（false）と一致しません")

    bypass = env.get("can_admins_bypass")
    if bypass is True:
        raise PreflightError("管理者による保護の迂回が許可されています")
    if bypass is None:
        notes.append("can_admins_bypass が取得できず未確認")

    policy = env.get("deployment_branch_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("custom_branch_policies") is not True
        or policy.get("protected_branches") is not False
    ):
        raise PreflightError("デプロイブランチはカスタムポリシーのみ（保護ブランチ方式は不可）です")
    listing = _dict(api(f"{base}/deployment-branch-policies?per_page=100"), "ブランチポリシー")
    policies = listing.get("branch_policies")
    if (
        not isinstance(policies, list)
        or len(policies) != 1
        or listing.get("total_count") != 1
        or not isinstance(policies[0], dict)
        or policies[0].get("name") != "main"
        or policies[0].get("type") != "branch"
    ):
        raise PreflightError("デプロイブランチは main（type=branch）の1件だけ許可されます")
    return notes


def check_approval(api: Api, repo: str, run_id: str, environment: str, approver: str) -> None:
    """このrunの承認記録に、指定した承認者の承認があることを確認する。"""
    data = api(f"/repos/{repo}/actions/runs/{run_id}/approvals")
    if not isinstance(data, list):
        raise PreflightError("承認記録の形式が想定と異なります")
    for item in data:
        if not isinstance(item, dict) or item.get("state") != "approved":
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        names = [
            env.get("name")
            for env in item.get("environments") or []
            if isinstance(env, dict)
        ]
        login = user.get("login")
        if isinstance(login, str) and login.lower() == approver.lower() and environment in names:
            return
    raise PreflightError("指定した承認者の承認記録が見つかりません")


def check_source(api: Api, git: Git, repo: str, sha: str, ref: str) -> str:
    """mainの祖先で、そのSHAのCI（push・main）が完全に成功していることを確認する。"""
    if ref != "refs/heads/main":
        raise PreflightError("mainブランチ以外からは起動できません")
    if not SHA_RE.fullmatch(sha):
        raise PreflightError("source_sha は40桁の小文字16進数で指定してください")
    if git(["cat-file", "-e", f"{sha}^{{commit}}"]) != 0:
        raise PreflightError("指定SHAがこのリポジトリのコミットとして存在しません")
    if git(["merge-base", "--is-ancestor", sha, "origin/main"]) != 0:
        raise PreflightError("指定SHAは origin/main の祖先ではありません")

    query = urllib.parse.urlencode(
        {"head_sha": sha, "event": "push", "branch": "main", "per_page": 100}
    )
    listing = _dict(
        api(f"/repos/{repo}/actions/workflows/{CI_WORKFLOW_FILE}/runs?{query}"), "CI run一覧"
    )
    runs = [
        run
        for run in listing.get("workflow_runs") or []
        if isinstance(run, dict)
        and run.get("head_sha") == sha
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("path") == CI_WORKFLOW_PATH
        and isinstance(run.get("id"), int)
        and isinstance(run.get("run_attempt"), int)
        and isinstance(run.get("run_number"), int)
        and isinstance(run.get("head_repository"), dict)
        and str(run["head_repository"].get("full_name", "")).lower() == repo.lower()
    ]
    if not runs:
        raise PreflightError("指定SHAのpushのCI runが見つかりません")
    latest = max(runs, key=lambda run: run["run_number"])
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise PreflightError("指定SHAの最新のCI runが success で完了していません")

    jobs_listing = _dict(
        api(
            f"/repos/{repo}/actions/runs/{latest['id']}/attempts/{latest['run_attempt']}"
            "/jobs?per_page=100"
        ),
        "CI job一覧",
    )
    jobs = [job for job in jobs_listing.get("jobs") or [] if isinstance(job, dict)]
    for name in REQUIRED_JOBS:
        matched = [job for job in jobs if job.get("name") == name]
        if len(matched) != 1:
            raise PreflightError(f"CIのジョブ {name} がちょうど1件ありません")
        if matched[0].get("status") != "completed" or matched[0].get("conclusion") != "success":
            raise PreflightError(f"CIのジョブ {name} が success で完了していません")
    return f"https://github.com/{repo}/actions/runs/{latest['id']}"


def _required_env(name: str, pattern: re.Pattern[str]) -> str:
    value = os.environ.get(name, "")
    if not pattern.fullmatch(value):
        raise PreflightError(f"環境変数 {name} がないか不正です")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    source = sub.add_parser("source")
    source.add_argument("--sha", required=True)
    environment = sub.add_parser("environment")
    environment.add_argument("--approver", required=True)
    environment.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    approval = sub.add_parser("approval")
    approval.add_argument("--approver", required=True)
    approval.add_argument("--run-id", required=True)
    approval.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    args = parser.parse_args(argv)

    try:
        repo = _required_env("GH_REPO", REPO_RE)
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            raise PreflightError("環境変数 GH_TOKEN がありません")
        api = github_api(token)
        if args.command == "source":
            url = check_source(api, run_git, repo, args.sha, os.environ.get("GITHUB_REF", ""))
            print(f"CI run: {url}")
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"ci_run_url={url}\n")
        else:
            if not LOGIN_RE.fullmatch(args.approver):
                raise PreflightError("--approver が不正です")
            if not ENV_RE.fullmatch(args.environment):
                raise PreflightError("--environment が不正です")
            if args.command == "environment":
                notes = check_environment(api, repo, args.environment, args.approver)
                for note in notes:
                    print(f"注意: {note}")
                print("Environmentの保護設定: 合意と一致")
            else:
                if not RUN_RE.fullmatch(args.run_id):
                    raise PreflightError("--run-id が不正です")
                check_approval(api, repo, args.run_id, args.environment, args.approver)
                print("承認記録: 一致")
    except PreflightError as error:
        print(f"事前確認に失敗: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
