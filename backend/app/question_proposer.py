"""設問の追加案を作る、AIの応答のモック（架空）。

実サービスのAIではない（Bedrockなどへは接続しない。実接続は別Issue）。
固定の規則で、生の出力（辞書）を返す。出力は信用せず、question_validation で検証する。
実接続の実装は、同じ generate の形で差し替える。
"""

from typing import Any

from app.question_validation import DemoQuestion

# 例の依頼: 「APIキーの失効履歴を確認する設問を追加して」。
_REVOCATION_TEXT = "対象期間にAPIキーが失効されたか？"


class MockQuestionProposer:
    def generate(self, prompt: str, current: tuple[DemoQuestion, ...]) -> Any:
        """「失効」と「APIキー」を含む依頼だけ、A13の追加案を返す。

        それ以外の依頼には、操作のない出力を返す（検証で拒否される）。
        """
        if "失効" in prompt and "APIキー" in prompt:
            return {
                "operation": "add_question",
                "question": {"id": "A13", "text": _REVOCATION_TEXT},
            }
        return {"operation": "none", "question": {}}
