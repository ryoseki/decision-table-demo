"""AIの出力（モック）を、アプリ側で検証する純粋な関数。

AIの出力は信用せず、形式・追加してよいID・重複・必須項目をここで確かめる。
DBにもHTTPにも依存しない。通った出力だけが、サーバーが持つ検証済みの提案になる。
追加してよいIDは、現行の一覧の最大番号の次（A01〜A12があればA13）だけ。
"""

import re
from dataclasses import dataclass, replace
from typing import Any

from app.domain import InvalidProposal

OPERATION_ADD = "add_question"
OPERATION_RETIRE = "retire_question"
MAX_TEXT_LENGTH = 200

_ID_PATTERN = re.compile(r"A[0-9]{2}")


@dataclass(frozen=True)
class DemoQuestion:
    id: str
    text: str
    # 廃止した設問は、一覧に残す（設問文は保持し、回答の対象から外す）。
    retired: bool = False


@dataclass(frozen=True)
class ValidatedProposal:
    """検証済みの追加案。基準にした一覧（base）と版（base_revision）を持つ。"""

    base_revision: int
    base: tuple[DemoQuestion, ...]
    added: DemoQuestion

    @property
    def after(self) -> tuple[DemoQuestion, ...]:
        return (*self.base, self.added)


@dataclass(frozen=True)
class ValidatedRetirement:
    """検証済みの廃止案。対象の設問は、一覧に残したまま「廃止」にする。"""

    base_revision: int
    base: tuple[DemoQuestion, ...]
    target: DemoQuestion

    @property
    def after(self) -> tuple[DemoQuestion, ...]:
        return tuple(
            replace(q, retired=True) if q.id == self.target.id else q for q in self.base
        )


def next_question_id(current: tuple[DemoQuestion, ...]) -> str:
    numbers = [int(q.id[1:]) for q in current if _ID_PATTERN.fullmatch(q.id)]
    return f"A{max(numbers, default=0) + 1:02d}"


def _validate_retirement(
    raw: dict, current: tuple[DemoQuestion, ...], base_revision: int
) -> ValidatedRetirement:
    if set(raw) != {"operation", "question_id"}:
        raise InvalidProposal(
            "The AI output format is invalid (operation and question_id are required)"
        )
    question_id = raw["question_id"]
    if not isinstance(question_id, str) or not _ID_PATTERN.fullmatch(question_id):
        raise InvalidProposal("The question ID format is invalid (example: A13)")
    target = next((q for q in current if q.id == question_id), None)
    if target is None:
        raise InvalidProposal(f"Question {question_id} does not exist")
    if target.retired:
        raise InvalidProposal(f"Question {question_id} is already deactivated")
    return ValidatedRetirement(base_revision, current, target)


def find_rule_references(question_id: str, rules: list[dict]) -> list[dict]:
    """現行（最新の版）の判定ルールのうち、設問を条件に持つもの。

    rules は、rule_id ごとに最新の版の行（rule_id・revision・result_code・
    conditions）。条件の値がYesでもNoでも、参照に数える。
    """
    return [
        {
            "rule_id": r["rule_id"],
            "revision": r["revision"],
            "result_code": r["result_code"],
        }
        for r in rules
        if question_id in r["conditions"]
    ]


def find_case_impact(question_id: str, cases: list[dict]) -> dict[str, list[dict]]:
    """設問を持つ既存案件を、判定の登録の有無で分ける（案件は変えない）。

    cases は、案件の詳細（case_id・summary・questions・result）。
    判定の登録の有無は、案件の結果（result）の有無で決まる。個別の設問の回答状態ではない。
    answer は保存済みの回答（yes・no・pending。未回答は None）。
    """
    impact: dict[str, list[dict]] = {"unjudged": [], "judged": []}
    for case in cases:
        question = next(
            (q for q in case["questions"] if q["question_code"] == question_id), None
        )
        if question is None:
            continue
        row = {
            "case_id": case["case_id"],
            "summary": case["summary"],
            "answer": question["answer"],
        }
        result = case.get("result")
        if result is None:
            impact["unjudged"].append(row)
        else:
            impact["judged"].append({**row, "result_code": result["result_code"]})
    return impact


def validate_model_output(
    raw: Any, current: tuple[DemoQuestion, ...], base_revision: int
) -> ValidatedProposal | ValidatedRetirement:
    """形式・ID・重複・対象を確かめる。不正なら InvalidProposal（理由つき）。"""
    if isinstance(raw, dict) and raw.get("operation") == OPERATION_RETIRE:
        return _validate_retirement(raw, current, base_revision)
    if not isinstance(raw, dict) or set(raw) != {"operation", "question"}:
        raise InvalidProposal(
            "The AI output format is invalid (operation and question are required)"
        )
    if raw["operation"] != OPERATION_ADD:
        raise InvalidProposal(
            "The AI output operation is invalid (only add is supported)"
        )
    question = raw["question"]
    if not isinstance(question, dict) or set(question) != {"id", "text"}:
        raise InvalidProposal(
            "The question to add has an invalid format (id and text are required)"
        )
    question_id, text = question["id"], question["text"]
    if not isinstance(question_id, str) or not _ID_PATTERN.fullmatch(question_id):
        raise InvalidProposal("The question ID format is invalid (example: A13)")
    if not isinstance(text, str) or text.strip() == "":
        raise InvalidProposal("The question text is empty")
    text = text.strip()
    if len(text) > MAX_TEXT_LENGTH or any(ord(c) < 32 for c in text):
        raise InvalidProposal(
            "The question text is too long or contains control characters"
        )
    if any(q.id == question_id for q in current):
        raise InvalidProposal(f"Question {question_id} already exists (duplicate)")
    if any(q.text == text for q in current):
        raise InvalidProposal(
            "A question with the same text already exists (duplicate)"
        )
    expected = next_question_id(current)
    if question_id != expected:
        raise InvalidProposal(
            f"The only question ID that can be added is {expected} (the proposal used "
            f"{question_id})"
        )
    return ValidatedProposal(base_revision, current, DemoQuestion(question_id, text))
