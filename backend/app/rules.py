"""判定ルールの一覧（架空）。HTTPモックは呼ばない。

ルールは不変の行（rule_id と revision の組）で、条件を変えるときは
新しい revision を足す。
一覧は、rule_id ごとに最新の revision の行だけを返す（rule_id 順）。

案件の照合に使う一覧は、承認済みの最新のルールを反映する（判定未登録の案件を開くたびに、
この一覧で照合する。登録済みの判定結果は、登録時の保存のまま変わらない）。
- 承認で削除したルール（デモの共有設定で外れたルール）は、一覧に出さない。
- 共有DBが有効なら、一覧を承認済みの共有スナップショット1件から取得する。
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
def list_rules(request: Request) -> list[dict]:
    shared = getattr(request.app.state, "rule_store", None)
    if (
        shared is not None
        and getattr(request.app.state, "persistence", None) == "database"
    ):
        return shared.list_rows()
    store = request.app.state.case_store
    if not hasattr(store, "list_rules"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store.list_rules()
