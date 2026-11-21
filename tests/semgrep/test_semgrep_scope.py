"""ルールの適用範囲と、ファイル名・リンクの扱いを、固定したSemgrepの実エンジンで確かめる。

  python3 -B -I -m unittest discover -s tests/semgrep

- 各例は、小さな架空のソース。検出するはずの行に、行末の印 `HIT: <ルールID>`（`//` か `#` の注釈）を付ける。
  印のない行は、検出しないことを確かめる。印は、検査の入力のコメントであり、Semgrepの出力を読んだ結果の期待ではない。
- 「限界」の例は、**この版（固定したdigest）の現在の挙動の記録**であり、あるべき姿ではない。
  版やルールを変えて挙動が変わったときは、テストが失敗する。そのとき、挙動と docs/semgrep.md の限界を読み直して、
  両方を一緒に直す（期待を自動では更新しない）。
- 実エンジンが使えないときは、スキップせず失敗にする（test_semgrep.py と同じ理由）。
"""

import re
import subprocess
import unittest
from pathlib import Path

import test_semgrep as base

FINDING = re.compile(r"^  (\S+)  (.+):(\d+)$", re.MULTILINE)
HIT = re.compile(r"HIT:\s*([A-Za-z0-9_.-]+)")

SHELL = "ts-child-process-shell"
SQL = "py-sql-dynamic-string"
TS_EVAL = "ts-eval-dynamic-code"
PY_EVAL = "py-eval-exec-dynamic"


def expected_hits(files):
    """各ソースの `HIT: <ルールID>` の印から、（ルールID, 相対パス, 行）の期待を集める。"""
    hits = set()
    for name, body in files.items():
        for number, line in enumerate(body.splitlines(), start=1):
            found = HIT.search(line)
            if found:
                hits.add((found.group(1), name, number))
    return hits


class Scope(base.TempCase):
    def scan(self, files):
        for name, body in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        result = subprocess.run(
            [base.BASH, str(base.SCRIPT), "scan", "--root", str(self.root)],
            capture_output=True, encoding="utf-8", timeout=300, check=False,
        )  # fmt: skip
        self.assertNotEqual(result.returncode, base.UNAVAILABLE, "docker または固定イメージが使えません: " + result.stderr)
        self.assertIn(result.returncode, (base.CLEAN, base.FINDINGS), result.stdout + result.stderr)
        return result

    def assert_hits(self, files):
        result = self.scan(files)
        actual = {(rule, path, int(line)) for rule, path, line in FINDING.findall(result.stdout)}
        expected = expected_hits(files)
        self.assertEqual(
            actual,
            expected,
            f"検出されるはずが検出されない: {sorted(expected - actual)} / 検出されないはずが検出された: {sorted(actual - expected)}",
        )
        self.assertIn(f"{len(files)} ファイルを検査", result.stdout)
        self.assertIn(f"検出 {len(expected)} 件", result.stdout)
        return result

    # ---- 2. ts-child-process-shell: import の形ごとの範囲（ファイルごとに分け、import の範囲が混ざらないようにする） ----

    def test_child_process_detected_import_forms(self):
        self.assert_hits(
            {
                # 指定子が "child_process"（node: なし）なら、別名も解決して検出する。
                "frontend/src/ns_alias.ts": 'import * as cp from "child_process";\nexport const a = (x: string) => {\n  cp.exec(`ls ${x}`); // HIT: %s\n};\n' % SHELL,
                "frontend/src/named_alias.ts": 'import { exec as run } from "child_process";\nexport const a = (x: string) => {\n  run(`ls ${x}`); // HIT: %s\n};\n' % SHELL,
                "frontend/src/require_ns.js": 'const cp = require("child_process");\nfunction a(x) {\n  cp.exec(`ls ${x}`); // HIT: %s\n}\nmodule.exports = a;\n' % SHELL,
                "frontend/src/require_alias.js": 'const { execSync: es } = require("child_process");\nfunction a(x) {\n  es(`ls ${x}`); // HIT: %s\n}\nmodule.exports = a;\n' % SHELL,
                "frontend/src/require_destructure.js": 'const { exec } = require("child_process");\nfunction a(x) {\n  exec(`ls ${x}`); // HIT: %s\n}\nmodule.exports = a;\n' % SHELL,
                # 別名なしの名前付きimportは、node: ありでも検出する。
                "frontend/src/named_node.ts": 'import { exec } from "node:child_process";\nexport const a = (x: string) => {\n  exec(`ls ${x}`); // HIT: %s\n};\n' % SHELL,
                # 名前空間の名前そのものが child_process なら、node: ありでも検出する（名前で一致する）。
                "frontend/src/ns_literal_node.ts": 'import * as child_process from "node:child_process";\nexport const a = (x: string) => {\n  child_process.exec(`ls ${x}`); // HIT: %s\n};\n' % SHELL,
            }
        )

    def test_child_process_unrelated_code_is_not_detected(self):
        # 別ファイルに分けて、import の範囲が、ほかの例を巻き込まないようにする。
        self.assert_hits(
            {
                "frontend/src/other_module.ts": 'import { exec } from "./runner";\nexport const a = (x: string) => {\n  exec(`ls ${x}`);\n};\n',
                "frontend/src/local_function.ts": 'import { execFileSync } from "node:child_process";\nfunction exec(c: string) {\n  return execFileSync("echo", [c]);\n}\nexport const a = (x: string) => {\n  return exec(`ls ${x}`);\n};\n',
                "frontend/src/no_import.ts": "function exec(c: string) {\n  return c;\n}\nexport const a = (x: string) => {\n  return exec(`ls ${x}`);\n};\n",
                "frontend/src/object_method.ts": "const other = {\n  exec(c: string) {\n    return c;\n  },\n};\nexport const a = (x: string) => {\n  return other.exec(`ls ${x}`);\n};\n",
                "frontend/src/non_shell_function.ts": 'import { execFileSync } from "node:child_process";\nexport const a = (c: string, n: string) => {\n  execFileSync(c, [n]);\n};\n',
                "frontend/src/fixed_command.ts": 'import { exec } from "node:child_process";\nexport const a = () => {\n  exec("ls -l");\n};\n',
            }
        )

    def test_child_process_known_limits_node_prefixed_aliases_and_default_import(self):
        # 限界（現在の挙動の記録。あるべき姿ではない）: 次の書き方は、シェルを通す呼び出しでも、検出しない。
        # 検出できる書き方は、test_child_process_detected_import_forms を参照。
        self.assert_hits(
            {
                "frontend/src/named_alias_node.ts": 'import { exec as run } from "node:child_process";\nexport const a = (x: string) => {\n  run(`ls ${x}`);\n};\n',
                "frontend/src/ns_alias_node.ts": 'import * as cp from "node:child_process";\nexport const a = (x: string) => {\n  cp.execSync(`ls ${x}`);\n};\n',
                "frontend/src/require_node.js": 'const cp = require("node:child_process");\nfunction a(x) {\n  cp.exec(`ls ${x}`);\n}\nmodule.exports = a;\n',
                "frontend/src/default_import.ts": 'import cp from "child_process";\nexport const a = (x: string) => {\n  cp.execSync(`ls ${x}`);\n};\n',
            }
        )

    # ---- Python: import の別名と、自前の関数 ----

    def test_python_shell_aliases_are_detected_and_local_functions_are_not(self):
        self.assert_hits(
            {
                "backend/app/imported.py": 'import os as o\nimport subprocess as sp\nfrom os import system\nfrom subprocess import run as r\n\n\ndef a(x):\n    sp.run(f"ls {x}", shell=True)  # HIT: py-shell-command-injection\n    r(f"ls {x}", shell=True)  # HIT: py-shell-command-injection\n    system(x)  # HIT: py-shell-command-injection\n    o.popen(x)  # HIT: py-shell-command-injection\n',
                "backend/app/local.py": 'def run(command, shell=False):\n    return command\n\n\ndef system(command):\n    return command\n\n\ndef a(x):\n    run(f"ls {x}", shell=True)\n    system(x)\n',
                "backend/app/list_form.py": 'from subprocess import run\n\n\ndef a(x):\n    run(["ls", x])\n    run("ls -l", shell=True)\n',
            }
        )

    # ---- 3. SQL: 再代入（保守的な挙動の記録） ----

    def test_sql_reassignment_is_not_tracked(self):
        # 同じ関数内で、動的な文字列を代入した変数を実行すると、後で固定の文字列に代入し直していても検出する（誤検出側）。
        # 動的な代入が、分岐やループの中にあるときは、検出しない（見逃し側）。ルールは、代入の順序や分岐を追わない。
        self.assert_hits(
            {
                "backend/app/dynamic_then_fixed.py": 'def a(cur, t):\n    q = f"DELETE FROM {t}"\n    q = "SELECT 1"\n    cur.execute(q)  # HIT: %s\n' % SQL,
                "backend/app/fixed_then_dynamic.py": 'def a(cur, t):\n    q = "SELECT 1"\n    q = f"DELETE FROM {t}"\n    cur.execute(q)  # HIT: %s\n' % SQL,
                "backend/app/dynamic_then_fixed_in_branch.py": 'def a(cur, t, flag):\n    q = f"DELETE FROM {t}"\n    if flag:\n        q = "SELECT 1"\n    cur.execute(q)  # HIT: %s\n' % SQL,
                "backend/app/dynamic_in_branch.py": 'def a(cur, t):\n    q = "SELECT 1"\n    if t:\n        q = f"DELETE FROM {t}"\n    cur.execute(q)\n',
                "backend/app/dynamic_in_loop.py": 'def a(cur, t):\n    q = "SELECT 1"\n    for _ in range(1):\n        q = f"DELETE FROM {t}"\n    cur.execute(q)\n',
                "backend/app/value_not_statement.py": 'def a(cur, t):\n    q = f"DELETE FROM {t}"\n    q = "SELECT 1"\n    cur.execute("SELECT 2", (q,))\n',
            }
        )

    # ---- 4. eval と文字列のタイマー ----

    def test_fixed_string_eval_is_detected_in_typescript_but_not_in_python(self):
        self.assert_hits(
            {
                "frontend/src/fixed_eval.ts": 'export const a = eval("1 + 1"); // HIT: %s\n' % TS_EVAL,
                "backend/app/fixed_eval.py": 'a = eval("1 + 1")\n',
                "backend/app/dynamic_eval.py": "def a(x):\n    return eval(x)  # HIT: %s\n" % PY_EVAL,
            }
        )

    def test_eval_and_timer_known_limits(self):
        # 限界（現在の挙動の記録）: window・globalThis 経由の eval、間接呼び出し、引数の変数にある文字列のタイマーは、検出しない。
        # 定数に束縛した固定の文字列を渡す setTimeout は、検出する（Semgrepの定数の解決）。
        self.assert_hits(
            {
                "frontend/src/qualified_eval.ts": "export const a = (x: string) => {\n  window.eval(x);\n  globalThis.eval(x);\n  (0, eval)(x);\n};\n",
                "frontend/src/timer_const.ts": 'export const a = () => {\n  const t = "tick()";\n  setTimeout(t, 1); // HIT: %s\n};\n' % TS_EVAL,
                "frontend/src/timer_param.ts": "export const a = (x: string) => {\n  setTimeout(x, 1);\n  setInterval(x, 1);\n};\n",
                "frontend/src/timer_qualified.ts": 'export const a = () => {\n  window.setTimeout("tick()", 1);\n  globalThis.setTimeout("tick()", 1);\n};\n',
            }
        )

    # ---- 5. 特殊なファイル名（対象のディレクトリの下） ----

    def test_unusual_file_names_are_parsed_inventoried_and_located_exactly(self):
        evil_py = "eval(input())  # HIT: %s\n" % PY_EVAL
        evil_ts = "eval(code); // HIT: %s\n" % TS_EVAL
        result = self.assert_hits(
            {
                "backend/app/my file.py": evil_py,  # 空白
                "backend/app/日本語.py": evil_py,  # 日本語
                "backend/app/-lead.py": evil_py,  # 先頭がハイフン
                "backend/-dir/x.py": evil_py,  # 先頭がハイフンのディレクトリ
                "backend/日本 語/-y z.py": evil_py,  # 空白・日本語・ハイフンの組み合わせ
                "frontend/src/日本 語.ts": evil_ts,
                "backend/app/clean file.py": "x = 1\n",  # 構文チェックと一覧には入り、検出はない
                "frontend/src/清潔 -1.ts": "export const x = 1;\n",
            }
        )
        self.assertIn("8 ファイルを検査（.py 6, .ts 2）", result.stdout)
        self.assertIn("  py-eval-exec-dynamic  backend/日本 語/-y z.py:1", result.stdout)  # 規則・パス・行を一字一句

    # ---- 7. シンボリックリンク（同じルートの中の、読めるリンク） ----

    def test_readable_symlinks_inside_the_root_are_not_followed(self):
        # ルートの外のリンクは、test_semgrep.py の test_symlinks_are_not_followed が確かめる。
        # ここでは、基準（正常）は、実ファイル1つだけが検査され、リンクをたどらないことを確かめる。
        # リンクをたどる変更を入れると、重複の件数になるとは限らず、エラー（30）で失敗することもある。
        write = base.write
        write(self.root, "backend/real/evil.py", base.PY_EVIL)
        (self.root / "backend" / "link_file.py").symlink_to("real/evil.py")  # ファイルのリンク
        (self.root / "backend" / "link_dir").symlink_to("real", target_is_directory=True)  # 中のディレクトリのリンク
        (self.root / "frontend").symlink_to("backend", target_is_directory=True)  # 対象のディレクトリそのものがリンク
        result = self.scan({})
        self.assertEqual(result.returncode, base.FINDINGS, result.stdout + result.stderr)
        self.assertEqual(
            {(rule, path, int(line)) for rule, path, line in FINDING.findall(result.stdout)},
            {(PY_EVAL, "backend/real/evil.py", 1)},
            result.stdout,
        )
        self.assertIn("1 ファイルを検査", result.stdout)
        self.assertIn("検出 1 件", result.stdout)
        for leaked in ("link_file", "link_dir", "frontend/"):
            self.assertNotIn(leaked, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
