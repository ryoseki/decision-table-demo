"""AIの出力（モック）を、アプリ側で検証する純粋な関数。

AIの出力は信用せず、形式・追加してよいID・重複・必須項目をここで確かめる。
DBにもHTTPにも依存しない。通った出力だけが、サーバーが持つ検証済みの提案になる。
追加してよいIDは、現行の一覧の最大番号の次（A01〜A12があればA13）だけ。
"""

import re
from dataclasses import dataclass
from typing import Any

from app.domain import InvalidProposal

OPERATION_ADD = "add_question"
MAX_TEXT_LENGTH = 200

_ID_PATTERN = re.compile(r"A[0-9]{2}")


@dataclass(frozen=True)
class DemoQuestion:
    id: str
    text: str


@dataclass(frozen=True)
class ValidatedProposal:
    """検証済みの追加案。基準にした一覧（base）と版（base_revision）を持つ。"""

    base_revision: int
    base: tuple[DemoQuestion, ...]
    added: DemoQuestion

    @property
    def after(self) -> tuple[DemoQuestion, ...]:
        return (*self.base, self.added)


def next_question_id(current: tuple[DemoQuestion, ...]) -> str:
    numbers = [int(q.id[1:]) for q in current if _ID_PATTERN.fullmatch(q.id)]
    return f"A{max(numbers, default=0) + 1:02d}"


def validate_model_output(
    raw: Any, current: tuple[DemoQuestion, ...], base_revision: int
) -> ValidatedProposal:
    """形式・ID・重複・対象を確かめる。不正なら InvalidProposal（理由つき）。"""
    if not isinstance(raw, dict) or set(raw) != {"operation", "question"}:
        raise InvalidProposal(
            "AIの出力の形式が不正です（operation と question が必要）"
        )
    if raw["operation"] != OPERATION_ADD:
        raise InvalidProposal("AIの出力の操作が不正です（追加だけ扱えます）")
    question = raw["question"]
    if not isinstance(question, dict) or set(question) != {"id", "text"}:
        raise InvalidProposal("追加する設問の形式が不正です（id と text が必要）")
    question_id, text = question["id"], question["text"]
    if not isinstance(question_id, str) or not _ID_PATTERN.fullmatch(question_id):
        raise InvalidProposal("設問IDの形式が不正です（例: A13）")
    if not isinstance(text, str) or text.strip() == "":
        raise InvalidProposal("設問の文言が空です")
    text = text.strip()
    if len(text) > MAX_TEXT_LENGTH or any(ord(c) < 32 for c in text):
        raise InvalidProposal("設問の文言が長すぎるか、制御文字を含みます")
    if any(q.id == question_id for q in current):
        raise InvalidProposal(f"設問ID {question_id} は既に存在します（重複）")
    if any(q.text == text for q in current):
        raise InvalidProposal("同じ文言の設問が既に存在します（重複）")
    expected = next_question_id(current)
    if question_id != expected:
        raise InvalidProposal(
            f"追加できる設問IDは {expected} だけです（提案は {question_id}）"
        )
    return ValidatedProposal(base_revision, current, DemoQuestion(question_id, text))
