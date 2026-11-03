"""The one HTTP seam of the readers: tests put a fake behind it, production uses httpx.

A reader builds the whole request (URL, headers, body bytes) and reads the reply bytes
itself, so the bytes sent are exactly the bytes that were built.
`TransportError.kind` tells the engine whether the request can have reached the provider:
"not_sent" (it cannot have: not billed), "timeout" and "broken" (it may have been
processed and billed, as in `models.openai_client`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx


class TransportError(Exception):
    """The exchange failed before a reply. The message is never kept or logged."""

    def __init__(self, kind: Literal["not_sent", "timeout", "broken"]) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def post(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout_s: float
    ) -> HttpReply: ...


class HttpxTransport:
    """POST with httpx: no redirects followed (a key must not follow a redirect), no
    retries (the engine owns them, each under its own reservation)."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(follow_redirects=False)

    def post(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout_s: float
    ) -> HttpReply:
        try:
            response = self._client.post(
                url, headers=dict(headers), content=content, timeout=timeout_s
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TransportError("not_sent") from None
        except httpx.TimeoutException:
            raise TransportError("timeout") from None
        except httpx.HTTPError:
            raise TransportError("broken") from None
        return HttpReply(response.status_code, response.content, dict(response.headers))
