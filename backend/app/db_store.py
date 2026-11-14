"""PostgreSQLによる案件の保存と再表示。docs/decision-support-schema.md のDDLに沿う。

受付の2回の保存（CaseStore）と、［判定する］の一括保存・再表示を持つ。
ここで確認できるのはローカルPostgreSQLまで。Aurora DSQL（IAMトークン接続、OCC再試行、
カスタムロール）は未対応・未検証で、別Issueの範囲。
"""

import os
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.domain import (
    AUTO_ANSWER_KEYS,
    HUMAN_ANSWERS,
    OPERATOR_ID,
    RESULT_CODES,
    CaseNotFound,
    InvalidJudgment,
    new_case_id,
)


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

    def list_rules(self) -> list[dict[str, Any]]:
        """rule_id ごとに最新の revision の行。rule_id 順。HTTPモックは呼ばない。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (rule_id)"
                " rule_id, revision, result_code, conditions"
                " FROM decision_rules ORDER BY rule_id, revision DESC"
            ).fetchall()
        return [
            {
                "rule_id": rule_id,
                "revision": revision,
                "result_code": result_code,
                "conditions": conditions,
            }
            for rule_id, revision, result_code, conditions in rows
        ]

    def list_cases(self) -> list[dict[str, Any]]:
        """一覧の各行。並びは案件ID順（受付日時の列がないため）。HTTPモックは呼ばない。

        状態（登録済み・取得エラー・未審査）は返さず、結果と回答の件数から画面側で導く。
        """
        with self._connect() as conn:
            cases = conn.execute(
                "SELECT c.case_id, c.summary, c.user_data, c.inquiry_data,"
                " r.result_code"
                " FROM review_cases c LEFT JOIN case_results r"
                " ON r.case_id = c.case_id ORDER BY c.case_id"
            ).fetchall()
            totals = dict(
                conn.execute(
                    "SELECT case_id, count(*) FROM case_questions GROUP BY case_id"
                ).fetchall()
            )
            answered: dict[tuple[str, str], int] = {
                (case_id, answer): count
                for case_id, answer, count in conn.execute(
                    "SELECT case_id, answer, count(*) FROM case_checks"
                    " GROUP BY case_id, answer"
                ).fetchall()
            }
        rows = []
        for case_id, summary, user_data, inquiry_data, result_code in cases:
            counts = {
                answer: answered.get((case_id, answer), 0)
                for answer in ("yes", "no", "pending")
            }
            counts["unanswered"] = totals.get(case_id, 0) - sum(counts.values())
            rows.append(
                {
                    "case_id": case_id,
                    "summary": summary,
                    "user_id": user_data["user_id"],
                    "inquiry": inquiry_data,
                    "result_code": result_code,
                    "counts": counts,
                }
            )
        return rows

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
