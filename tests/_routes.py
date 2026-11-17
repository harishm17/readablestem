"""Which routes of the app the test run requested, and which of them gave their own answer.

Every request a test makes goes through `FastAPI.__call__`, whatever client made it. While
the tests run, that call is wrapped (`install`): the request's method and path are matched
against the app's own routes, in the order the app would try them, and the route that takes
the request is noted as requested. When the response starts, the route is also noted as
answered if the answer is the route's own and not a refusal (`own_answer`). Every route of
every app a test sent a request to is noted as known, so a route that exists only under some
setting is known once a test turns that setting on. Only the product's app counts (the one
`web.app.create_app` builds, which carries `app.state.ctx`), not a stub server a test
starts. A route is written `METHOD /path/{name}`, the way the app declares it.

The run's report (`--shard-report`, tests/conftest.py) carries the three lists, and
`scripts/check_shards.py --routes` fails CI for a known route that no shard's tests got an
answer from. `pytest --route-coverage` judges one whole local run the same way.

Why "answered" and not "requested": a route whose only tests are a signed-out visitor
being sent to sign in and a stranger being refused has no test of what it does.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import Iterator
from typing import Any

# Routes that may go without a test of their own answer, each with the reason. No route of
# the product is here: one with no such test gets one.
ALLOWED: dict[str, str] = {
    "GET /db-down": "added to the app by tests/test_web_error_pages.py to show the 503 page",
}

KNOWN: set[str] = set()
REQUESTED: set[str] = set()
ANSWERED: set[str] = set()
Route = tuple[re.Pattern[str], str, frozenset[str]]
# app -> its routes in matching order, for as long as the app lives
_ROUTES: weakref.WeakKeyDictionary[Any, list[Route]] = weakref.WeakKeyDictionary()
SIGN_IN = "/login"


def walk(routes: list[Any], prefix: str = "") -> Iterator[Route]:
    """Every route under `routes` as (pattern of its path, declared path, methods), in
    matching order. A router that was included is walked into; a mount is one route for
    everything under it."""
    from starlette.routing import Mount, compile_path

    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from walk(inner.routes, prefix + route.include_context.prefix)
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        if isinstance(route, Mount):
            path, methods = path + "/{path:path}", frozenset({"GET"})
        else:
            methods = frozenset(getattr(route, "methods", None) or {"GET"})
        yield compile_path(prefix + path)[0], prefix + path, methods


def name(method: str, path: str, methods: frozenset[str]) -> str:
    """A route that answers HEAD because it answers GET is one route, with one test."""
    return f"{'GET' if method == 'HEAD' and 'GET' in methods else method} {path}"


def names(routes: list[Route]) -> set[str]:
    return {name(method, path, methods) for _, path, methods in routes for method in methods}


def note(app: Any, scope: dict[str, Any]) -> str | None:
    """Note the route of `app` that takes this request, if one does, and return its name."""
    if scope.get("type") != "http" or getattr(app.state, "ctx", None) is None:
        return None  # not a request, or not the product's app (a stub provider, a fake server)
    routes = _ROUTES.get(app)
    if routes is None:
        routes = _ROUTES[app] = list(walk(app.routes))
        KNOWN.update(names(routes))
    method = scope["method"]
    for pattern, path, methods in routes:
        if method in methods and pattern.match(scope["path"]):
            taken = name(method, path, methods)
            REQUESTED.add(taken)
            return taken
    return None


def own_answer(status: int, location: str | None) -> bool:
    """True for an answer the route gives when it does its work: 2xx, or a redirect that is
    not the one to the sign-in page. A 4xx or 5xx is a refusal or a failure."""
    if 200 <= status < 300:
        return True
    return 300 <= status < 400 and not (location or "").split("?")[0].endswith(SIGN_IN)


def install() -> None:
    """Wrap `FastAPI.__call__` for this process. Called once, when pytest starts."""
    from fastapi import FastAPI

    if getattr(FastAPI.__call__, "_notes_routes", False):
        return
    inner = FastAPI.__call__

    async def call(self, scope, receive, send):
        taken = note(self, scope)
        if taken is None or taken in ANSWERED:
            await inner(self, scope, receive, send)
            return

        async def watched(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers") or [])
                location = headers.get(b"location", b"").decode("latin-1")
                if own_answer(message["status"], location):
                    ANSWERED.add(taken)
            await send(message)

        await inner(self, scope, receive, watched)

    call._notes_routes = True  # ty: ignore[unresolved-attribute]
    FastAPI.__call__ = call  # ty: ignore[invalid-assignment]


def untested(known: set[str], requested: set[str], answered: set[str]) -> list[str]:
    """One line for each known route with no test of its own answer, apart from the allowed
    ones: either no test requests it, or every test of it is refused."""
    lines = []
    for route in sorted(known - answered - set(ALLOWED)):
        if route in requested:
            lines.append(f"tests of this route only get refusals, never its own answer: {route}")
        else:
            lines.append(f"no test requests the route: {route}")
    return lines
