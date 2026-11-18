"""scripts/package-lambda.py のオフラインテスト（標準ライブラリのみ）。

エッジ（CloudFront Functions）のソースがZIPに入り、manifestの宣言とアプリのヘッダー名が一致するときだけ
接続元IPの契約（viewerIpContract）を宣言すること、配布の検証（deploy-lambda.py）がそのZIPを受け入れる
ことを確認する。実際のDockerビルド・AWSの検証ではない。
  python3 -B -I -m unittest discover -s tests/cd
"""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA = "d" * 40
_spec = importlib.util.spec_from_file_location("deploy_lambda_pkg", ROOT / "scripts" / "deploy-lambda.py")
dl = importlib.util.module_from_spec(_spec)
sys.modules["deploy_lambda_pkg"] = dl
_spec.loader.exec_module(dl)


class PackageEdgeSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "deps").mkdir()
        (self.dir / "deps" / "dep.py").write_text("x = 1\n", encoding="utf-8")
        self.app = self.dir / "app"
        shutil.copytree(ROOT / "backend" / "app", self.app, ignore=shutil.ignore_patterns("__pycache__"))
        (self.dir / "static").mkdir()
        (self.dir / "static" / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
        (self.dir / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self.edge = self.dir / "viewer-ip.js"
        shutil.copy(ROOT / "infra" / "edge" / "viewer-ip.js", self.edge)

    def package(self, out="out", extra=()):
        output = self.dir / out
        done = subprocess.run(
            [
                sys.executable, "-B", "-I", str(ROOT / "scripts" / "package-lambda.py"),
                "--deps", str(self.dir / "deps"), "--app", str(self.app), "--static", str(self.dir / "static"),
                "--run-sh", str(self.dir / "run.sh"), "--edge-function", str(self.edge),
                "--source-sha", SHA, "--release-eligible", "true", "--dirty", "false",
                "--output", str(output), *extra,
            ],
            capture_output=True, text=True,
        )
        return done, output

    def manifest(self, output):
        return json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    def test_real_sources_produce_a_zip_with_the_reviewed_edge_copy_and_the_contract(self):
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest(output)
        source = (ROOT / "infra" / "edge" / "viewer-ip.js").read_bytes()
        with zipfile.ZipFile(output / f"lambda-{SHA}.zip") as archive:
            self.assertEqual(archive.read("edge/viewer-ip.js"), source)
        self.assertEqual(manifest["capabilities"], {"publicWriteGuard": 1, "viewerIpContract": 1})
        self.assertEqual(manifest["edge"], {"file": "edge/viewer-ip.js", "sha256Hex": hashlib.sha256(source).hexdigest()})

    def test_deploy_validation_accepts_the_package_and_derives_the_contract(self):
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest(output)
        _, loaded = dl.load_manifest(output, SHA, manifest["zip"]["sha256Hex"])
        self.assertTrue(dl.manifest_has_viewer_ip_contract(loaded))
        self.assertTrue(dl.manifest_has_write_guard(loaded))

    def test_header_name_mismatch_between_app_and_edge_withholds_the_contract(self):
        self.edge.write_text(
            self.edge.read_text(encoding="utf-8").replace("x-demo-viewer-ip", "x-other-ip"), encoding="utf-8"
        )
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_internal_token_name_drift_between_app_and_edge_withholds_the_contract(self):
        # エッジが捨てる名前が、アプリが信じる名前と違えば、利用者が偽のトークンを通せる
        self.edge.write_text(
            self.edge.read_text(encoding="utf-8").replace("x-internal-mock-token", "x-other-token"), encoding="utf-8"
        )
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_app_side_token_name_change_withholds_the_contract(self):
        guard = self.app / "public_guard.py"
        guard.write_text(
            guard.read_text(encoding="utf-8").replace("x-internal-mock-token", "x-renamed-token"), encoding="utf-8"
        )
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_edge_that_writes_but_does_not_drop_the_viewer_header_withholds_the_contract(self):
        text = self.edge.read_text(encoding="utf-8")
        self.edge.write_text(text.replace("lowered === 'x-demo-viewer-ip' || ", ""), encoding="utf-8")
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_app_without_the_token_definition_withholds_the_contract(self):
        guard = self.app / "public_guard.py"
        guard.write_text(guard.read_text(encoding="utf-8").replace("_INTERNAL_TOKEN_HEADER =", "_OTHER ="), encoding="utf-8")
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_app_without_the_dedicated_header_definition_withholds_the_contract(self):
        quota = self.app / "usage_quota.py"
        quota.write_text(quota.read_text(encoding="utf-8").replace("VIEWER_IP_HEADER =", "OTHER ="), encoding="utf-8")
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"]["viewerIpContract"], 0)

    def test_app_without_the_guard_files_declares_neither_capability(self):
        (self.app / "public_guard.py").unlink()
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.manifest(output)["capabilities"], {"publicWriteGuard": 0, "viewerIpContract": 0})

    def test_missing_or_symlinked_edge_source_fails_the_build(self):
        self.edge.unlink()
        done, _ = self.package()
        self.assertNotEqual(done.returncode, 0)
        (self.dir / "real.js").write_text("function handler(event) {}", encoding="utf-8")
        self.edge.symlink_to(self.dir / "real.js")
        done, _ = self.package("out2")
        self.assertNotEqual(done.returncode, 0)

    def test_tampered_zip_entry_is_rejected_by_the_deploy_validation(self):
        done, output = self.package()
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = self.manifest(output)
        manifest["edge"]["sha256Hex"] = "0" * 64  # manifestだけを書き換えても、ZIPの中の写しと一致しない
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(dl.DeployError) as caught:
            dl.load_manifest(output, SHA, manifest["zip"]["sha256Hex"])
        self.assertIn("edgeSource", str(caught.exception))


class DockerBuildWiring(unittest.TestCase):
    """通常のビルド入口（scripts/build-lambda.sh → infra/cd/Dockerfile）が、エッジのソースを渡すこと。"""

    def test_dockerfile_copies_and_passes_the_edge_source(self):
        dockerfile = (ROOT / "infra" / "cd" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY infra/edge/viewer-ip.js /in/edge/viewer-ip.js", dockerfile)
        self.assertIn("--edge-function /in/edge/viewer-ip.js", dockerfile)

    def test_dockerignore_allows_only_the_edge_source_file(self):
        lines = (ROOT / "infra" / "cd" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], "*")  # 既定は全拒否のまま
        self.assertIn("!infra/edge/viewer-ip.js", lines)
        self.assertNotIn("!infra/edge", lines)  # テストなどを入れない
        self.assertTrue((ROOT / "infra" / "edge" / "viewer-ip.js").is_file())


if __name__ == "__main__":
    unittest.main()
