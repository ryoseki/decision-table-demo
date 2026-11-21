"""scripts/semgrep.sh（通常の入口）の、結果の区別を確かめる。

  python3 -B -I -m unittest discover -s tests/semgrep

- 実エンジンのテスト: 固定したSemgrepのコンテナを実際に動かす（docker と、固定イメージが必要）。
  使えないときは、スキップせず失敗にする（「検査していない」を成功扱いにしないため）。
- 偽のdockerのテスト: 実行できない（20）と、実行の失敗（30）の分岐を、エンジンなしで確かめる。
終了コード: 0=問題なし / 10=検出あり / 20=実行できない / 30=失敗 / 2=引数の誤り。
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "semgrep.sh"
REPORT = ROOT / "scripts" / "semgrep_report.py"
DOC = ROOT / "docs" / "semgrep.md"
BASH = shutil.which("bash")
_SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")
VERSION = re.search(r"^SEMGREP_VERSION=(\S+)$", _SCRIPT_TEXT, re.M).group(1)
IMAGE = re.search(r"^SEMGREP_IMAGE=(\S+)$", _SCRIPT_TEXT, re.M).group(1)

CLEAN, FINDINGS, UNAVAILABLE, FAILURE, USAGE = 0, 10, 20, 30, 2

PY_EVIL = "eval(input())\n"  # py-eval-exec-dynamic が検出する
TS_EVIL = "eval(code);\n"  # ts-eval-dynamic-code が検出する


def documented_names(label):
    """docs/semgrep.md の「対象と除外」の表から、バッククォートで囲んだ名前を集める（文書が根拠）。"""
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {label} |"):
            return re.findall(r"`([^`]+)`", line)
    raise AssertionError(f"docs/semgrep.md に「{label}」の行がありません")


def run(*args, env=None, cwd=None, script=SCRIPT):
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [BASH, str(script), *args], capture_output=True, text=True, env=full_env, cwd=cwd, timeout=300, check=False
    )


def snapshot(path):
    """ファイルとディレクトリの、相対パス・種別・中身・更新時刻を記録する（変更されていないことの確認用）。"""
    path = Path(path)
    state = {}
    for item in sorted([path, *path.rglob("*")]):
        stat_result = item.lstat()
        relative = item.relative_to(path).as_posix()
        body = item.read_bytes() if item.is_file() and not item.is_symlink() else None
        state[relative] = (stat.S_IFMT(stat_result.st_mode), body, stat_result.st_mtime_ns)
    return state


def write(root, relative, content):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="semgrep-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        self.root.mkdir()


class RealEngine(TempCase):
    def scan(self, **kwargs):
        result = run("scan", "--root", str(self.root), **kwargs)
        self.assertNotEqual(
            result.returncode, UNAVAILABLE, "docker または固定イメージが使えません: " + result.stderr
        )
        return result

    def test_fixtures_pass_through_normal_entry(self):
        result = run("fixtures")
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn(f"Semgrep {VERSION}", result.stdout)
        self.assertRegex(result.stdout, r"ルール \d+ 件")

    def test_planted_violations_are_detected_with_rule_id_and_relative_location(self):
        write(
            self.root,
            "backend/app/planted.py",
            """\
            import subprocess


            def run(value):
                subprocess.run(f"echo {value}", shell=True)
            """,
        )
        write(
            self.root,
            "frontend/src/Planted.tsx",
            """\
            export function Planted({ note }: { note: string }) {
              return <p dangerouslySetInnerHTML={{ __html: note }} />;
            }
            """,
        )
        result = self.scan()
        self.assertEqual(result.returncode, FINDINGS, result.stdout + result.stderr)
        self.assertIn("  py-shell-command-injection  backend/app/planted.py:5", result.stdout)
        self.assertIn("  ts-react-dangerously-set-inner-html  frontend/src/Planted.tsx:2", result.stdout)
        self.assertIn("検出 2 件", result.stdout)
        self.assertNotIn(str(self.root), result.stdout + result.stderr)  # 絶対パスを出さない

    def test_clean_source_reports_zero_findings_and_inventory(self):
        write(self.root, "backend/app/ok.py", 'def f(cur, v):\n    cur.execute("SELECT %s", (v,))\n')
        write(self.root, "frontend/src/ok.ts", "export const x = 1;\n")
        result = self.scan()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn("2 ファイルを検査（.py 1, .ts 1）", result.stdout)
        self.assertIn("検出 0 件", result.stdout)

    def test_no_targets_is_reported_as_not_scanned(self):
        # 対象の外のファイル、除外するディレクトリ、対象外の拡張子だけ。違反を入れても検査しない。
        write(self.root, "docs/x.py", "eval(input())\n")
        write(self.root, "frontend/node_modules/dep/index.js", "eval(input());\n")
        write(self.root, "backend/notes.md", "eval(x)\n")
        result = self.scan()
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("対象のファイルがありません（0件）。検査していません", result.stderr)
        self.assertNotIn("検出 0 件", result.stdout)

    def test_empty_root_is_a_failure_not_a_clean_result(self):
        result = self.scan()
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("検査していません", result.stderr)

    def test_every_target_extension_is_scanned_and_parsed(self):
        for name, body in {
            "a.py": "x = 1\n",
            "b.ts": "export const b: number = 2;\n",
            "c.tsx": "export const C = () => <p>x</p>;\n",
            "d.js": "export const d = 1;\n",
            "e.jsx": "export const E = () => <p>x</p>;\n",
            "f.mjs": "export const f = 1;\n",
            "g.cjs": "module.exports = 1;\n",
        }.items():
            write(self.root, f"frontend/src/{name}", body)
        result = self.scan()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn("7 ファイルを検査（.cjs 1, .js 1, .jsx 1, .mjs 1, .py 1, .ts 1, .tsx 1）", result.stdout)

    def test_syntax_errors_are_a_failure_not_a_clean_result(self):
        # どのルールの候補にもならない語だけのファイル。Semgrepの検査だけでは、構文エラーを報告しない。
        write(self.root, "backend/app/broken.py", "def broken(:\n    pass\n")
        write(self.root, "frontend/src/Broken.tsx", "export const A = () => <div>{</div>;\n")
        write(self.root, "frontend/src/fine.ts", "export const x = 1;\n")
        result = self.scan()
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("構文を解析できないファイルがあります（2 件）", result.stderr)
        self.assertIn("backend/app/broken.py", result.stderr)
        self.assertIn("frontend/src/Broken.tsx", result.stderr)
        self.assertNotIn("fine.ts", result.stderr)
        self.assertNotIn("検出 0 件", result.stdout)

    def test_invalid_rule_configuration_is_a_failure(self):
        rules = self.tmp / "rules"
        write(rules, "broken.yml", "rules:\n  - id: broken\n    pattern: [unclosed\n")
        write(self.root, "backend/app/ok.py", "x = 1\n")
        result = self.scan(env={"SEMGREP_RULES_DIR": str(rules)})
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("Semgrepの実行が異常終了しました", result.stderr)
        self.assertNotIn("検出", result.stdout)

    def test_fixture_check_fails_when_a_rule_stops_detecting(self):
        rules = self.tmp / "rules"
        shutil.copytree(ROOT / "security" / "semgrep" / "rules", rules)
        python_rules = rules / "python.yml"
        text = python_rules.read_text(encoding="utf-8")
        self.assertIn("pattern: eval($X, ...)", text)
        python_rules.write_text(text.replace("pattern: eval($X, ...)", "pattern: not_eval($X, ...)"), encoding="utf-8")
        result = run("fixtures", env={"SEMGREP_RULES_DIR": str(rules)})
        self.assertNotEqual(result.returncode, UNAVAILABLE, result.stderr)
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn(
            "検出されるはずが、検出されませんでした: py-eval-exec-dynamic  security/semgrep/fixtures/python/eval_exec.py:8",
            result.stderr,
        )

    def test_fixture_check_fails_when_a_rule_over_matches_a_control(self):
        rules = self.tmp / "rules"
        shutil.copytree(ROOT / "security" / "semgrep" / "rules", rules)
        ts_rules = rules / "typescript.yml"
        text = ts_rules.read_text(encoding="utf-8")
        literal_only = r"""regex: '^(?!\s*("[^"\\]*"|''[^''\\]*'')\s*$)'"""
        self.assertIn(literal_only, text)
        # 固定の文字列を除く条件を、何でも通す条件に替える（検出しないはずの例まで検出する）
        ts_rules.write_text(text.replace(literal_only, "regex: '.*'"), encoding="utf-8")
        result = run("fixtures", env={"SEMGREP_RULES_DIR": str(rules)})
        self.assertNotEqual(result.returncode, UNAVAILABLE, result.stderr)
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("検出されないはずが、検出されました: ts-dom-html-sink", result.stderr)

    def test_default_fixtures_run_works_from_another_working_directory(self):
        result = run("fixtures", cwd=self.tmp)
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"ルール \d+ 件")

    # ---- 対象のディレクトリと除外（文書の表が根拠。クリーンや対象0件の失敗で、検出の確認が隠れないようにする） ----

    def test_every_documented_target_directory_contributes_a_detection(self):
        targets = documented_names("対象のディレクトリ")
        self.assertEqual(len(targets), 7, targets)
        for name in targets:
            write(self.root, f"{name}/pkg/evil.py", PY_EVIL)
        write(self.root, "docs/evil.py", PY_EVIL)  # 対象の外
        write(self.root, "security/evil.py", PY_EVIL)  # 対象の外
        result = self.scan()
        self.assertEqual(result.returncode, FINDINGS, result.stdout + result.stderr)
        for name in targets:
            self.assertIn(f"  py-eval-exec-dynamic  {name}/pkg/evil.py:1", result.stdout)
        self.assertIn("7 ファイルを検査", result.stdout)
        self.assertIn("検出 7 件", result.stdout)
        self.assertNotIn("docs/evil.py", result.stdout)
        self.assertNotIn("security/evil.py", result.stdout)

    def test_every_documented_excluded_directory_is_skipped_at_any_depth(self):
        excluded = documented_names("除外するディレクトリ名")
        self.assertEqual(len(excluded), 12, excluded)
        write(self.root, "backend/included.py", PY_EVIL)  # 同じ内容が、除外の外では検出される（対照）
        for name in excluded:
            write(self.root, f"backend/{name}/direct.py", PY_EVIL)
            write(self.root, f"frontend/src/{name}/deep/nested.ts", TS_EVIL)
            write(self.root, f"tests/a/{name}/b/c.py", PY_EVIL)
        result = self.scan()
        self.assertEqual(result.returncode, FINDINGS, result.stdout + result.stderr)
        self.assertIn("1 ファイルを検査", result.stdout)
        self.assertIn("検出 1 件", result.stdout)
        self.assertIn("  py-eval-exec-dynamic  backend/included.py:1", result.stdout)
        for name in excluded:
            self.assertNotIn(f"/{name}/", result.stdout)

    def test_symlinks_are_not_followed(self):
        outside = self.tmp / "outside"
        write(outside, "evil.py", PY_EVIL)
        write(outside, "sub/evil2.py", PY_EVIL)
        write(self.root, "backend/included.py", PY_EVIL)  # 対照: これだけが検出される
        (self.root / "frontend").symlink_to(outside, target_is_directory=True)  # 対象のディレクトリそのものがリンク
        (self.root / "backend" / "link_dir").symlink_to(outside, target_is_directory=True)  # 中のディレクトリがリンク
        (self.root / "backend" / "link_file.py").symlink_to(outside / "evil.py")  # ファイルがリンク
        result = self.scan()
        self.assertEqual(result.returncode, FINDINGS, result.stdout + result.stderr)
        self.assertIn("1 ファイルを検査", result.stdout)
        self.assertIn("検出 1 件", result.stdout)
        self.assertIn("  py-eval-exec-dynamic  backend/included.py:1", result.stdout)
        for leaked in ("link_dir", "link_file", "frontend/", str(outside)):
            self.assertNotIn(leaked, result.stdout + result.stderr)

    # ---- .git（コンテナの中だけで空に見せる。ホスト側は変えない） ----

    def assert_scans_with_git_marker(self, make_git):
        git = self.root / ".git"
        make_git(git)
        write(self.root, "backend/ok.py", "x = 1\n")
        before = snapshot(git)
        clean = self.scan()
        self.assertEqual(clean.returncode, CLEAN, clean.stdout + clean.stderr)
        self.assertIn("1 ファイルを検査", clean.stdout)
        write(self.root, "backend/evil.py", PY_EVIL)
        detected = self.scan()
        self.assertEqual(detected.returncode, FINDINGS, detected.stdout + detected.stderr)
        self.assertIn("  py-eval-exec-dynamic  backend/evil.py:1", detected.stdout)
        self.assertEqual(snapshot(git), before, ".git が変更されました")

    def test_a_git_directory_does_not_break_scans_and_is_left_unmodified(self):
        def make(git):
            write(git, "HEAD", "ref: refs/heads/main\n")
            write(git, "config", "[core]\n\trepositoryformatversion = 0\n")
            write(git, "refs/heads/main", "0123456789012345678901234567890123456789\n")

        self.assert_scans_with_git_marker(make)

    def test_a_worktree_git_file_pointing_outside_does_not_break_scans_and_is_left_unmodified(self):
        pointer = self.tmp / "does-not-exist" / "worktrees" / "w1"
        self.assert_scans_with_git_marker(lambda git: git.write_text(f"gitdir: {pointer}\n", encoding="utf-8"))
        self.assertFalse(pointer.exists())

    # ---- 複合した失敗 ----

    def test_a_violation_together_with_a_syntax_error_is_a_failure_not_findings(self):
        write(self.root, "backend/app/evil.py", PY_EVIL)
        write(self.root, "backend/app/broken.py", "def broken(:\n    pass\n")
        result = self.scan()
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("構文を解析できないファイルがあります（1 件）", result.stderr)
        self.assertIn("backend/app/broken.py", result.stderr)
        self.assertNotIn("py-eval-exec-dynamic", result.stdout)
        self.assertNotIn("検出", result.stdout)


class Usage(TempCase):
    def test_unknown_argument_and_missing_root_are_usage_errors(self):
        self.assertEqual(run("bogus").returncode, USAGE)
        self.assertEqual(run("scan", "--root", str(self.tmp / "missing")).returncode, USAGE)
        self.assertEqual(run("fixtures", "--root", str(self.root)).returncode, USAGE)

    def test_help_prints_usage_and_exits_zero_without_docker(self):
        for flag in ("--help", "-h"):
            result = run(flag)
            self.assertEqual(result.returncode, CLEAN, result.stderr)
            self.assertIn("bash scripts/semgrep.sh fixtures", result.stdout)
            self.assertIn("終了コード", result.stdout)

    def test_root_option_without_a_value_is_a_usage_error(self):
        for args in (("scan", "--root"), ("--root",), ("scan", "--root", "")):
            result = run(*args)
            self.assertEqual(result.returncode, USAGE, (args, result.stdout, result.stderr))
            self.assertIn("bash scripts/semgrep.sh", result.stderr)  # 使い方を出す

    def test_missing_rules_directory_is_a_usage_error(self):
        result = run("scan", "--root", str(self.root), env={"SEMGREP_RULES_DIR": str(self.tmp / "no-rules")})
        self.assertEqual(result.returncode, USAGE, result.stdout + result.stderr)
        self.assertIn("ルールの置き場所がありません", result.stderr)
        self.assertNotIn(str(self.tmp), result.stderr)


class FakeDocker(TempCase):
    """偽のdockerで、エンジンなしに、分岐を確かめる。"""

    def setUp(self):
        super().setUp()
        write(self.root, "backend/app/a.py", "x = 1\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "docker"
        fake.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                case "$1" in
                  info) exit "${FAKE_INFO_RC:-0}" ;;
                  image) exit "${FAKE_INSPECT_RC:-0}" ;;
                  pull) exit "${FAKE_PULL_RC:-0}" ;;
                  run)
                    if [ -n "${FAKE_LOG_DIR:-}" ]; then  # docker run の引数を、1回ごとに記録する（NUL区切り）
                      n=$(ls "$FAKE_LOG_DIR" | wc -l)
                      printf '%s\\0' "$@" >"$FAKE_LOG_DIR/call-$n"
                    fi
                    for arg in "$@"; do
                      if [ "$arg" = sh ]; then  # 構文チェック（sh -c ...）
                        printf '%s' "${FAKE_PARSE_OUTPUT:-}"; echo "fake docker parse failed" >&2; exit "${FAKE_PARSE_RC:-0}"
                      fi
                    done
                    printf '%s' "${FAKE_RUN_OUTPUT:-}"; echo "fake docker run failed" >&2; exit "${FAKE_RUN_RC:-0}" ;;
                esac
                exit 99
                """
            ),
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}"}

    def scan(self, **env):
        return run("scan", "--root", str(self.root), env={**self.env, **env})

    def payload(self, **overrides):
        data = {"version": VERSION, "results": [], "errors": [], "paths": {"scanned": ["backend/app/a.py"]}}
        data.update(overrides)
        return json.dumps(data)

    def test_missing_docker_is_unavailable(self):
        tools = self.tmp / "only-dirname"
        tools.mkdir()
        (tools / "dirname").symlink_to(shutil.which("dirname"))
        result = run("scan", "--root", str(self.root), env={"PATH": str(tools)})
        self.assertEqual(result.returncode, UNAVAILABLE, result.stderr)
        self.assertIn("docker コマンドが見つかりません", result.stderr)
        self.assertIn("検査していません", result.stderr)

    def test_unreachable_daemon_is_unavailable(self):
        result = self.scan(FAKE_INFO_RC="1")
        self.assertEqual(result.returncode, UNAVAILABLE, result.stderr)
        self.assertIn("Dockerデーモンに接続できません", result.stderr)

    def test_missing_pinned_image_that_cannot_be_pulled_is_unavailable(self):
        result = self.scan(FAKE_INSPECT_RC="1", FAKE_PULL_RC="1")
        self.assertEqual(result.returncode, UNAVAILABLE, result.stderr)
        self.assertIn("固定したイメージ", result.stderr)

    def test_engine_crash_is_a_failure(self):
        result = self.scan(FAKE_RUN_RC="125")
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("異常終了", result.stderr)

    def test_parse_check_crash_is_a_failure(self):
        result = self.scan(FAKE_PARSE_RC="125", FAKE_RUN_OUTPUT=self.payload(), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("構文チェックの実行が異常終了", result.stderr)

    def test_unparsable_file_is_a_failure_even_if_the_scan_looks_clean(self):
        result = self.scan(
            FAKE_PARSE_RC="1", FAKE_PARSE_OUTPUT="backend/app/a.py\n", FAKE_RUN_OUTPUT=self.payload(), FAKE_RUN_RC="0"
        )
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("構文を解析できないファイルがあります（1 件）", result.stderr)

    def test_unreadable_output_is_a_failure_even_when_engine_exits_zero(self):
        result = self.scan(FAKE_RUN_OUTPUT="not json", FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FAILURE, result.stderr)

    def test_engine_errors_in_output_are_a_failure_even_when_engine_exits_zero(self):
        errors = [{"type": ["PartialParsing", []], "message": "Syntax error", "spans": [{"file": "backend/app/a.py"}]}]
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(errors=errors), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("backend/app/a.py", result.stderr)

    def test_a_file_that_was_not_scanned_is_a_failure(self):
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(paths={"scanned": []}), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("検査されなかったファイル", result.stderr)

    def test_a_version_other_than_the_pinned_one_is_a_failure(self):
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(version="0.0.1"), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FAILURE, result.stderr)
        self.assertIn("版が固定と違います", result.stderr)

    def test_a_finding_in_the_output_is_reported_even_if_exit_code_is_zero(self):
        finding = {
            "check_id": "rules.py-eval-exec-dynamic",
            "path": "backend/app/a.py",
            "start": {"line": 1},
            "extra": {},
        }
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(results=[finding]), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, FINDINGS, result.stderr)
        self.assertIn("  py-eval-exec-dynamic  backend/app/a.py:1", result.stdout)

    def test_clean_output_is_clean(self):
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(), FAKE_RUN_RC="0")
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn("検出 0 件", result.stdout)

    def test_engine_exit_one_with_empty_results_is_a_failure_not_clean(self):
        # エンジンは「検出あり」（--error の終了コード1）と言うのに、出力の results が空。矛盾なので、問題なしにしない。
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(), FAKE_RUN_RC="1")
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn("results が空", result.stderr)
        self.assertNotIn("検出 0 件", result.stdout)

    def test_engine_exit_one_with_a_finding_is_findings(self):
        finding = {"check_id": "rules.py-eval-exec-dynamic", "path": "backend/app/a.py", "start": {"line": 1}, "extra": {}}
        result = self.scan(FAKE_RUN_OUTPUT=self.payload(results=[finding]), FAKE_RUN_RC="1")
        self.assertEqual(result.returncode, FINDINGS, result.stdout + result.stderr)
        self.assertIn("  py-eval-exec-dynamic  backend/app/a.py:1", result.stdout)

    def test_findings_together_with_engine_errors_are_a_failure_not_findings(self):
        finding = {"check_id": "rules.py-eval-exec-dynamic", "path": "backend/app/a.py", "start": {"line": 1}, "extra": {}}
        errors = [{"type": ["PartialParsing", []], "message": "Syntax error", "spans": [{"file": "backend/app/a.py"}]}]
        for engine_rc in ("0", "1"):
            with self.subTest(engine_rc=engine_rc):
                result = self.scan(FAKE_RUN_OUTPUT=self.payload(results=[finding], errors=errors), FAKE_RUN_RC=engine_rc)
                self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
                self.assertIn("構文エラーなど、検査を完了できなかった項目があります", result.stderr)
                self.assertNotIn("py-eval-exec-dynamic", result.stdout)

    # ---- コンテナの保護（docker run の実際の引数を記録して確かめる） ----

    def scan_logged(self, script=SCRIPT, root=None, **kwargs):
        logs = Path(tempfile.mkdtemp(prefix="calls-", dir=self.tmp))  # 呼び出しごとに新しい記録の置き場
        env = {**self.env, "FAKE_LOG_DIR": str(logs), "FAKE_RUN_OUTPUT": self.payload(), "FAKE_RUN_RC": "0"}
        result = run("scan", "--root", str(root or self.root), env=env, script=script, **kwargs)
        calls = [
            path.read_bytes().decode().split("\0")[:-1]
            for path in sorted(logs.iterdir(), key=lambda p: int(p.name.split("-")[1]))
        ]
        return result, calls

    def container_problems(self, calls, root, rules):
        """docker run の2回（構文チェックと検査）の、保護の不足を、一覧にして返す。"""
        if len(calls) != 2:
            return [f"docker run が {len(calls)} 回です（構文チェックと検査の2回のはず）"]
        problems = []
        for label, args in zip(("構文チェック", "検査"), calls):
            pairs = set(zip(args, args[1:]))
            required = [
                ("--pull", "never"),
                ("--network", "none"),
                ("--cap-drop", "ALL"),
                ("--security-opt", "no-new-privileges"),
                ("-e", "SEMGREP_SEND_METRICS=off"),
                ("-e", "SEMGREP_ENABLE_VERSION_CHECK=0"),
                ("--user", f"{os.getuid()}:{os.getgid()}"),
                ("-v", f"{root}:/src:ro"),
                ("-v", f"{rules}:/rules:ro"),
            ]
            problems += [f"{label}: {flag} {value}" for flag, value in required if (flag, value) not in pairs]
            if "--read-only" not in args:
                problems.append(f"{label}: --read-only")
            if IMAGE not in args:
                problems.append(f"{label}: 固定したdigestのイメージ")
            writable = [b for a, b in pairs if a == "-v" and not b.endswith(":ro")]
            problems += [f"{label}: 書き込める -v {mount}" for mount in writable]
        after_image = calls[0][calls[0].index(IMAGE) + 1 :] if IMAGE in calls[0] else []
        if after_image[:1] != ["sh"]:
            problems.append("構文チェック: コンテナの中の sh で解析していない")
        scan = calls[1]
        tail = scan[scan.index(IMAGE) + 1 :] if IMAGE in scan else []
        if tail[:2] != ["semgrep", "scan"]:
            problems.append("検査: semgrep scan を実行していない")
        scan_pairs = set(zip(tail, tail[1:]))
        for flag, value in (("--config", "/rules"), ("--metrics", "off")):
            if (flag, value) not in scan_pairs:
                problems.append(f"検査: {flag} {value}")
        for flag in ("--disable-version-check", "--strict", "--error"):
            if flag not in tail:
                problems.append(f"検査: {flag}")
        return problems

    def test_both_container_runs_enforce_the_protections(self):
        result, calls = self.scan_logged()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        problems = self.container_problems(calls, self.root.resolve(), (ROOT / "security" / "semgrep" / "rules").resolve())
        self.assertEqual(problems, [])
        self.assertRegex(IMAGE, r"^semgrep/semgrep@sha256:[0-9a-f]{64}$")
        for args in calls:
            self.assertIn("-w", args)
            self.assertEqual(args[args.index("-w") + 1], "/src")

    # ---- .git のマスク（実エンジンのテストは、マスクなしでも通りうるため、docker run の引数で直接確かめる） ----

    def assert_both_runs_mask(self, calls, expected_pair, other_flags):
        self.assertEqual(len(calls), 2, f"docker run が {len(calls)} 回です（構文チェックと検査の2回のはず）")
        for label, args in zip(("構文チェック", "検査"), calls):
            self.assertIn(expected_pair, set(zip(args, args[1:])), f"{label}: .git のマスク {expected_pair} がありません")
            for flag, value in other_flags:
                self.assertNotIn((flag, value), set(zip(args, args[1:])), f"{label}: 別種のマスク {flag} {value}")

    def test_both_container_runs_mask_a_git_directory(self):
        git = self.root / ".git"
        write(git, "HEAD", "ref: refs/heads/main\n")
        before = snapshot(git)
        result, calls = self.scan_logged()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assert_both_runs_mask(calls, ("--tmpfs", "/src/.git:ro,size=4k"), [("-v", "/dev/null:/src/.git:ro")])
        self.assertEqual(snapshot(git), before, ".git が変更されました")

    def test_both_container_runs_mask_a_worktree_git_file(self):
        git = self.root / ".git"
        git.write_text(f"gitdir: {self.tmp / 'does-not-exist'}\n", encoding="utf-8")
        before = snapshot(git)
        result, calls = self.scan_logged()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assert_both_runs_mask(calls, ("-v", "/dev/null:/src/.git:ro"), [("--tmpfs", "/src/.git:ro,size=4k")])
        self.assertEqual(snapshot(git), before, ".git が変更されました")

    def test_no_git_mask_is_added_without_a_git_marker(self):
        result, calls = self.scan_logged()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        for args in calls:
            self.assertFalse([a for a in args if "/src/.git" in a], args)

    def test_the_protection_check_detects_each_removed_protection(self):
        # 検査用の複製（リポジトリは変えない）から、保護を1つずつ外して、上の確認が、外したものを指摘することを確かめる。
        removals = {
            "--pull never": ("--pull never ", ""),
            "--network none": ("--network none ", ""),
            "--read-only": ("--read-only ", ""),
            "--cap-drop ALL": ("--cap-drop ALL ", ""),
            "no-new-privileges": ("--security-opt no-new-privileges ", ""),
            "--user": ('--user "$(id -u):$(id -g)"', ""),
            "SEMGREP_SEND_METRICS=off": ("-e SEMGREP_SEND_METRICS=off ", ""),
            "SEMGREP_ENABLE_VERSION_CHECK=0": ("-e SEMGREP_ENABLE_VERSION_CHECK=0", ""),
            "/src:ro": ('-v "$root:/src:ro"', '-v "$root:/src"'),
            "/rules:ro": ('-v "$rules_dir:/rules:ro"', '-v "$rules_dir:/rules"'),
            "固定したdigest": (IMAGE.split("@", 1)[1], "latest"),
            "--metrics off": ("--metrics off ", ""),
            "--disable-version-check": ("--disable-version-check ", ""),
        }
        for expected, (old, new) in removals.items():
            with self.subTest(removed=expected):
                copy = self.tmp / f"copy-{abs(hash(expected))}"
                (copy / "scripts").mkdir(parents=True)
                rules = copy / "security" / "semgrep" / "rules"
                rules.mkdir(parents=True)
                shutil.copy(REPORT, copy / "scripts" / REPORT.name)
                self.assertIn(old, _SCRIPT_TEXT)
                (copy / "scripts" / SCRIPT.name).write_text(_SCRIPT_TEXT.replace(old, new), encoding="utf-8")
                result, calls = self.scan_logged(script=copy / "scripts" / SCRIPT.name)
                self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
                problems = self.container_problems(calls, self.root.resolve(), rules.resolve())
                self.assertTrue(any(expected in p for p in problems), (expected, problems))

    def test_default_root_is_the_repository_even_when_started_from_another_directory(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        logs = self.tmp / "calls"
        logs.mkdir()
        env = {**self.env, "FAKE_LOG_DIR": str(logs), "FAKE_RUN_OUTPUT": self.payload(), "FAKE_RUN_RC": "0"}
        result = run("scan", env=env, cwd=elsewhere)
        self.assertNotIn(result.returncode, (USAGE, UNAVAILABLE), result.stderr)
        scan = (logs / "call-1").read_bytes().decode().split("\0")
        self.assertIn(f"{ROOT}:/src:ro", scan)
        files = scan[scan.index("--") + 1 :]
        self.assertIn("scripts/semgrep_report.py", files)  # リポジトリのルートからの相対パス
        self.assertIn("tests/semgrep/test_semgrep.py", files)
        self.assertFalse(any(name.startswith("/") for name in files if name))


class ReportHelper(TempCase):
    """scripts/semgrep_report.py の fixtures 判定を、入口（コマンド）から確かめる。基準は、全部が整合した入力。"""

    RULES = {"r.yml": "rules:\n  - id: rule-a\n    languages: [python]\n"}
    FIXTURES = {"a.py": "# ruleid: rule-a\nbad()\n# ok: rule-a\ngood()\n"}

    def report(self, rules=None, fixtures=None, results="default", scanned="default"):
        work = self.tmp / "work"
        shutil.rmtree(work, ignore_errors=True)
        rules = self.RULES if rules is None else rules
        fixtures = self.FIXTURES if fixtures is None else fixtures
        (work / "rules").mkdir(parents=True)
        (work / "fixtures").mkdir()
        for name, body in rules.items():
            (work / "rules" / name).write_text(body, encoding="utf-8")
        for name, body in fixtures.items():
            write(work / "fixtures", name, body)
        if results == "default":
            results = [("fixtures/a.py", 2, "rule-a")]
        if scanned == "default":
            scanned = [f"fixtures/{name}" for name in fixtures]
        payload = {
            "version": VERSION,
            "errors": [],
            "paths": {"scanned": scanned},
            "results": [
                {"check_id": f"work.rules.{rule}", "path": path, "start": {"line": line}, "extra": {}}
                for path, line, rule in results
            ],
        }
        (work / "out.json").write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", "-I", str(REPORT), "fixtures", "--json", "out.json", "--version", VERSION,
             "--rules", "rules", "--fixtures", "fixtures"],
            capture_output=True, text=True, cwd=work, timeout=60, check=False,
        )  # fmt: skip

    def assert_failure(self, result, message):
        self.assertEqual(result.returncode, FAILURE, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)

    def test_consistent_input_passes(self):
        result = self.report()
        self.assertEqual(result.returncode, CLEAN, result.stdout + result.stderr)
        self.assertIn("ルール 1 件", result.stdout)

    def test_duplicate_rule_ids_fail(self):
        rules = {"r.yml": self.RULES["r.yml"], "s.yml": self.RULES["r.yml"]}
        self.assert_failure(self.report(rules=rules), "ルールが0件、またはIDが重複しています")

    def test_zero_rules_fail(self):
        self.assert_failure(self.report(rules={}), "ルールが0件、またはIDが重複しています")

    def test_rule_without_a_violating_example_fails(self):
        fixtures = {"a.py": "# ok: rule-a\ngood()\n"}
        result = self.report(fixtures=fixtures, results=[])
        self.assert_failure(result, "ルール rule-a に、検出を期待する例（ruleid）がありません")

    def test_rule_without_a_control_example_fails(self):
        fixtures = {"a.py": "# ruleid: rule-a\nbad()\n"}
        self.assert_failure(self.report(fixtures=fixtures), "ルール rule-a に、検出しない例（ok）がありません")

    def test_annotation_for_an_unknown_rule_fails(self):
        fixtures = {**self.FIXTURES, "b.py": "# ruleid: no-such-rule\nbad()\n"}
        self.assert_failure(self.report(fixtures=fixtures), "注釈が、存在しないルールを指しています: no-such-rule")

    def test_fixture_file_that_was_not_scanned_fails(self):
        fixtures = {**self.FIXTURES, "b.py": "x = 1\n"}
        result = self.report(fixtures=fixtures, scanned=["fixtures/a.py"])
        self.assert_failure(result, "フィクスチャが検査されていません: fixtures/b.py")

    def test_same_line_marked_both_ruleid_and_ok_fails(self):
        fixtures = {"a.py": "# ruleid: rule-a  # ok: rule-a\nbad()\n"}
        self.assert_failure(self.report(fixtures=fixtures), "同じ行に、ruleid と ok の両方の注釈があります")

    def test_expected_detection_that_is_missing_fails(self):
        self.assert_failure(self.report(results=[]), "検出されるはずが、検出されませんでした: rule-a  fixtures/a.py:2")

    def test_control_that_is_detected_fails(self):
        results = [("fixtures/a.py", 2, "rule-a"), ("fixtures/a.py", 4, "rule-a")]
        self.assert_failure(self.report(results=results), "検出されないはずが、検出されました: rule-a  fixtures/a.py:4")


if __name__ == "__main__":
    unittest.main()
