"""デモの設問一覧と、検証済みの提案の保存先（メモリ上の仮実装）。

DBは使わない。サーバーのメモリにだけあり、画面の再読み込みでは残るが、
バックエンドを再起動すると初期状態（A01〜A12）に戻る。永続保存ではない。
案件の設問・回答（DB）とは別のデータで、互いに影響しない。
提案の承認（比較と反映）は、ロックの中で1回にまとめて行う。
"""

import threading
import uuid
from collections import OrderedDict

from app.domain import ProposalConflict, ProposalNotFound
from app.question_validation import DemoQuestion, ValidatedProposal

# 未承認の提案を持つ上限（メモリを使い切らないため。超えたら古いものから捨てる）。
MAX_PENDING_PROPOSALS = 50

# 初期のデモ一覧。docs/requirements.md の設問A01〜A12（架空）の文言。
_INITIAL = (
    ("A01", "問い合わせにあったログインは成功したか？"),
    ("A02", "本人は「自分が行ったログインではない」と回答したか？"),
    ("A03", "対象期間に登録メールアドレスが変更されたか？"),
    ("A04", "本人は「自分が行ったメールアドレス変更ではない」と回答したか？"),
    ("A05", "対象期間にパスワードが変更されたか？"),
    ("A06", "本人は「自分が行ったパスワード変更ではない」と回答したか？"),
    ("A07", "対象期間にクレジットが消費されたか？"),
    ("A08", "本人は「その利用に心当たりがない」と回答したか？"),
    ("A09", "対象期間にAPIキーが新しく発行されたか？"),
    ("A10", "本人は「そのAPIキーは自分が発行したものではない」と回答したか？"),
    ("A11", "対象のAPIキーで成功した呼び出しがあったか？"),
    ("A12", "本人は「そのAPI利用に心当たりがない」と回答したか？"),
)


class InMemoryQuestionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._questions: tuple[DemoQuestion, ...] = tuple(
            DemoQuestion(i, t) for i, t in _INITIAL
        )
        self._revision = 1
        self._proposals: OrderedDict[str, ValidatedProposal] = OrderedDict()

    def snapshot(self) -> tuple[int, tuple[DemoQuestion, ...]]:
        """現行の（版, 一覧）。提案の基準に使う。"""
        with self._lock:
            return self._revision, self._questions

    def register(self, proposal: ValidatedProposal) -> str:
        """検証済みの提案を保存し、提案IDを返す（一覧は変えない）。"""
        proposal_id = uuid.uuid4().hex
        with self._lock:
            self._proposals[proposal_id] = proposal
            while len(self._proposals) > MAX_PENDING_PROPOSALS:
                self._proposals.popitem(last=False)
        return proposal_id

    def approve(self, proposal_id: str) -> tuple[int, tuple[DemoQuestion, ...]]:
        """基準の版を比べ、一致したときだけ一覧へ反映する。提案は1回で使い切る。"""
        with self._lock:
            proposal = self._proposals.pop(proposal_id, None)
            if proposal is None:
                raise ProposalNotFound(proposal_id)
            if proposal.base_revision != self._revision:
                raise ProposalConflict(proposal_id)
            self._questions = proposal.after
            self._revision += 1
            return self._revision, self._questions

    def reject(self, proposal_id: str) -> None:
        """提案を破棄する。一覧は変えない。"""
        with self._lock:
            if self._proposals.pop(proposal_id, None) is None:
                raise ProposalNotFound(proposal_id)
