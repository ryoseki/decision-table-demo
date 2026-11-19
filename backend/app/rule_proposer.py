"""判定ルールの追加案・削除案を作る、AIの応答のモック（架空）。

実サービスのAIではない（Bedrockなどへは接続しない。実接続は別Issue）。
固定の規則で、生の出力（辞書）を返す。出力は信用せず、rule_validation で検証する。
実接続の実装は、同じ generate の形で差し替える。
"""

import re
from typing import Any

from app.domain import InvalidProposal
from app.question_validation import DemoQuestion
from app.rule_validation import DemoRule, read_update_intent

_RESULT_KEYWORDS = (
    ("アカウント削除", "account_deletion"),
    ("一時凍結", "account_suspension"),
    ("問題なし", "no_issue"),
    ("注意", "caution"),
)


class MockRuleProposer:
    def generate(
        self,
        prompt: str,
        current: tuple[DemoRule, ...],
        next_id: str,
        questions: tuple[DemoQuestion, ...],
    ) -> Any:
        """「ルール」、またはルールID（例: R04）を含む依頼だけ扱う。

        - 「削除」とルールID（例: R03）を含む: その削除案。
        - 「追加」と「A09がYes」のような条件、ルールID（例: R04）を含む: そのルールへの
          条件の追加（更新案）。新しいルールは作らない（検証が、依頼との整合を確かめる）。
        - 「追加」と「設問A09がYes」のような条件を1つ以上含む（ルールIDなし）: 追加案
          （結果は、結果の名前（注意・問題なしなど）を含めば、それ。なければ注意）。
          存在しない設問でも、そのまま出力する（拒否は検証の役目）。
        - それ以外: 操作のない出力（検証で拒否される）。
        """
        none = {"operation": "none", "rule": {}}
        if "ルール" not in prompt and not re.search(r"R[0-9]{2,}", prompt):
            return none
        # 結果の名前（アカウント削除）は、削除の操作と見なさない。
        # 名前を除いた文で、操作を判断する。
        operation_text = prompt
        for word, _ in _RESULT_KEYWORDS:
            operation_text = operation_text.replace(word, "")
        if "削除" in operation_text:
            found = re.search(r"R[0-9]{2,}", prompt)
            if found:
                return {"operation": "remove_rule", "rule_id": found.group()}
            return none
        conditions = {
            question_id: answer.lower()
            for question_id, answer in re.findall(
                r"(A[0-9]{2})が(Yes|No)", prompt, flags=re.IGNORECASE
            )
        }
        # 更新と読めるかは、検証と同じ読み取り（read_update_intent）で決める。
        try:
            intent = read_update_intent(prompt)
        except InvalidProposal:
            return none  # 依頼の食い違いは、検証が理由つきで拒否する
        if intent is not None:
            return {
                "operation": "update_rule",
                "rule_id": intent.rule_ids[0],
                "add_conditions": intent.conditions,
            }
        if "追加" in prompt and conditions:
            result_code = next(
                (code for word, code in _RESULT_KEYWORDS if word in prompt), "caution"
            )
            return {
                "operation": "add_rule",
                "rule": {
                    "id": next_id,
                    "result_code": result_code,
                    "conditions": conditions,
                },
            }
        return none
