"""Did the sandbox for uploaded TeX pass its probe here? (`readablestem ops sandbox-probe`.)

There is no shell on the hosting platform to run the probe by hand, so the web process runs it
itself, once, in the background, when it starts and LaTeXML is in the image
(`start_probe`). The answer is:

- one log line, `sandbox_probe result=PASS|FAIL <check>=<value> ...`: the probe's own check
  names and answers, nothing from a document, a setting or the environment;
- the "sandbox" object of `/healthz/ops` (`SandboxStatus.public`);
- the switch for everything that would run LaTeXML: `FEATURE_LATEX_SOURCE`,
  `FEATURE_BEAMER_SOURCE` and a `FEATURE_BATCH_ZIP` zip that holds a LaTeX project take effect only
  while `SandboxStatus.allows_source()` is true, that is, only when the last probe PASSED with
  `--require-latexml` semantics. Until it has finished, after a FAIL, or after a timeout, such an
  upload gets the existing 503 `source_unavailable` answer. A flag switched on by mistake can
  therefore never run LaTeXML unsandboxed or under a failed probe.

States: `not_run` (the startup probe is switched off), `not_installed` (the image has no LaTeXML;
nothing is done), `running`, `pass`, `fail`, `timeout` (counts as a failure).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)

# Free text that may hold a path or an error line: never logged or shown.
_WITHHELD = frozenset({"sandbox_start_error"})


class SandboxStatus:
    """The last sandbox probe result of this process; safe to read from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "not_run"
        self._checks: dict[str, str] = {}
        self._checked_at: datetime | None = None

    # ------------------------------------------------------------------ writing

    def set(self, state: str, checks: dict[str, str] | None = None) -> None:
        with self._lock:
            self._state = state
            self._checks = dict(checks or {})
            self._checked_at = datetime.now(UTC)

    def running(self) -> None:
        with self._lock:
            self._state = "running"
            self._checks = {}
            self._checked_at = None

    # ------------------------------------------------------------------ reading

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allows_source(self) -> bool:
        """Whether LaTeX source may be taken and converted: the last probe passed."""
        return self.state == "pass"

    def public(self) -> dict[str, object]:
        """What `/healthz/ops` shows: the state, the checks (names and one-word answers), the
        bound in MB, whether a cgroup and seccomp are in force, and when."""
        with self._lock:
            checks = dict(self._checks)
            return {
                "state": self._state,
                "result": {"pass": "pass", "fail": "fail", "timeout": "fail"}.get(self._state),
                "checks": checks,
                "bound_mb": _int(checks.get("sandbox_total_bound_mb")),
                "cgroup": checks.get("sandbox_cgroup"),
                "seccomp": checks.get("sandbox_seccomp"),
                "checked_at": self._checked_at.isoformat() if self._checked_at else None,
            }


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def source_allowed(ctx_status: SandboxStatus, enabled: bool) -> bool:
    """A source feature is in effect when it is switched on and the probe passed."""
    return enabled and ctx_status.allows_source()


# ---------------------------------------------------------------------------- the probe


def run_probe(
    status: SandboxStatus,
    *,
    timeout_s: float,
    probe: Callable[[], object] | None = None,
    installed: Callable[[], bool] | None = None,
) -> threading.Thread | None:
    """Start the probe in the background and return its thread (None when LaTeXML is not
    installed: the state is then `not_installed` and nothing else happens). It sets `status` and
    logs one line when it is done, or `timeout` when `timeout_s` runs out first."""
    from readablestem.ops import sandbox_probe
    from readablestem.source import latexml

    if not (installed or latexml.installed)():
        status.set("not_installed")
        log_event(logger, "sandbox_probe", result="not_installed")
        return None
    status.running()
    run = probe or (lambda: sandbox_probe.probe(require_latexml=True))
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["report"] = run()
        except Exception as exc:  # the probe itself broke: a failure, never a crash of startup
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=work, name="sandbox-probe-run", daemon=True)

    def watch() -> None:
        worker.start()
        worker.join(timeout_s)
        if worker.is_alive():
            status.set("timeout")
            log_event(logger, "sandbox_probe", result="FAIL", reason="timeout")
            return
        report = box.get("report")
        if report is None:
            status.set("fail")
            log_event(
                logger, "sandbox_probe", result="FAIL", reason=str(box.get("error", "no_report"))
            )
            return
        checks = {c.name: c.value for c in report.checks if c.name not in _WITHHELD}
        passed = bool(report.passed)
        status.set("pass" if passed else "fail", checks)
        log_event(logger, "sandbox_probe", result="PASS" if passed else "FAIL", **checks)

    thread = threading.Thread(target=watch, name="sandbox-probe", daemon=True)
    thread.start()
    return thread
