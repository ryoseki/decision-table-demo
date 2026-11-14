"""管理者AIの試作: 設問の追加案の生成・確認・承認（モック。DBなし）。

提案の生成（AIのモック）、検証（純粋な関数）、承認と保存（メモリ上の保存先）の4つを
境界で分けている。ここは、HTTPの入力と出力、ステータスへの対応だけを持つ。
生成と保存の実装は app.main が app.state に置き、ここは共通の形（Protocol）だけを呼ぶ。

- 生成は、現行の一覧を変えない。AIの出力は検証し、不正なら422で拒否する。
- 承認はYes／Noの2つで、Yesだけが反映する。
  承認できるのは、サーバーが持つ検証済みの提案だけ。
- 提案は1回で使い切る。未知・使用済みは404、基準の一覧が変わっていたときは409。
- AIの応答を得られないときは502（成功した応答に見せない）。
"""

from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictStr, StringConstraints

from app.domain import (
    InvalidProposal,
    ProposalConflict,
    ProposalNotFound,
    ProposerUnavailable,
)
from app.question_validation import (
    DemoQuestion,
    ValidatedProposal,
    validate_model_output,
)

MAX_PROMPT_LENGTH = 500

router = APIRouter(prefix="/api/admin", tags=["admin-questions"])


class QuestionProposer(Protocol):
    """提案の生成の境界。モックと、後の実接続（別Issue）が、この形に従う。"""

    def generate(self, prompt: str, current: tuple[DemoQuestion, ...]) -> Any:
        """生の出力を返す。応答を得られなければ ProposerUnavailable。"""


class QuestionStore(Protocol):
    """デモの設問一覧と提案の保存の境界。実装はメモリ（question_store）。"""

    def snapshot(self) -> tuple[int, tuple[DemoQuestion, ...]]: ...

    def register(self, proposal: ValidatedProposal) -> str: ...

    def approve(self, proposal_id: str) -> tuple[int, tuple[DemoQuestion, ...]]: ...

    def reject(self, proposal_id: str) -> None: ...


class ProposalRequest(BaseModel):
    prompt: Annotated[
        StrictStr,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_PROMPT_LENGTH
        ),
    ]


class DecisionRequest(BaseModel):
    decision: Literal["yes", "no"]


def _rows(questions: tuple[DemoQuestion, ...]) -> list[dict[str, str]]:
    return [{"id": q.id, "text": q.text} for q in questions]


def _notice() -> dict[str, Any]:
    # 画面に「モックであること」と保存の範囲を出すための情報。
    return {"mock": True, "persistence": "memory"}


@router.get("/questions")
def list_questions(request: Request) -> dict[str, Any]:
    revision, questions = request.app.state.question_store.snapshot()
    return {"revision": revision, "questions": _rows(questions), **_notice()}


@router.post("/question-proposals", status_code=201)
def create_proposal(body: ProposalRequest, request: Request) -> dict[str, Any]:
    store: QuestionStore = request.app.state.question_store
    proposer: QuestionProposer = request.app.state.question_proposer
    revision, current = store.snapshot()
    try:
        raw = proposer.generate(body.prompt, current)
    except ProposerUnavailable:
        raise HTTPException(
            status_code=502, detail="AIの応答を得られませんでした（モック）"
        ) from None
    try:
        proposal = validate_model_output(raw, current, revision)
    except InvalidProposal as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "proposal_id": store.register(proposal),
        "base_revision": proposal.base_revision,
        "before": _rows(proposal.base),
        "after": _rows(proposal.after),
        "added": {"id": proposal.added.id, "text": proposal.added.text},
        **_notice(),
    }


@router.post("/question-proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: str, body: DecisionRequest, request: Request
) -> dict[str, Any]:
    store: QuestionStore = request.app.state.question_store
    try:
        if body.decision == "no":
            store.reject(proposal_id)
            revision, questions = store.snapshot()
            return {
                "applied": False,
                "revision": revision,
                "questions": _rows(questions),
                **_notice(),
            }
        revision, questions = store.approve(proposal_id)
    except ProposalNotFound:
        raise HTTPException(
            status_code=404, detail="提案がありません（未知、または処理済みです）"
        ) from None
    except ProposalConflict:
        raise HTTPException(
            status_code=409,
            detail="設問の一覧が提案の後に変わったため、反映しませんでした",
        ) from None
    return {
        "applied": True,
        "revision": revision,
        "questions": _rows(questions),
        **_notice(),
    }
