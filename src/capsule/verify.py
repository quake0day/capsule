"""Declarative verification runner.

Each Check in a capsule's `verification` section is a shell command. We run
it, capture stdout/stderr (tail), enforce a timeout, and produce a structured
report. Integration tests are skipped unless their `requires_capsules` are
present in the active composition.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from capsule.loader import LoadedCapsule
from capsule.schema import Check

_TAIL_BYTES = 4000


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    ERROR = "error"  # could not run at all (e.g. binary missing)


CheckCategory = Literal["health", "functional", "integration"]


@dataclass
class CheckResult:
    capsule: str
    category: CheckCategory
    id: str
    status: Status
    duration_ms: int
    command: str
    stdout_tail: str = ""
    stderr_tail: str = ""
    skip_reason: str | None = None
    exit_code: int | None = None


@dataclass
class VerifyReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status in (Status.PASS, Status.SKIPPED) for r in self.results)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in Status}
        for r in self.results:
            out[r.status.value] += 1
        return out

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "results": [_result_to_dict(r) for r in self.results],
        }


def _result_to_dict(r: CheckResult) -> dict:
    d = asdict(r)
    d["status"] = r.status.value
    return d


def verify(
    capsules: list[LoadedCapsule],
    *,
    include_integration: bool = True,
) -> VerifyReport:
    """Run verification for one or more capsules.

    Integration tests run only when all their `requires_capsules` are present
    in `capsules`.
    """
    report = VerifyReport()
    present = {c.name for c in capsules}

    for lc in capsules:
        v = lc.capsule.verification
        for chk in v.health_checks:
            report.results.append(_run(lc, chk, "health"))
        for chk in v.functional_tests:
            report.results.append(_run(lc, chk, "functional"))
        if include_integration:
            for chk in v.integration_tests:
                missing = [r for r in chk.requires_capsules if r not in present]
                if missing:
                    report.results.append(
                        CheckResult(
                            capsule=lc.name,
                            category="integration",
                            id=chk.id,
                            status=Status.SKIPPED,
                            duration_ms=0,
                            command=chk.command,
                            skip_reason=f"requires capsules not in composition: {', '.join(missing)}",
                        )
                    )
                    continue
                report.results.append(_run(lc, chk, "integration"))
    return report


def _run(lc: LoadedCapsule, chk: Check, category: CheckCategory) -> CheckResult:
    cwd = lc.root if chk.cwd is None else (lc.root / chk.cwd).resolve()
    env = os.environ.copy()
    env.update(chk.env)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            chk.command,
            shell=True,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=chk.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            capsule=lc.name,
            category=category,
            id=chk.id,
            status=Status.TIMEOUT,
            duration_ms=int((time.monotonic() - started) * 1000),
            command=chk.command,
            stdout_tail=_tail(exc.stdout or b""),
            stderr_tail=_tail(exc.stderr or b""),
        )
    except FileNotFoundError as exc:
        return CheckResult(
            capsule=lc.name,
            category=category,
            id=chk.id,
            status=Status.ERROR,
            duration_ms=int((time.monotonic() - started) * 1000),
            command=chk.command,
            stderr_tail=str(exc),
        )
    except OSError as exc:
        return CheckResult(
            capsule=lc.name,
            category=category,
            id=chk.id,
            status=Status.ERROR,
            duration_ms=int((time.monotonic() - started) * 1000),
            command=chk.command,
            stderr_tail=str(exc),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    return CheckResult(
        capsule=lc.name,
        category=category,
        id=chk.id,
        status=Status.PASS if proc.returncode == 0 else Status.FAIL,
        duration_ms=duration_ms,
        command=chk.command,
        stdout_tail=_tail(proc.stdout),
        stderr_tail=_tail(proc.stderr),
        exit_code=proc.returncode,
    )


def _tail(data: str | bytes) -> str:
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8", errors="replace")
        except Exception:
            data = repr(data)
    if len(data) > _TAIL_BYTES:
        return "...\n" + data[-_TAIL_BYTES:]
    return data
