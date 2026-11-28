"""管理者AIの試作: 設問の追加案の生成・確認・承認（モック。DBなし）。

提案の生成（AIのモック）、検証（純粋な関数）、承認と保存（メモリ上の保存先）の4つを
境界で分けている。ここは、HTTPの入力と出力、ステータスへの対応だけを持つ。
生成と保存の実装は app.main が app.state に置き、ここは共通の形（Protocol）だけを呼ぶ。

- 生成は、現行の一覧を変えない。AIの出力は検証し、不正なら422で拒否する。
- 承認はYes／Noの2つで、Yesだけが反映する。
  承認できるのは、サーバーが持つ検証済みの提案だけ。
- 提案は1回で使い切る。未知・使用済みは404、基準の一覧が変わっていたときは409。
- AIの応答を得られないときは502（待ち時間の超過は504）。AIの出力が使えない形なら422。
  どれも成功した応答に見せず、モックへの代替もしない。
- 応答には、生成元（source: モックか、Amazon Bedrock の実接続か）を付ける。
"""

import re
import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr, StringConstraints, field_validator

from app.domain import (
    GenerationSource,
    InvalidProposal,
    ProposalConflict,
    ProposalNotFound,
    ProposerUnavailable,
    RetirementBlocked,
    StoreBusy,
    source_of,
)
from app.question_validation import (
    DemoQuestion,
    ValidatedProposal,
    ValidatedRetirement,
    find_case_impact,
    find_rule_references,
    next_question_id,
    validate_model_output,
)
from app.usage_quota import (
    VIEWER_IP_HEADER,
    DuplicateAiRequest,
    QuotaExceeded,
    ViewerAddressUnavailable,
    viewer_key,
)

MAX_PROMPT_LENGTH = 500

router = APIRouter(prefix="/api/admin", tags=["admin-questions"])


class QuestionProposer(Protocol):
    """提案の生成の境界。モックと、Bedrockの実接続（bedrock_proposer）が、この形に従う。

    生成元は、属性 source（GenerationSource）で申告する。
    申告しないものはモックとして扱う。
    """

    def generate(self, prompt: str, current: tuple[DemoQuestion, ...]) -> Any:
        """生の出力を返す。応答を得られなければ ProposerUnavailable、
        出力が使えない形なら InvalidProposal。"""


class QuestionStore(Protocol):
    """デモの設問一覧と提案の保存の境界。実装はメモリ（question_store）。"""

    def snapshot(self) -> tuple[int, tuple[DemoQuestion, ...]]: ...

    def register(self, proposal: ValidatedProposal | ValidatedRetirement) -> str: ...

    def approve(
        self,
        proposal_id: str,
        references_of: Callable[[str], tuple[str, ...]] | None = None,
    ) -> tuple[int, tuple[DemoQuestion, ...]]: ...

    def reject(self, proposal_id: str) -> None: ...


class ProposalRequest(BaseModel):
    prompt: Annotated[
        StrictStr,
        StringConstraints(min_length=1, max_length=MAX_PROMPT_LENGTH),
    ]

    force_ai: bool = False

    @field_validator("prompt", mode="before")
    @classmethod
    def normalize_prompt(cls, value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("Enter a request")
            return value.strip(" ")
        return value


def find_template(
    request: Request,
    target: str,
    body: ProposalRequest,
    materialize: Callable[[Any], Any] = lambda raw: raw,
) -> Any:
    reuse = getattr(request.app.state, "proposal_reuse", None)
    if reuse is None or body.force_ai:
        return None
    raw = reuse.lookup(target, body.prompt)
    return None if raw is None else materialize(raw)


def question_from_template(raw: Any, current: tuple[DemoQuestion, ...]) -> Any:
    """追加の定型文は、承認時のIDではなく、現行データの次番号で組み立て直す。

    保存済みの行にidがあっても読み捨てる。検証は、通常の提案と同じ関数で行う。
    """
    if not isinstance(raw, dict) or raw.get("operation") != "add_question":
        return raw
    question = raw.get("question")
    if not isinstance(question, dict):
        return raw
    return {
        "operation": "add_question",
        "question": {"id": next_question_id(current), "text": question.get("text")},
    }


def register_proposal(
    request: Request, store: Any, proposal: Any, source: GenerationSource, prompt: str
) -> str:
    """定型文が有効なら、生成元を問わず依頼文を提案に添える（Yesで反映したとき自動登録する）。"""
    del source  # 登録するかは、生成元ではなく、Yesで実際に反映したかで決まる。
    if getattr(request.app.state, "proposal_reuse", None) is not None:
        return store.register_for_reuse(proposal, prompt)
    return store.register(proposal)


class DecisionRequest(BaseModel):
    decision: Literal["yes", "no"]


def _rows(questions: tuple[DemoQuestion, ...]) -> list[dict[str, Any]]:
    # 廃止した設問の行だけ、"retired": true を付ける（設問文は残す）。
    return [
        {"id": q.id, "text": q.text, **({"retired": True} if q.retired else {})}
        for q in questions
    ]


def _case_side(request: Request):  # noqa: ANN202
    """廃止の確認に使う、現行ルールと既存案件の読み取り。DBの有無はここでは決めない。"""
    store = (
        request.app.state.case_store
        if hasattr(request.app.state, "case_store")
        else None
    )
    if store is None or not all(
        hasattr(store, name) for name in ("list_rules", "list_cases", "get_case")
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "The database for rules and cases needed to check the "
                "deactivation is not "
                "available"
            ),
        )
    return store


_FALLBACK_ADMIN_LOCK = threading.Lock()


def admin_lock(request: Request) -> Any:
    """設問の決定と判定ルールの決定を、1つずつ順に行うためのロック（コンテキストマネージャ）。

    設問の廃止（ルールの参照の確認 → 廃止）と、ルールの承認（設問の確認 → 反映）は、
    間に割り込まれると、廃止された設問を参照するルールが残る。app.main が
    app.state に1つ置き、両方の決定が同じものを使う。置いていない構成
    （部品単体のテスト）では、プロセス内で共有する既定のロックを使う。
    """
    return getattr(request.app.state, "admin_lock", _FALLBACK_ADMIN_LOCK)


def _current_rules(request: Request, store: Any) -> list[dict[str, Any]]:
    """廃止の参照の確認に使う現行ルール。案件の保存先のルールに、デモのルール一覧を足す。

    デモのルール一覧（app.state.rule_store。判定ルールの追加案で増える）が参照する設問も、
    廃止させない。両方の行を、参照の判定に使う。同じルールID・版でも条件や結果が違えば
    別の行として数える（片方の参照を隠さない）。全項目が一致する行だけ、1件に数える。
    デモ側で削除したルールが、案件の保存先に残っていれば、参照に数える（保守的。
    どちらの一覧も変更しない）。
    """
    if getattr(request.app.state, "persistence", None) == "database":
        return request.app.state.rule_store.list_rows()
    rules: list[dict[str, Any]] = list(store.list_rules())
    demo = getattr(request.app.state, "rule_store", None)
    if demo is not None:
        rules += [r for r in demo.list_rows() if r not in rules]
    return rules


def _references_of(request: Request, store: Any) -> Callable[[str], tuple[str, ...]]:
    def references(question_id: str) -> tuple[str, ...]:
        rules = find_rule_references(question_id, _current_rules(request, store))
        return tuple(r["rule_id"] for r in rules)

    return references


def _retirement_body(
    proposal: ValidatedRetirement, request: Request, store: Any
) -> dict[str, Any]:
    """廃止案の差分。参照先のルールと、既存案件への影響を示す。何も変更しない。"""
    question_id = proposal.target.id
    references = find_rule_references(question_id, _current_rules(request, store))
    cases = [store.get_case(row["case_id"]) for row in store.list_cases()]
    return {
        "kind": "retire",
        "retired": {"id": question_id, "text": proposal.target.text},
        # 参照が1件以上あれば、Yesでも廃止しない。
        "references": references,
        "can_retire": not references,
        "impact": find_case_impact(question_id, cases),
        # ルール・案件は、この操作では変更しない（連鎖して変えない）。
        "cascade": [],
    }


_QUOTA_DETAILS = {
    "ip": (
        "The limit on AI proposals from this IP address for today (Japan time) "
        "has been reached. "
        "Please try again after midnight Japan time. "
        "Users on the same IP address share the count. The shared data has not changed"
    ),
    "global": (
        "The limit on AI proposals for the whole demo for today (Japan time) has been "
        "reached. "
        "Please try again after midnight Japan time. The shared data has not changed"
    ),
}


def reserve_ai_call(request: Request, proposer: Any) -> None:
    """Bedrockを呼ぶ前に、日次の利用枠を予約する（モックは消費しない）。

    予約はDB（または開発用のメモリ）に確定してから戻る。戻ったあとに、呼び出し側が生成を
    1回だけ呼ぶ。上限・接続元の不明・保存先の不調のときは、例外で止まり、生成を呼ばない。
    """
    if source_of(proposer).kind == "mock":
        return
    quota = getattr(request.app.state, "ai_quota", None)
    if quota is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "The database that manages the AI proposal usage quota is not available"
            ),
        )
    try:
        addresses = request.headers.getlist(VIEWER_IP_HEADER)
        # ローカル専用: 専用ヘッダーがなく、ローカルの再利用構成のときだけ、
        # 直接の接続元（ポートなし）を使う。
        # 重複・不正な専用ヘッダーはフォールバックせず拒否する。
        # 公開側（既定）は厳格なまま。
        if not addresses and getattr(request.app.state, "local_quota_address", False):
            if request.client is not None:
                addresses = [request.client.host]
        key = viewer_key(addresses[0] if len(addresses) == 1 else None)
    except ViewerAddressUnavailable:
        raise HTTPException(
            status_code=400,
            detail=(
                "The IP address could not be determined, so an AI proposal cannot be "
                "created. The shared data has not changed"
            ),
        ) from None
    try:
        keys = request.headers.getlist("Idempotency-Key")
        if len(keys) != 1 or not re.fullmatch(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", keys[0]
        ):
            raise HTTPException(
                status_code=400, detail="A valid request ID is required"
            )
        quota.reserve(key, keys[0])
    except DuplicateAiRequest:
        raise HTTPException(
            status_code=409,
            detail=(
                "This request has already been sent. The AI call will not be repeated"
            ),
        ) from None
    except QuotaExceeded as error:
        raise HTTPException(
            status_code=429,
            detail=_QUOTA_DETAILS[error.scope],
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from None
    # StoreBusy は install_handlers の503になる（生成は呼ばれない）。


def install_handlers(application: FastAPI) -> None:
    """保存先の衝突（再試行を使い切った）を、入力が残る503にそろえる。"""

    @application.exception_handler(StoreBusy)
    def _store_busy(_request: Request, _error: StoreBusy) -> JSONResponse:
        return JSONResponse(
            {"detail": "The database is busy. Please try again in a while"},
            status_code=503,
            headers={"Retry-After": "1"},
        )


def source_body(source: GenerationSource) -> dict[str, Any]:
    """応答に付ける生成元。資格情報は含まない。"""
    body: dict[str, Any] = {"kind": source.kind}
    if source.model_id is not None:
        body["model_id"] = source.model_id
    if source.region is not None:
        body["region"] = source.region
    return body


def notice(request: Request, proposer_name: str) -> dict[str, Any]:
    """画面に「生成元（モックか実接続か）」と保存の範囲を出すための情報。

    mock は、生成元がモックのときだけ true（実接続のとき false）。
    """
    source = source_of(getattr(request.app.state, proposer_name, None))
    return {
        "mock": source.kind == "mock",
        "persistence": getattr(request.app.state, "persistence", "memory"),
        "source": source_body(source),
        **(
            {"template_reuse": True}
            if getattr(request.app.state, "proposal_reuse", None)
            else {}
        ),
    }


# 生成を得られなかった原因ごとの、利用者に出す固定の文言。
# SDKの文言・AIの出力は出さない。
_UNAVAILABLE_DETAILS = {
    "timeout": "The AI response timed out",
    "network": "Could not connect to the AI service",
    "credentials": (
        "The AI service credentials cannot be used (check the server configuration)"
    ),
    "access": (
        "Access to the AI service is not permitted "
        "(check the permissions and model access settings)"
    ),
    "throttled": ("The AI service is busy. Please try again in a while"),
    "config": "The AI service connection settings (region and model) do not match",
    "service": "An error occurred on the AI service side",
}


def unavailable_error(
    error: ProposerUnavailable, source: GenerationSource
) -> HTTPException:
    """生成を得られなかったときのHTTPエラー。待ち時間の超過は504、それ以外は502。

    成功した応答に見せない。モックの既定の文言は従来のまま（実接続では、原因ごとの固定の文言）。
    """
    reason = getattr(error, "reason", "unavailable")
    status = 504 if reason == "timeout" else 502
    detail = _UNAVAILABLE_DETAILS.get(reason)
    if detail is None:
        detail = (
            "Could not get an AI response (mock)"
            if source.kind == "mock"
            else "Could not get an AI response"
        )
    return HTTPException(status_code=status, detail=detail)


@router.get("/questions")
def list_questions(request: Request) -> dict[str, Any]:
    revision, questions = request.app.state.question_store.snapshot()
    return {
        "revision": revision,
        "questions": _rows(questions),
        **notice(request, "question_proposer"),
    }


@router.post("/question-proposals", status_code=201)
def create_proposal(body: ProposalRequest, request: Request) -> dict[str, Any]:
    store: QuestionStore = request.app.state.question_store
    proposer: QuestionProposer = request.app.state.question_proposer
    revision, current = store.snapshot()
    raw = find_template(
        request, "question", body, lambda raw: question_from_template(raw, current)
    )
    source = GenerationSource("template") if raw is not None else source_of(proposer)
    try:
        if raw is None:
            reserve_ai_call(request, proposer)
            raw = proposer.generate(body.prompt.strip(), current)
    except ProposerUnavailable as error:
        raise unavailable_error(error, source_of(proposer)) from None
    except InvalidProposal as error:  # 実接続の出力が、JSONとして読めないなど
        raise HTTPException(status_code=422, detail=str(error)) from None
    try:
        proposal = validate_model_output(raw, current, revision)
    except InvalidProposal as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    detail: dict[str, Any]
    if isinstance(proposal, ValidatedRetirement):
        detail = _retirement_body(proposal, request, _case_side(request))
    else:
        detail = {
            "kind": "add",
            "added": {"id": proposal.added.id, "text": proposal.added.text},
        }
    return {
        "proposal_id": register_proposal(request, store, proposal, source, body.prompt),
        "base_revision": proposal.base_revision,
        "before": _rows(proposal.base),
        "after": _rows(proposal.after),
        **detail,
        **notice(request, "question_proposer"),
        "source": source_body(source),
        "mock": source.kind == "mock",
    }


@router.post("/question-proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: str, body: DecisionRequest, request: Request
) -> dict[str, Any]:
    # 判定ルールの承認と同時に進まないよう、1つずつ順に決める（admin_lock）。
    with admin_lock(request):
        return _decide(proposal_id, body, request)


def _decide(
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
                **notice(request, "question_proposer"),
            }
        # 廃止案だけ、承認の時点の現行ルールを調べ直す（提案後に変わっても拒否する）。
        revision, questions = store.approve(
            proposal_id,
            lambda question_id: _references_of(request, _case_side(request))(
                question_id
            ),
        )
    except RetirementBlocked as error:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Question {error.question_id} cannot be deactivated because "
                "the current judgment rules "
                f"({', '.join(error.references)}) reference it. "
                "The question list has not changed"
            ),
        ) from None
    except ProposalNotFound:
        raise HTTPException(
            status_code=404, detail="Proposal not found (unknown or already processed)"
        ) from None
    except ProposalConflict:
        raise HTTPException(
            status_code=409,
            detail=(
                "The question list changed after the proposal, so nothing was applied"
            ),
        ) from None
    return {
        "applied": True,
        "revision": revision,
        "questions": _rows(questions),
        **notice(request, "question_proposer"),
    }
