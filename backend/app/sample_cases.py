"""共有サンプル案件の作成。POST /api/sample-cases。

新しい操作ごとに1件、操作IDから承認済みの架空例を選んで追加し、
保存先が案件IDを採番する。
A09にはモックと同じ規則の固定値を使い、取得失敗の例は確認待ちにする。
Bedrock・内部HTTPモックは呼ばず、AIの利用枠も消費しない。
"""

import re

from fastapi import APIRouter, HTTPException, Request, Response

from app.domain import StoreBusy

router = APIRouter(prefix="/api/sample-cases", tags=["sample-cases"])


def _template(
    summary: str,
    user_id: str,
    a09_answer: str | None,
    result_code: str | None = None,
) -> dict:
    """a09_answer が None のときは、A09の取得に失敗した（確認待ちのまま）案件。"""
    return {
        "summary": summary,
        "user": {"user_id": user_id},
        "inquiry": {"subject": "架空の問い合わせ", "body": f"{summary}（架空）"},
        "a09_answer": a09_answer,
        "result_code": result_code,
    }


# user_id が偶数なら A09=Yes、奇数なら No（モックの規則と同じ）。
SAMPLE_TEMPLATES: tuple[dict, ...] = (
    _template("身に覚えのないログインの通知が届いた", "456", "yes"),
    _template("メールアドレス変更の通知に心当たりがない", "457", "no"),
    _template("APIキー発行の通知に心当たりがない（取得に失敗）", "458", None),
    _template("ログイン失敗の通知が夜間に何件も届いた", "460", "yes", "no_issue"),
)

_OPERATION_KEY = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


def sample_for_operation(operation_key: str) -> dict:
    """依頼IDから架空例の型を決める。案件IDは保存先が採番する。"""
    digits = operation_key.replace("-", "")
    template = SAMPLE_TEMPLATES[int(digits, 16) % len(SAMPLE_TEMPLATES)]
    return {
        "operation_key": operation_key,
        **template,
    }


def _operation_key(request: Request) -> str:
    keys = request.headers.getlist("Idempotency-Key")
    if len(keys) != 1 or not _OPERATION_KEY.fullmatch(keys[0]):
        raise HTTPException(
            status_code=422,
            detail="有効な依頼ID（Idempotency-Key）が必要です。案件は作成されていません",
        )
    return keys[0]


def _store(request: Request):  # noqa: ANN202
    store = request.app.state.case_store
    if not hasattr(store, "create_sample_operation"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store


@router.post("")
def create_sample_case(request: Request, response: Response) -> dict:
    """新規作成は201、同じ操作IDの再送は200で同じ案件IDを返す。

    不正な操作IDは422、DB未設定や保存時の混雑・採番上限は503。
    保存に失敗した操作は同じIDで再試行できる。
    """
    sample = sample_for_operation(_operation_key(request))
    store = _store(request)
    try:
        case_id, created = store.create_sample_operation(sample)
    except StoreBusy:
        raise HTTPException(
            status_code=503,
            detail="保存先が混み合っています。もう一度押してください。"
            "同じ操作の再試行なので、案件は重複しません",
            headers={"Retry-After": "1"},
        ) from None
    response.status_code = 201 if created else 200
    return {"case_id": case_id, "created": created}
