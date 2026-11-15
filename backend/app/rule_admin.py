"""管理者AIの試作: 判定ルールの追加案・削除案の生成・確認・承認（モック。DBなし）。

設問の追加案（question_admin）と同じ流れで、操作の対象を判定ルールに広げる。
提案の生成（AIのモック）、検証（純粋な関数）、承認と保存（メモリ上の保存先）を境界で分け、
ここは、HTTPの入力と出力、ステータスへの対応だけを持つ。

- 生成は、現行の一覧を変えない。AIの出力は、現行の設問とルールの一覧と照らして検証し、
  不正（存在しない設問・結果コード、重複など）なら422で拒否する。
- 承認はYes／Noの2つで、Yesだけが反映する。承認できるのは、検証済みの提案だけ。
- 提案は1回で使い切る。未知・使用済みは404、基準のルール一覧または設問一覧の版が
  変わっていたときは409（部分反映しない。設問の変更を無関係とは見なさない）。
- 設問の決定（question_admin）とルールの決定は、admin_lock で1つずつ順に行う。
- AIの応答を得られないときは502（成功した応答に見せない）。
"""

from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request

from app.domain import (
    InvalidProposal,
    ProposalConflict,
    ProposalNotFound,
    ProposerUnavailable,
)
from app.question_admin import DecisionRequest, ProposalRequest, admin_lock
from app.question_validation import DemoQuestion
from app.rule_validation import (
    DemoRule,
    ValidatedRuleAdd,
    ValidatedRuleRemoval,
    next_rule_id,
    validate_rule_output,
)

router = APIRouter(prefix="/api/admin", tags=["admin-rules"])


class RuleProposer(Protocol):
    """提案の生成の境界。モックと、後の実接続（別Issue）が、この形に従う。"""

    def generate(
        self,
        prompt: str,
        current: tuple[DemoRule, ...],
        next_id: str,
        questions: tuple[DemoQuestion, ...],
    ) -> Any:
        """生の出力を返す。応答を得られなければ ProposerUnavailable。"""


class RuleStore(Protocol):
    """デモの判定ルール一覧と提案の保存の境界。実装はメモリ（rule_store）。"""

    def snapshot(self) -> tuple[int, tuple[DemoRule, ...]]: ...

    def removed_ids(self) -> frozenset[str]: ...

    def register(self, proposal: ValidatedRuleAdd | ValidatedRuleRemoval) -> str: ...

    def pending(self, proposal_id: str) -> ValidatedRuleAdd | ValidatedRuleRemoval: ...

    def approve(self, proposal_id: str) -> tuple[int, tuple[DemoRule, ...]]: ...

    def reject(self, proposal_id: str) -> None: ...


def _row(rule: DemoRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "revision": rule.revision,
        "result_code": rule.result_code,
        "conditions": dict(rule.conditions),
    }


def _rows(rules: tuple[DemoRule, ...]) -> list[dict[str, Any]]:
    return [_row(r) for r in rules]


def _notice() -> dict[str, Any]:
    # 画面に「モックであること」と保存の範囲を出すための情報。
    return {"mock": True, "persistence": "memory"}


@router.get("/rules")
def list_rules(request: Request) -> dict[str, Any]:
    revision, rules = request.app.state.rule_store.snapshot()
    return {"revision": revision, "rules": _rows(rules), **_notice()}


@router.post("/rule-proposals", status_code=201)
def create_proposal(body: ProposalRequest, request: Request) -> dict[str, Any]:
    store: RuleStore = request.app.state.rule_store
    proposer: RuleProposer = request.app.state.rule_proposer
    # 設問の版と一覧を、1つの基準として読む（承認のとき、この版と比べる）。
    question_revision, questions = request.app.state.question_store.snapshot()
    revision, current = store.snapshot()
    removed = store.removed_ids()
    try:
        raw = proposer.generate(
            body.prompt, current, next_rule_id(current, removed), questions
        )
    except ProposerUnavailable:
        raise HTTPException(
            status_code=502, detail="AIの応答を得られませんでした（モック）"
        ) from None
    try:
        proposal = validate_rule_output(
            raw,
            current,
            questions,
            revision,
            removed,
            question_revision=question_revision,
        )
    except InvalidProposal as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    detail: dict[str, Any]
    if isinstance(proposal, ValidatedRuleRemoval):
        detail = {"kind": "remove", "removed": _row(proposal.target)}
    else:
        detail = {"kind": "add", "added": _row(proposal.added)}
    return {
        "proposal_id": store.register(proposal),
        "base_revision": proposal.base_revision,
        "before": _rows(proposal.base),
        "after": _rows(proposal.after),
        **detail,
        **_notice(),
    }


@router.post("/rule-proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: str, body: DecisionRequest, request: Request
) -> dict[str, Any]:
    # 設問の廃止・追加の決定と同時に進まないよう、1つずつ順に決める（admin_lock）。
    # 設問の確認から反映までの間に、設問の廃止が割り込めない。
    with admin_lock(request):
        return _decide(proposal_id, body, request)


def _decide(
    proposal_id: str, body: DecisionRequest, request: Request
) -> dict[str, Any]:
    store: RuleStore = request.app.state.rule_store
    try:
        if body.decision == "no":
            store.reject(proposal_id)
            revision, rules = store.snapshot()
            return {
                "applied": False,
                "revision": revision,
                "rules": _rows(rules),
                **_notice(),
            }
        proposal = store.pending(proposal_id)
        # 提案は、ルール一覧の版と設問一覧の版を1つの基準にしている。設問の版が変わって
        # いたら（どの設問の変更でも）、部分反映せず拒否する。
        # ルール一覧の版は approve が比べる。
        question_revision, _ = request.app.state.question_store.snapshot()
        if proposal.base_question_revision != question_revision:
            store.reject(proposal_id)
            raise HTTPException(
                status_code=409,
                detail="設問の一覧が提案の後に変わったため、反映しませんでした。"
                "判定ルールの一覧は変わっていません",
            )
        revision, rules = store.approve(proposal_id)
    except ProposalNotFound:
        raise HTTPException(
            status_code=404, detail="提案がありません（未知、または処理済みです）"
        ) from None
    except ProposalConflict:
        raise HTTPException(
            status_code=409,
            detail="判定ルールの一覧が提案の後に変わったため、反映しませんでした",
        ) from None
    return {"applied": True, "revision": revision, "rules": _rows(rules), **_notice()}
