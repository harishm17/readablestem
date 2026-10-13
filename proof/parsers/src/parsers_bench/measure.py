"""Run one command in a fresh process and measure it. Everything recorded is content-free.

Wall time, user and system CPU time and peak resident memory of the process tree come from
`wait4`, so the numbers cover the tool and anything it started, and nothing else on the box.
The 1-minute load average before and after is recorded because a busy box makes every time an
upper bound (PARSERS-PLAN.md section 7.4 asks for a load under 1.5 before a timed run).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Measurement:
    status: str  # ok | crashed | timeout
    exit_code: int | None
    wall_seconds: float
    user_seconds: float
    system_seconds: float
    max_rss_kb: int
    load_before: float
    load_after: float
    timeout_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


def host_info() -> dict[str, object]:
    """CPU model, logical CPUs and RAM: what a timing was taken on."""
    model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    mem_kb = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
                break
    return {"cpu_model": model, "logical_cpus": os.cpu_count(), "mem_total_kb": mem_kb}


def run_measured(
    argv: list[str],
    *,
    env: dict[str, str],
    log_path: Path,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> Measurement:
    """Run `argv`; tool output (stdout and stderr) goes to `log_path`, never to ours."""
    load_before = os.getloadavg()[0]
    started = time.monotonic()
    timed_out = False
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            argv,
            env=env,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() - started > timeout_seconds:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                pid, status, usage = os.wait4(proc.pid, 0)
                break
            time.sleep(0.05)
    wall = time.monotonic() - started
    proc.returncode = os.waitstatus_to_exitcode(status)
    verdict = "timeout" if timed_out else ("ok" if proc.returncode == 0 else "crashed")
    return Measurement(
        status=verdict,
        exit_code=None if timed_out else proc.returncode,
        wall_seconds=round(wall, 3),
        user_seconds=round(usage.ru_utime, 3),
        system_seconds=round(usage.ru_stime, 3),
        max_rss_kb=usage.ru_maxrss,
        load_before=round(load_before, 2),
        load_after=round(os.getloadavg()[0], 2),
        timeout_seconds=timeout_seconds,
    )
