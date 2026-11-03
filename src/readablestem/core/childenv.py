"""The environment handed to child processes that parse PDFs from the internet.

MuPDF is C code reading untrusted bytes. If a crafted file ever got code running in the child,
the child's environment would be the first place to look for keys, so it gets only what an
interpreter needs to start: no API keys, session secret, cron token or database URL
(`tests/test_audit_uploads.py`).
"""

from __future__ import annotations

import os

# Names an interpreter or the C library may want; nothing here is a secret.
_ALLOWED = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SYSTEMROOT")


# Every child that reads a PDF runs under an address-space cap (RLIMIT_AS). numpy's BLAS and
# OpenMP start one thread per core, and each thread reserves stack and a malloc arena, so on a
# many-core host (16+ cores) importing numpy alone can exceed the cap and the child fails before
# reading anything (an upload refused as "could not be opened"). None of these children needs
# parallel linear algebra.
_FIXED = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}


def child_env() -> dict[str, str]:
    """A copy of the allowed variables of this process's environment, plus fixed thread and
    arena limits that keep a capped child inside its address-space limit on any host."""
    env = {name: os.environ[name] for name in _ALLOWED if name in os.environ}
    env.update(_FIXED)
    return env
