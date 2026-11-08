"""回答と架空ルールの照合ロジック（初版・A01〜A12）。

純粋な計算だけを行う。HTTP・DB・保存は扱わない。
R01〜R03は docs/requirements.md の受入例用の架空の組み合わせで、
業務上の不正認定ルールではない。照合結果は表示（通常／グレー）の判断材料であり、
人が選べる判定結果を制限しない。
"""

from dataclasses import dataclass
from enum import StrEnum

QUESTION_IDS: tuple[str, ...] = tuple(f"A{n:02d}" for n in range(1, 13))


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    # 未回答（まだ答えていない）と確認待ち（資料不足・取得失敗など）は別の状態。
    # どちらもYes／Noの扱いにせず、食い違いの根拠にしない。
    UNANSWERED = "unanswered"
    PENDING = "pending"


class ConditionState(StrEnum):
    MATCH = "match"  # 回答が条件と一致
    MISMATCH = "mismatch"  # Yes／Noが条件と食い違う
    UNANSWERED = "unanswered"  # 未回答のため判断できない
    PENDING = "pending"  # 確認待ちのため判断できない（未回答・No とは別）


class RowDisplay(StrEnum):
    NORMAL = "normal"
    GRAY = "gray"


class Result(StrEnum):
    """定義済みの判定結果（重い順）。値は表示名で、DBの結果コードではない。"""

    ACCOUNT_DELETION = "アカウント削除"
    ACCOUNT_SUSPENSION = "アカウント一時凍結"
    CAUTION = "注意"
    NO_ISSUE = "問題なし"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    result: Result
    conditions: dict[str, Answer]  # A01〜A12すべてのYes／No


def _parse(pattern: str) -> dict[str, Answer]:
    marks = pattern.split()
    if len(marks) != len(QUESTION_IDS) or set(marks) - {"Y", "N"}:
        raise ValueError("条件はY／Nを12問分指定してください")
    return {
        q: Answer.YES if m == "Y" else Answer.NO
        for q, m in zip(QUESTION_IDS, marks, strict=True)
    }


DEMO_RULES: tuple[Rule, ...] = (
    Rule("R01", Result.CAUTION, _parse("Y Y Y Y Y N Y Y Y N Y Y")),
    Rule("R02", Result.NO_ISSUE, _parse("Y N Y Y Y Y Y Y Y Y Y N")),
    Rule("R03", Result.ACCOUNT_SUSPENSION, _parse("N Y Y Y Y N Y Y Y N Y Y")),
)


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    result: Result
    display: RowDisplay
    states: dict[str, ConditionState]

    @property
    def mismatched_questions(self) -> tuple[str, ...]:
        return tuple(q for q, s in self.states.items() if s is ConditionState.MISMATCH)


@dataclass(frozen=True)
class MatchReport:
    rules: tuple[RuleMatch, ...]
    # 照合結果にかかわらず、定義済みの全結果を重い順に返す。
    selectable_results: tuple[Result, ...]


def _state(answer: Answer, expected: Answer) -> ConditionState:
    if answer is Answer.UNANSWERED:
        return ConditionState.UNANSWERED
    if answer is Answer.PENDING:
        return ConditionState.PENDING
    return ConditionState.MATCH if answer is expected else ConditionState.MISMATCH


def match_rules(
    answers: dict[str, Answer], rules: tuple[Rule, ...] = DEMO_RULES
) -> MatchReport:
    """A01〜A12への回答をルールと照合する。

    answersの値はAnswerまたはその文字列値。answersに無い設問は未回答として扱う。A01〜A12以外のIDは誤入力として拒否する。
    1問でもYes／Noが条件と食い違う行はグレー、それ以外（未回答・確認待ちのみ
    の不一致なし）は通常表示にする。
    """
    unknown = set(answers) - set(QUESTION_IDS)
    if unknown:
        raise ValueError(f"未知の設問ID: {sorted(unknown)}")
    # 文字列（"yes"等）も受け付けるが、Answerにない値は黙って扱わず拒否する。
    answers = {q: Answer(a) for q, a in answers.items()}
    matches = []
    for rule in rules:
        states = {
            q: _state(answers.get(q, Answer.UNANSWERED), rule.conditions[q])
            for q in QUESTION_IDS
        }
        gray = ConditionState.MISMATCH in states.values()
        matches.append(
            RuleMatch(
                rule.rule_id,
                rule.result,
                RowDisplay.GRAY if gray else RowDisplay.NORMAL,
                states,
            )
        )
    return MatchReport(tuple(matches), tuple(Result))
