"""Terminal man-page renderer.

Mirrors the HTML man page in `server/functions/_lib/render.ts` — same
sections, same order, same emphasis on AI-orientation and avoid lists.
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from capsule.schema import Capsule


def render_man(c: Capsule, console: Console | None = None) -> None:
    """Print a terminal man page for a capsule to the given console."""
    out = console or Console()

    # Title block.
    title = Text()
    title.append(c.name, style="bold")
    title.append(f"  v{c.version}", style="dim")
    title.append(f"   {c.type}", style="cyan")
    if c.domain:
        title.append(f"   {c.domain}", style="dim")
    out.print(title)
    out.print(c.purpose.summary.strip(), style="dim")
    out.print()

    if c.purpose.owns:
        _section(out, "OWNS", c.purpose.owns)
    if c.purpose.does_not_own:
        _section(out, "DOES NOT OWN", c.purpose.does_not_own, style="dim")

    if c.agent.summary_for_ai:
        out.print(Panel(
            c.agent.summary_for_ai.strip(),
            title="AI ORIENTATION",
            title_align="left",
            style="yellow",
            border_style="yellow",
        ))
        out.print()

    if c.agent.avoid:
        _section(out, "AVOID", c.agent.avoid, style="red")

    if c.agent.extension_points:
        out.print(Text("EXTENSION POINTS", style="bold cyan"))
        for ep in c.agent.extension_points:
            head = Text()
            head.append("  ")
            head.append(ep.id, style="bold")
            head.append("  at ", style="dim")
            head.append(ep.where, style="cyan")
            out.print(head)
            out.print(Padding(ep.contract.strip(), (0, 0, 0, 4)), style="dim")
        out.print()

    if c.interfaces.provides:
        _iface(out, "PROVIDES",
               [(p.kind, p.name, p.description) for p in c.interfaces.provides])
    if c.interfaces.requires:
        _iface(out, "REQUIRES",
               [(r.kind, f"{r.name}" + (f" from {r.from_capsule}" if r.from_capsule else ""),
                 r.description) for r in c.interfaces.requires])

    if c.verification.invariants:
        out.print(Panel(
            "\n".join(f"• {inv}" for inv in c.verification.invariants),
            title="INVARIANTS",
            title_align="left",
            style="green",
            border_style="green",
        ))
        out.print()

    if c.handoff:
        h = c.handoff
        body = [Text(f"Objective. {h.objective.strip()}")]
        if h.remaining:
            body.append(Text())
            body.append(Text("Remaining:", style="bold"))
            for r in h.remaining:
                body.append(Text(f"  • {r}"))
        if h.next_agent_should:
            body.append(Text())
            body.append(Text("Next agent should:", style="bold"))
            for n in h.next_agent_should:
                body.append(Text(f"  • {n}"))
        out.print(Panel(
            Group(*body),
            title="HANDOFF — work in progress",
            title_align="left",
            style="magenta",
            border_style="magenta",
        ))
        out.print()
    else:
        out.print(Text("HANDOFF", style="bold cyan"))
        out.print("  — capsule at rest", style="dim")
        out.print()


def _section(out: Console, title: str, items: list[str], *, style: str = "") -> None:
    out.print(Text(title, style="bold cyan"))
    for item in items:
        line = Text("  • ")
        line.append(item, style=style)
        out.print(line)
    out.print()


def _iface(out: Console, title: str, rows: list[tuple[str, str, str | None]]) -> None:
    out.print(Text(title, style="bold cyan"))
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan")
    table.add_column(style="")
    table.add_column(style="dim")
    for kind, name, desc in rows:
        table.add_row(f"{kind}:{name}", "", desc or "")
    out.print(Padding(table, (0, 0, 0, 2)))
    out.print()
