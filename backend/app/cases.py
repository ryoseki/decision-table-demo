"""案件の再表示と［判定する］（架空）。docs/intake-mock-contract-design.md 2.6。

どちらの操作もHTTPモックを呼ばない（取得は受付時の1回だけ）。入力途中の自動保存はない。
保存先がDBでない場合（開発用のメモリ保存）は503を返す。
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictInt, StrictStr, model_validator

from app.domain import CaseNotFound, InvalidJudgment

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
    if not hasattr(store, "save_judgment"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store


@router.get("/{case_id}")
def get_case(case_id: str, request: Request) -> dict:
    try:
        return _db_store(request).get_case(case_id)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="案件がありません") from None


@router.put("/{case_id}/judgment", status_code=204)
def save_judgment(case_id: str, body: JudgmentRequest, request: Request) -> None:
    adopted = (body.rule_id, body.rule_revision) if body.rule_id is not None else None
    try:
        _db_store(request).save_judgment(
            case_id, body.result_code, body.answers, adopted
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="案件がありません") from None
    except InvalidJudgment as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
