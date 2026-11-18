"""The CI test shards (tests/_sharding.py, scripts/check_shards.py) lose and repeat nothing,
and together request every route of the app (tests/_routes.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

import _routes

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_shards", ROOT / "scripts" / "check_shards.py")
assert spec and spec.loader
check_shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_shards)

pytest_plugins = ["pytester"]


def items(*nodeids):
    return [SimpleNamespace(nodeid=n) for n in nodeids]


def report(shard, collected, ran):
    return {"shard": shard, "collected": collected, "ran": ran}


def test_check_shards_names_a_test_no_shard_ran():
    reports = [report("1/2", ["a", "b"], ["a"]), report("2/2", ["a", "b"], [])]
    assert any("ran in no shard: b" in p for p in check_shards.check(reports, 2))


def test_a_route_is_tested_once_a_test_gets_its_own_answer_not_a_refusal(monkeypatch):
    for kept in ("KNOWN", "REQUESTED", "ANSWERED"):
        monkeypatch.setattr(_routes, kept, set())
    app, inner = FastAPI(openapi_url=None), APIRouter()
    app.state.ctx = "the product's app carries one"

    def paper(paper_id: int):
        if paper_id == 0:
            raise HTTPException(403)
        return RedirectResponse("/login?next=/papers" if paper_id == 1 else "/done", 303)

    app.get("/papers/{paper_id}")(paper)
    app.post("/papers/{paper_id}")(paper)
    inner.get("/pages/{number}")(lambda number: number)
    inner.get("/never")(lambda: "")
    app.include_router(inner, prefix="/papers/{paper_id}")
    client = TestClient(app, follow_redirects=False)
    client.head("/papers/7")  # no route takes HEAD: 405
    client.delete("/papers/7")  # 405
    client.get("/nothing/here")  # 404
    assert not _routes.REQUESTED
    client.get("/papers/0")  # refused
    client.get("/papers/1")  # sent to sign in
    client.post("/papers/7")  # the route's own redirect
    client.get("/papers/7/pages/2?zoom=3")
    assert _routes.untested(_routes.KNOWN, _routes.REQUESTED, _routes.ANSWERED) == [
        "tests of this route only get refusals, never its own answer: GET /papers/{paper_id}",
        "no test requests the route: GET /papers/{paper_id}/never",
    ]


def test_check_shards_names_a_route_no_shard_got_an_answer_from():
    def seen(shard, requested, answered, allowed=()):
        routes = {"known": ["GET /a", "GET /b", "POST /c", "POST /d"], "requested": requested}
        routes |= {"answered": answered, "allowed": list(allowed)}
        return report(shard, [], []) | {"routes": routes}

    count, problems = check_shards.routes(
        [seen("1/2", ["GET /a", "POST /d"], ["GET /a"]), seen("2/2", [], [], ["POST /c"])]
    )
    assert count == 4 and problems == [
        "no test requests the route: GET /b",
        "tests of this route only get refusals, never its own answer: POST /d",
    ]
    # A shard that measured nothing is not a pass.
    assert check_shards.routes([seen("1/2", [], []), report("2/2", [], [])])[1][0] == (
        "shard 2/2 wrote no list of routes"
    )
