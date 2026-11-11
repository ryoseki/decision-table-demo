"""PostgreSQLによる案件の保存と再表示。docs/decision-support-schema.md のDDLに沿う。

受付の2回の保存（CaseStore）と、［判定する］の一括保存・再表示を持つ。
ここで確認できるのはローカルPostgreSQLまで。Aurora DSQL（IAMトークン接続、OCC再試行、
カスタムロール）は未対応・未検証で、別Issueの範囲。
"""

import os
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.mock_api import AUTO_ANSWER_KEYS

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


def connect_from_env() -> psycopg.Connection:
    """DB_HOSTなどの環境変数から接続する（ローカルPostgreSQL用。DSQLのトークン接続は別Issue）。"""
    schema = os.environ.get("DB_SCHEMA", "")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "disable"),
        options=f"-c search_path={schema}" if schema else None,
    )


class PostgresCaseStore:
    """1回の操作を1接続・1トランザクションで行う。

    `with conn` が成功時にコミットし、例外時に戻す。
    """

    def __init__(self, connect: Callable[[], psycopg.Connection] = connect_from_env):
        self._connect = connect

    # --- 受付（CaseStore） ---------------------------------------------------

    def create_case(self, case: dict[str, Any]) -> str:
        """1回目の保存。案件・全設問のコピー・未判定の結果・自動回答対象の回答（pending）。"""
        case_id = new_case_id()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data,"
                " period_start_at, period_end_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    case_id,
                    case["summary"],
                    Jsonb(case["user"]),
                    Jsonb(case["inquiry"]),
                    case.get("period_start_at"),
                    case.get("period_end_at"),
                ),
            )
            conn.execute(
                "INSERT INTO case_questions (case_id, question_code, revision,"
                " category, prompt, source_kind, source_field)"
                " SELECT %s, question_code, revision, category, prompt, source_kind,"
                " source_field FROM review_questions",
                (case_id,),
            )
            conn.execute("INSERT INTO case_results (case_id) VALUES (%s)", (case_id,))
            for question_code in AUTO_ANSWER_KEYS:
                conn.execute(
                    "INSERT INTO case_checks (case_id, question_code, answer)"
                    " VALUES (%s, %s, 'pending')",
                    (case_id, question_code),
                )
        return case_id

    def set_auto_answer(self, case_id: str, question_code: str, answer: str) -> None:
        """2回目の保存。取得に成功したときだけ、answerとautomated_answerを入れる。"""
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE case_checks SET answer = %s, automated_answer = %s"
                " WHERE case_id = %s AND question_code = %s AND answer = 'pending'"
                " AND automated_answer IS NULL",
                (answer, answer, case_id, question_code),
            ).rowcount
            if updated != 1:
                raise RuntimeError("自動回答を保存できる確認待ちの回答がありません")

    # --- 再表示 ---------------------------------------------------------------

    def get_case(self, case_id: str) -> dict[str, Any]:
        """保存済みの値だけを返す。HTTPモックは呼ばない。automated_answerは表示に使わない。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary, user_data, inquiry_data,"
                " period_start_at, period_end_at"
                " FROM review_cases WHERE case_id = %s",
                (case_id,),
            ).fetchone()
            if row is None:
                raise CaseNotFound(case_id)
            questions = conn.execute(
                "SELECT q.question_code, q.revision, q.category, q.prompt,"
                " q.source_kind,"
                " q.source_field, c.answer"
                " FROM case_questions q LEFT JOIN case_checks c"
                " ON c.case_id = q.case_id AND c.question_code = q.question_code"
                " WHERE q.case_id = %s ORDER BY q.question_code",
                (case_id,),
            ).fetchall()
            result = conn.execute(
                "SELECT result_code, rule_id, rule_revision, recorded_by, recorded_at"
                " FROM case_results WHERE case_id = %s",
                (case_id,),
            ).fetchone()
        summary, user_data, inquiry_data, period_start, period_end = row
        judged = result is not None and result[0] is not None
        return {
            "case_id": case_id,
            "summary": summary,
            "user": user_data,
            "inquiry": inquiry_data,
            "period_start_at": period_start.isoformat() if period_start else None,
            "period_end_at": period_end.isoformat() if period_end else None,
            "questions": [
                {
                    "question_code": code,
                    "revision": revision,
                    "category": category,
                    "prompt": prompt,
                    "source_kind": source_kind,
                    "source_field": source_field,
                    # None は未回答（回答の行がない）。pending は確認待ち。
                    "answer": answer,
                }
                for (
                    code,
                    revision,
                    category,
                    prompt,
                    source_kind,
                    source_field,
                    answer,
                ) in questions
            ],
            "result": (
                {
                    "result_code": result[0],
                    "adopted_rule": (
                        {"rule_id": result[1], "rule_revision": result[2]}
                        if result[1] is not None
                        else None
                    ),
                    "recorded_by": result[3],
                    "recorded_at": result[4].isoformat(),
                }
                if judged
                else None
            ),
        }

    # --- ［判定する］ ---------------------------------------------------------

    def save_judgment(
        self,
        case_id: str,
        result_code: str,
        answers: Mapping[str, str],
        adopted_rule: tuple[str, int] | None,
    ) -> None:
        """結果と回答を1トランザクションで保存する（最新のみ保持、履歴なし）。

        - 結果は回答や照合にかかわらず保存できる（未回答・確認待ち・照合0件でも）。
        - 回答の行は削除しない。answers に無い設問は変更しない。
          automated_answerは更新しない。
        - 採用ルールは参考の記録で、渡されたときだけ入れる。
          結果を選んだだけでは入れない。
        - 失敗すれば結果も回答も保存されない。
        """
        if result_code not in RESULT_CODES:
            raise InvalidJudgment(
                f"result_code が定義済みのコードではありません: {result_code}"
            )
        rule_id, rule_revision = adopted_rule if adopted_rule else (None, None)
        with self._connect() as conn:
            if (
                conn.execute(
                    # 同じ案件の同時保存を直列にする。確認待ちの遷移検査と更新の間に
                    # 別の保存が割り込まないようにする（行ロックはコミットまで続く）。
                    "SELECT 1 FROM case_results WHERE case_id = %s FOR UPDATE",
                    (case_id,),
                ).fetchone()
                is None
            ):
                raise CaseNotFound(case_id)
            existing = dict(
                conn.execute(
                    "SELECT question_code, answer FROM case_checks WHERE case_id = %s",
                    (case_id,),
                ).fetchall()
            )
            valid_questions = {
                code
                for (code,) in conn.execute(
                    "SELECT question_code FROM case_questions WHERE case_id = %s",
                    (case_id,),
                ).fetchall()
            }
            for question_code, answer in answers.items():
                if question_code not in valid_questions:
                    raise InvalidJudgment(f"案件にない設問です: {question_code}")
                # 確認待ちは、登録でyes／noに変える以外では作れない。
                # yes／noから戻せない。
                if answer == "pending" and existing.get(question_code) != "pending":
                    raise InvalidJudgment(f"{question_code}: pending にはできません")
                if answer not in (*HUMAN_ANSWERS, "pending"):
                    raise InvalidJudgment(f"{question_code}: 回答が不正です")
            conn.execute(
                "UPDATE case_results SET result_code = %s, rule_id = %s,"
                " rule_revision = %s,"
                " recorded_by = %s, recorded_at = now() WHERE case_id = %s",
                (result_code, rule_id, rule_revision, OPERATOR_ID, case_id),
            )
            for question_code, answer in answers.items():
                conn.execute(
                    "INSERT INTO case_checks (case_id, question_code, answer)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (case_id, question_code)"
                    " DO UPDATE SET answer = EXCLUDED.answer",
                    (case_id, question_code, answer),
                )
