import itertools
import unittest

from app.rule_matching import (
    DEMO_RULES,
    QUESTION_IDS,
    Answer,
    ConditionState,
    Result,
    RowDisplay,
    Rule,
    match_rules,
)

Y, N, U, P = Answer.YES, Answer.NO, Answer.UNANSWERED, Answer.PENDING


def displays(answers):
    return {m.rule_id: m.display for m in match_rules(answers).rules}


class AcceptanceExamples(unittest.TestCase):
    """docs/requirements.md の受入例1〜3（照合の部分）。"""

    def test_example1(self):
        self.assertEqual(
            match_rules({"A01": Y, "A03": Y}).rules[2].mismatched_questions, ("A01",)
        )
        self.assertEqual(
            displays({"A01": Y, "A03": Y}),
            {
                "R01": RowDisplay.NORMAL,
                "R02": RowDisplay.NORMAL,
                "R03": RowDisplay.GRAY,
            },
        )

    def test_example2_correcting_a01_to_no(self):
        self.assertEqual(
            displays({"A01": N, "A03": Y}),
            {"R01": RowDisplay.GRAY, "R02": RowDisplay.GRAY, "R03": RowDisplay.NORMAL},
        )

    def test_example3_all_gray_but_every_result_selectable(self):
        report = match_rules({"A01": Y, "A03": N})
        self.assertEqual(
            [m.mismatched_questions for m in report.rules],
            [("A03",), ("A03",), ("A01", "A03")],
        )
        self.assertTrue(all(m.display is RowDisplay.GRAY for m in report.rules))
        self.assertEqual(
            report.selectable_results,
            (
                Result.ACCOUNT_DELETION,
                Result.ACCOUNT_SUSPENSION,
                Result.CAUTION,
                Result.NO_ISSUE,
            ),
        )


class AnswerStates(unittest.TestCase):
    def test_mismatch_reports_which_questions(self):
        report = match_rules({"A01": Y, "A03": N})
        r01 = report.rules[0]
        self.assertEqual(r01.mismatched_questions, ("A03",))
        self.assertIs(r01.states["A01"], ConditionState.MATCH)

    def test_unanswered_and_pending_never_mismatch(self):
        for filler in (U, P):
            answers = {q: filler for q in QUESTION_IDS}
            state = ConditionState.UNANSWERED if filler is U else ConditionState.PENDING
            for m in match_rules(answers).rules:
                self.assertIs(m.display, RowDisplay.NORMAL)
                self.assertEqual(m.mismatched_questions, ())
                self.assertTrue(all(s is state for s in m.states.values()))

    def test_missing_question_is_unanswered(self):
        self.assertEqual(match_rules({}), match_rules({q: U for q in QUESTION_IDS}))

    def test_pending_is_not_treated_as_no_or_yes(self):
        # A09: R01はY。確認待ちは食い違いにならず、Noに変わると食い違う。
        base = {"A01": Y, "A09": P}
        self.assertIs(displays(base)["R01"], RowDisplay.NORMAL)
        self.assertEqual(
            match_rules(base).rules[0].states["A09"], ConditionState.PENDING
        )
        self.assertIs(displays({"A01": Y, "A09": N})["R01"], RowDisplay.GRAY)

    def test_pending_does_not_hide_other_mismatch(self):
        self.assertIs(displays({"A01": N, "A09": P})["R01"], RowDisplay.GRAY)

    def test_full_answer_matching_r01_only_fully_matches_r01(self):
        answers = dict(DEMO_RULES[0].conditions)
        report = match_rules(answers)
        self.assertIs(report.rules[0].display, RowDisplay.NORMAL)
        self.assertTrue(
            all(s is ConditionState.MATCH for s in report.rules[0].states.values())
        )
        self.assertIs(report.rules[1].display, RowDisplay.GRAY)  # R02はA02が食い違う

    def test_each_rule_fully_matches_its_own_conditions(self):
        for rule in DEMO_RULES:
            report = match_rules(dict(rule.conditions))
            own = next(m for m in report.rules if m.rule_id == rule.rule_id)
            self.assertIs(own.display, RowDisplay.NORMAL)
            self.assertTrue(all(s is ConditionState.MATCH for s in own.states.values()))

    def test_pending_on_a09_never_grays_any_rule(self):
        for rule in DEMO_RULES:
            answers = dict(rule.conditions)
            answers["A09"] = P
            answers["A10"] = P
            own = next(
                m for m in match_rules(answers).rules if m.rule_id == rule.rule_id
            )
            self.assertIs(own.display, RowDisplay.NORMAL)

    def test_string_answers_are_accepted_and_compared_by_value(self):
        self.assertEqual(
            match_rules({"A01": "yes", "A03": "no"}),
            match_rules({"A01": Y, "A03": N}),
        )

    def test_invalid_answer_value_rejected(self):
        for bad in ("Yes", "maybe", None, True):
            with self.assertRaises(ValueError):
                match_rules({"A01": bad})

    def test_invalid_rule_pattern_rejected(self):
        from app.rule_matching import _parse

        for bad in ("Y " * 11, "Y y Y Y Y N Y Y Y N Y Y", "Y " * 13):
            with self.assertRaises(ValueError):
                _parse(bad)

    def test_unknown_question_rejected(self):
        with self.assertRaises(ValueError):
            match_rules({"A13": Y})
        with self.assertRaises(ValueError):
            match_rules({"C01": Y})


class RulesWithSomeConditions(unittest.TestCase):
    """条件のない設問は「問わない」。条件のある設問だけで、通常・グレーが決まる。"""

    RULE = Rule("P1", Result.CAUTION, {"A03": Answer.NO, "A09": Answer.YES})

    def match(self, answers):
        return match_rules(answers, (self.RULE,)).rules[0]

    def test_unconstrained_questions_are_any_and_never_mismatch(self):
        m = self.match({"A01": Y, "A02": N, "A12": P})
        self.assertEqual(m.states["A01"], ConditionState.ANY)
        self.assertEqual(m.display, RowDisplay.NORMAL)

    def test_constrained_questions_decide_the_display(self):
        self.assertEqual(self.match({"A03": N, "A09": Y}).display, RowDisplay.NORMAL)
        matched = self.match({"A03": N, "A09": Y})
        self.assertEqual(matched.states["A03"], ConditionState.MATCH)
        gray = self.match({"A03": Y})
        self.assertEqual(gray.display, RowDisplay.GRAY)
        self.assertEqual(gray.mismatched_questions, ("A03",))


class SelectableResultsNeverRestricted(unittest.TestCase):
    def test_all_results_selectable_for_varied_answers(self):
        expected = tuple(Result)
        samples = itertools.product((Y, N, U, P), repeat=2)
        for a01, a03 in samples:
            report = match_rules({"A01": a01, "A03": a03})
            self.assertEqual(report.selectable_results, expected)
        self.assertEqual(match_rules({}).selectable_results, expected)

    def test_no_rules_still_selectable(self):
        report = match_rules({"A01": Y}, rules=())
        self.assertEqual(report.rules, ())
        self.assertEqual(report.selectable_results, tuple(Result))

    def test_result_order_heavy_first(self):
        self.assertEqual(
            [r.value for r in Result],
            ["アカウント削除", "アカウント一時凍結", "注意", "問題なし"],
        )


class DemoRulesMatchRequirements(unittest.TestCase):
    def test_rules_equal_requirements_table(self):
        table = {
            "R01": ("注意", "YYYYYNYYYNYY"),
            "R02": ("問題なし", "YNYYYYYYYYYN"),
            "R03": ("アカウント一時凍結", "NYYYYNYYYNYY"),
        }
        for rule in DEMO_RULES:
            label, pattern = table[rule.rule_id]
            self.assertEqual(rule.result.value, label)
            actual = "".join(
                "Y" if rule.conditions[q] is Y else "N" for q in QUESTION_IDS
            )
            self.assertEqual(actual, pattern)


if __name__ == "__main__":
    unittest.main()
