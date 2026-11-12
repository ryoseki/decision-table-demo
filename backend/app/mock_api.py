"""自動回答のHTTPモック（架空）。docs/intake-mock-contract-design.md 2.2。

実サービスのAPIではない。受付処理が実際にHTTPで呼ぶための置き場で、
実サービス接続時に差し替える。補完用のモックは作らない（補完情報の概念を持たない）。
"""

import asyncio
import re
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, StrictInt, StrictStr

from app.domain import AUTO_ANSWER_KEYS

# 失敗指示ヘッダー X-Mock-Fault: timeout を受けたとき、応答を遅らせる秒数。
# 受付側のタイムアウト（3秒）より十分長くする。
FAULT_DELAY_SECONDS = 10.0
FAULT_TIMEOUT = "timeout"

router = APIRouter(prefix="/mock/v1", tags=["mock"])


class AutoAnswerRequest(BaseModel):
    user_id: StrictStr
    sample_number: StrictInt | None = None


@router.post("/auto-answers/{question_code}")
async def auto_answer(
    question_code: str,
    body: AutoAnswerRequest,
    x_mock_fault: Annotated[str | None, Header()] = None,
) -> dict[str, str | int]:
    if question_code not in AUTO_ANSWER_KEYS:
        raise HTTPException(status_code=404, detail="自動回答の対象外の設問です")
    if x_mock_fault == FAULT_TIMEOUT:
        await asyncio.sleep(FAULT_DELAY_SECONDS)
    if body.sample_number is not None:
        number = body.sample_number
    elif re.fullmatch(r"[0-9]+", body.user_id):
        number = int(body.user_id)
    else:
        raise HTTPException(
            status_code=422,
            detail="user_id を整数にできません（sample_number もありません）",
        )
    # 偶数がYes、奇数がNo（設計案の規則）。
    answer = "yes" if number % 2 == 0 else "no"
    return {"question_code": question_code, "answer": answer, "sample_number": number}
