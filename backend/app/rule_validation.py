"""判定ルールの追加案・削除案（管理者AIの試作）を、アプリ側で検証する純粋な関数。

AIの出力は信用せず、形式・参照する設問と結果・重複・対象をここで確かめる。
DBにもHTTPにも依存しない。通った出力だけが、サーバーが持つ検証済みの提案になる。

デモ限定の方針（本番の仕様ではない。docs/admin-proposal-demo.md）:
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
            problems.append(f"設問{question_id}は存在しません")
        elif question.retired:
            problems.append(f"設問{question_id}は廃止済みです")
    return problems


def _check_rule_id(rule_id: Any) -> None:
    if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
        raise InvalidProposal("ルールIDの形式が不正です（例: R04）")


def _check_conditions(
    conditions: Any, questions: tuple[DemoQuestion, ...], empty_message: str
) -> None:
    """条件（設問ID→yes/no）の形式と、参照する設問が使えることを確かめる。"""
    if not isinstance(conditions, dict) or not conditions:
        raise InvalidProposal(empty_message)
    for question_id, answer in conditions.items():
        if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
            raise InvalidProposal("条件の設問IDの形式が不正です（例: A09）")
        if not isinstance(answer, str) or answer not in _ANSWERS:
            raise InvalidProposal(f"設問{question_id}の条件は yes か no だけです")
    problems = find_invalid_question_references(sorted(conditions), questions)
    if problems:
        raise InvalidProposal("、".join(problems) + "（ルールは参照できません）")


def _validate_removal(
    raw: dict,
    current: tuple[DemoRule, ...],
    base_revision: int,
    question_revision: int,
) -> ValidatedRuleRemoval:
    if set(raw) != {"operation", "rule_id"}:
        raise InvalidProposal("AIの出力の形式が不正です（operation と rule_id が必要）")
    rule_id = raw["rule_id"]
    _check_rule_id(rule_id)
    target = next((r for r in current if r.rule_id == rule_id), None)
    if target is None:
        raise InvalidProposal(f"ルールID {rule_id} は存在しません")
    return ValidatedRuleRemoval(base_revision, current, target, question_revision)


# 曖昧な依頼の検知専用（小文字も含む）。ID・条件を読み取る正規表現には使わない。
_RULE_ID_ANY_CASE = re.compile(_RULE_ID.pattern, re.IGNORECASE)
_QUESTION_ID_ANY_CASE = re.compile(_QUESTION_ID.pattern, re.IGNORECASE)
_PROMPT_CONDITION = re.compile(
    r"(A[0-9]{2})\s*(?:が|は|を|=|＝|:|：)\s*(Yes|No)", re.IGNORECASE
)
# 「新規」「新しい」だけでは、新規作成と見なさない（「条件を新規に追加」は更新）。
# 新しいルールを作る意図が明らかな言い回しだけを、新規作成とする。
_NEW_RULE_PHRASE = re.compile(
    r"新しい(?:\s*)ルール|新規(?:の)?(?:\s*)ルール|新たな(?:\s*)ルール|新たにルール"
    r"|ルールを新規|ルールを新たに|ルールを新しく|新設|新規作成|ルールとして"
)
_RESULT_NAMES = ("アカウント削除", "一時凍結", "問題なし", "注意")


@dataclass(frozen=True)
class UpdateIntent:
    """依頼の文面から読み取れる、条件の追加の指定（対象のルールIDと、追加する条件）。"""

    rule_ids: tuple[str, ...]
    conditions: dict[str, str]


def mentions_removal(prompt: str) -> bool:
    """結果の名前（アカウント削除）を除いた文に「削除」があるか。"""
    operation_text = prompt
    for name in _RESULT_NAMES:
        operation_text = operation_text.replace(name, "")
    return "削除" in operation_text


def read_update_intent(prompt: str) -> UpdateIntent | None:
    """依頼が「既存ルールに条件を追加する」ものか、文面から判断する（なければ None）。

    ルールID・追加・設問ごとのYes／Noがそろい、削除や新規の言葉がないとき。
    AIの出力を信用せず、出力と突き合わせるために、サーバー側で読む。
    """
    ids = tuple(dict.fromkeys(_RULE_ID.findall(prompt)))
    pairs = [(q, a.lower()) for q, a in _PROMPT_CONDITION.findall(prompt)]
    if not ids or not pairs or "追加" not in prompt:
        return None
    if _NEW_RULE_PHRASE.search(prompt) or mentions_removal(prompt):
        return None
    conditions: dict[str, str] = {}
    for question_id, answer in pairs:
        if conditions.setdefault(question_id, answer) != answer:
            raise InvalidProposal(
                f"依頼の中で、設問{question_id}のYes／Noが食い違っています"
                "（指定が曖昧です）"
            )
    return UpdateIntent(ids, conditions)


def _check_against_request(raw: Any, prompt: str) -> None:
    """生成された出力が、依頼の文面と整合するか確かめる。食い違えば InvalidProposal。"""
    intent = read_update_intent(prompt)
    operation = raw.get("operation") if isinstance(raw, dict) else None
    if intent is None:
        if operation == OPERATION_UPDATE:
            raise InvalidProposal(
                "依頼から、更新するルールと追加する条件を読み取れません"
                "（例: R04にA09がYesを追加して）。更新案は反映できません"
            )
        # 既存のルールIDと「追加」、条件を足す兆候（設問IDまたは「条件」）があるのに、
        # 更新と読めず、新規作成の明示もない依頼は、更新の依頼が新規作成に落ちないよう、
        # 新規追加の案も反映しない（曖昧）。兆候のない依頼（例: R04を追加）は対象外。
        # 小文字のID（r04・a09）は正式な入力ではないが、既存ルールを指す疑いがあるので、
        # 大文字小文字を区別せず兆候として扱う（受理はしない。新規作成にも落とさない）。
        if (
            operation == OPERATION_ADD
            and _RULE_ID_ANY_CASE.search(prompt)
            and "追加" in prompt
            and (_QUESTION_ID_ANY_CASE.search(prompt) or "条件" in prompt)
            and not _NEW_RULE_PHRASE.search(prompt)
            and not mentions_removal(prompt)
        ):
            raise InvalidProposal(
                "ルールIDを含む追加の依頼で、既存ルールへの条件の追加か、新しいルールの"
                "作成かが曖昧です（IDは大文字の R04・A09 で指定してください）。"
                "更新は「R04にA09がYesを追加して」、新規は"
                "「新しいルールとして…追加して」のように指定してください。"
                "新規追加案は反映できません"
            )
        return
    if len(intent.rule_ids) > 1:
        raise InvalidProposal(
            "依頼に複数のルール（"
            + "、".join(intent.rule_ids)
            + "）があり、更新の対象が曖昧です。1つのルールを指定してください"
        )
    target_id = intent.rule_ids[0]
    if operation != OPERATION_UPDATE:
        raise InvalidProposal(
            f"ルール{target_id}の条件を更新する依頼ですが、更新ではない案が返りました。"
            "新しいルールの追加などには置き換えず、反映できません"
        )
    if raw.get("rule_id") != target_id:
        raise InvalidProposal(
            f"更新の対象が依頼と違います（依頼は{target_id}、案は{raw.get('rule_id')}）"
        )
    if raw.get("add_conditions") != intent.conditions:
        raise InvalidProposal(
            "追加する条件が依頼と違います"
            f"（依頼は{intent.conditions}、案は{raw.get('add_conditions')}）"
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
            "AIの出力の形式が不正です（operation・rule_id・add_conditions が必要）"
        )
    rule_id, additions = raw["rule_id"], raw["add_conditions"]
    _check_rule_id(rule_id)
    _check_conditions(
        additions, questions, "追加する条件が空です（設問ごとのYes／Noが必要）"
    )
    target = next((r for r in current if r.rule_id == rule_id), None)
    if target is None:
        raise InvalidProposal(f"ルールID {rule_id} は存在しません")
    existing = dict(target.conditions)
    for question_id, answer in sorted(additions.items()):
        if question_id not in existing:
            continue
        if existing[question_id] == answer:
            raise InvalidProposal(
                f"ルール{rule_id}には、設問{question_id}={answer}の条件が"
                "追加済みです（重複）"
            )
        raise InvalidProposal(
            f"ルール{rule_id}は設問{question_id}の条件を既に持っています"
            f"（{existing[question_id]}）。既存の条件の書き換えは扱えません"
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
            f"更新後の条件と結果が、ルール{same.rule_id}と同じになります（重複）"
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
        raise InvalidProposal("AIの出力の形式が不正です（operation と rule が必要）")
    if raw["operation"] != OPERATION_ADD:
        raise InvalidProposal(
            "AIの出力の操作が不正です（追加・削除・条件の更新だけ扱えます）"
        )
    rule = raw["rule"]
    if not isinstance(rule, dict) or set(rule) != {"id", "result_code", "conditions"}:
        raise InvalidProposal(
            "追加するルールの形式が不正です（id・result_code・conditions が必要）"
        )
    rule_id, result_code = rule["id"], rule["result_code"]
    conditions = rule["conditions"]
    _check_rule_id(rule_id)
    if not isinstance(result_code, str) or result_code not in RESULT_CODES:
        allowed = "・".join(RESULT_CODES)
        raise InvalidProposal(f"結果コードが不正です（{allowed} のいずれか）")
    _check_conditions(
        conditions, questions, "ルールの条件が空です（設問ごとのYes／Noが必要）"
    )
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
