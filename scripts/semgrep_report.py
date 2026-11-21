#!/usr/bin/env python3
"""Semgrepの `--json` 出力を判定する。`scripts/semgrep.sh` から呼ぶ（単独では使わない）。

終了コードで、結果を区別する。Semgrepの終了コードだけには頼らず、JSONの中身を見る。
  0  = 問題なし（検出なし、エラーなし、渡した全ファイルを検査した）
  10 = 検出あり（エラーなし、渡した全ファイルを検査した）
  30 = 失敗（JSONが読めない、版が違う、構文エラーなどのエラー、検査されなかったファイル、
       エンジンの終了コード1（検出あり）なのに出力の results が空、
       フィクスチャの期待との不一致）。検査したファイルが0件のときも、成功として扱わない（呼び出し側が失敗にする）。
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

CLEAN, FINDINGS, FAILURE = 0, 10, 30

# フィクスチャの注釈。`# ruleid: <id>` / `// ruleid: <id>` / `{/* ruleid: <id> */}`。
# 注釈の次の行で、そのルールが検出される（ruleid）、検出されない（ok）ことを期待する。
ANNOTATION = re.compile(r"(?:#|//|/\*)\s*(ruleid|ok):\s*([A-Za-z0-9_.-]+)")
RULE_ID = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$", re.MULTILINE)


def fail(message):
    print(f"Semgrep: 失敗: {message}", file=sys.stderr)
    return FAILURE


def load(path, expected_version):
    """JSONを読み、版・エラーを確かめる。問題があれば、メッセージを返す。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"Semgrepの出力（JSON）を読めません: {type(exc).__name__}"
    if not isinstance(data, dict) or "results" not in data or "paths" not in data:
        return None, "Semgrepの出力に、results か paths がありません。"
    if data.get("version") != expected_version:
        return None, f"Semgrepの版が固定と違います（期待 {expected_version}、実際 {data.get('version')}）。"
    errors = data.get("errors") or []
    if errors:
        lines = []
        for error in errors[:10]:
            location = (error.get("spans") or [{}])[0].get("file") or error.get("path") or "-"
            kind = error.get("type")
            if isinstance(kind, list) and kind:
                kind = kind[0]
            lines.append(f"  - {kind} {location}: {str(error.get('message') or error.get('long_msg') or '')[:200]}")
        more = f"\n  （ほか {len(errors) - 10} 件）" if len(errors) > 10 else ""
        return None, "構文エラーなど、検査を完了できなかった項目があります。\n" + "\n".join(lines) + more
    return data, None


def missing_files(data, expected):
    scanned = set(data["paths"].get("scanned") or [])
    return sorted(set(expected) - scanned), scanned


def finding_key(result):
    # 設定ファイルの場所に由来する接頭辞（security.semgrep.rules.python. など）を外し、ルールIDにする。
    return result["path"], result["start"]["line"], result["check_id"].rsplit(".", 1)[-1]


def read_expected(path):
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def command_scan(args):
    data, problem = load(args.json, args.version)
    if problem:
        return fail(problem)
    expected = read_expected(args.expected)
    if not expected:
        return fail("検査に渡したファイルが0件です。検査していません。")
    missing, scanned = missing_files(data, expected)
    if missing:
        listing = "\n".join(f"  - {name}" for name in missing[:10])
        return fail(f"検査に渡したのに、検査されなかったファイルが {len(missing)} 件あります（除外・読み取り失敗）。\n{listing}")
    by_suffix = Counter(Path(name).suffix for name in expected)
    inventory = ", ".join(f"{suffix} {count}" for suffix, count in sorted(by_suffix.items()))
    print(f"Semgrep {data['version']}: {len(expected)} ファイルを検査（{inventory}）。")
    results = data["results"]
    if args.engine_rc == 1 and not results:
        # --error の終了コード1は「検出あり」。出力の results が空なら矛盾しているので、問題なしにしない。
        return fail("Semgrepは検出ありの終了コード（1）を返したのに、出力の results が空です。結果を信用できません。")
    for result in sorted(results, key=lambda r: (r["path"], r["start"]["line"], r["check_id"])):
        path, line, rule = finding_key(result)
        print(f"  {rule}  {path}:{line}")
    if results:
        print(f"Semgrep: 検出 {len(results)} 件。内容を読んで、真の問題か誤検出かを判断してください。")
        return FINDINGS
    print("Semgrep: 検出 0 件。")
    return CLEAN


def parse_annotations(fixtures_dir):
    """フィクスチャから、期待（ruleid／ok）を集める。キーは（相対パス, 行, ルールID）。"""
    should, should_not, files = set(), set(), []
    for path in sorted(fixtures_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.as_posix()
        files.append(relative)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for kind, rule in ANNOTATION.findall(line):
                (should if kind == "ruleid" else should_not).add((relative, number + 1, rule))
    return should, should_not, files


def command_fixtures(args):
    rules_dir, fixtures_dir = Path(args.rules), Path(args.fixtures)
    data, problem = load(args.json, args.version)
    if problem:
        return fail(problem)
    rule_ids = []
    for path in sorted(rules_dir.glob("*.yml")):
        rule_ids += RULE_ID.findall(path.read_text(encoding="utf-8"))
    if not rule_ids or len(rule_ids) != len(set(rule_ids)):
        return fail("ルールが0件、またはIDが重複しています。")
    should, should_not, files = parse_annotations(fixtures_dir)
    missing, _ = missing_files(data, files)
    if missing:
        return fail("フィクスチャが検査されていません: " + ", ".join(missing))
    problems = []
    for rule in rule_ids:
        if not any(r == rule for _, _, r in should):
            problems.append(f"ルール {rule} に、検出を期待する例（ruleid）がありません。")
        if not any(r == rule for _, _, r in should_not):
            problems.append(f"ルール {rule} に、検出しない例（ok）がありません。")
    for _, _, rule in should | should_not:
        if rule not in rule_ids:
            problems.append(f"注釈が、存在しないルールを指しています: {rule}")
    if should & should_not:
        problems.append("同じ行に、ruleid と ok の両方の注釈があります。")
    actual = {finding_key(r) for r in data["results"]}
    for path, line, rule in sorted(should - actual):
        problems.append(f"検出されるはずが、検出されませんでした: {rule}  {path}:{line}")
    for path, line, rule in sorted(actual - should):
        problems.append(f"検出されないはずが、検出されました: {rule}  {path}:{line}")
    if problems:
        return fail("フィクスチャが、期待と一致しません。\n" + "\n".join(f"  - {p}" for p in problems))
    print(
        f"Semgrep {data['version']}: ルール {len(rule_ids)} 件、フィクスチャ {len(files)} ファイル。"
        f"検出を期待する {len(should)} 箇所をすべて検出し、検出しない例 {len(should_not)} 箇所は検出しませんでした。"
    )
    return CLEAN


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("scan", command_scan), ("fixtures", command_fixtures)):
        p = sub.add_parser(name)
        p.add_argument("--json", required=True)
        p.add_argument("--version", required=True)
        if name == "scan":
            p.add_argument("--expected", required=True, help="Semgrepに渡したファイルの一覧（1行1件）")
            p.add_argument("--engine-rc", type=int, required=True, help="Semgrepの終了コード（1なら検出ありのはず）")
        else:
            p.add_argument("--rules", required=True)
            p.add_argument("--fixtures", required=True)
        p.set_defaults(handler=handler)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
