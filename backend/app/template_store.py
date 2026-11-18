"""定型文（対象・依頼文・操作の3列）をDBから読み、削除する。

反映した変更の自動登録は、承認と同じトランザクションで行うため、`shared_db` が
`save_template` を呼ぶ。PostgreSQLとAurora DSQLの両方で成り立つSQLだけを使う。
"""

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db_retry import with_occ_retry
from app.domain import INITIAL_TEMPLATES, operation_from_receipt

__all__ = [
    "INITIAL_TEMPLATES",
    "PostgresProposalTemplates",
    "operation_from_receipt",
    "save_template",
]


def save_template(
    conn: psycopg.Connection, target: str, prompt: str, operation: dict[str, Any]
) -> None:
    """同じ対象・文言があれば操作を置き換える（1行のまま）。呼び出し側の操作に含める。"""
    conn.execute(
        "INSERT INTO proposal_templates (target, prompt, operation)"
        " VALUES (%s, %s, %s)"
        " ON CONFLICT (target, prompt) DO UPDATE SET operation = EXCLUDED.operation",
        (target, prompt, Jsonb(operation)),
    )


class PostgresProposalTemplates:
    def __init__(self, connect: Callable[[], psycopg.Connection]) -> None:
        self._connect = connect

    @with_occ_retry
    def bootstrap(self) -> None:
        """初期の定型文を、初回の1度だけ登録する（削除した初期文言を復活させない）。"""
        with self._connect() as conn:
            marker = conn.execute(
                "INSERT INTO proposal_template_migrations (name) VALUES (%s)"
                " ON CONFLICT DO NOTHING",
                ("initial-templates-v1",),
            )
            if marker.rowcount != 1:
                return
            for target, prompt, operation in INITIAL_TEMPLATES:
                conn.execute(
                    "INSERT INTO proposal_templates (target, prompt, operation)"
                    " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (target, prompt, Jsonb(operation)),
                )

    def lookup(self, target: str, prompt: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT operation FROM proposal_templates"
                " WHERE target = %s AND prompt = %s",
                (target, prompt),
            ).fetchone()
        return row[0] if row else None

    def list_prompts(self, target: str) -> list[str]:
        with self._connect() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT prompt FROM proposal_templates"
                    " WHERE target = %s ORDER BY prompt",
                    (target,),
                )
            ]

    @with_occ_retry
    def remove(self, target: str, prompt: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM proposal_templates WHERE target = %s AND prompt = %s",
                (target, prompt),
            )
