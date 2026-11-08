"""scripts/cd-preflight.py のオフラインテスト（偽のGitHub APIを使う。実APIの検証ではない）。
  python3 -B -I -m unittest discover -s tests/cd
"""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("cd_preflight", ROOT / "scripts" / "cd-preflight.py")
pf = importlib.util.module_from_spec(_spec)
sys.modules["cd_preflight"] = pf
_spec.loader.exec_module(pf)

REPO = "example/repo"
SHA = "a" * 40
APPROVER = "sole-approver"


def environment_payload():
    return {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [{"type": "User", "reviewer": {"login": "Sole-Approver"}}],
            }
        ],
        "can_admins_bypass": False,
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    }


def policies_payload():
    return {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}


def ci_run(**overrides):
    run = {
        "id": 500,
        "run_number": 7,
        "run_attempt": 2,
        "head_sha": SHA,
        "event": "push",
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
        "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


def jobs_payload(**conclusions):
    jobs = []
    for name in ("frontend", "backend", "e2e"):
        jobs.append({"name": name, "status": "completed", "conclusion": conclusions.get(name, "success")})
    return {"jobs": jobs}


class FakeApi:
    def __init__(self, routes):
        self.routes = routes
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        for prefix, payload in self.routes.items():
            if path.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return copy.deepcopy(payload)
        raise pf.PreflightError(f"未設定のパス: {path}")


def env_api(env=None, policies=None):
    base = f"/repos/{REPO}/environments/demo"
    return FakeApi(
        {
            f"{base}/deployment-branch-policies": policies or policies_payload(),
            base: env or environment_payload(),
        }
    )


class EnvironmentChecks(unittest.TestCase):
    def check(self, env=None, policies=None):
        return pf.check_environment(env_api(env, policies), REPO, "demo", APPROVER)

    def test_agreed_settings_pass(self):
        self.assertEqual(self.check(), [])

    def test_absent_environment_fails_closed(self):
        api = FakeApi({"/repos": pf.PreflightError("HTTP 404")})
        with self.assertRaises(pf.PreflightError):
            pf.check_environment(api, REPO, "demo", APPROVER)

    def test_missing_reviewers_rule_fails(self):
        env = environment_payload()
        env["protection_rules"] = []
        with self.assertRaises(pf.PreflightError):
            self.check(env)

    def test_wrong_or_extra_reviewer_fails(self):
        env = environment_payload()
        env["protection_rules"][0]["reviewers"][0]["reviewer"]["login"] = "someone-else"
        with self.assertRaises(pf.PreflightError):
            self.check(env)
        env = environment_payload()
        env["protection_rules"][0]["reviewers"].append({"type": "User", "reviewer": {"login": APPROVER}})
        with self.assertRaises(pf.PreflightError):
            self.check(env)

    def test_team_reviewer_fails(self):
        env = environment_payload()
        env["protection_rules"][0]["reviewers"][0]["type"] = "Team"
        with self.assertRaises(pf.PreflightError):
            self.check(env)

    def test_self_review_prevention_enabled_fails(self):
        env = environment_payload()
        env["protection_rules"][0]["prevent_self_review"] = True
        with self.assertRaises(pf.PreflightError):
            self.check(env)

    def test_admin_bypass_fails_but_missing_field_is_noted(self):
        env = environment_payload()
        env["can_admins_bypass"] = True
        with self.assertRaises(pf.PreflightError):
            self.check(env)
        env = environment_payload()
        del env["can_admins_bypass"]
        self.assertEqual(len(self.check(env)), 1)

    def test_branch_policy_must_be_exactly_main_branch(self):
        for policies in (
            {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]},
            {"total_count": 1, "branch_policies": [{"name": "main", "type": "tag"}]},
            {"total_count": 2, "branch_policies": [
                {"name": "main", "type": "branch"}, {"name": "release/*", "type": "branch"}]},
            {"total_count": 0, "branch_policies": []},
        ):
            with self.assertRaises(pf.PreflightError, msg=str(policies)):
                self.check(policies=policies)

    def test_protected_branches_mode_fails(self):
        env = environment_payload()
        env["deployment_branch_policy"] = {"protected_branches": True, "custom_branch_policies": False}
        with self.assertRaises(pf.PreflightError):
            self.check(env)
        env["deployment_branch_policy"] = None
        with self.assertRaises(pf.PreflightError):
            self.check(env)


class SourceChecks(unittest.TestCase):
    def run_check(self, runs=None, jobs=None, git_codes=(0, 0), ref="refs/heads/main", sha=SHA):
        api = FakeApi(
            {
                f"/repos/{REPO}/actions/workflows/ci.yml/runs": {
                    "workflow_runs": [ci_run()] if runs is None else runs
                },
                f"/repos/{REPO}/actions/runs/500/attempts/2/jobs": jobs or jobs_payload(),
                f"/repos/{REPO}/actions/runs/600/attempts/1/jobs": jobs or jobs_payload(),
            }
        )
        codes = iter(git_codes)
        self.git_calls = []

        def git(args):
            self.git_calls.append(args)
            return next(codes)

        self.api = api
        return pf.check_source(api, git, REPO, sha, ref)

    def test_success_returns_run_url_and_uses_argv_git(self):
        url = self.run_check()
        self.assertEqual(url, f"https://github.com/{REPO}/actions/runs/500")
        self.assertEqual(self.git_calls[1], ["merge-base", "--is-ancestor", SHA, "origin/main"])
        self.assertIn("head_sha=" + SHA, self.api.paths[0])

    def test_rejects_non_main_ref_and_bad_sha(self):
        with self.assertRaises(pf.PreflightError):
            self.run_check(ref="refs/heads/feature")
        for sha in ("main", "abc1234", "A" * 40, SHA + "0"):
            with self.assertRaises(pf.PreflightError):
                self.run_check(sha=sha)

    def test_rejects_unknown_commit_and_non_ancestor(self):
        with self.assertRaises(pf.PreflightError):
            self.run_check(git_codes=(1,))
        with self.assertRaises(pf.PreflightError):
            self.run_check(git_codes=(0, 1))

    def test_requires_a_matching_ci_run(self):
        for runs in (
            [],
            [ci_run(event="pull_request")],
            [ci_run(head_branch="feature")],
            [ci_run(path=".github/workflows/other.yml")],
            [ci_run(head_sha="b" * 40)],
            [ci_run(head_repository={"full_name": "fork/repo"})],
        ):
            with self.assertRaises(pf.PreflightError, msg=str(runs)):
                self.run_check(runs=runs)

    def test_latest_run_decides_even_if_older_succeeded(self):
        runs = [ci_run(), ci_run(id=600, run_number=8, run_attempt=1, conclusion="failure")]
        with self.assertRaises(pf.PreflightError):
            self.run_check(runs=runs)

    def test_incomplete_run_fails(self):
        with self.assertRaises(pf.PreflightError):
            self.run_check(runs=[ci_run(status="in_progress", conclusion=None)])

    def test_skipped_or_failed_job_fails(self):
        for conclusion in ("skipped", "failure", "cancelled", None):
            with self.assertRaises(pf.PreflightError, msg=str(conclusion)):
                self.run_check(jobs=jobs_payload(e2e=conclusion))

    def test_missing_or_duplicate_required_job_fails(self):
        jobs = jobs_payload()
        jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] != "backend"]
        with self.assertRaises(pf.PreflightError):
            self.run_check(jobs=jobs)
        jobs = jobs_payload()
        jobs["jobs"].append(dict(jobs["jobs"][0]))
        with self.assertRaises(pf.PreflightError):
            self.run_check(jobs=jobs)


class ApprovalChecks(unittest.TestCase):
    def check(self, payload):
        api = FakeApi({f"/repos/{REPO}/actions/runs/9/approvals": payload})
        pf.check_approval(api, REPO, "9", "demo", APPROVER)

    def approval(self, **overrides):
        item = {"state": "approved", "user": {"login": "SOLE-approver"}, "environments": [{"name": "demo"}]}
        item.update(overrides)
        return item

    def test_matching_approval_passes(self):
        self.check([self.approval()])

    def test_rejected_other_user_or_other_environment_fails(self):
        for item in (
            self.approval(state="rejected"),
            self.approval(user={"login": "other"}),
            self.approval(environments=[{"name": "staging"}]),
        ):
            with self.assertRaises(pf.PreflightError):
                self.check([item])
        with self.assertRaises(pf.PreflightError):
            self.check([])


if __name__ == "__main__":
    unittest.main()
