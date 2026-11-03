"""Offline stand-in for tests: a transport that replays scripted replies.

`FakeTransport` pops one scripted item per send (an `HttpReply` or a `TransportError`) and
records what was sent, so a test can check the URL, the headers (keys, no-retention
settings) and the body. It never touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from readablestem.models.math_readers.transport import HttpReply, TransportError


@dataclass(frozen=True)
class SentRequest:
    url: str
    headers: Mapping[str, str]
    content: bytes
    timeout_s: float

    def json(self) -> Any:
        return json.loads(self.content)


@dataclass
class FakeTransport:
    script: list[HttpReply | TransportError]
    sent: list[SentRequest] = field(default_factory=list)

    def post(
        self, url: str, *, headers: Mapping[str, str], content: bytes, timeout_s: float
    ) -> HttpReply:
        self.sent.append(SentRequest(url, dict(headers), content, timeout_s))
        if not self.script:
            raise AssertionError("FakeTransport: more sends than scripted replies")
        item = self.script.pop(0)
        if isinstance(item, TransportError):
            raise item
        return item


def json_reply(body: object, status: int = 200, **headers: str) -> HttpReply:
    return HttpReply(status, json.dumps(body).encode(), headers)
