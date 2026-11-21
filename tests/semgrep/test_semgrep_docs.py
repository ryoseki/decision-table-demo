"""docs/semgrep.md の、宣言した値（版・digest・対象・ルールID・終了コード）が、実際の値と合っているかを確かめる。

  python3 -B -I -m unittest discover -s tests/semgrep

- 文章の言い回しは確かめない。文書から、バッククォートや表の値を取り出し、スクリプト・ルール・フィクスチャの実際の値と比べる。
- 期待値を、このテストに書き写さない（実際の値から取り出す）。ずれたときは、文書か実装の、正しいほうに合わせて、手で直す。
- Dockerやエンジンは使わない（速い）。
"""

import re
import unittest

import test_semgrep as base

SCRIPT_TEXT = base._SCRIPT_TEXT
DOC_TEXT = base.DOC.read_text(encoding="utf-8")
RULES_DIR = base.ROOT / "security" / "semgrep" / "rules"
FIXTURES_DIR = base.ROOT / "security" / "semgrep" / "fixtures"

RULE_ID_LINE = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$", re.MULTILINE)
ANNOTATION = re.compile(r"(?:#|//|/\*)\s*(ruleid|ok):\s*([A-Za-z0-9_.-]+)")
RULE_LIKE = re.compile(r"^(?:py|ts)-[a-z0-9]+(?:-[a-z0-9]+)+$")


def doc_section(title):
    """`## <title>` から、次の `## ` までの本文（`###` は含む）。"""
    lines, found = [], False
    for line in DOC_TEXT.splitlines():
        if line.startswith("## "):
            if found:
                break
            found = line == f"## {title}"
        elif found:
            lines.append(line)
    if not found:
        raise AssertionError(f"docs/semgrep.md に「{title}」の節がありません")
    return "\n".join(lines)


def script_array(name):
    found = re.search(rf"^{name}=\(([^)]*)\)", SCRIPT_TEXT, re.MULTILINE)
    if not found:
        raise AssertionError(f"scripts/semgrep.sh に {name} の配列がありません")
    return found.group(1).split()


def actual_rule_ids():
    ids = []
    for path in sorted(RULES_DIR.glob("*.yml")):
        ids += RULE_ID_LINE.findall(path.read_text(encoding="utf-8"))
    return ids


def documented_rule_ids():
    """「ルールと理由」の表（行頭が `| \\`id\\` |`）から、ルールIDを集める。"""
    return re.findall(r"^\| `([^`]+)` \|", doc_section("ルールと理由"), re.MULTILINE)


class DocumentedValuesMatchActualValues(unittest.TestCase):
    def test_documented_version_and_digest_match_the_pinned_values(self):
        section = doc_section("版の固定")
        documented_version = re.search(r"Semgrep \*\*(\d+\.\d+\.\d+)\*\*", section)
        self.assertIsNotNone(documented_version, "文書に、固定した版（Semgrep **x.y.z**）がありません")
        self.assertEqual(documented_version.group(1), base.VERSION)
        digest = base.IMAGE.split("@", 1)[1]
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(re.findall(r"`(sha256:[0-9a-f]{64})`", section), [digest])
        # 実在の確認で挙げたタグ（GitHub の `v<版>`、Docker Hub の `<版>`）も、固定した版と同じ
        self.assertIn(f"`v{base.VERSION}`", section)
        self.assertIn(f"`{base.VERSION}`", section)
        for other in set(re.findall(r"`v?(\d+\.\d+\.\d+)`", section)) - {base.VERSION}:
            self.fail(f"文書の「版の固定」に、固定した版と違う版のタグがあります: {other}")
        self.assertIn("SEMGREP_VERSION", section)
        self.assertIn("SEMGREP_IMAGE", section)

    def test_documented_targets_and_exclusions_match_the_script(self):
        self.assertEqual(sorted(base.documented_names("対象のディレクトリ")), sorted(script_array("TARGET_DIRS")))
        self.assertEqual(
            sorted(name.lstrip(".") for name in base.documented_names("対象の拡張子")), sorted(script_array("TARGET_EXTENSIONS"))
        )
        self.assertTrue(all(name.startswith(".") for name in base.documented_names("対象の拡張子")))
        self.assertEqual(sorted(base.documented_names("除外するディレクトリ名")), sorted(script_array("EXCLUDE_DIRS")))

    def test_documented_rule_ids_match_the_rules_and_the_fixtures(self):
        actual = actual_rule_ids()
        self.assertTrue(actual)
        self.assertEqual(len(actual), len(set(actual)), "ルールIDが重複しています")
        self.assertEqual(sorted(documented_rule_ids()), sorted(actual), "文書の表と、ルールのファイルのIDが違います")
        annotated = {"ruleid": set(), "ok": set()}
        for path in sorted(FIXTURES_DIR.rglob("*")):
            if path.is_file():
                for kind, rule in ANNOTATION.findall(path.read_text(encoding="utf-8")):
                    annotated[kind].add(rule)
        self.assertEqual(annotated["ruleid"], set(actual), "ruleid の例がないルール、または存在しないルールを指す注釈があります")
        self.assertEqual(annotated["ok"], set(actual), "ok の例がないルール、または存在しないルールを指す注釈があります")

    def test_rule_ids_mentioned_anywhere_in_the_document_exist(self):
        actual = set(actual_rule_ids())
        mentioned = {token for token in re.findall(r"`([^`\s]+)`", DOC_TEXT) if RULE_LIKE.match(token)}
        self.assertTrue(mentioned)
        self.assertEqual(mentioned - actual, set(), "文書が、存在しないルールIDに触れています")

    def test_documented_exit_codes_match_the_script(self):
        in_script = {int(code) for code in re.findall(r"^#\s+(\d+)\s+=", SCRIPT_TEXT, re.MULTILINE)}
        self.assertEqual(in_script, {base.CLEAN, base.FINDINGS, base.UNAVAILABLE, base.FAILURE, base.USAGE})
        table = doc_section("使い方").split("### 終了コード", 1)[1]
        in_doc = {int(code) for code in re.findall(r"^\| (\d+) \|", table, re.MULTILINE)}
        self.assertEqual(in_doc, in_script)


def section_of(text, heading_prefix, title):
    """`<heading_prefix> <title>` の行から、同じ階層以上の次の見出しまでの本文。"""
    level = len(heading_prefix)
    lines, found = [], False
    for line in text.splitlines():
        match = re.match(r"^(#+) ", line)
        if match and found and len(match.group(1)) <= level:
            break
        if line == f"{heading_prefix} {title}":
            found = True
        elif found:
            lines.append(line)
    if not found:
        raise AssertionError(f"「{heading_prefix} {title}」の節がありません")
    return "\n".join(lines)


def canonical_exit_codes():
    """docs/semgrep.md の終了コードの表から、{コード: 意味の最初の文}。"""
    table = doc_section("使い方").split("### 終了コード", 1)[1]
    return {int(code): meaning for code, meaning in re.findall(r"^\| (\d+) \| ([^|]+) \|", table, re.MULTILINE)}


NOT_HANDED_OVER = "渡さない"


class CrossDocConsistency(unittest.TestCase):
    """複数の文書が、同じ決まりを、食い違わずに書いているか（文章全体ではなく、宣言した値と制限の要点だけ）。"""

    JA_NAMES = {"問題なし": 0, "検出あり": 10, "実行できない": 20, "失敗": 30}
    EN_TO_JA = {"no findings": "問題なし", "findings": "検出あり", "unavailable": "実行できない", "failure": "失敗"}

    def assert_pairs_match_canonical(self, pairs, label):
        canonical = canonical_exit_codes()
        self.assertEqual({code for code, _ in pairs}, {0, 10, 20, 30}, f"{label}: 4区分のコードが揃っていません: {pairs}")
        for code, name in pairs:
            self.assertTrue(
                canonical[code].startswith(name), f"{label}: コード {code} を「{name}」としているが、正本は「{canonical[code]}」"
            )

    def test_readme_result_codes_agree_with_the_canonical_table(self):
        readme = (base.ROOT / "README.md").read_text(encoding="utf-8")
        section = section_of(readme, "##", "Static analysis with Semgrep")
        pairs = [
            (int(code), self.EN_TO_JA[word])
            for code, word in re.findall(r"`(\d+)` (?:means )?(no findings|findings|unavailable|failure)\b", section)
        ]
        self.assert_pairs_match_canonical(pairs, "README.md")

    def test_testing_policy_result_codes_agree_with_the_canonical_table(self):
        policy = (base.ROOT / "docs" / "testing-policy.md").read_text(encoding="utf-8")
        pairs = [(int(code), name) for name, code in re.findall(r"(問題なし|検出あり|実行できない|失敗)（(\d+)）", policy)]
        self.assert_pairs_match_canonical(pairs, "docs/testing-policy.md")

    def sentences_about_semgrep_and_test_gap_review(self, text):
        found = [
            part
            for part in re.split(r"。", text)
            if "Semgrep" in part and "テスト不足のレビュー" in part
        ]
        self.assertTrue(found, "Semgrepの結果と「テスト不足のレビュー」を結ぶ記述がありません")
        return found

    def assert_not_handed_over(self, fragments, label):
        for fragment in fragments:
            self.assertIn(NOT_HANDED_OVER, fragment, f"{label}: 「渡さない」がありません: {fragment}")
            self.assertNotIn("渡す", fragment, f"{label}: 「渡す」と書いています: {fragment}")

    def test_semgrep_results_are_not_handed_to_the_test_gap_review_in_every_document(self):
        agents = (base.ROOT / "AGENTS.md").read_text(encoding="utf-8")
        agents_fragments = self.sentences_about_semgrep_and_test_gap_review(agents)
        for fragment in agents_fragments:
            self.assertIn("triage", fragment)  # 確認記録と作業メモも渡さない、という要点
            self.assertIn("作業メモ", fragment)
        self.assert_not_handed_over(agents_fragments, "AGENTS.md")

        policy = (base.ROOT / "docs" / "testing-policy.md").read_text(encoding="utf-8")
        policy_fragments = self.sentences_about_semgrep_and_test_gap_review(policy)
        for fragment in policy_fragments:
            self.assertIn("確認記録", fragment)
        self.assert_not_handed_over(policy_fragments, "docs/testing-policy.md")

        own = section_of(DOC_TEXT, "###", "テスト不足のレビューには渡さない")
        own_fragments = [part for part in re.split(r"。", own) if "Semgrep" in part]
        self.assertTrue(own_fragments)
        for fragment in own_fragments:
            self.assertIn("triage", fragment)
            self.assertIn("作業メモ", fragment)
        self.assert_not_handed_over(own_fragments, "docs/semgrep.md")


if __name__ == "__main__":
    unittest.main()
