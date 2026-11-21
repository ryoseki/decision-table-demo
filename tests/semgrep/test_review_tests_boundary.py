"""scripts/review-tests.sh の境界（レビュー担当に何を渡すか・どう起動するか）を、偽の claude で確かめる。

  python3 -B -I -m unittest discover -s tests/semgrep

- 一時的なGitリポジトリ（架空の中身）に、スクリプトの複製を置いて動かす。本物の claude は起動しない
  （PATH の先頭に置いた偽の claude が、引数・標準入力・環境変数を記録するだけ）。リポジトリは変えない。
- 確かめるのは、スクリプトが **注入するもの** と **起動の形**。変更したファイルの一覧（未追跡を含む）と差分の統計は渡すが、
  ファイルの中身（作者の報告、検出の確認記録、Semgrepの出力、差分の本文）は、プロンプトに注入しない。
  一覧に確認記録のパスが載ることは許す。レビュー担当が、自分でそのファイルを読めるかどうかは、このテストの対象ではない
  （読み取りの制限は、起動の引数の許可・拒否で決まる）。
- 各情報源に、架空の目印を1つずつ置く。目印は、実際の秘密情報ではない。
"""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_semgrep as base
import test_semgrep_docs as docs

SCRIPT = base.ROOT / "scripts" / "review-tests.sh"

FEATURE_MARK = "SENTINEL-FEATURE-0001"  # 渡すもの（機能の詳細）
DIFF_MARK = "SENTINEL-DIFFBODY-0002"  # 追跡中のファイルの変更の本文（統計だけを渡す）
TRIAGE_MARK = "SENTINEL-TRIAGE-0003"  # 検出の確認記録の中身
REPORT_MARK = "SENTINEL-AUTHORREPORT-0004"  # 作者の報告の中身
OUTPUT_MARK = "SENTINEL-SEMGREPOUTPUT-0005"  # Semgrepの出力の中身
API_MARK = "FICTIONAL_SENTINEL_API_VALUE_0006"  # 認証の環境変数（架空の値）
NOT_INJECTED = (DIFF_MARK, TRIAGE_MARK, REPORT_MARK, OUTPUT_MARK, API_MARK)

FAKE_CLAUDE = """\
#!/bin/sh
# 偽の claude: 引数（NUL区切り）・標準入力・環境変数・作業ディレクトリを記録するだけ。
for a in "$@"; do printf '%s\\0' "$a"; done >"$FAKE_CLAUDE_LOG.argv"
cat >"$FAKE_CLAUDE_LOG.stdin"
printf '%s' "${ANTHROPIC_API_KEY-__UNSET__}" >"$FAKE_CLAUDE_LOG.key"
pwd -P >"$FAKE_CLAUDE_LOG.cwd"
echo "fake review output"
exit "${FAKE_CLAUDE_RC:-0}"
"""


class ReviewTestsBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review-tests-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.log = self.tmp / "claude-log"
        self.feature = self.tmp / "feature.md"
        self.feature.write_text(f"機能の詳細（架空）\n{FEATURE_MARK}\n", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "scripts" / "review-tests.sh")
        base.write(self.repo, "docs/test-review-prompt.md", "PROMPT-HEAD（架空のプロンプト）\n")
        base.write(self.repo, "src/app.py", "VALUE = 1\n")
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
        # 作業中の変更: 追跡中のファイルの変更（本文に目印）、未追跡の3ファイル（確認記録・作者の報告・Semgrepの出力）
        base.write(self.repo, "src/app.py", f"VALUE = 2  # {DIFF_MARK}\n")
        base.write(self.repo, "security/semgrep/triage.tsv", f"py-eval-exec-dynamic\tx.py\t1\t{TRIAGE_MARK}\n")
        base.write(self.repo, "notes/author_report.md", f"作者の報告（架空） {REPORT_MARK}\n")
        base.write(self.repo, "notes/semgrep_output.txt", f"Semgrepの出力（架空） {OUTPUT_MARK}\n")

    def env(self, path=None):
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "FAKE_CLAUDE_RC")}
        env.update(
            PATH=path or f"{self.bin}:{os.environ['PATH']}",
            HOME=str(self.tmp),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            ANTHROPIC_API_KEY=API_MARK,
            FAKE_CLAUDE_LOG=str(self.log),
        )
        return env

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env(), check=True, capture_output=True)

    def review(self, *args, env=None):
        return subprocess.run(
            [base.BASH, str(self.repo / "scripts" / "review-tests.sh"), *args],
            capture_output=True, encoding="utf-8", env=env or self.env(), cwd=self.tmp, timeout=60, check=False,
        )  # fmt: skip

    def claude_started(self):
        return Path(f"{self.log}.argv").exists()

    def recorded_argv(self):
        return Path(f"{self.log}.argv").read_bytes().decode().split("\0")[:-1]

    # ---- 渡すもの ----

    def test_dry_run_passes_the_feature_file_list_and_stat_only_and_starts_no_claude(self):
        result = self.review("--feature-file", str(self.feature), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.claude_started(), "dry-run で claude を起動した")
        prompt = result.stdout
        self.assertIn("PROMPT-HEAD", prompt)
        self.assertIn(self.feature.read_text(encoding="utf-8").strip(), prompt)  # 機能の詳細を、そのまま渡す
        self.assertIn("M\tsrc/app.py", prompt)  # 追跡中の変更
        for untracked in ("security/semgrep/triage.tsv", "notes/author_report.md", "notes/semgrep_output.txt"):
            self.assertIn(f"?\t{untracked}", prompt)  # 一覧には、パスだけが載る
        self.assertRegex(prompt, r"src/app\.py\s+\|\s+\d+")  # 差分の統計
        for mark in NOT_INJECTED:
            self.assertNotIn(mark, prompt, "中身がプロンプトに注入されている")

    def test_actual_run_sends_the_same_prompt_on_stdin_without_content_or_credentials(self):
        dry = self.review("--feature-file", str(self.feature), "--dry-run")
        result = self.review("--feature-file", str(self.feature))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.claude_started())
        stdin = Path(f"{self.log}.stdin").read_text(encoding="utf-8")
        self.assertEqual(stdin, dry.stdout)  # 実行と dry-run で、渡す内容が同じ
        argv = self.recorded_argv()
        self.assertIn(FEATURE_MARK, stdin)  # 機能の詳細は、標準入力で渡す
        for mark in NOT_INJECTED:
            self.assertNotIn(mark, stdin, "中身が標準入力に注入されている")
        for mark in NOT_INJECTED + (FEATURE_MARK,):
            self.assertNotIn(mark, "".join(argv), "内容が、起動の引数に載っている")
        self.assertEqual(Path(f"{self.log}.cwd").read_text().strip(), str(self.repo.resolve()))  # リポジトリのルートで動く

    # ---- 起動の形 ----

    def test_actual_run_removes_api_key_and_uses_fresh_read_only_session_options(self):
        self.assertEqual(self.review("--feature-file", str(self.feature)).returncode, 0)
        self.assertEqual(Path(f"{self.log}.key").read_text(), "__UNSET__", "ANTHROPIC_API_KEY が claude に渡っている")
        argv = self.recorded_argv()
        self.assertEqual(argv[0], "-p")
        self.assertIn(("--permission-mode", "dontAsk"), set(zip(argv, argv[1:])))
        self.assertIn("--no-session-persistence", argv)
        for forbidden in ("--resume", "--continue", "-c", "-r", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(argv.count("--permission-mode"), 1)
        allowed_at, denied_at = argv.index("--allowedTools"), argv.index("--disallowedTools")
        self.assertLess(allowed_at, denied_at)
        allowed, denied = argv[allowed_at + 1 : denied_at], argv[denied_at + 1 :]
        self.assertTrue({"Read", "Grep", "Glob"} <= set(allowed), allowed)
        self.assertTrue({"Edit", "Write", "NotebookEdit", "Read(**/.env)", "Read(**/.env.*)"} <= set(denied), denied)
        for entry in allowed:
            self.assertTrue(
                entry in ("Read", "Grep", "Glob") or entry.startswith(("Bash(git ", "Bash(docker compose ")),
                f"許可の一覧に、読み取り・git・docker compose 以外がある: {entry}",
            )
            for word in ("Edit", "Write", "NotebookEdit", ".env", "semgrep", "scan-secrets"):
                self.assertNotIn(word, entry, f"許可の一覧に {word}: {entry}")

    def test_failure_exit_code_of_claude_is_propagated(self):
        env = self.env()
        env["FAKE_CLAUDE_RC"] = "3"
        result = self.review("--feature-file", str(self.feature), env=env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    # ---- 起動しない場合 ----

    def test_missing_or_empty_feature_file_is_a_usage_error_and_starts_no_claude(self):
        empty = self.tmp / "empty.md"
        empty.write_text("", encoding="utf-8")
        for args in ((), ("--feature-file", str(empty)), ("--feature-file", str(self.tmp / "none.md")), ("--feature-file",)):
            with self.subTest(args=args):
                result = self.review(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse(self.claude_started())

    def test_no_changes_is_a_failure_and_starts_no_claude(self):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "all")
        result = self.review("--feature-file", str(self.feature))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.claude_started())

    def test_missing_claude_command_is_a_failure_not_a_review(self):
        # claude を含まない PATH（必要なコマンドだけをリンクする）。手元に本物の claude があっても、起動しない。
        tools = self.tmp / "tools"
        tools.mkdir()
        for name in ("dirname", "cat", "sed", "git", "env"):
            (tools / name).symlink_to(shutil.which(name))
        result = self.review("--feature-file", str(self.feature), env=self.env(path=str(tools)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("claude コマンドが見つかりません", result.stderr)
        self.assertFalse(self.claude_started())


def reviewer_command_tokens():
    """docs/semgrep.md の、一般のレビュー担当の起動例（`claude -p` を含むコードブロック）を、shlex で分解する。"""
    section = docs.section_of(docs.DOC_TEXT, "##", "独立したレビューでの使い方")
    blocks = [b for b in re.findall(r"```[a-z]*\n(.*?)```", section, re.DOTALL) if "claude -p" in b]
    if len(blocks) != 1:
        raise AssertionError(f"`claude -p` を含むコードブロックが {len(blocks)} 個です（1個のはず）")
    return shlex.split(blocks[0].replace("\\\n", " "))


READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}
ALLOWED_BASH = frozenset(
    {
        "Bash(git diff)", "Bash(git diff HEAD)", "Bash(git diff origin/main)", "Bash(git diff --cached)",
        "Bash(git diff --check)", "Bash(git diff --stat)", "Bash(git status --short)",
        "Bash(git log --oneline -10)", "Bash(git show --stat)",
        "Bash(bash scripts/semgrep.sh)", "Bash(bash scripts/semgrep.sh fixtures)",
        "Bash(bash scripts/scan-secrets.sh)",
        "Bash(python3 -B -I -m unittest discover -s tests/semgrep)",
    }
)  # fmt: skip  # 完全一致だけ。ワイルドカード・任意の引数（git diff の --output、--no-index など）は許さない
REQUIRED_DENIED = {"Edit", "Write", "NotebookEdit", "Read(**/.env)", "Read(**/.env.*)"}
FORBIDDEN_FLAGS = {
    "--resume", "--continue", "-c", "-r", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
}  # fmt: skip


def option_values(tokens, flag):
    """`flag` の後ろから、次の `-` で始まる語までの値（可変長）。"""
    if tokens.count(flag) != 1:
        return None
    values = []
    for token in tokens[tokens.index(flag) + 1 :]:
        if token.startswith("--"):
            break
        values.append(token)
    return values


def reviewer_permission_problems(tokens):
    """起動例の権限の問題を、一覧にして返す（引数の順序や、文章の体裁には依存しない）。"""
    problems = []
    pairs = set(zip(tokens, tokens[1:]))
    if "claude" not in tokens or "-p" not in tokens:
        problems.append("claude -p の起動ではない")
    if ("-u", "ANTHROPIC_API_KEY") not in pairs or tokens[0] != "env":
        problems.append("ANTHROPIC_API_KEY を外していない（env -u）")
    if ("--permission-mode", "dontAsk") not in pairs or tokens.count("--permission-mode") != 1:
        problems.append("--permission-mode dontAsk がない")
    if "--no-session-persistence" not in tokens:
        problems.append("--no-session-persistence がない（新しいセッションではない）")
    problems += [f"禁止の引数: {flag}" for flag in sorted(FORBIDDEN_FLAGS & set(tokens))]
    allowed, denied = option_values(tokens, "--allowedTools"), option_values(tokens, "--disallowedTools")
    if allowed is None or denied is None:
        return problems + ["--allowedTools / --disallowedTools が、1つずつではない"]
    for entry in allowed:
        if entry not in READ_ONLY_TOOLS and entry not in ALLOWED_BASH:
            problems.append(f"許可してよい形ではない: {entry}")
    for name in READ_ONLY_TOOLS:
        if name not in allowed:
            problems.append(f"許可がない: {name}")
    for needle in ("scripts/semgrep.sh", "unittest discover -s tests/semgrep"):
        if not any(needle in entry for entry in allowed):
            problems.append(f"許可がない: {needle}")
    problems += [f"拒否がない: {name}" for name in sorted(REQUIRED_DENIED - set(denied))]
    problems += [f"許可と拒否の両方にある: {name}" for name in sorted(set(allowed) & set(denied))]
    return problems


class ReviewerCommandExampleInDocs(unittest.TestCase):
    """docs/semgrep.md の起動例の権限を、意味で確かめる（本物の claude は起動しない）。"""

    def tokens(self):
        return reviewer_command_tokens()

    def test_documented_example_is_fresh_read_only_and_allows_only_named_commands(self):
        self.assertEqual(reviewer_permission_problems(self.tokens()), [])

    def test_the_permission_check_rejects_each_unsafe_change(self):
        base_tokens = self.tokens()
        allowed_at = base_tokens.index("--allowedTools") + 1
        denied_at = base_tokens.index("--disallowedTools") + 1
        cases = {
            "unrestricted Bash": base_tokens[:allowed_at] + ["Bash"] + base_tokens[allowed_at:],
            "Bash wildcard": base_tokens[:allowed_at] + ["Bash(*)"] + base_tokens[allowed_at:],
            "Write allowed": base_tokens[:allowed_at] + ["Write"] + base_tokens[allowed_at:],
            "Edit allowed": base_tokens[:allowed_at] + ["Edit"] + base_tokens[allowed_at:],
            ".env readable via Read(**)": base_tokens[:allowed_at] + ["Read(**/.env)"] + base_tokens[allowed_at:],
            "git push allowed": base_tokens[:allowed_at] + ["Bash(git push *)"] + base_tokens[allowed_at:],
            "git diff wildcard": base_tokens[:allowed_at] + ["Bash(git diff *)"] + base_tokens[allowed_at:],
            "git status wildcard": base_tokens[:allowed_at] + ["Bash(git status *)"] + base_tokens[allowed_at:],
            "git log wildcard": base_tokens[:allowed_at] + ["Bash(git log *)"] + base_tokens[allowed_at:],
            "git show wildcard": base_tokens[:allowed_at] + ["Bash(git show *)"] + base_tokens[allowed_at:],
            "git diff --output": base_tokens[:allowed_at] + ["Bash(git diff --output=x)"] + base_tokens[allowed_at:],
            "git diff --no-index": base_tokens[:allowed_at] + ["Bash(git diff --no-index a b)"] + base_tokens[allowed_at:],
            "semgrep wildcard": base_tokens[:allowed_at] + ["Bash(bash scripts/semgrep.sh *)"] + base_tokens[allowed_at:],
            "scan-secrets wildcard": base_tokens[:allowed_at] + ["Bash(bash scripts/scan-secrets.sh *)"] + base_tokens[allowed_at:],
            "docker allowed": base_tokens[:allowed_at] + ["Bash(docker *)"] + base_tokens[allowed_at:],
            "Write not denied": [t for t in base_tokens if t != "Write"],
            "NotebookEdit not denied": [t for t in base_tokens if t != "NotebookEdit"],
            ".env not denied": [t for t in base_tokens if t != "Read(**/.env)"],
            ".env.* not denied": [t for t in base_tokens if t != "Read(**/.env.*)"],
            "no deny list": base_tokens[: denied_at - 1],
            "api key kept": [t for t in base_tokens if t not in ("env", "-u", "ANTHROPIC_API_KEY")],
            "no dontAsk": [t for t in base_tokens if t != "dontAsk"],
            "session persisted": [t for t in base_tokens if t != "--no-session-persistence"],
            "skip permissions": base_tokens + ["--dangerously-skip-permissions"],
            "resume": base_tokens[:3] + ["--resume"] + base_tokens[3:],
            "no semgrep": [t for t in base_tokens if "scripts/semgrep.sh" not in t],
        }
        for name, tokens in cases.items():
            with self.subTest(change=name):
                self.assertTrue(reviewer_permission_problems(tokens), f"{name}: 問題を見つけられませんでした")

    def test_documented_example_has_no_wildcards_and_no_unsafe_git_arguments(self):
        allowed = option_values(self.tokens(), "--allowedTools")
        self.assertFalse([e for e in allowed if "*" in e], allowed)
        self.assertFalse([e for e in allowed if "--output" in e or "--no-index" in e], allowed)
        self.assertIn("Bash(git diff --check)", allowed)

    def test_the_check_does_not_depend_on_argument_order(self):
        tokens = self.tokens()
        allowed_at, denied_at = tokens.index("--allowedTools"), tokens.index("--disallowedTools")
        reordered = tokens[:allowed_at] + tokens[denied_at:] + tokens[allowed_at:denied_at]
        self.assertEqual(reviewer_permission_problems(reordered), [])


if __name__ == "__main__":
    unittest.main()
