"""共有データのDB実装: 設問・判定ルールの承認結果と提案、Bedrockの日次の利用枠。

PostgreSQLとAurora DSQLで共通の構文を使う（SELECT・INSERT・UPDATE・
DELETE、ON CONFLICT DO NOTHING、主キー）。FOR UPDATEには頼らない。
DSQL は衝突を検出して失敗させる方式（OCC。SQLSTATE 40001）で、書き込みどうしの衝突だけを
検出する（読み取りの変化は検出しない）。そのため、次の2つで、同時の操作を直列にする。

- 承認は、主キー（config_revision）= 最新+1 で新しい版を追記する。同時の承認は
  同じ主キーを書くため、片方が衝突で失敗して再試行され、再読込した最新の版と基準を比べ直す
  （設問の承認とルールの承認が、互いの確認の間に割り込めない）。
- 利用枠は、日別の件数の行を「上限に満たないときだけ +1」で更新する（同じ行を書く）。

承認は、行の追加だけで、過去の版は変えない（運営が、前の版を新しい版として復元できる）。
保存するのは、設定（設問・ルール）と、未処理の提案の最小の内容（操作と基準の版。依頼の文章・AIの
出力そのものは保存しない）と、日別の件数だけ。保持期間は docs/development-workflow.md。

衝突の再試行（app.db_retry）は、この中のDB操作だけを繰り返す。Bedrockは、ここから呼ばない。
"""

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import psycopg.errors
from psycopg.types.json import Jsonb

from app.db_retry import with_occ_retry
from app.domain import (
    ProposalConflict,
    ProposalNotFound,
    QuestionBaseChanged,
    RetirementBlocked,
    operation_from_receipt,
)
from app.question_store import initial_questions
from app.question_validation import (
    DemoQuestion,
    ValidatedProposal,
    ValidatedRetirement,
)
from app.rule_store import initial_rules
from app.rule_validation import (
    DemoRule,
    ValidatedRuleAdd,
    ValidatedRuleRemoval,
    ValidatedRuleUpdate,
    with_added_conditions,
)
from app.template_store import save_template
from app.usage_quota import (
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_PER_IP_LIMIT,
    GLOBAL_SUBJECT,
    MIN_SECRET_BYTES,
    RETENTION_DAYS,
    DuplicateAiRequest,
    QuotaExceeded,
    QuotaLimits,
    hash_subject,
    seconds_until_reset,
    usage_day,
)

logger = logging.getLogger(__name__)

# 未処理の提案を残す時間（これより古いものは消してよい）。
PROPOSAL_TTL_HOURS = 24


def _question_json(q: DemoQuestion) -> dict[str, Any]:
    return {"id": q.id, "text": q.text, "retired": q.retired}


def _question_of(row: dict[str, Any]) -> DemoQuestion:
    return DemoQuestion(row["id"], row["text"], bool(row.get("retired", False)))


def _rule_json(r: DemoRule) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "revision": r.revision,
        "result_code": r.result_code,
        "conditions": dict(r.conditions),
    }


def _rule_of(row: dict[str, Any]) -> DemoRule:
    return DemoRule(
        row["rule_id"],
        row["revision"],
        row["result_code"],
        tuple(sorted(row["conditions"].items())),
    )


@dataclass(frozen=True)
class SharedConfig:
    config_revision: int
    question_revision: int
    rule_revision: int
    questions: tuple[DemoQuestion, ...]
    rules: tuple[DemoRule, ...]
    removed_rule_ids: frozenset[str]


_SELECT_LATEST = (
    "SELECT config_revision, question_revision, rule_revision, questions, rules,"
    " removed_rule_ids FROM shared_config_revisions"
    " ORDER BY config_revision DESC LIMIT 1"
)


def _config_of(row: tuple[Any, ...]) -> SharedConfig:
    return SharedConfig(
        row[0],
        row[1],
        row[2],
        tuple(_question_of(q) for q in row[3]),
        tuple(_rule_of(r) for r in row[4]),
        frozenset(row[5]),
    )


def _insert_config(
    conn: psycopg.Connection,
    config: SharedConfig,
    origin: str,
    restored_from: int | None = None,
) -> None:
    inserted = conn.execute(
        "INSERT INTO shared_config_revisions (config_revision, question_revision,"
        " rule_revision, questions, rules, removed_rule_ids, origin, restored_from,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())"
        " ON CONFLICT DO NOTHING",
        (
            config.config_revision,
            config.question_revision,
            config.rule_revision,
            Jsonb([_question_json(q) for q in config.questions]),
            Jsonb([_rule_json(r) for r in config.rules]),
            Jsonb(sorted(config.removed_rule_ids)),
            origin,
            restored_from,
        ),
    )
    if inserted.rowcount != 1:
        # 同じ版を、同時の操作が先に確定した（主キーの衝突）。一意制約のエラー（500）に
        # せず、DSQLの衝突（40001）と同じに扱う。全体を戻し、最新の版から再試行する。
        raise psycopg.errors.SerializationFailure("共有設定の版が同時に追加された")


def _latest(conn: psycopg.Connection) -> SharedConfig:
    """最新の共有設定。まだ1件もなければ、初期の版（設問A01〜A12・ルールR01〜R03）を作る。"""
    row = conn.execute(_SELECT_LATEST).fetchone()
    if row is None:
        seed = SharedConfig(1, 1, 1, initial_questions(), initial_rules(), frozenset())
        conn.execute(
            "INSERT INTO shared_config_revisions (config_revision, question_revision,"
            " rule_revision, questions, rules, removed_rule_ids, origin, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'seed', now())"
            " ON CONFLICT DO NOTHING",
            (
                1,
                1,
                1,
                Jsonb([_question_json(q) for q in seed.questions]),
                Jsonb([_rule_json(r) for r in seed.rules]),
                Jsonb([]),
            ),
        )
        row = conn.execute(_SELECT_LATEST).fetchone()
        if row is None:
            raise RuntimeError("共有設定の初期の版を作れませんでした")
    return _config_of(row)


class SharedConfigDb:
    """共有設定（設問・判定ルールの版つきの一覧）と、未処理の提案のDB操作。"""

    def __init__(
        self, connect: Callable[[], psycopg.Connection], *, reuse_enabled: bool = False
    ) -> None:
        self._connect = connect
        self._reuse_enabled = reuse_enabled

    # --- 読み取り --------------------------------------------------------------

    @with_occ_retry
    def latest(self) -> SharedConfig:
        with self._connect() as conn:
            return _latest(conn)

    # --- 提案 ------------------------------------------------------------------

    @with_occ_retry
    def register(self, kind: str, payload: dict[str, Any]) -> str:
        proposal_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO shared_proposals (proposal_id, kind, payload, created_at)"
                " VALUES (%s, %s, %s, now())",
                (proposal_id, kind, Jsonb(payload)),
            )
        self._purge_expired()
        return proposal_id

    def _purge_expired(self) -> None:
        """古い未処理の提案を消す。失敗しても、提案の登録は成功のまま（次の機会に消す）。"""
        try:
            with self._connect() as conn:
                cutoff = datetime.now(UTC) - timedelta(hours=PROPOSAL_TTL_HOURS)
                conn.execute(
                    "DELETE FROM shared_proposals WHERE created_at < %s", (cutoff,)
                )
        except Exception:  # noqa: BLE001
            logger.warning("古い提案の削除に失敗しました（次の機会に再試行）")

    @with_occ_retry
    def pending(
        self, kind: str, proposal_id: str
    ) -> tuple[dict[str, Any], SharedConfig]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM shared_proposals"
                " WHERE proposal_id = %s AND kind = %s",
                (proposal_id, kind),
            ).fetchone()
            if row is None:
                raise ProposalNotFound(proposal_id)
            return row[0], _latest(conn)

    @with_occ_retry
    def reject(self, kind: str, proposal_id: str) -> None:
        with self._connect() as conn:
            if self._reuse_enabled:
                self._take(conn, kind, proposal_id)
                return
            deleted = conn.execute(
                "DELETE FROM shared_proposals WHERE proposal_id = %s AND kind = %s",
                (proposal_id, kind),
            ).rowcount
        if deleted != 1:
            raise ProposalNotFound(proposal_id)

    # --- 承認 ------------------------------------------------------------------

    def approve_question(
        self,
        proposal_id: str,
        references_of: Callable[[str], tuple[str, ...]] | None,
    ) -> SharedConfig:
        outcome = self._approve_question(proposal_id)
        if isinstance(outcome, Exception):
            raise outcome  # 提案の消費を確定したあとに、拒否を伝える
        return outcome

    @with_occ_retry
    def _approve_question(
        self,
        proposal_id: str,
    ) -> SharedConfig | Exception:
        with self._connect() as conn:
            payload = self._take(conn, "question", proposal_id)
            current = _latest(conn)
            if payload["base_revision"] != current.question_revision:
                return ProposalConflict(proposal_id)
            if payload["op"] == "retire":
                target_id = payload["target_id"]
                # 確認できない一時障害は、例外のまま（提案の消費を取り消す）。
                references = tuple(
                    rule.rule_id
                    for rule in current.rules
                    if any(code == target_id for code, _ in rule.conditions)
                )
                if references:
                    return RetirementBlocked(target_id, references)
                if not any(
                    q.id == target_id and not q.retired for q in current.questions
                ):
                    return ProposalConflict(proposal_id)
                questions = tuple(
                    DemoQuestion(q.id, q.text, True) if q.id == target_id else q
                    for q in current.questions
                )
            else:
                added = DemoQuestion(payload["id"], payload["text"])
                if any(q.id == added.id for q in current.questions):
                    return ProposalConflict(proposal_id)
                questions = (*current.questions, added)
            updated = SharedConfig(
                current.config_revision + 1,
                current.question_revision + 1,
                current.rule_revision,
                questions,
                current.rules,
                current.removed_rule_ids,
            )
            _insert_config(conn, updated, "approval")
            self._mark_applied(conn, "question", proposal_id, payload)
            return updated

    def approve_rule(self, proposal_id: str) -> SharedConfig:
        outcome = self._approve_rule(proposal_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @with_occ_retry
    def _approve_rule(self, proposal_id: str) -> SharedConfig | Exception:
        with self._connect() as conn:
            payload = self._take(conn, "rule", proposal_id)
            current = _latest(conn)
            if payload["base_question_revision"] != current.question_revision:
                return QuestionBaseChanged(proposal_id)
            if payload["base_revision"] != current.rule_revision:
                return ProposalConflict(proposal_id)
            removed = current.removed_rule_ids
            if payload["op"] == "remove":
                target_id = payload["rule_id"]
                if not any(r.rule_id == target_id for r in current.rules):
                    return ProposalConflict(proposal_id)
                rules = tuple(r for r in current.rules if r.rule_id != target_id)
                removed = removed | {target_id}
            elif payload["op"] == "update":
                target_id = payload["rule_id"]
                target = next(
                    (r for r in current.rules if r.rule_id == target_id), None
                )
                if target is None:
                    return ProposalConflict(proposal_id)
                additions = payload["add_conditions"]
                # 対象の条件が提案後に変わっていたら足さない（版の比較への追加の防御）。
                if any(question in dict(target.conditions) for question in additions):
                    return ProposalConflict(proposal_id)
                new_rule = with_added_conditions(target, additions)
                rules = tuple(
                    new_rule if r.rule_id == target_id else r for r in current.rules
                )
                # 旧い版の行は残す（登録済みの判定は、当時の版を参照し続ける）。
                self._insert_rule_row(conn, new_rule)
            else:
                added = _rule_of(payload["rule"])
                if any(r.rule_id == added.rule_id for r in current.rules):
                    return ProposalConflict(proposal_id)
                rules = (*current.rules, added)
                # 登録済みの判定が採用ルールとして参照できるよう、不変の行として残す。
                self._insert_rule_row(conn, added)
            updated = SharedConfig(
                current.config_revision + 1,
                current.question_revision,
                current.rule_revision + 1,
                current.questions,
                rules,
                removed,
            )
            _insert_config(conn, updated, "approval")
            self._mark_applied(conn, "rule", proposal_id, payload)
            return updated

    @staticmethod
    def _insert_rule_row(conn: psycopg.Connection, rule: DemoRule) -> None:
        conn.execute(
            "INSERT INTO decision_rules (rule_id, revision, result_code,"
            " conditions, created_at) VALUES (%s, %s, %s, %s, now())"
            " ON CONFLICT DO NOTHING",
            (
                rule.rule_id,
                rule.revision,
                rule.result_code,
                Jsonb(dict(rule.conditions)),
            ),
        )

    def _take(
        self, conn: psycopg.Connection, kind: str, proposal_id: str
    ) -> dict[str, Any]:
        """提案を読み、同じ操作の中で消す（1回で使い切る）。なければ ProposalNotFound。

        消した行は、承認が失敗（例外）すれば元に戻る。拒否（返り値の例外）はコミットされ、
        使い切った提案として残らない。
        """
        row = conn.execute(
            "SELECT payload FROM shared_proposals WHERE proposal_id = %s AND kind = %s",
            (proposal_id, kind),
        ).fetchone()
        if row is None:
            raise ProposalNotFound(proposal_id)
        deleted = conn.execute(
            "DELETE FROM shared_proposals WHERE proposal_id = %s AND kind = %s",
            (proposal_id, kind),
        ).rowcount
        if deleted != 1:
            raise ProposalNotFound(proposal_id)
        if self._reuse_enabled:
            conn.execute(
                "INSERT INTO processed_shared_proposals"
                " (proposal_id, target, payload, applied) VALUES (%s, %s, %s, false)",
                (
                    proposal_id,
                    kind,
                    Jsonb({k: v for k, v in row[0].items() if k != "reuse_prompt"}),
                ),
            )
        return row[0]

    def _mark_applied(
        self,
        conn: psycopg.Connection,
        kind: str,
        proposal_id: str,
        payload: dict[str, Any],
    ) -> None:
        """処理済み記録と定型文の自動登録を、設定の新しい版と同じ操作で確定する。

        反映した（Yesで実際に変わった）提案だけがここへ来る。生成元は問わない。
        同じ対象・文言があれば、今回反映した操作で置き換える。
        """
        if not self._reuse_enabled:
            return
        receipt = {k: v for k, v in payload.items() if k != "reuse_prompt"}
        conn.execute(
            "UPDATE processed_shared_proposals SET applied = true, payload = %s"
            " WHERE proposal_id = %s",
            (Jsonb(receipt), proposal_id),
        )
        prompt = payload.get("reuse_prompt")
        if prompt is not None:
            save_template(conn, kind, prompt, operation_from_receipt(kind, receipt))

    # --- 運営による復元 ----------------------------------------------------------

    @with_occ_retry
    def restore(self, target_revision: int) -> SharedConfig:
        """過去の共有設定を、新しい版として追加する（過去の版は変えない）。

        設問・ルールの版は、最新より1つ進める（復元前に作られた未処理の提案は、基準が合わず
        拒否される。未処理の提案は消す）。ルールIDは、復元で外れたものも含めて再利用しない。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config_revision, question_revision, rule_revision, questions,"
                " rules, removed_rule_ids FROM shared_config_revisions"
                " WHERE config_revision = %s",
                (target_revision,),
            ).fetchone()
            if row is None:
                raise LookupError(f"共有設定の版 {target_revision} はありません")
            target = _config_of(row)
            current = _latest(conn)
            active = {r.rule_id for r in target.rules}
            removed = (
                target.removed_rule_ids
                | current.removed_rule_ids
                | {r.rule_id for r in current.rules}
            ) - active
            target_ids = {question.id for question in target.questions}
            questions = target.questions + tuple(
                DemoQuestion(question.id, question.text, True)
                for question in current.questions
                if question.id not in target_ids
            )
            restored = SharedConfig(
                current.config_revision + 1,
                current.question_revision + 1,
                current.rule_revision + 1,
                questions,
                target.rules,
                frozenset(removed),
            )
            _insert_config(conn, restored, "restore", restored_from=target_revision)
            conn.execute("DELETE FROM shared_proposals")
            return restored


class DbQuestionStore:
    """設問一覧と提案の保存先（DB）。question_admin.QuestionStore の形に従う。"""

    def __init__(self, db: SharedConfigDb) -> None:
        self._db = db

    def snapshot(self) -> tuple[int, tuple[DemoQuestion, ...]]:
        config = self._db.latest()
        return config.question_revision, config.questions

    def retired_ids(self) -> frozenset[str]:
        return frozenset(q.id for q in self._db.latest().questions if q.retired)

    def register(
        self,
        proposal: ValidatedProposal | ValidatedRetirement,
        *,
        prompt: str | None = None,
    ) -> str:
        payload: dict[str, Any]
        if isinstance(proposal, ValidatedRetirement):
            payload = {"op": "retire", "target_id": proposal.target.id}
        else:
            payload = {
                "op": "add",
                "id": proposal.added.id,
                "text": proposal.added.text,
            }
        payload["base_revision"] = proposal.base_revision
        if prompt is not None:
            payload["reuse_prompt"] = prompt
        return self._db.register("question", payload)

    def register_for_reuse(
        self, proposal: ValidatedProposal | ValidatedRetirement, prompt: str
    ) -> str:
        return self.register(proposal, prompt=prompt)

    def approve(
        self,
        proposal_id: str,
        references_of: Callable[[str], tuple[str, ...]] | None = None,
    ) -> tuple[int, tuple[DemoQuestion, ...]]:
        config = self._db.approve_question(proposal_id, references_of)
        return config.question_revision, config.questions

    def reject(self, proposal_id: str) -> None:
        self._db.reject("question", proposal_id)


class DbRuleStore:
    """判定ルール一覧と提案の保存先（DB）。rule_admin.RuleStore の形に従う。"""

    def __init__(self, db: SharedConfigDb) -> None:
        self._db = db

    def snapshot(self) -> tuple[int, tuple[DemoRule, ...]]:
        config = self._db.latest()
        return config.rule_revision, config.rules

    def removed_ids(self) -> frozenset[str]:
        return self._db.latest().removed_rule_ids

    def list_rows(self) -> list[dict[str, Any]]:
        return [_rule_json(r) for r in self._db.latest().rules]

    def register(
        self,
        proposal: ValidatedRuleAdd | ValidatedRuleRemoval | ValidatedRuleUpdate,
        *,
        prompt: str | None = None,
    ) -> str:
        payload: dict[str, Any]
        if isinstance(proposal, ValidatedRuleRemoval):
            payload = {"op": "remove", "rule_id": proposal.target.rule_id}
        elif isinstance(proposal, ValidatedRuleUpdate):
            payload = {
                "op": "update",
                "rule_id": proposal.target.rule_id,
                "add_conditions": dict(proposal.added_conditions),
            }
        else:
            payload = {"op": "add", "rule": _rule_json(proposal.added)}
        payload["base_revision"] = proposal.base_revision
        payload["base_question_revision"] = proposal.base_question_revision
        if prompt is not None:
            payload["reuse_prompt"] = prompt
        return self._db.register("rule", payload)

    def register_for_reuse(
        self,
        proposal: ValidatedRuleAdd | ValidatedRuleRemoval | ValidatedRuleUpdate,
        prompt: str,
    ) -> str:
        return self.register(proposal, prompt=prompt)

    def pending(
        self, proposal_id: str
    ) -> ValidatedRuleAdd | ValidatedRuleRemoval | ValidatedRuleUpdate:
        payload, config = self._db.pending("rule", proposal_id)
        base = (
            payload["base_revision"],
            config.rules,
            payload["base_question_revision"],
        )
        if payload["op"] == "remove":
            target = next(
                (r for r in config.rules if r.rule_id == payload["rule_id"]), None
            )
            if target is None:  # 基準が変わっている（承認は拒否される）
                raise ProposalConflict(proposal_id)
            return ValidatedRuleRemoval(base[0], base[1], target, base[2])
        if payload["op"] == "update":
            target = next(
                (r for r in config.rules if r.rule_id == payload["rule_id"]), None
            )
            if target is None:  # 基準が変わっている（承認は拒否される）
                raise ProposalConflict(proposal_id)
            additions = tuple(sorted(payload["add_conditions"].items()))
            return ValidatedRuleUpdate(base[0], base[1], target, additions, base[2])
        return ValidatedRuleAdd(base[0], base[1], _rule_of(payload["rule"]), base[2])

    def approve(self, proposal_id: str) -> tuple[int, tuple[DemoRule, ...]]:
        config = self._db.approve_rule(proposal_id)
        return config.rule_revision, config.rules

    def reject(self, proposal_id: str) -> None:
        self._db.reject("rule", proposal_id)


class PostgresAiQuota:
    """Bedrock推論の日次の利用枠（DB）。quota_store.InMemoryAiQuota と同じ形。

    reserve は全体と接続元ごとの枠を1つのトランザクションで予約し、
    コミットしてから戻る。両方に空きがあるときだけ成功する。
    衝突（40001）の再試行は、この予約の中だけで、Bedrockは呼ばない。
    返却はしない（送信後に結果が不明な呼び出しも、数えたままにする）。
    """

    def __init__(
        self,
        connect: Callable[[], psycopg.Connection],
        secret: bytes,
        limits: QuotaLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        hash_subject(secret, "check")
        self._connect = connect
        self._secret = secret
        self._limits = limits if limits is not None else QuotaLimits()
        self._clock = clock

    def reserve(self, key: str, request_id: str) -> None:
        now = self._clock()
        subject = hash_subject(self._secret, key)
        exceeded = self._reserve(usage_day(now), subject, request_id)
        if exceeded is not None:
            raise QuotaExceeded(exceeded, seconds_until_reset(now))
        self._purge_old(usage_day(now))

    @with_occ_retry
    def _reserve(self, day: Any, subject: str, request_id: str) -> str | None:
        """予約できれば None、上限なら超えた範囲（"ip" か "global"）を返す。"""
        with self._connect() as conn:
            reserved = conn.execute(
                "INSERT INTO ai_usage_requests (request_id) VALUES (%s)"
                " ON CONFLICT DO NOTHING",
                (request_id,),
            )
            if reserved.rowcount != 1:
                raise DuplicateAiRequest
            for scope, name in (("ip", subject), ("global", GLOBAL_SUBJECT)):
                conn.execute(
                    "INSERT INTO ai_usage_counters (usage_day, scope, subject, used)"
                    " VALUES (%s, %s, %s, 0) ON CONFLICT DO NOTHING",
                    (day, scope, name),
                )
            for scope, name, cap in (
                ("ip", subject, self._limits.per_ip),
                ("global", GLOBAL_SUBJECT, self._limits.global_),
            ):
                updated = conn.execute(
                    "UPDATE ai_usage_counters SET used = used + 1"
                    " WHERE usage_day = %s AND scope = %s AND subject = %s"
                    " AND used < %s",
                    (day, scope, name, cap),
                ).rowcount
                if updated != 1:
                    conn.rollback()  # もう一方の+1も取り消す
                    return scope
            return None

    def _purge_old(self, today: Any) -> None:
        """保持期間を過ぎた日の行を消す。失敗しても、予約は成功のまま。"""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM ai_usage_counters WHERE usage_day < %s",
                    (today - timedelta(days=RETENTION_DAYS),),
                )
        except Exception:  # noqa: BLE001
            logger.warning("古い利用枠の行の削除に失敗しました（次の機会に再試行）")

    def used(self, key: str) -> tuple[int, int]:
        """(この接続元の今日の件数, 全体の今日の件数)。テストと運用の確認用。"""
        day = usage_day(self._clock())
        subject = hash_subject(self._secret, key)
        with self._connect() as conn:
            rows = dict(
                conn.execute(
                    "SELECT scope, used FROM ai_usage_counters WHERE usage_day = %s"
                    " AND ((scope = 'ip' AND subject = %s)"
                    " OR (scope = 'global' AND subject = %s))",
                    (day, subject, GLOBAL_SUBJECT),
                ).fetchall()
            )
        return rows.get("ip", 0), rows.get("global", 0)


QUOTA_KEY_ENV = "AI_QUOTA_HMAC_KEY"
PER_IP_LIMIT_ENV = "AI_DAILY_LIMIT_PER_IP"
GLOBAL_LIMIT_ENV = "AI_DAILY_LIMIT_GLOBAL"


class QuotaConfigError(RuntimeError):
    """利用枠の設定が足りない・不正。起動時に失敗させる（枠なしでBedrockを使わせない）。"""


def _limit(env: Mapping[str, str], name: str, default: int) -> int:
    text = env.get(name, "").strip()
    if text == "":
        return default
    try:
        return int(text)
    except ValueError:
        raise QuotaConfigError(f"{name} は整数で指定してください") from None


def build_ai_quota(
    env: Mapping[str, str],
    *,
    uses_bedrock: bool,
    connect: Callable[[], psycopg.Connection] | None,
) -> PostgresAiQuota | None:
    """Bedrock実接続のときだけ、DBの利用枠を作る（モックは枠を使わない）。

    実接続では、DB（共有の利用枠）と、HMACの鍵（32バイト以上。値はログに出さない）が必須。
    足りなければ BedrockではなくQuotaConfigErrorで、起動時に失敗させる。
    """
    if not uses_bedrock:
        return None
    if connect is None:
        raise QuotaConfigError(
            "Bedrockの実接続には、共有の利用枠を管理するDBが必要です"
            "（DB_HOST または DSQL_ENDPOINT）"
        )
    secret = env.get(QUOTA_KEY_ENV, "").encode("utf-8")
    if len(secret) < MIN_SECRET_BYTES:
        raise QuotaConfigError(
            f"{QUOTA_KEY_ENV} に、{MIN_SECRET_BYTES}バイト以上の"
            "秘密の値を設定してください"
        )
    try:
        limits = QuotaLimits(
            per_ip=_limit(env, PER_IP_LIMIT_ENV, DEFAULT_PER_IP_LIMIT),
            global_=_limit(env, GLOBAL_LIMIT_ENV, DEFAULT_GLOBAL_LIMIT),
        )
    except ValueError as error:
        raise QuotaConfigError(str(error)) from None
    return PostgresAiQuota(connect, secret, limits)
