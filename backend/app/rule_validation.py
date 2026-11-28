"""判定ルールの追加案・削除案（管理者AIの試作）を、アプリ側で検証する純粋な関数。

AIの出力は信用せず、形式・参照する設問と結果・重複・対象をここで確かめる。
DBにもHTTPにも依存しない。通った出力だけが、サーバーが持つ検証済みの提案になる。

デモ限定の方針（本番の仕様ではない。docs/admin-rule-proposal-demo.md）:
- 重複は、「同じID」、または「条件（設問ごとのYes／No。並びは無関係）と結果が
  すべて一致する既存ルール」。条件が同じで結果が違うルールは、重複にしない（優先順位や禁止は決めない）。
- 追加してよいIDは、これまでに使ったIDの最大番号の次（削除したIDは再利用しない）。
- 更新は、既存ルールへの条件の追加だけ（ID・既存の条件・結果は変えない。結果の変更や
  条件の削除・Yes／Noの書き換えは扱わない）。依頼の文面と生成された出力が食い違うとき
  （更新の依頼を新規作成として返したなど）は、反映可能な提案にしない。
"""

import re
from dataclasses import dataclass
from typing import Any

from app.domain import RESULT_CODES, InvalidProposal
from app.question_validation import DemoQuestion

OPERATION_ADD = "add_rule"
OPERATION_REMOVE = "remove_rule"
OPERATION_UPDATE = "update_rule"

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


@dataclass(frozen=True)
class ValidatedRuleUpdate:
    """検証済みの更新案。対象のルール（target）に条件を足した版（updated）を、同じIDで置き換える。

    ルールの並び・ID・結果・既存の条件は変えない。版（revision）は1つ進める。
    """

    base_revision: int
    base: tuple[DemoRule, ...]
    target: DemoRule
    added_conditions: tuple[tuple[str, str], ...]
    base_question_revision: int

    @property
    def updated(self) -> DemoRule:
        return with_added_conditions(self.target, dict(self.added_conditions))

    @property
    def after(self) -> tuple[DemoRule, ...]:
        return tuple(
            self.updated if r.rule_id == self.target.rule_id else r for r in self.base
        )


def with_added_conditions(rule: DemoRule, additions: dict[str, str]) -> DemoRule:
    """同じID・結果で、条件を足した次の版（条件は設問ID順）。"""
    merged = tuple(sorted({**dict(rule.conditions), **additions}.items()))
    return DemoRule(rule.rule_id, rule.revision + 1, rule.result_code, merged)


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
            problems.append(f"Question {question_id} does not exist")
        elif question.retired:
            problems.append(f"Question {question_id} is deactivated")
    return problems


def _check_rule_id(rule_id: Any) -> None:
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise InvalidProposal("The rule ID format is invalid (example: R04)")


def _check_conditions(
    conditions: Any, questions: tuple[DemoQuestion, ...], empty_message: str
) -> None:
    """条件（設問ID→yes/no）の形式と、参照する設問が使えることを確かめる。"""
    if not isinstance(conditions, dict) or not conditions:
        raise InvalidProposal(empty_message)
    for question_id, answer in conditions.items():
        if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
            raise InvalidProposal(
                "The question ID format in the conditions is invalid (example: A09)"
            )
        if not isinstance(answer, str) or answer not in _ANSWERS:
            raise InvalidProposal(
                f"The condition for question {question_id} must be yes or no"
            )
    problems = find_invalid_question_references(sorted(conditions), questions)
    if problems:
        raise InvalidProposal(
            ", ".join(problems)
            + " (a rule cannot reference nonexistent or deactivated questions)"
        )


def _validate_removal(
    raw: dict,
    current: tuple[DemoRule, ...],
    base_revision: int,
    question_revision: int,
) -> ValidatedRuleRemoval:
    if set(raw) != {"operation", "rule_id"}:
        raise InvalidProposal(
            "The AI output format is invalid (operation and rule_id are required)"
        )
    rule_id = raw["rule_id"]
    _check_rule_id(rule_id)
    target = next((r for r in current if r.rule_id == rule_id), None)
    if target is None:
        raise InvalidProposal(f"Rule ID {rule_id} does not exist")
    return ValidatedRuleRemoval(base_revision, current, target, question_revision)


# 曖昧な依頼の検知専用（小文字も含む）。ID・条件を読み取る正規表現には使わない。
_RULE_ID_ANY_CASE = re.compile(_RULE_ID.pattern, re.IGNORECASE)
_QUESTION_ID_ANY_CASE = re.compile(_QUESTION_ID.pattern, re.IGNORECASE)
_PROMPT_CONDITION = re.compile(
    r"(A[0-9]{2})(?:\s*(?:が|は|を|=|＝|:|：)\s*|\s+(?:is|to)\s+)(Yes|No)",
    re.IGNORECASE,
)
# 「新規」「新しい」だけでは、新規作成と見なさない（「条件を新規に追加」は更新）。
# 新しいルールを作る意図が明らかな言い回しだけを、新規作成とする。
_NEW_RULE_PHRASE = re.compile(
    r"新しい(?:\s*)ルール|新規(?:の)?(?:\s*)ルール|新たな(?:\s*)ルール|新たにルール"
    r"|ルールを新規|ルールを新たに|ルールを新しく|新設|新規作成|ルールとして"
    r"|\bnew\s+rule\b|\bcreate\s+(?:a\s+)?rule\b|\bas\s+a\s+rule\b"
)
_RESULT_NAMES = (
    "アカウント削除",
    "一時凍結",
    "問題なし",
    "注意",
    "account deletion",
    "account suspension",
    "no issue",
    "caution",
)


@dataclass(frozen=True)
class UpdateIntent:
    """依頼の文面から読み取れる、条件の追加の指定（対象のルールIDと、追加する条件）。"""

    rule_ids: tuple[str, ...]
    conditions: dict[str, str]


def mentions_removal(prompt: str) -> bool:
    """結果の名前（アカウント削除）を除いた文に「削除」があるか。"""
    operation_text = prompt
    for name in _RESULT_NAMES:
        operation_text = re.sub(
            re.escape(name), "", operation_text, flags=re.IGNORECASE
        )
    return "削除" in operation_text or bool(
        re.search(r"\b(?:delete|remove)\b", operation_text, re.IGNORECASE)
    )


def mentions_addition(prompt: str) -> bool:
    """依頼に「追加」（または add）の言葉があるか。"""
    return "追加" in prompt or bool(re.search(r"\badd\b", prompt, re.IGNORECASE))


def read_update_intent(prompt: str) -> UpdateIntent | None:
    """依頼が「既存ルールに条件を追加する」ものか、文面から判断する（なければ None）。

    ルールID・追加・設問ごとのYes／Noがそろい、削除や新規の言葉がないとき。
    AIの出力を信用せず、出力と突き合わせるために、サーバー側で読む。
    """
    ids = tuple(dict.fromkeys(_RULE_ID.findall(prompt)))
    pairs = [(q, a.lower()) for q, a in _PROMPT_CONDITION.findall(prompt)]
    if not ids or not pairs or not mentions_addition(prompt):
        return None
    if _NEW_RULE_PHRASE.search(prompt) or mentions_removal(prompt):
        return None
    conditions: dict[str, str] = {}
    for question_id, answer in pairs:
        if conditions.setdefault(question_id, answer) != answer:
            raise InvalidProposal(
                f"The request gives conflicting Yes/No for question {question_id} "
                "(ambiguous)"
            )
    return UpdateIntent(ids, conditions)


def _check_against_request(raw: Any, prompt: str) -> None:
    """生成された出力が、依頼の文面と整合するか確かめる。食い違えば InvalidProposal。"""
    intent = read_update_intent(prompt)
    operation = raw.get("operation") if isinstance(raw, dict) else None
    if intent is None:
        if operation == OPERATION_UPDATE:
            raise InvalidProposal(
                "Cannot read the rule to update and the conditions to add from the "
                "request "
                "(example: Add condition A09=Yes to R04). "
                "The update proposal cannot be applied"
            )
        # 既存のルールIDと「追加」、条件を足す兆候（設問IDまたは「条件」）があるのに、
        # 更新と読めず、新規作成の明示もない依頼は、更新の依頼が新規作成に落ちないよう、
        # 新規追加の案も反映しない（曖昧）。兆候のない依頼（例: R04を追加）は対象外。
        # 小文字のID（r04・a09）は正式な入力ではないが、既存ルールを指す疑いがあるので、
        # 大文字小文字を区別せず兆候として扱う（受理はしない。新規作成にも落とさない）。
        if (
            operation == OPERATION_ADD
            and _RULE_ID_ANY_CASE.search(prompt)
            and mentions_addition(prompt)
            and (
                _QUESTION_ID_ANY_CASE.search(prompt)
                or "条件" in prompt
                or re.search(r"\bconditions?\b", prompt, re.IGNORECASE)
            )
            and not _NEW_RULE_PHRASE.search(prompt)
            and not mentions_removal(prompt)
        ):
            raise InvalidProposal(
                "This add request includes a rule ID, and it is ambiguous whether it "
                "adds conditions to an existing rule "
                "or creates a new rule (specify IDs in upper case, such as R04 and "
                "A09). "
                'Write an update as "Add condition A09=Yes to R04" and a new rule as '
                '"Add a new rule ...". '
                "The new-rule proposal cannot be applied"
            )
        return
    if len(intent.rule_ids) > 1:
        raise InvalidProposal(
            "The request mentions multiple rules ("
            + ", ".join(intent.rule_ids)
            + "), so the update target is ambiguous. Specify one rule"
        )
    target_id = intent.rule_ids[0]
    if operation != OPERATION_UPDATE:
        raise InvalidProposal(
            f"This request updates the conditions of rule {target_id}, but the "
            "proposal returned is not an update. "
            "It is not replaced by adding a new rule or similar, so it cannot be "
            "applied"
        )
    if raw.get("rule_id") != target_id:
        raise InvalidProposal(
            f"The update target differs from the request (request: {target_id}, "
            f"proposal: {raw.get('rule_id')})"
        )
    if raw.get("add_conditions") != intent.conditions:
        raise InvalidProposal(
            "The conditions to add differ from the request "
            f"(request: {intent.conditions}, proposal: {raw.get('add_conditions')})"
        )


def _validate_update(
    raw: dict,
    current: tuple[DemoRule, ...],
    questions: tuple[DemoQuestion, ...],
    base_revision: int,
    question_revision: int,
) -> ValidatedRuleUpdate:
    if set(raw) != {"operation", "rule_id", "add_conditions"}:
        raise InvalidProposal(
            "The AI output format is invalid (operation, rule_id, and add_conditions "
            "are required)"
        )
    rule_id, additions = raw["rule_id"], raw["add_conditions"]
    _check_rule_id(rule_id)
    _check_conditions(
        additions,
        questions,
        "The conditions to add are empty (Yes/No per question is required)",
    )
    target = next((r for r in current if r.rule_id == rule_id), None)
    if target is None:
        raise InvalidProposal(f"Rule ID {rule_id} does not exist")
    existing = dict(target.conditions)
    for question_id, answer in sorted(additions.items()):
        if question_id not in existing:
            continue
        if existing[question_id] == answer:
            raise InvalidProposal(
                f"Rule {rule_id} already has the condition "
                f"{question_id}={answer} "
                "(duplicate)"
            )
        raise InvalidProposal(
            f"Rule {rule_id} already has a condition for question {question_id} "
            f"({existing[question_id]}). Rewriting existing conditions is not supported"
        )
    proposal = ValidatedRuleUpdate(
        base_revision,
        current,
        target,
        tuple(sorted(additions.items())),
        question_revision,
    )
    same = next(
        (
            r
            for r in current
            if r.rule_id != rule_id
            and r.result_code == target.result_code
            and r.conditions == proposal.updated.conditions
        ),
        None,
    )
    if same is not None:
        raise InvalidProposal(
            "The updated conditions and result would be the same as rule "
            f"{same.rule_id} (duplicate)"
        )
    return proposal


def validate_rule_output(
    raw: Any,
    current: tuple[DemoRule, ...],
    questions: tuple[DemoQuestion, ...],
    base_revision: int,
    used_ids: frozenset[str] = frozenset(),
    *,
    question_revision: int,
    prompt: str | None = None,
) -> ValidatedRuleAdd | ValidatedRuleRemoval | ValidatedRuleUpdate:
    """形式・参照・重複・対象を確かめる。不正なら InvalidProposal（理由つき）。

    prompt（利用者の依頼）があれば、出力が依頼と整合するかも確かめる
    （更新の依頼を別の操作で返す、対象や条件が依頼と違う、など）。

    questions は question_revision の時点の設問一覧。ルール一覧の版（base_revision）と
    設問一覧の版を、1つの基準として提案に持たせる。
    """
    if prompt is not None:
        _check_against_request(raw, prompt)
    if isinstance(raw, dict) and raw.get("operation") == OPERATION_UPDATE:
        return _validate_update(
            raw, current, questions, base_revision, question_revision
        )
    if isinstance(raw, dict) and raw.get("operation") == OPERATION_REMOVE:
        return _validate_removal(raw, current, base_revision, question_revision)
    if not isinstance(raw, dict) or set(raw) != {"operation", "rule"}:
        raise InvalidProposal(
            "The AI output format is invalid (operation and rule are required)"
        )
    if raw["operation"] != OPERATION_ADD:
        raise InvalidProposal(
            "The AI output operation is invalid (only add, remove, and condition "
            "updates are supported)"
        )
    rule = raw["rule"]
    if not isinstance(rule, dict) or set(rule) != {"id", "result_code", "conditions"}:
        raise InvalidProposal(
            "The rule to add has an invalid format (id, result_code, and conditions "
            "are required)"
        )
    rule_id, result_code = rule["id"], rule["result_code"]
    conditions = rule["conditions"]
    _check_rule_id(rule_id)
    if not isinstance(result_code, str) or result_code not in RESULT_CODES:
        allowed = ", ".join(RESULT_CODES)
        raise InvalidProposal(f"The result code is invalid (one of {allowed})")
    _check_conditions(
        conditions,
        questions,
        "The rule conditions are empty (Yes/No per question is required)",
    )
    normalized = tuple(sorted(conditions.items()))
    if any(r.rule_id == rule_id for r in current):
        raise InvalidProposal(f"Rule ID {rule_id} already exists (duplicate)")
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
            f"Rule {same.rule_id} with the same conditions and result already exists "
            "(duplicate)"
        )
    expected = next_rule_id(current, used_ids)
    if rule_id != expected:
        raise InvalidProposal(
            f"The only rule ID that can be added is {expected} (the proposal used "
            f"{rule_id})"
        )
    return ValidatedRuleAdd(
        base_revision,
        current,
        DemoRule(rule_id, 1, result_code, normalized),
        question_revision,
    )
