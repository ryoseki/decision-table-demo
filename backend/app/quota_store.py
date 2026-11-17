"""日次の利用枠の予約（メモリ上の実装。DBなしのテストと開発用）。

reserve は予約と記録を1回で行う。予約できたら呼び出し側が
Bedrock を1回だけ呼ぶ。結果にかかわらず返却しない（送信後に結果が不明な呼び出しも、
数えたままにする。保守的な数え方）。
複数のインスタンスで共有する利用枠は、DB（shared_db.PostgresAiQuota）が管理する。
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from app.usage_quota import (
    GLOBAL_SUBJECT,
    DuplicateAiRequest,
    QuotaExceeded,
    QuotaLimits,
    hash_subject,
    seconds_until_reset,
    usage_day,
)


class InMemoryAiQuota:
    def __init__(
        self,
        secret: bytes,
        limits: QuotaLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        hash_subject(secret, "check")  # 鍵が短すぎれば、ここで失敗させる
        self._secret = secret
        self._limits = limits if limits is not None else QuotaLimits()
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: set[str] = set()
        self._counts: dict[tuple[object, str, str], int] = {}

    def reserve(self, key: str, request_id: str) -> None:
        now = self._clock()
        day = usage_day(now)
        ip_key = (day, "ip", hash_subject(self._secret, key))
        global_key = (day, "global", GLOBAL_SUBJECT)
        with self._lock:
            if request_id in self._requests:
                raise DuplicateAiRequest
            # 古い日の行は捨てる（件数だけの保持）。
            for stale in [k for k in self._counts if k[0] != day]:
                del self._counts[stale]
            if self._counts.get(ip_key, 0) >= self._limits.per_ip:
                raise QuotaExceeded("ip", seconds_until_reset(now))
            if self._counts.get(global_key, 0) >= self._limits.global_:
                raise QuotaExceeded("global", seconds_until_reset(now))
            self._counts[ip_key] = self._counts.get(ip_key, 0) + 1
            self._counts[global_key] = self._counts.get(global_key, 0) + 1
            self._requests.add(request_id)

    def used(self, key: str) -> tuple[int, int]:
        """(この接続元の今日の件数, 全体の今日の件数)。テストと運用の確認用。"""
        day = usage_day(self._clock())
        with self._lock:
            return (
                self._counts.get((day, "ip", hash_subject(self._secret, key)), 0),
                self._counts.get((day, "global", GLOBAL_SUBJECT), 0),
            )
