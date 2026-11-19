"""案件の判定に関する、DBにもHTTPにも依存しない定数・例外・ID生成。

どの層からも取り込んでよい（依存の向きの検査で、fastapi・psycopg を取り込まないことを
確認している。ARCHITECTURE.md を参照）。保存先（db_store）にもHTTP層（cases、intake、
mock_api）にも置かない共有の定義は、ここに置く。
"""

import uuid
from dataclasses import dataclass
from typing import Any

# 自動回答の対象設問。設問コード -> 自動回答の種類
# （設問マスターの auto_answer_key 相当）。
# 初版の対象はA09のみ。設問の追加はここへ足す。
AUTO_ANSWER_KEYS: dict[str, str] = {"A09": "parity"}

# 保存できる結果コード（2026-11-12に、この4つで確定）。表示名ではなくコードで保存する。
# 画面の表示名（アカウント削除、アカウント一時凍結、注意、問題なし）との対応は、
# 画面側の1か所に置く。
RESULT_CODES = ("account_deletion", "account_suspension", "caution", "no_issue")
# オペレータは担当者1名固定（ログインなし）。
OPERATOR_ID = "OP-1"
HUMAN_ANSWERS = ("yes", "no")


class CaseNotFound(Exception):
    pass


class InvalidJudgment(Exception):
    """入力が保存の条件を満たさない（案件は変更しない）。"""


class JudgmentAlreadyRecorded(Exception):
    """判定を登録済みの案件。結果も回答も、再保存で変えられない（案件は変更しない）。"""


class StoreBusy(Exception):
    """保存先が衝突（OCC。SQLSTATE 40001）で、決めた回数の再試行でも保存できなかった。

    保存は行われていない（1回の操作は1トランザクション）。HTTP層は503にして、
    利用者には「混み合っている。入力は残っている。もう一度」と伝える。
    """


# --- 管理者AIの試作（設問の追加案）。docs/admin-question-proposal-demo.md ---


class InvalidProposal(Exception):
    """AIの出力（モック）が、アプリ側の検証を満たさない。現行データは変えない。"""


class ProposerUnavailable(Exception):
    """AIの応答を得られない（接続エラー・タイムアウトなど）。成功した応答に見せない。

    reason は原因の分類。HTTP層が、利用者に出す固定の文言に対応づける（SDKの文言や
    AIの出力は、利用者へ出さない）。値は unavailable（分類なし。モックなど）、timeout、
    network、credentials、access、throttled、config、service。
    """

    def __init__(self, *args: object, reason: str = "unavailable") -> None:
        super().__init__(*args)
        self.reason = reason


@dataclass(frozen=True)
class GenerationSource:
    """提案の生成元。画面と検証記録で、モックと実接続を区別するための情報。

    kind は "mock"（固定の規則）か "bedrock"（Amazon Bedrock の実接続）。
    model_id・region は実接続のときだけ持つ（資格情報は持たない）。
    """

    kind: str
    model_id: str | None = None
    region: str | None = None


MOCK_SOURCE = GenerationSource("mock")


def source_of(proposer: Any) -> GenerationSource:
    """提案の生成の実装が申告する生成元。申告しないもの（モック・テスト用）は mock。"""
    source = getattr(proposer, "source", None)
    return source if isinstance(source, GenerationSource) else MOCK_SOURCE


class ProposalNotFound(Exception):
    """提案がない（未知のID、または承認・却下済みで再利用できない）。"""


class ProposalConflict(Exception):
    """提案の基準にした一覧が、その後に変わっている。反映しない。"""


class QuestionBaseChanged(ProposalConflict):
    """判定ルールの提案の基準にした設問一覧の版が、その後に変わっている。反映しない。"""


# デモの初期の判定ルールID。これ以外（承認で追加したルール）は、案件の照合に重ねて使う。
SEED_RULE_IDS = frozenset({"R01", "R02", "R03"})


class RetirementBlocked(Exception):
    """廃止しようとした設問を、現行の判定ルールが参照している。廃止しない。"""

    def __init__(self, question_id: str, references: tuple[str, ...]) -> None:
        super().__init__(question_id)
        self.question_id = question_id
        self.references = references


def operation_from_receipt(target: str, payload: dict) -> dict:
    """反映した提案の記録（承認時にサーバーが検証した操作）から、定型文の操作を作る。

    追加の操作は、承認時に採番したIDを持たない（再利用のたびに現行の次番号を付ける）。
    廃止・削除の対象IDは、反映した対象のものを使う。
    """
    if target == "question":
        if payload["op"] == "retire":
            return {"operation": "retire_question", "question_id": payload["target_id"]}
        return {"operation": "add_question", "question": {"text": payload["text"]}}
    if payload["op"] == "remove":
        return {"operation": "remove_rule", "rule_id": payload["rule_id"]}
    if payload["op"] == "update":
        return {
            "operation": "update_rule",
            "rule_id": payload["rule_id"],
            "add_conditions": payload["add_conditions"],
        }
    rule = payload["rule"]
    return {
        "operation": "add_rule",
        "rule": {
            "result_code": rule["result_code"],
            "conditions": rule["conditions"],
        },
    }


INITIAL_TEMPLATES = (
    (
        "question",
        "設問A13を廃止して",
        {"operation": "retire_question", "question_id": "A13"},
    ),
    ("rule", "ルールR03を削除して", {"operation": "remove_rule", "rule_id": "R03"}),
)


def new_case_id() -> str:
    return f"CASE-{uuid.uuid4().hex[:8].upper()}"  # 仮の形式（採番形式は未決）
