"""共有のサンプル案件（架空）。POST /api/sample-cases。

公開デモでは、来訪者全員が同じ架空案件を使う。ボタンを押す1回の操作ごとに、全員が共有する
新しい架空案件を1件追加する（件数の上限はない）。来訪者別のデータ、リセット、
既存案件の削除はない。案件の中身は scripts/seed-demo-cases.sh と同じ4つの型
（未審査2、A09の取得エラーで確認待ち1、登録済み（問題なし）1）から、操作ごとに
決まる1つを使う。

- 1回の操作は、画面が付ける依頼ID（Idempotency-Key、UUID）で表す。案件IDは依頼IDから
  決まるので、同じ依頼の再送・失敗後の再試行・同時の重複到達は、同じ案件になり、
  重複しない。別の依頼IDは、別の案件になる。型の選び方も依頼IDだけで決まる
  （採番表やカウンターはない）。
- 1件の作成は1トランザクション。判定（登録済みの型は結果も）を作成と同時に入れるので、
  作成済みの案件の判定を、あとから書き換えることはない。保存先の主キーが重複を弾く。
- 以前の固定の案件（CASE-SAMPLE-01〜04）は、そのまま残る。この経路は触らない。
- HTTPモックもAIも呼ばない（A09の回答は、モックと同じ規則の固定値）。
  AIの利用枠は使わない。
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
    """依頼ID（小文字のUUID）から、作る案件を決める。同じ依頼IDなら、いつも同じ案件。

    案件IDは依頼IDの全32桁（16進。切り詰めない）から、型は依頼IDの値を型の数で割った余りから
    決める。切り詰めると、前半が同じ別の依頼IDが同じ案件になり、追加されない。
    """
    digits = operation_key.replace("-", "")
    template = SAMPLE_TEMPLATES[int(digits, 16) % len(SAMPLE_TEMPLATES)]
    return {"case_id": f"CASE-SAMPLE-{digits.upper()}", **template}


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
    if not hasattr(store, "create_sample_case"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store


@router.post("")
def create_sample_case(request: Request, response: Response) -> dict:
    """1回の操作につき、共有のサンプル案件を1件作る。作成済みの案件・判定は変えない。

    新しく作ったら201、同じ依頼IDの再送で作成済みだったら200（同じ案件IDを返す）。
    どちらも一覧を再読み込みしてよい。入力が不正なら422（何も作らない）。
    保存先が混み合って作れなかったときは503（1件の作成は全部か何もか。同じ依頼IDで再試行できる）。
    """
    sample = sample_for_operation(_operation_key(request))
    store = _store(request)
    try:
        created = store.create_sample_case(sample)
    except StoreBusy:
        raise HTTPException(
            status_code=503,
            detail="保存先が混み合っています。もう一度押してください。"
            "同じ操作の再試行なので、案件は重複しません",
            headers={"Retry-After": "1"},
        ) from None
    response.status_code = 201 if created else 200
    return {"case_id": sample["case_id"], "created": created}
