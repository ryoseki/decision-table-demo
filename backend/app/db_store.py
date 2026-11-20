"""PostgreSQLによる案件の保存と再表示。docs/decision-support-schema.md のDDLに沿う。

受付の2回の保存（CaseStore）と、［判定する］の一括保存・再表示を持つ。
接続先は2通り。DB_HOST ならローカルPostgreSQL（パスワード接続）、DSQL_ENDPOINT なら
Aurora DSQL（IAMトークン接続）。書き込む操作は、衝突（SQLSTATE 40001）で再試行する
（app.db_retry）。実Aurora DSQLでの確認は人の操作が要り、ここでは未検証。
"""

import os
from collections.abc import Callable, Mapping
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db_retry import with_occ_retry
from app.domain import (
    AUTO_ANSWER_KEYS,
    HUMAN_ANSWERS,
    OPERATOR_ID,
    RESULT_CODES,
    CaseNotFound,
    InvalidJudgment,
    JudgmentAlreadyRecorded,
    new_case_id,
)


def connect_from_env() -> psycopg.Connection:
    """環境変数から接続する。DSQL_ENDPOINTならDSQL、なければDB_HOSTのPostgreSQL。"""
    schema = os.environ.get("DB_SCHEMA", "")
    options = f"-c search_path={schema}" if schema else None
    endpoint = os.environ.get("DSQL_ENDPOINT")
    if endpoint:
        user = os.environ["DB_USER"]
        return psycopg.connect(
            host=endpoint,
            port="5432",
            dbname="postgres",
            user=user,
            password=_dsql_token(endpoint, user),
            sslmode="require",
            options=options,
        )
    conn = psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "disable"),
        options=options,
    )
    # Aurora DSQLの分離レベルはREPEATABLE READだけ。ローカルもそろえ、同時保存の衝突
    # （40001）をローカルのPostgreSQLでも再現して、再試行を確認できるようにする。
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


def _dsql_token(endpoint: str, user: str) -> str:
    """接続のたびに、短時間有効なIAM認証トークンを作る（トークンは接続時だけ使う）。

    資格情報は実行環境（Lambdaの実行ロールなど）から boto3 が取得する。
    adminロールは管理者用、カスタムロールは通常用のトークン関数を使う。
    """
    import boto3  # DSQL接続のときだけ読み込む

    region = os.environ.get("DSQL_REGION") or os.environ["AWS_REGION"]
    client = boto3.client("dsql", region_name=region)
    if user == "admin":
        return client.generate_db_connect_admin_auth_token(endpoint, region)
    return client.generate_db_connect_auth_token(endpoint, region)


class PostgresCaseStore:
    """1回の操作を1接続・1トランザクションで行う。

    `with conn` が成功時にコミットし、例外時に戻す。
    """

    def __init__(self, connect: Callable[[], psycopg.Connection] = connect_from_env):
        self._connect = connect

    # --- 受付（CaseStore） ---------------------------------------------------

    @with_occ_retry
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

    @with_occ_retry
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

    # --- 共有のサンプル案件（app.sample_cases） ------------------------------------

    @with_occ_retry
    def create_sample_case(self, sample: dict[str, Any]) -> bool:
        """依頼IDから決まる案件IDの案件を、1トランザクションで、まだ無いときだけ作る。作ればTrue。

        案件・設問のコピー・結果・A09の回答（登録済みのサンプルは結果も）を同時に入れる。
        すでにあれば何も書かずFalse（同じ依頼の再送。既存の案件・判定は変えない）。同時に同じIDを作ろうとした
        側は、主キーの重複でDO NOTHINGになるか、衝突（40001）で再試行され、重複しない。
        ON CONFLICT DO NOTHINGとINSERTだけを使い、FOR UPDATEには頼らない
        （Aurora DSQL互換）。
        """
        case_id = sample["case_id"]
        with self._connect() as conn:
            inserted = conn.execute(
                "INSERT INTO review_cases (case_id, summary, user_data, inquiry_data)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (case_id) DO NOTHING",
                (
                    case_id,
                    sample["summary"],
                    Jsonb(sample["user"]),
                    Jsonb(sample["inquiry"]),
                ),
            ).rowcount
            if inserted != 1:
                return False
            conn.execute(
                "INSERT INTO case_questions (case_id, question_code, revision,"
                " category, prompt, source_kind, source_field)"
                " SELECT %s, question_code, revision, category, prompt, source_kind,"
                " source_field FROM review_questions",
                (case_id,),
            )
            if sample["result_code"] is None:
                conn.execute(
                    "INSERT INTO case_results (case_id) VALUES (%s)", (case_id,)
                )
            else:
                conn.execute(
                    "INSERT INTO case_results (case_id, result_code, recorded_by,"
                    " recorded_at) VALUES (%s, %s, %s, now())",
                    (case_id, sample["result_code"], OPERATOR_ID),
                )
            answer = sample["a09_answer"]
            for question_code in AUTO_ANSWER_KEYS:
                conn.execute(
                    "INSERT INTO case_checks (case_id, question_code, answer,"
                    " automated_answer) VALUES (%s, %s, %s, %s)",
                    (case_id, question_code, answer or "pending", answer),
                )
        return True

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

    @with_occ_retry
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
          結果を選んだだけでは入れない。decision_rules にない版は拒否する
          （結果と違うことは拒否しない）。
        - 登録済み（result_code が入っている）の案件は、結果も回答も変えない
          （JudgmentAlreadyRecorded）。登録できるのは、未登録の案件への1回だけ。
        - 失敗すれば結果も回答も保存されない。
        """
        if result_code not in RESULT_CODES:
            raise InvalidJudgment(
                f"result_code が定義済みのコードではありません: {result_code}"
            )
        rule_id, rule_revision = adopted_rule if adopted_rule else (None, None)
        with self._connect() as conn:
            current = conn.execute(
                # 同じ案件の同時保存を直列にする。確認待ちの遷移検査と更新の間に
                # 別の保存が割り込まないようにする（行ロックはコミットまで続く）。
                "SELECT result_code FROM case_results WHERE case_id = %s FOR UPDATE",
                (case_id,),
            ).fetchone()
            if current is None:
                raise CaseNotFound(case_id)
            if current[0] is not None:
                raise JudgmentAlreadyRecorded(case_id)
            # 採用ルールは参考の記録だが、decision_rules にない版は記録しない
            # （外部キーは持たないので、ここで検査する）。ルールは不変の行で
            # 削除もしないので、検査のあとで消えることはない。結果との一致は検査しない。
            if (
                adopted_rule is not None
                and conn.execute(
                    "SELECT 1 FROM decision_rules WHERE rule_id = %s AND revision = %s",
                    adopted_rule,
                ).fetchone()
                is None
            ):
                raise InvalidJudgment(
                    f"採用ルールが存在しません: {rule_id} の版 {rule_revision}"
                )
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
            # 条件付きの更新: 未登録（result_code が NULL）のときだけ書く。ロックに
            # 頼らず、競合した保存（OCCで後から来た側）も、登録済みを上書きしない。
            # 0行なら、回答も書かずに戻す（with が例外でロールバックする）。
            registered = conn.execute(
                "UPDATE case_results SET result_code = %s, rule_id = %s,"
                " rule_revision = %s,"
                " recorded_by = %s, recorded_at = now()"
                " WHERE case_id = %s AND result_code IS NULL",
                (result_code, rule_id, rule_revision, OPERATOR_ID, case_id),
            ).rowcount
            if registered != 1:
                raise JudgmentAlreadyRecorded(case_id)
            for question_code, answer in answers.items():
                conn.execute(
                    "INSERT INTO case_checks (case_id, question_code, answer)"
                    " VALUES (%s, %s, %s)"
                    " ON CONFLICT (case_id, question_code)"
                    " DO UPDATE SET answer = EXCLUDED.answer",
                    (case_id, question_code, answer),
                )
