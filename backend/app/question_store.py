"""デモの設問一覧と、検証済みの提案の保存先（メモリ上の仮実装）。

DBは使わない。サーバーのメモリにだけあり、画面の再読み込みでは残るが、
バックエンドを再起動すると初期状態（A01〜A12）に戻る。永続保存ではない。
案件の設問・回答（DB）とは別のデータで、互いに影響しない。
提案の承認（比較と反映）は、ロックの中で1回にまとめて行う。
"""

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.domain import (
    ProposalConflict,
    ProposalNotFound,
    RetirementBlocked,
    operation_from_receipt,
)
from app.question_validation import (
    DemoQuestion,
    ValidatedProposal,
    ValidatedRetirement,
)

Proposal = ValidatedProposal | ValidatedRetirement

# 未承認の提案を持つ上限（メモリを使い切らないため。超えたら古いものから捨てる）。
MAX_PENDING_PROPOSALS = 50

# 初期のデモ一覧。docs/requirements.md の設問A01〜A12（架空）の文言。
_INITIAL = (
    (
        "A01",
        "Did the login mentioned in the inquiry succeed? / "
        "問い合わせにあったログインは成功したか？",
    ),
    (
        "A02",
        "Did the user answer that they did not perform the login? / "
        "本人は「自分が行ったログインではない」と回答したか？",
    ),
    (
        "A03",
        "Was the registered email address changed during the target period? / "
        "対象期間に登録メールアドレスが変更されたか？",
    ),
    (
        "A04",
        "Did the user answer that they did not make the email address change? / "
        "本人は「自分が行ったメールアドレス変更ではない」と回答したか？",
    ),
    (
        "A05",
        "Was the password changed during the target period? / "
        "対象期間にパスワードが変更されたか？",
    ),
    (
        "A06",
        "Did the user answer that they did not make the password change? / "
        "本人は「自分が行ったパスワード変更ではない」と回答したか？",
    ),
    (
        "A07",
        "Were credits consumed during the target period? / "
        "対象期間にクレジットが消費されたか？",
    ),
    (
        "A08",
        "Did the user answer that they do not recognize that usage? / "
        "本人は「その利用に心当たりがない」と回答したか？",
    ),
    (
        "A09",
        "Was an API key newly issued during the target period? / "
        "対象期間にAPIキーが新しく発行されたか？",
    ),
    (
        "A10",
        "Did the user answer that they did not issue that API key? / "
        "本人は「そのAPIキーは自分が発行したものではない」と回答したか？",
    ),
    (
        "A11",
        "Were there successful calls with the target API key? / "
        "対象のAPIキーで成功した呼び出しがあったか？",
    ),
    (
        "A12",
        "Did the user answer that they do not recognize that API usage? / "
        "本人は「そのAPI利用に心当たりがない」と回答したか？",
    ),
)


def initial_questions() -> tuple[DemoQuestion, ...]:
    """初期のデモ一覧（A01〜A12）。DBの共有設定の初期値にも使う。"""
    return tuple(DemoQuestion(i, t) for i, t in _INITIAL)


class InMemoryQuestionStore:
    def __init__(self, templates: Any = None) -> None:
        # templates があれば、Yesで反映した依頼文と操作を、同じロックの中で登録する。
        self._templates = templates
        self._prompts: dict[str, str] = {}
        self._lock = threading.Lock()
        self._questions: tuple[DemoQuestion, ...] = initial_questions()
        self._revision = 1
        self._proposals: OrderedDict[str, Proposal] = OrderedDict()

    def snapshot(self) -> tuple[int, tuple[DemoQuestion, ...]]:
        """現行の（版, 一覧）。提案の基準に使う。"""
        with self._lock:
            return self._revision, self._questions

    def retired_ids(self) -> frozenset[str]:
        """廃止済みの設問ID。案件の画面で、回答の対象から外すために使う。"""
        with self._lock:
            return frozenset(q.id for q in self._questions if q.retired)

    def register(self, proposal: Proposal) -> str:
        """検証済みの提案を保存し、提案IDを返す（一覧は変えない）。"""
        proposal_id = uuid.uuid4().hex
        with self._lock:
            self._proposals[proposal_id] = proposal
            while len(self._proposals) > MAX_PENDING_PROPOSALS:
                self._proposals.popitem(last=False)
        return proposal_id

    def register_for_reuse(self, proposal: Proposal, prompt: str) -> str:
        """register に加え、反映に成功したとき定型文へ登録する依頼文を覚える。"""
        proposal_id = self.register(proposal)
        with self._lock:
            self._prompts[proposal_id] = prompt
        return proposal_id

    def approve(
        self,
        proposal_id: str,
        references_of: Callable[[str], tuple[str, ...]] | None = None,
    ) -> tuple[int, tuple[DemoQuestion, ...]]:
        """基準の版を比べ、一致したときだけ一覧へ反映する。提案は1回で使い切る。

        廃止案は、承認の時点の現行ルールを references_of(設問ID) で調べ直す。
        参照するルールが1件以上あれば廃止しない（RetirementBlocked。一覧は変えない）。
        """
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise ProposalNotFound(proposal_id)
            if proposal.base_revision != self._revision:
                del self._proposals[proposal_id]
                self._prompts.pop(proposal_id, None)
                raise ProposalConflict(proposal_id)
            references: tuple[str, ...] = ()
            if isinstance(proposal, ValidatedRetirement):
                if references_of is None:
                    # 参照の確認なしに廃止しない（呼び出し側の誤り）。
                    raise ValueError(
                        "references_of is required to approve a deactivation"
                    )
                # 確認できない一時障害はここで例外になる。提案は残す（再試行可）。
                references = references_of(proposal.target.id)
            del self._proposals[proposal_id]
            prompt = self._prompts.pop(proposal_id, None)
            if references:
                raise RetirementBlocked(proposal.target.id, references)
            self._questions = proposal.after
            self._revision += 1
            if self._templates is not None and prompt is not None:
                receipt = (
                    {"op": "retire", "target_id": proposal.target.id}
                    if isinstance(proposal, ValidatedRetirement)
                    else {"op": "add", "text": proposal.added.text}
                )
                self._templates.save(
                    "question", prompt, operation_from_receipt("question", receipt)
                )
            return self._revision, self._questions

    def reject(self, proposal_id: str) -> None:
        """提案を破棄する。一覧は変えない。"""
        with self._lock:
            self._prompts.pop(proposal_id, None)
            if self._proposals.pop(proposal_id, None) is None:
                raise ProposalNotFound(proposal_id)
