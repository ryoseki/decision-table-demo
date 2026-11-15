"""判定ルールの追加案・削除案（管理者AIの試作）を、アプリ側で検証する純粋な関数。

AIの出力は信用せず、形式・参照する設問と結果・重複・対象をここで確かめる。
DBにもHTTPにも依存しない。通った出力だけが、サーバーが持つ検証済みの提案になる。

デモ限定の方針（本番の仕様ではない。docs/admin-rule-proposal-demo.md）:
- 重複は、「同じID」、または「条件（設問ごとのYes／No。並びは無関係）と結果が
  すべて一致する既存ルール」。条件が同じで結果が違うルールは、重複にしない（優先順位や禁止は決めない）。
- 追加してよいIDは、これまでに使ったIDの最大番号の次（削除したIDは再利用しない）。
"""

import re
from dataclasses import dataclass
from typing import Any

from app.domain import RESULT_CODES, InvalidProposal
from app.question_validation import DemoQuestion

OPERATION_ADD = "add_rule"
OPERATION_REMOVE = "remove_rule"

# 2桁以上（R99の次はR100）。採番の上限は設けない。
_RULE_ID = re.compile(r"R[0-9]{2,}")
_QUESTION_ID = re.compile(r"A[0-9]{2}")
_ANSWERS = ("yes", "no")


@dataclass(frozen=True)
class DemoRule:
    rule_id: str
    revision: int
    result_code: str
    # (設問ID, "yes" | "no") を設問ID順に並べたもの。並び順の違いで別のルールにしない。
    conditions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ValidatedRuleAdd:
    """検証済みの追加案。基準にしたルール一覧（base）とその版（base_revision）、
    検証に使った設問一覧の版（base_question_revision）を持つ。どちらが変わっても、承認は拒否する。"""

    base_revision: int
    base: tuple[DemoRule, ...]
    added: DemoRule
    base_question_revision: int

    @property
    def after(self) -> tuple[DemoRule, ...]:
        return (*self.base, self.added)


@dataclass(frozen=True)
class ValidatedRuleRemoval:
    """検証済みの削除案。デモの一覧から外す（過去版の保持・物理削除の方針は決めない）。"""

    base_revision: int
    base: tuple[DemoRule, ...]
    target: DemoRule
    base_question_revision: int

    @property
    def after(self) -> tuple[DemoRule, ...]:
        return tuple(r for r in self.base if r.rule_id != self.target.rule_id)


def next_rule_id(current: tuple[DemoRule, ...], used_ids: frozenset[str]) -> str:
    ids = {r.rule_id for r in current} | used_ids
    numbers = [int(i[1:]) for i in ids if _RULE_ID.fullmatch(i)]
    return f"R{max(numbers, default=0) + 1:02d}"


def find_invalid_question_references(
    question_ids: list[str] | tuple[str, ...], questions: tuple[DemoQuestion, ...]
) -> list[str]:
    """設問の一覧にない、または廃止済みの設問を、理由つきで返す（なければ空）。"""
    by_id = {q.id: q for q in questions}
    problems = []
    for question_id in question_ids:
        question = by_id.get(question_id)
        if question is None:
            problems.append(f"設問{question_id}は存在しません")
        elif question.retired:
            problems.append(f"設問{question_id}は廃止済みです")
    return problems


def _validate_removal(
    raw: dict,
    current: tuple[DemoRule, ...],
    base_revision: int,
    question_revision: int,
) -> ValidatedRuleRemoval:
    if set(raw) != {"operation", "rule_id"}:
        raise InvalidProposal("AIの出力の形式が不正です（operation と rule_id が必要）")
    rule_id = raw["rule_id"]
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise InvalidProposal("ルールIDの形式が不正です（例: R04）")
    target = next((r for r in current if r.rule_id == rule_id), None)
    if target is None:
        raise InvalidProposal(f"ルールID {rule_id} は存在しません")
    return ValidatedRuleRemoval(base_revision, current, target, question_revision)


def validate_rule_output(
    raw: Any,
    current: tuple[DemoRule, ...],
    questions: tuple[DemoQuestion, ...],
    base_revision: int,
    used_ids: frozenset[str] = frozenset(),
    *,
    question_revision: int,
) -> ValidatedRuleAdd | ValidatedRuleRemoval:
    """形式・参照・重複・対象を確かめる。不正なら InvalidProposal（理由つき）。

    questions は question_revision の時点の設問一覧。ルール一覧の版（base_revision）と
    設問一覧の版を、1つの基準として提案に持たせる。
    """
    if isinstance(raw, dict) and raw.get("operation") == OPERATION_REMOVE:
        return _validate_removal(raw, current, base_revision, question_revision)
    if not isinstance(raw, dict) or set(raw) != {"operation", "rule"}:
        raise InvalidProposal("AIの出力の形式が不正です（operation と rule が必要）")
    if raw["operation"] != OPERATION_ADD:
        raise InvalidProposal("AIの出力の操作が不正です（追加と削除だけ扱えます）")
    rule = raw["rule"]
    if not isinstance(rule, dict) or set(rule) != {"id", "result_code", "conditions"}:
        raise InvalidProposal(
            "追加するルールの形式が不正です（id・result_code・conditions が必要）"
        )
    rule_id, result_code = rule["id"], rule["result_code"]
    conditions = rule["conditions"]
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise InvalidProposal("ルールIDの形式が不正です（例: R04）")
    if not isinstance(result_code, str) or result_code not in RESULT_CODES:
        allowed = "・".join(RESULT_CODES)
        raise InvalidProposal(f"結果コードが不正です（{allowed} のいずれか）")
    if not isinstance(conditions, dict) or not conditions:
        raise InvalidProposal("ルールの条件が空です（設問ごとのYes／Noが必要）")
    for question_id, answer in conditions.items():
        if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
            raise InvalidProposal("条件の設問IDの形式が不正です（例: A09）")
        if not isinstance(answer, str) or answer not in _ANSWERS:
            raise InvalidProposal(f"設問{question_id}の条件は yes か no だけです")
    problems = find_invalid_question_references(sorted(conditions), questions)
    if problems:
        raise InvalidProposal("、".join(problems) + "（ルールは参照できません）")
    normalized = tuple(sorted(conditions.items()))
    if any(r.rule_id == rule_id for r in current):
        raise InvalidProposal(f"ルールID {rule_id} は既に存在します（重複）")
    same = next(
        (
            r
            for r in current
            if r.result_code == result_code and r.conditions == normalized
        ),
        None,
    )
    if same is not None:
        raise InvalidProposal(
            f"条件と結果が同じルール{same.rule_id}が既に存在します（重複）"
        )
    expected = next_rule_id(current, used_ids)
    if rule_id != expected:
        raise InvalidProposal(
            f"追加できるルールIDは {expected} だけです（提案は {rule_id}）"
        )
    return ValidatedRuleAdd(
        base_revision,
        current,
        DemoRule(rule_id, 1, result_code, normalized),
        question_revision,
    )
