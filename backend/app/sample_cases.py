"""共有のサンプル案件（架空）。POST /api/sample-cases。

公開デモでは、来訪者全員が同じ架空案件を使う。初回に押した人が共有の4件を作り、以降は
作成済みの案件を表示する。来訪者別のデータ、リセット、既存案件の削除はない。
構成は scripts/seed-demo-cases.sh と同じ: 未審査2件、A09の取得エラーで確認待ち1件、
登録済み（問題なし）1件。

- 案件IDは固定。同じ案件を2回作ろうとしても、保存先が主キーで弾く（重複しない）。
- 1件の作成は1トランザクション。判定（登録済みの1件）も作成と同時に入れるので、
  作成済みの案件の判定を、あとから書き換えることはない。
- 途中で失敗しても、作成済みの案件は残り、再試行は足りない案件だけを作る。
- HTTPモックもAIも呼ばない（A09の回答は、モックと同じ規則の固定値）。
  AIの利用枠は使わない。
"""

from fastapi import APIRouter, HTTPException, Request, Response

from app.domain import StoreBusy

router = APIRouter(prefix="/api/sample-cases", tags=["sample-cases"])


def _sample(
    number: int,
    summary: str,
    user_id: str,
    a09_answer: str | None,
    result_code: str | None = None,
) -> dict:
    """a09_answer が None のときは、A09の取得に失敗した（確認待ちのまま）案件。"""
    return {
        "case_id": f"CASE-SAMPLE-{number:02d}",
        "summary": summary,
        "user": {"user_id": user_id},
        "inquiry": {"subject": "架空の問い合わせ", "body": f"{summary}（架空）"},
        "a09_answer": a09_answer,
        "result_code": result_code,
    }


# user_id が偶数なら A09=Yes、奇数なら No（モックの規則と同じ）。
SAMPLE_CASES: tuple[dict, ...] = (
    _sample(1, "身に覚えのないログインの通知が届いた", "456", "yes"),
    _sample(2, "メールアドレス変更の通知に心当たりがない", "457", "no"),
    _sample(3, "APIキー発行の通知に心当たりがない（取得に失敗）", "458", None),
    _sample(4, "ログイン失敗の通知が夜間に何件も届いた", "460", "yes", "no_issue"),
)
SAMPLE_CASE_IDS: tuple[str, ...] = tuple(s["case_id"] for s in SAMPLE_CASES)


def _store(request: Request):  # noqa: ANN202
    store = request.app.state.case_store
    if not hasattr(store, "create_sample_case") or not hasattr(
        store, "existing_case_ids"
    ):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store


@router.post("")
def create_sample_cases(request: Request, response: Response) -> dict:
    """足りない共有サンプルだけを作る。作成済みの案件・判定は変えない。

    作成した案件があれば201、すべて作成済みなら200。どちらも一覧を再読み込みしてよい。
    保存先が混み合って作れなかったときは503（作成済みの分は残る。再試行できる）。
    """
    store = _store(request)
    existing = set(store.existing_case_ids(SAMPLE_CASE_IDS))
    created: list[str] = []
    for sample in SAMPLE_CASES:
        if sample["case_id"] in existing:
            continue
        try:
            if store.create_sample_case(sample):
                created.append(sample["case_id"])
        except StoreBusy:
            raise HTTPException(
                status_code=503,
                detail="保存先が混み合っています。作成済みの案件は残っています。"
                "もう一度押してください",
                headers={"Retry-After": "1"},
            ) from None
    response.status_code = 201 if created else 200
    return {
        "created": created,
        "existing": [i for i in SAMPLE_CASE_IDS if i not in created],
        "case_ids": list(SAMPLE_CASE_IDS),
    }
