"""CDワークフローの文面の確認（YAMLの構文検証ではなく、順序・条件の取り違えを検出する簡易テスト）。

実際のGitHub Actionsでの動作の検証ではない。
  python3 -B -I -m unittest discover -s tests/cd
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CD = (ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class CdWorkflow(unittest.TestCase):
    def test_artifact_is_verified_before_aws_credentials(self):
        verify = CD.index("--verify-artifact-only")
        credentials = CD.index("aws-actions/configure-aws-credentials@")
        self.assertLess(verify, credentials)
        # 検証の呼び出しは、AWSの資格情報の前の1か所だけで、実際の配布の呼び出しより前にある
        self.assertLess(verify, CD.index("scripts/deploy-lambda.py \\\n            --artifact-dir"))

    def test_non_main_dispatch_fails_instead_of_skipping(self):
        build = CD[CD.index("  build:") : CD.index("  deploy:")]
        self.assertNotIn("    if: ${{ github.ref == 'refs/heads/main' }}", build)
        self.assertIn('"$GITHUB_REF" != "refs/heads/main"', build)
        # 失敗のステップは、チェックアウトより前（対象外のrefでは何も取得しない）
        self.assertLess(build.index('"$GITHUB_REF" != "refs/heads/main"'), build.index("actions/checkout@"))

    def test_summary_does_not_claim_no_aws_operation_blindly(self):
        self.assertNotIn("AWSは操作していません", CD)
        self.assertIn("未確認", CD)

    def test_edge_function_variable_is_optional_in_the_precheck_and_passed_to_the_deploy(self):
        check = CD[CD.index("承認後の設定・承認記録・変数を確認する") : CD.index("--verify-artifact-only")]
        required = check[check.index("for name in ") : check.index("; do")]
        self.assertNotIn("CLOUDFRONT_VIEWER_FUNCTION_ARN", required)  # 読み取り構成では未設定でよい
        self.assertIn("^arn:aws:cloudfront::[0-9]{12}:function/", check)  # 設定する場合は形式を確認する
        deploy = CD[CD.index("scripts/deploy-lambda.py \\\n            --artifact-dir") :]
        self.assertIn('--edge-function-arn "$CLOUDFRONT_VIEWER_FUNCTION_ARN"', deploy)

    def test_manual_only_trigger(self):
        triggers = CD[CD.index("\non:") : CD.index("permissions: {}")]
        self.assertIn("workflow_dispatch", triggers)
        for forbidden in ("push:", "pull_request", "schedule:"):
            self.assertNotIn(forbidden, triggers)


class CiWorkflow(unittest.TestCase):
    def test_cd_offline_job_exists_and_existing_jobs_are_kept(self):
        for name in ("frontend", "backend", "e2e"):
            self.assertIn(f"\n    name: {name}\n", CI)
        job = CI[CI.index("  cd-offline:") :]
        self.assertIn("unittest discover -s tests/cd", job)
        self.assertIn("python -B -I", job)
        self.assertIn("--network none", job)
        self.assertNotIn("secrets.", job)
        self.assertNotIn("github.token", job)

    def test_edge_job_runs_the_edge_tests_without_secrets(self):
        job = CI[CI.index("  edge:") :]
        self.assertIn("\n    name: edge\n", CI)
        self.assertIn("bash scripts/edge-test.sh", job)
        self.assertNotIn("secrets.", job)
        self.assertNotIn("github.token", job)


if __name__ == "__main__":
    unittest.main()
