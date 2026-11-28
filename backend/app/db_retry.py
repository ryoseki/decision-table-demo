"""Aurora DSQL の衝突（OCC。SQLSTATE 40001）に対する再試行。

方針（決定済み）: 全体で4回まで（最初の1回＋再試行3回）。待ち時間は50・100・200msと
倍にし、上限は500ms。待ち時間にはジッター（上限の50〜100%の乱数）を付ける。
40001だけを再試行し、使い切ったら StoreBusy を投げる。回数と待ち時間は
AWSの資料に数値の指定がなく、このプロジェクトの判断である。

再試行の単位は、ストアのメソッド1つ（1トランザクション）。失敗したトランザクションは
戻っているので、やり直しても保存は二重にならない。受付のHTTPモック呼び出しは
ストアの外にあるので、再試行で再び呼ばれることはない。
"""

import functools
import random
import time
from collections.abc import Callable

import psycopg.errors

from app.domain import StoreBusy

MAX_ATTEMPTS = 4
BASE_DELAY_SECONDS = 0.05
MAX_DELAY_SECONDS = 0.5

# テストで差し替える（実際には待たない・乱数を固定する）。
sleep: Callable[[float], None] = time.sleep
jitter: Callable[[], float] = random.random


def delay_ceiling(failed_attempt: int) -> float:
    """failed_attempt回目（1始まり）が失敗したあとの待ち時間の上限（秒）。"""
    return min(MAX_DELAY_SECONDS, BASE_DELAY_SECONDS * 2 ** (failed_attempt - 1))


def with_occ_retry[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return method(*args, **kwargs)
            except psycopg.errors.SerializationFailure as error:
                if attempt == MAX_ATTEMPTS:
                    raise StoreBusy(
                        f"Still conflicting after {MAX_ATTEMPTS} attempts"
                    ) from error
                sleep(delay_ceiling(attempt) * (0.5 + 0.5 * jitter()))
        raise AssertionError("Unreachable")  # pragma: no cover

    return wrapper
