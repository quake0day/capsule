"""capsule diff — compare two capsule versions.

This is the "context diff" feature from the original product brief: not
just code diff, but a diff of *what the AI was told about the subsystem*.
A capsule version bump that touches handoff / invariants / contracts is
materially different from one that touches only implementation — agents
need to see that difference before deciding to trust the new version.

Diff scope (intentionally not "everything"):
  - version              (numeric bump, indicates intent)
  - purpose.summary      (the one-liner)
  - purpose.owns         (added / removed lines)
  - purpose.does_not_own (added / removed lines)
  - interfaces.provides  (by (kind, name): added / removed / changed)
  - interfaces.requires  (by (kind, name): added / removed / changed)
  - dependencies.capsules (added / removed / version-changed)
  - agent.summary_for_ai (changed? — text-level)
  - agent.avoid          (added / removed lines)
  - verification.invariants (added / removed lines)
  - handoff              (changed? appeared? disappeared?)

Intentionally NOT in scope: comment churn inside scalar values; rephrasing
of summary text beyond presence detection (a future LLM-backed diff could
do that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from capsule.schema import Capsule, Handoff


Side = Literal["a", "b"]


@dataclass
class FieldDiff:
    """A single semantic change between two capsules."""

    section: str           # e.g. "invariants", "interfaces.provides"
    change: str            # "added" | "removed" | "changed" | "appeared" | "disappeared"
    label: str             # human-readable summary of the change
    before: str | None = None
    after: str | None = None


@dataclass
class CapsuleDiff:
    a_name: str
    a_version: str
    b_name: str
    b_version: str
    changes: list[FieldDiff] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return len(self.changes) == 0

    def by_section(self) -> dict[str, list[FieldDiff]]:
        out: dict[str, list[FieldDiff]] = {}
        for ch in self.changes:
            out.setdefault(ch.section, []).append(ch)
        return out


def diff(a: Capsule, b: Capsule) -> CapsuleDiff:
    """Compute a semantic diff between two capsule versions."""
    out = CapsuleDiff(
        a_name=a.name, a_version=a.version,
        b_name=b.name, b_version=b.version,
    )

    if a.name != b.name:
        out.changes.append(FieldDiff(
            section="identity",
            change="changed",
            label=f"name: {a.name} → {b.name}",
            before=a.name, after=b.name,
        ))

    if a.version != b.version:
        out.changes.append(FieldDiff(
            section="version",
            change="changed",
            label=f"{a.version} → {b.version}",
            before=a.version, after=b.version,
        ))

    if a.type != b.type:
        out.changes.append(FieldDiff(
            section="type",
            change="changed",
            label=f"{a.type} → {b.type}",
            before=a.type, after=b.type,
        ))

    if a.purpose.summary.strip() != b.purpose.summary.strip():
        out.changes.append(FieldDiff(
            section="purpose.summary",
            change="changed",
            label="purpose summary rewritten",
            before=a.purpose.summary.strip(),
            after=b.purpose.summary.strip(),
        ))

    _diff_list(out, "purpose.owns", a.purpose.owns, b.purpose.owns)
    _diff_list(out, "purpose.does_not_own", a.purpose.does_not_own, b.purpose.does_not_own)

    _diff_interfaces(out, "interfaces.provides",
                     _index_interfaces(a.interfaces.provides),
                     _index_interfaces(b.interfaces.provides))
    _diff_interfaces(out, "interfaces.requires",
                     _index_interfaces(a.interfaces.requires),
                     _index_interfaces(b.interfaces.requires))

    _diff_deps(out, a, b)

    if (a.agent.summary_for_ai or "").strip() != (b.agent.summary_for_ai or "").strip():
        out.changes.append(FieldDiff(
            section="agent.summary_for_ai",
            change="changed",
            label="AI orientation rewritten",
            before=a.agent.summary_for_ai,
            after=b.agent.summary_for_ai,
        ))

    _diff_list(out, "agent.avoid", a.agent.avoid, b.agent.avoid)
    _diff_list(out, "verification.invariants",
               a.verification.invariants, b.verification.invariants)

    _diff_handoff(out, a.handoff, b.handoff)

    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _diff_list(out: CapsuleDiff, section: str, a_list: list[str], b_list: list[str]) -> None:
    a_set = {x.strip() for x in a_list if x.strip()}
    b_set = {x.strip() for x in b_list if x.strip()}
    for added in sorted(b_set - a_set):
        out.changes.append(FieldDiff(section=section, change="added", label=added, after=added))
    for removed in sorted(a_set - b_set):
        out.changes.append(FieldDiff(section=section, change="removed", label=removed, before=removed))


def _index_interfaces(items) -> dict[tuple[str, str], dict]:
    """Index by (kind, name) so we can detect added / removed / version-changed."""
    out: dict[tuple[str, str], dict] = {}
    for it in items:
        key = (it.kind, it.name)
        out[key] = {
            "version": getattr(it, "version", None),
            "from_capsule": getattr(it, "from_capsule", None),
            "description": getattr(it, "description", None),
        }
    return out


def _diff_interfaces(out: CapsuleDiff, section: str, a: dict, b: dict) -> None:
    a_keys = set(a.keys())
    b_keys = set(b.keys())
    for k in sorted(b_keys - a_keys):
        out.changes.append(FieldDiff(
            section=section, change="added",
            label=f"{k[0]}:{k[1]}",
        ))
    for k in sorted(a_keys - b_keys):
        out.changes.append(FieldDiff(
            section=section, change="removed",
            label=f"{k[0]}:{k[1]}",
        ))
    for k in sorted(a_keys & b_keys):
        a_v, b_v = a[k]["version"], b[k]["version"]
        if a_v != b_v:
            out.changes.append(FieldDiff(
                section=section, change="changed",
                label=f"{k[0]}:{k[1]} version: {a_v or '∅'} → {b_v or '∅'}",
                before=a_v, after=b_v,
            ))


def _diff_deps(out: CapsuleDiff, a: Capsule, b: Capsule) -> None:
    a_map = {d.name: d.version for d in a.dependencies.capsules}
    b_map = {d.name: d.version for d in b.dependencies.capsules}
    for name in sorted(set(b_map) - set(a_map)):
        out.changes.append(FieldDiff(
            section="dependencies.capsules", change="added",
            label=f"{name} {b_map[name] or ''}".strip(),
        ))
    for name in sorted(set(a_map) - set(b_map)):
        out.changes.append(FieldDiff(
            section="dependencies.capsules", change="removed",
            label=f"{name} {a_map[name] or ''}".strip(),
        ))
    for name in sorted(set(a_map) & set(b_map)):
        if a_map[name] != b_map[name]:
            out.changes.append(FieldDiff(
                section="dependencies.capsules", change="changed",
                label=f"{name}: {a_map[name] or '∅'} → {b_map[name] or '∅'}",
                before=a_map[name], after=b_map[name],
            ))


def _diff_handoff(out: CapsuleDiff, a: Handoff | None, b: Handoff | None) -> None:
    if a is None and b is None:
        return
    if a is None and b is not None:
        out.changes.append(FieldDiff(
            section="handoff", change="appeared",
            label=f"in-progress: {b.objective.strip()[:80]}",
            after=b.objective.strip(),
        ))
        return
    if a is not None and b is None:
        out.changes.append(FieldDiff(
            section="handoff", change="disappeared",
            label=f"was in-progress: {a.objective.strip()[:80]}",
            before=a.objective.strip(),
        ))
        return
    # both present
    assert a is not None and b is not None
    if a.objective.strip() != b.objective.strip():
        out.changes.append(FieldDiff(
            section="handoff.objective", change="changed",
            label="objective rewritten",
            before=a.objective.strip(), after=b.objective.strip(),
        ))
    _diff_list(out, "handoff.completed", a.completed, b.completed)
    _diff_list(out, "handoff.remaining", a.remaining, b.remaining)
    _diff_list(out, "handoff.next_agent_should", a.next_agent_should, b.next_agent_should)


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------


def render_text(d: CapsuleDiff) -> str:
    """Plain-text rendering, designed for terminals."""
    if d.empty:
        return f"no changes between {d.a_name}@{d.a_version} and {d.b_name}@{d.b_version}\n"
    lines = [
        f"diff: {d.a_name}@{d.a_version}  →  {d.b_name}@{d.b_version}",
        f"      {len(d.changes)} change(s) across {len(d.by_section())} section(s)",
        "",
    ]
    for section, changes in d.by_section().items():
        lines.append(f"  {section}")
        for ch in changes:
            sym = {
                "added": "+",
                "removed": "-",
                "changed": "~",
                "appeared": "★",
                "disappeared": "✕",
            }.get(ch.change, "·")
            lines.append(f"    {sym} {ch.label}")
        lines.append("")
    return "\n".join(lines)


def render_markdown(d: CapsuleDiff) -> str:
    if d.empty:
        return f"_No changes between `{d.a_name}@{d.a_version}` and `{d.b_name}@{d.b_version}`._\n"
    lines = [
        f"# capsule diff",
        f"`{d.a_name}@{d.a_version}` → `{d.b_name}@{d.b_version}`",
        "",
        f"{len(d.changes)} change(s) across {len(d.by_section())} section(s).",
        "",
    ]
    for section, changes in d.by_section().items():
        lines.append(f"## {section}")
        for ch in changes:
            tag = {
                "added": "**added**",
                "removed": "**removed**",
                "changed": "**changed**",
                "appeared": "**appeared**",
                "disappeared": "**disappeared**",
            }.get(ch.change, ch.change)
            lines.append(f"- {tag}: {ch.label}")
        lines.append("")
    return "\n".join(lines)


def to_json_dict(d: CapsuleDiff) -> dict:
    return {
        "a": {"name": d.a_name, "version": d.a_version},
        "b": {"name": d.b_name, "version": d.b_version},
        "changes": [
            {"section": c.section, "change": c.change, "label": c.label,
             "before": c.before, "after": c.after}
            for c in d.changes
        ],
    }
