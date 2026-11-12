"""案件の判定に関する、DBにもHTTPにも依存しない定数・例外・ID生成。

どの層からも取り込んでよい（依存の向きの検査で、fastapi・psycopg を取り込まないことを
確認している。ARCHITECTURE.md を参照）。保存先（db_store）にもHTTP層（cases、intake、
mock_api）にも置かない共有の定義は、ここに置く。
"""

import uuid

# 自動回答の対象設問。設問コード -> 自動回答の種類
# （設問マスターの auto_answer_key 相当）。
# 初版の対象はA09のみ。設問の追加はここへ足す。
AUTO_ANSWER_KEYS: dict[str, str] = {"A09": "parity"}

# 保存できる結果コード。値は仮（未合意）。表示名ではなくコードで保存する。
RESULT_CODES = ("account_deletion", "account_suspension", "caution", "no_issue")
# オペレータは担当者1名固定（ログインなし）。
OPERATOR_ID = "OP-1"
HUMAN_ANSWERS = ("yes", "no")


class CaseNotFound(Exception):
    pass


class InvalidJudgment(Exception):
    """入力が保存の条件を満たさない（案件は変更しない）。"""


def new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:8].upper()}"  # 仮の形式（採番形式は未決）
