"""Runs a site's queued jobs to the end in a process of its own (`harness.ReplaySite.work`).

A file made to hurt the app may hang the worker, end it or fill its memory. In a process of
its own the test can stop it after a time limit, see whether it lived, and read its highest
memory use. In production the worker is a thread of the web process, so a worker that dies
here is a site that would have gone down.

argv: the pickled settings of the site, and where to write what happened (JSON).
"""

from __future__ import annotations

import json
import pickle
import resource
import sys
from pathlib import Path
from typing import Any

from readablestem.config import Settings
from readablestem.web.app import create_app
from readablestem.web.worker import Worker, default_client_factory


def _peak_kb() -> int:
    """This process's own highest memory use, from /proc. `ru_maxrss` of a process that
    another started begins at the highest use of the one that started it, so it would count
    whatever the test worker had run before."""
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main(settings_file: str, out_file: str) -> None:
    settings: Settings = pickle.loads(Path(settings_file).read_bytes())
    clients: list[Any] = []

    def client(settings: Settings, budget: Any) -> Any:
        made = default_client_factory(settings, budget)
        clients.append(made)
        if getattr(made, "second_check", None) is not None:
            clients.append(made.second_check)
        return made

    ctx = create_app(settings, client_factory=client).state.ctx
    worker = Worker(settings, ctx.factory, converter=ctx.converter, client_factory=client)
    missed: dict[str, int] = {}
    used: dict[str, list[str]] = {}
    while True:
        before = len(clients)
        job_id = worker.run_once()
        if job_id is None:
            break
        missed[job_id] = sum(sum(c.missed_because.values()) for c in clients[before:])
        used[job_id] = sorted(set().union(*(getattr(c, "used", set()) for c in clients[before:])))
        # Written after each job: a later job that ends this process leaves the earlier ones.
        Path(out_file).write_text(
            json.dumps(
                {
                    "missed": missed,
                    "used": used,  # per job: the keys of the recorded answers it read
                    # Linux counts both in KB. The second is the largest process this one
                    # started and waited for: a sandboxed program.
                    "own_kb": _peak_kb(),
                    "started_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
