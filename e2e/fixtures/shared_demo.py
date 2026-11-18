"""共有DBを使うE2E構成。推論は固定応答で、AWSへ接続しない。"""

from app.db_store import connect_from_env
from app.domain import GenerationSource
from app.main import app
from app.question_validation import next_question_id
from app.shared_db import PostgresAiQuota
from app.usage_quota import QuotaLimits


class FakeBedrock:
    source = GenerationSource("bedrock", "fixture", "fixture")

    def generate(self, prompt, current):
        return {
            "operation": "add_question",
            "question": {"id": next_question_id(current), "text": prompt},
        }


class FakeRuleBedrock:
    source = GenerationSource("bedrock", "fixture", "fixture")

    def generate(self, prompt, current, next_id, questions):
        return {
            "operation": "add_rule",
            "rule": {
                "id": next_id,
                "result_code": "caution",
                "conditions": {"A01": "yes"},
            },
        }


app.state.question_proposer = FakeBedrock()
app.state.rule_proposer = FakeRuleBedrock()
app.state.ai_quota = PostgresAiQuota(
    connect_from_env, b"e2e-test-key-not-used-outside-tests", QuotaLimits(1, 4)
)
