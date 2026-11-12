"""案件の受付（架空）。docs/intake-mock-contract-design.md 2.3。

この範囲はHTTPモックの呼び出しまで。DB保存はCaseStoreの境界の向こう側で、
実DBへの接続と保存は別Issue（保存の実装）の範囲。ここの既定の保存先はメモリ上の仮実装で、
案件IDの形式も仮（採番形式は未決）。

順序: 検査 -> 1回目の保存 -> 対象設問ごとにHTTP呼び出し（1回）
-> 2回目の保存 -> 201。
HTTPの失敗は確認待ち（pending）で、再試行しない。HTTPはどの保存の途中でも待たない。
"""

import http.client
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import (
    BaseModel,
    Field,
    StrictStr,
    StringConstraints,
    model_validator,
)

from app.db_store import new_case_id
from app.mock_api import AUTO_ANSWER_KEYS, FAULT_TIMEOUT

# 受付側のタイムアウト（ユーザー合意: 3秒）。
MOCK_TIMEOUT_SECONDS = 3.0

# 仮の文字数上限（根拠となる規定は未確認の独自案で、未合意）。
MAX_BODY_LENGTH = 4000
MAX_SHORT_LENGTH = 256

_VALID_ANSWERS = ("yes", "no")

router = APIRouter(prefix="/api/intake", tags=["intake"])


def _text(max_length: int):  # noqa: ANN202
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


ShortText = Annotated[StrictStr, _text(MAX_SHORT_LENGTH)]
LongText = Annotated[StrictStr, _text(MAX_BODY_LENGTH)]


class UserIn(BaseModel):
    user_id: ShortText


class InquiryIn(BaseModel):
    subject: ShortText
    body: LongText


class DemoControls(BaseModel):
    """検証用。省略すれば通常の受付。実サービス接続時に削除する。"""

    fault_questions: list[StrictStr] = Field(default_factory=list)


# 定義していない項目（identity_verified、received_at、元の案件IDなど）は無視する。
# pydanticの既定（extra="ignore"）のまま、保存にも渡さない。
class IntakeRequest(BaseModel):
    summary: LongText
    user: UserIn
    inquiry: InquiryIn
    period_start_at: datetime | None = None
    period_end_at: datetime | None = None
    demo_controls: DemoControls | None = None

    @model_validator(mode="after")
    def _period_is_both_or_neither(self) -> IntakeRequest:
        if (self.period_start_at is None) != (self.period_end_at is None):
            raise ValueError(
                "period_start_at と period_end_at は両方指定するか、両方省く"
            )
        if self.period_start_at is not None:
            try:
                ordered = self.period_start_at < self.period_end_at
            except TypeError:  # タイムゾーンの有無が揃っていない
                raise ValueError(
                    "period_start_at と period_end_at のタイムゾーンの有無を揃える"
                ) from None
            if not ordered:
                raise ValueError("period_start_at は period_end_at より前にする")
        return self


class CaseStore(Protocol):
    """受付の保存境界。実装はメモリ（開発用）とPostgreSQL（db_store）。"""

    def create_case(self, case: dict) -> str:
        """1回目の保存。A09などの自動回答対象は pending で作る。案件IDを返す。"""

    def set_auto_answer(self, case_id: str, question_code: str, answer: str) -> None:
        """2回目の保存。取得に成功した回答（yes／no）だけを入れる。"""


class InMemoryCaseStore:
    """メモリ上の仮の保存先（プロセスの再起動や別インスタンスでは残らない）。"""

    def __init__(self) -> None:
        self.cases: dict[str, dict] = {}

    def create_case(self, case: dict) -> str:
        case_id = new_case_id()
        self.cases[case_id] = {
            **case,
            "answers": {code: "pending" for code in AUTO_ANSWER_KEYS},
            "automated_answers": {},
        }
        return case_id

    def set_auto_answer(self, case_id: str, question_code: str, answer: str) -> None:
        stored = self.cases[case_id]
        stored["answers"][question_code] = answer
        stored["automated_answers"][question_code] = answer


class AutoAnswerError(Exception):
    """取得失敗（接続失敗・タイムアウト・200以外・不正な応答）。"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None  # 3xxは追わず、HTTPErrorとして失敗扱いにする


_opener = urllib.request.build_opener(_NoRedirect)


def fetch_auto_answer(
    base_url: str, question_code: str, user_id: str, *, inject_timeout: bool = False
) -> str:
    """モックを実際にHTTPで1回呼ぶ。成功時はyes／no、失敗はAutoAnswerError。"""
    url = f"{base_url.rstrip('/')}/mock/v1/auto-answers/{question_code}"
    headers = {"Content-Type": "application/json"}
    if inject_timeout:
        headers["X-Mock-Fault"] = FAULT_TIMEOUT
    request = urllib.request.Request(
        url,
        data=json.dumps({"user_id": user_id}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _opener.open(request, timeout=MOCK_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise AutoAnswerError(f"HTTP {response.status}")
            payload = json.loads(response.read())
    except AutoAnswerError:
        raise
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        http.client.HTTPException,
    ) as error:
        # 接続失敗・タイムアウト・200以外（HTTPError）・JSONとして読めない応答・
        # 途中で切れた応答（IncompleteReadなどのHTTPException）
        raise AutoAnswerError(type(error).__name__) from error
    if (
        not isinstance(payload, dict)
        or payload.get("question_code") != question_code
        or payload.get("answer") not in _VALID_ANSWERS
    ):
        raise AutoAnswerError("不正な応答")
    return payload["answer"]


@router.post("/cases", status_code=201)
def intake_case(body: IntakeRequest, request: Request) -> dict:
    base_url = os.environ.get("MOCK_BASE_URL")
    if not base_url:
        # 設定不備。案件を作る前に失敗させる。
        raise HTTPException(status_code=500, detail="MOCK_BASE_URL が未設定です")
    store: CaseStore = request.app.state.case_store

    case = body.model_dump(exclude={"demo_controls"}, mode="json")
    case_id = store.create_case(case)  # 失敗すればHTTPは呼ばれない

    fault_questions = body.demo_controls.fault_questions if body.demo_controls else []
    answers = {code: "pending" for code in AUTO_ANSWER_KEYS}
    for question_code in AUTO_ANSWER_KEYS:
        try:
            answer = fetch_auto_answer(
                base_url,
                question_code,
                body.user.user_id,
                inject_timeout=question_code in fault_questions,
            )
        except AutoAnswerError:
            continue  # 確認待ちのまま。再試行しない。
        try:
            store.set_auto_answer(case_id, question_code, answer)
        except Exception:
            continue  # 2回目の保存の失敗。pendingのまま、HTTPは再び呼ばない。
        answers[question_code] = answer
    return {"case_id": case_id, "answers": answers}
