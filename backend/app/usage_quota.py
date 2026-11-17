"""Bedrock推論の日次の利用枠（純粋な関数と定数・例外。DBにもHTTPにも依存しない）。

方針（docs/development-workflow.md「公開時の書き込みと利用枠」）:
- 接続元IPごとに1日5回、デモ全体で1日25回。両方に空きがあれば推論を始める。
- 日の区切りは日本時間（JST）の午前0時。
- 接続元は、CloudFront が付ける CloudFront-Viewer-Address（「IP:ポート」）だけを使う。
  IPv6 は /64 の先頭部分にまとめる（同じ /64 の端末は枠を共有する）。
  読み取れない値は拒否する（推測で別の値にしない）。
- 保存するのは鍵付きHMAC-SHA256の結果。鍵はサーバーで管理する。
  IPは利用者本人の識別ではない。同じIPの利用者は枠を共有する。
"""

import hmac
import ipaddress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

JST = timezone(timedelta(hours=9))

DEFAULT_PER_IP_LIMIT = 5
DEFAULT_GLOBAL_LIMIT = 25
# 設定で変えられる範囲（0・負数・極端な値は起動時に失敗させる）。
LIMIT_RANGE = (1, 1000)

VIEWER_ADDRESS_HEADER = "CloudFront-Viewer-Address"
GLOBAL_SUBJECT = "*"
# 利用枠の行（日別の件数）を残す日数。これより古い日の行は、消してよい。
RETENTION_DAYS = 7
MIN_SECRET_BYTES = 32


class DuplicateAiRequest(Exception):
    """この依頼IDは既に推論枠を予約した。"""


class QuotaExceeded(Exception):
    """日次の上限に達している。推論は始めない。scope は "ip" か "global"。"""

    def __init__(self, scope: str, retry_after_seconds: int) -> None:
        super().__init__(scope)
        self.scope = scope
        self.retry_after_seconds = retry_after_seconds


class ViewerAddressUnavailable(Exception):
    """接続元を、信頼できる形で取得できない（ヘッダーがない・読み取れない）。"""


@dataclass(frozen=True)
class QuotaLimits:
    per_ip: int = DEFAULT_PER_IP_LIMIT
    global_: int = DEFAULT_GLOBAL_LIMIT

    def __post_init__(self) -> None:
        for value in (self.per_ip, self.global_):
            if not LIMIT_RANGE[0] <= value <= LIMIT_RANGE[1]:
                raise ValueError(
                    f"利用枠の上限は {LIMIT_RANGE[0]} 以上 {LIMIT_RANGE[1]} 以下"
                )


def usage_day(now: datetime) -> date:
    """日本時間での、その時刻の日付（利用枠の1日）。タイムゾーンなしは拒否する。"""
    if now.tzinfo is None:
        raise ValueError("now はタイムゾーン付きにする")
    return now.astimezone(JST).date()


def seconds_until_reset(now: datetime) -> int:
    """次の日本時間の午前0時までの秒数（1以上）。"""
    local = now.astimezone(JST)
    tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time())
    return max(1, int((tomorrow.replace(tzinfo=JST) - local).total_seconds()))


def viewer_key(header_value: str | None) -> str:
    """CloudFront-Viewer-Address から、接続元の正規化した文字列を作る。

    受け付ける形: 「IPv4:ポート」「IPv6:ポート」「[IPv6]:ポート」。
    複数の値（カンマ区切り）、ポートのない値、IPとして読めない値は拒否する。
    """
    if header_value is None:
        raise ViewerAddressUnavailable("ヘッダーがありません")
    text = header_value.strip()
    if not text or "," in text or any(c.isspace() for c in text):
        raise ViewerAddressUnavailable("ヘッダーの形式が不正です")
    if text.startswith("["):
        host, sep, port = text[1:].partition("]:")
    else:
        host, sep, port = text.rpartition(":")
    if not sep or not port.isascii() or not port.isdigit() or int(port) > 65535:
        raise ViewerAddressUnavailable("ポートを読み取れません")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ViewerAddressUnavailable("IPアドレスを読み取れません") from None
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        else:
            network = ipaddress.ip_network((address, 64), strict=False)
            return f"v6:{network.network_address}/64"
    return f"v4:{address}"


def hash_subject(secret: bytes, key: str) -> str:
    """接続元の文字列を、鍵付きHMAC-SHA256の16進に変換する（保存・集計に使う値）。"""
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(f"HMACの鍵は {MIN_SECRET_BYTES} バイト以上にする")
    return hmac.new(secret, key.encode("ascii"), sha256).hexdigest()
