"""デモの判定ルール一覧と、検証済みの提案の保存先（メモリ上の仮実装）。

DBは使わない。サーバーのメモリにだけあり、バックエンドを再起動すると初期状態（R01〜R03）に
戻る。永続保存ではない。審査画面のルール（案件の保存先 case_store）とは別のデータで、
互いに影響しない（初期値は、開発用の架空ルールと同じ組み合わせの写し）。
提案の承認（版の比較と反映）は、ロックの中で1回にまとめて行う。
"""

import threading
import uuid
from collections import OrderedDict
from typing import Any

from app.domain import ProposalConflict, ProposalNotFound
from app.rule_validation import DemoRule, ValidatedRuleAdd, ValidatedRuleRemoval

Proposal = ValidatedRuleAdd | ValidatedRuleRemoval

MAX_PENDING_PROPOSALS = 50

_QUESTIONS = tuple(f"A{n:02d}" for n in range(1, 13))
# 架空のルール（docs/decision-support-demo-rules.json のR01〜R03と同じ組み合わせ）。
_INITIAL = (
    ("R01", "caution", "Y Y Y Y Y N Y Y Y N Y Y"),
    ("R02", "no_issue", "Y N Y Y Y Y Y Y Y Y Y N"),
    ("R03", "account_suspension", "N Y Y Y Y N Y Y Y N Y Y"),
)


def _initial_rules() -> tuple[DemoRule, ...]:
    return tuple(
        DemoRule(
            rule_id,
            1,
            result_code,
            tuple(
                (q, "yes" if mark == "Y" else "no")
                for q, mark in zip(_QUESTIONS, pattern.split(), strict=True)
            ),
        )
        for rule_id, result_code, pattern in _INITIAL
    )


class InMemoryRuleStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: tuple[DemoRule, ...] = _initial_rules()
        self._removed_ids: frozenset[str] = frozenset()
        self._revision = 1
        self._proposals: OrderedDict[str, Proposal] = OrderedDict()

    def snapshot(self) -> tuple[int, tuple[DemoRule, ...]]:
        """現行の（版, 一覧）。提案の基準に使う。"""
        with self._lock:
            return self._revision, self._rules

    def removed_ids(self) -> frozenset[str]:
        """削除済みのルールID。削除したIDを再利用しないために使う。"""
        with self._lock:
            return self._removed_ids

    def list_rows(self) -> list[dict[str, Any]]:
        """設問の廃止の確認に使う、現行ルールの行（list_rules と同じ形）。"""
        with self._lock:
            return [
                {
                    "rule_id": r.rule_id,
                    "revision": r.revision,
                    "result_code": r.result_code,
                    "conditions": dict(r.conditions),
                }
                for r in self._rules
            ]

    def register(self, proposal: Proposal) -> str:
        """検証済みの提案を保存し、提案IDを返す（一覧は変えない）。"""
        proposal_id = uuid.uuid4().hex
        with self._lock:
            self._proposals[proposal_id] = proposal
            while len(self._proposals) > MAX_PENDING_PROPOSALS:
                self._proposals.popitem(last=False)
        return proposal_id

    def pending(self, proposal_id: str) -> Proposal:
        """未処理の提案を返す（消費しない）。なければ ProposalNotFound。"""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ProposalNotFound(proposal_id)
        return proposal

    def approve(self, proposal_id: str) -> tuple[int, tuple[DemoRule, ...]]:
        """基準の版を比べ、一致したときだけ一覧へ反映する（部分反映しない）。1回で使い切る。"""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise ProposalNotFound(proposal_id)
            del self._proposals[proposal_id]
            if proposal.base_revision != self._revision:
                raise ProposalConflict(proposal_id)
            if isinstance(proposal, ValidatedRuleRemoval):
                self._removed_ids = self._removed_ids | {proposal.target.rule_id}
            self._rules = proposal.after
            self._revision += 1
            return self._revision, self._rules

    def reject(self, proposal_id: str) -> None:
        """提案を破棄する。一覧は変えない。"""
        with self._lock:
            if self._proposals.pop(proposal_id, None) is None:
                raise ProposalNotFound(proposal_id)
