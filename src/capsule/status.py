"""capsule status — one-screen snapshot of a capsule.

Combines:
- registry metadata (version, address)
- contract surface (provides / requires counts)
- which `requires.env` are unsatisfied in the current process env
- verify result if available (does NOT run verify; only reads cached)
- handoff state
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from rich.console import Console
from rich.table import Table

from capsule.loader import LoadedCapsule


@dataclass
class StatusReport:
    name: str
    version: str
    type: str
    domain: str | None
    address: str | None
    provides_count: int
    requires_count: int
    unsatisfied_env: list[str]
    invariant_count: int
    handoff_state: str  # "at-rest" | "in-progress"
    handoff_objective: str | None


def build(lc: LoadedCapsule, address: str | None = None) -> StatusReport:
    c = lc.capsule
    provides = c.interfaces.provides
    requires = c.interfaces.requires
    env_reqs = [r.name for r in requires if r.kind == "env"]
    unsatisfied = [e for e in env_reqs if not os.environ.get(e)]

    return StatusReport(
        name=c.name,
        version=c.version,
        type=c.type,
        domain=c.domain,
        address=address,
        provides_count=len(provides),
        requires_count=len(requires),
        unsatisfied_env=unsatisfied,
        invariant_count=len(c.verification.invariants),
        handoff_state="in-progress" if c.handoff else "at-rest",
        handoff_objective=c.handoff.objective.strip() if c.handoff else None,
    )


def print_status(report: StatusReport, console: Console | None = None) -> None:
    out = console or Console()
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column()

    table.add_row("name", report.name)
    table.add_row("version", report.version)
    table.add_row("type", report.type)
    if report.domain:
        table.add_row("domain", report.domain)
    if report.address:
        table.add_row("address", report.address)

    table.add_row("provides", f"{report.provides_count} interface(s)")

    requires_text = f"{report.requires_count} interface(s)"
    if report.unsatisfied_env:
        requires_text += f"  [red]unsatisfied env: {', '.join(report.unsatisfied_env)}[/red]"
    table.add_row("requires", requires_text)

    table.add_row("invariants", str(report.invariant_count))

    if report.handoff_state == "in-progress":
        table.add_row("handoff", f"[magenta]in progress[/magenta] — {report.handoff_objective}")
    else:
        table.add_row("handoff", "[dim]at rest[/dim]")

    out.print(table)


def to_json_dict(report: StatusReport) -> dict:
    return asdict(report)
