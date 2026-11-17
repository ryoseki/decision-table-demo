"""公開する書き込みを、サーバー側でも限る（HTTP層）。

CloudFront では GET・HEAD・OPTIONS・PUT・POST・
PATCH・DELETE の7つになる。そこで、サーバー側で、書き込み（GET・HEAD・OPTIONS以外）を
次の経路とメソッドだけに限る。それ以外は、アプリに届く前に405で拒否する。

- POST /api/intake/cases（案件の受付）
- PUT /api/cases/{case_id}/judgment（判定の保存）
- POST /api/admin/question-proposals と POST /api/admin/rule-proposals
- 上の提案経路に /{id}/decision を付けた承認POST

受付が自分自身のHTTPモック（POST /mock/v1/...）を呼ぶ内部の呼び出しだけは、CloudFront を
通らないため、公開環境ではインスタンス内の秘密トークンで確認する。
"""

import hmac
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.usage_quota import VIEWER_ADDRESS_HEADER

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SEGMENT = r"[^/]+"
PUBLIC_WRITE_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"/api/intake/cases")),
    ("PUT", re.compile(rf"/api/cases/{_SEGMENT}/judgment")),
    ("POST", re.compile(r"/api/admin/(question|rule)-proposals")),
    ("POST", re.compile(rf"/api/admin/(question|rule)-proposals/{_SEGMENT}/decision")),
)
_INTERNAL_MOCK = re.compile(r"/mock/v1/auto-answers/[A-Za-z0-9_-]+")
_VIEWER_HEADER = VIEWER_ADDRESS_HEADER.lower().encode("ascii")
_INTERNAL_TOKEN_HEADER = b"x-internal-mock-token"


def is_allowed_write(method: str, path: str, internal_mock: bool) -> bool:
    if method in _READ_METHODS:
        return True
    if any(
        method == allowed and pattern.fullmatch(path)
        for allowed, pattern in PUBLIC_WRITE_ROUTES
    ):
        return True
    return method == "POST" and internal_mock and bool(_INTERNAL_MOCK.fullmatch(path))


class PublicWriteGuard:
    def __init__(self, app: ASGIApp, internal_token: str = "") -> None:
        self.app = app
        self.internal_token = internal_token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope["headers"]
        internal = not any(name == _VIEWER_HEADER for name, _ in headers)
        if self.internal_token:
            tokens = [
                value for name, value in headers if name == _INTERNAL_TOKEN_HEADER
            ]
            internal = (
                internal
                and len(tokens) == 1
                and hmac.compare_digest(tokens[0], self.internal_token)
            )
        if not is_allowed_write(scope["method"], scope["path"], internal):
            response = JSONResponse(
                {"detail": "この経路・メソッドでの書き込みは、公開デモでは使えません"},
                status_code=405,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
