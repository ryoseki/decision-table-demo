"""案件の再表示と［判定する］（架空）。docs/intake-mock-contract-design.md 2.6。

どちらの操作もHTTPモックを呼ばない（取得は受付時の1回だけ）。入力途中の自動保存はない。
保存先がDBでない場合（開発用のメモリ保存）は503を返す。
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictInt, StrictStr, model_validator

from app.domain import CaseNotFound, InvalidJudgment, JudgmentAlreadyRecorded, StoreBusy

router = APIRouter(prefix="/api/cases", tags=["cases"])


class JudgmentRequest(BaseModel):
    result_code: StrictStr
    # 「このルールを採用」を操作したときだけ渡す。結果を選んだだけでは渡さない。
    rule_id: StrictStr | None = None
    rule_revision: StrictInt | None = None
    # 変更する設問だけ渡す。渡さない設問は変更しない。回答を空に戻す操作はない。
    answers: dict[str, Literal["yes", "no", "pending"]] = {}

    @model_validator(mode="after")
    def _rule_is_both_or_neither(self) -> JudgmentRequest:
        if (self.rule_id is None) != (self.rule_revision is None):
            raise ValueError("rule_id と rule_revision は両方指定するか、両方省く")
        if self.rule_revision is not None and self.rule_revision < 1:
            raise ValueError("rule_revision は1以上")
        return self


def _db_store(request: Request):  # noqa: ANN202
    store = request.app.state.case_store
    if not hasattr(store, "save_judgment") or not hasattr(store, "list_cases"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store


@router.get("")
def list_cases(request: Request) -> list[dict]:
    """案件の一覧。HTTPモックは呼ばない。並びは案件ID順（受付日時の列がないため）。"""
    return _db_store(request).list_cases()


def _retired_ids(request: Request) -> frozenset[str]:
    """管理者が廃止した設問のID（メモリ上のデモの一覧。なければ空）。"""
    questions = getattr(request.app.state, "question_store", None)
    return questions.retired_ids() if questions is not None else frozenset()


@router.get("/{case_id}")
def get_case(case_id: str, request: Request) -> dict:
    """保存済みの案件。廃止済みの設問は、保存済みの設問文・回答のまま retired を付ける。

    廃止は、案件の保存データを変えない（読み出すときに印を付けるだけ）。
    判定の登録の有無は judged（案件の結果の有無）。登録済みの案件は登録時の結果を、
    未登録の案件は最新の判定ルール（GET /api/rules）で照合する。
    """
    try:
        case = _db_store(request).get_case(case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="案件がありません") from None
    retired = _retired_ids(request)
    return {
        **case,
        "judged": case.get("result") is not None,
        "questions": [
            {**q, "retired": q["question_code"] in retired} for q in case["questions"]
        ],
    }


@router.put("/{case_id}/judgment", status_code=204)
def save_judgment(case_id: str, body: JudgmentRequest, request: Request) -> None:
    """未登録の案件への最初の1回だけ。登録済みは409で、結果も回答も変えない。"""
    adopted = (body.rule_id, body.rule_revision) if body.rule_id is not None else None
    store = _db_store(request)
    # 廃止済みの設問には、新たに回答できない（保存済みの回答は変えない）。
    closed = sorted(set(body.answers) & _retired_ids(request))
    if closed:
        raise HTTPException(
            status_code=422,
            detail=f"廃止済みの設問には回答できません: {', '.join(closed)}",
        )
    try:
        store.save_judgment(case_id, body.result_code, body.answers, adopted)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="案件がありません") from None
    except JudgmentAlreadyRecorded:
        raise HTTPException(
            status_code=409,
            detail="判定を登録済みの案件は、再保存できません（閲覧のみ）",
        ) from None
    except InvalidJudgment as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except StoreBusy:
        raise HTTPException(
            status_code=503,
            detail="保存先が混み合っています。入力は残っています。もう一度保存してください",
            headers={"Retry-After": "1"},
        ) from None
