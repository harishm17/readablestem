"""ReadableSTEM web app: FastAPI pages, Google sign-in, upload queue, worker, review screen.

`create_app` is imported lazily so that `readablestem.web.db` (used by the CLI, ops and
tests) does not pull in the whole web stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from readablestem.web.app import create_app

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from readablestem.web.app import create_app

        return create_app
    raise AttributeError(name)
