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


def child_env() -> dict[str, str]:
    """A copy of the allowed variables of this process's environment."""
    return {name: os.environ[name] for name in _ALLOWED if name in os.environ}
