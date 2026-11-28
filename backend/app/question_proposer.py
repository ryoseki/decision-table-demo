"""設問の追加案を作る、AIの応答のモック（架空）。

実サービスのAIではない（Bedrockなどへは接続しない。実接続は別Issue）。
固定の規則で、生の出力（辞書）を返す。出力は信用せず、question_validation で検証する。
実接続の実装は、同じ generate の形で差し替える。
"""

import re
from typing import Any

from app.question_validation import DemoQuestion

# 例の依頼: "Add a question that checks the API key revocation history"。
# 日本語の依頼（「APIキーの失効履歴を確認する設問を追加して」）も受け付ける。
_REVOCATION_TEXT = (
    "Was an API key revoked during the target period? / "
    "対象期間にAPIキーが失効されたか？"
)


class MockQuestionProposer:
    def generate(self, prompt: str, current: tuple[DemoQuestion, ...]) -> Any:
        """ "deactivate"・"retire"（または「廃止」）と設問ID（例: A13）を含む依頼には、
        その設問の廃止案を返す。

        それ以外では、"revoke"・"revocation"（または「失効」）と
        "API key"（または「APIキー」）を含む依頼だけ、A13の追加案を返す。

        それ以外の依頼には、操作のない出力を返す（検証で拒否される）。
        """
        if "廃止" in prompt or re.search(
            r"\b(?:deactivate|retire)\b", prompt, re.IGNORECASE
        ):
            found = re.search(r"A[0-9]{2}", prompt)
            if found:
                return {"operation": "retire_question", "question_id": found.group()}
            return {"operation": "none", "question": {}}
        if re.search(r"失効|revo(?:ke|cation)", prompt, re.IGNORECASE) and re.search(
            r"APIキー|API\s*keys?", prompt, re.IGNORECASE
        ):
            return {
                "operation": "add_question",
                "question": {"id": "A13", "text": _REVOCATION_TEXT},
            }
        return {"operation": "none", "question": {}}
