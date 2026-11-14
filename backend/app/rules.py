"""判定ルールの一覧（架空）。HTTPモックは呼ばない。

ルールは不変の行（rule_id と revision の組）で、条件を変えるときは
新しい revision を足す。
一覧は、rule_id ごとに最新の revision の行だけを返す（rule_id 順）。
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
def list_rules(request: Request) -> list[dict]:
    store = request.app.state.case_store
    if not hasattr(store, "list_rules"):
        raise HTTPException(status_code=503, detail="保存先のDBが設定されていません")
    return store.list_rules()
