"""Compose multiple capsules into a single dependency graph.

A composition is just a set of capsules whose `dependencies.capsules` and
`interfaces.requires[].from_capsule` references resolve against each other.
We do not pull from a registry in v0.1 — all capsules must already be on
disk and discovered by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from capsule.loader import LoadedCapsule


def _normalize_specifier(spec: str) -> str:
    """Accept space-separated specifiers (npm/cargo style) and convert to PEP 440 commas.

    `">=0.2.0 <1.0.0"` -> `">=0.2.0,<1.0.0"`. Already-comma-separated values
    pass through unchanged.
    """
    return re.sub(r"\s+", ",", spec.strip())


@dataclass
class CompositionIssue:
    capsule: str
    severity: str  # "error" | "warning"
    message: str


@dataclass
class Composition:
    capsules: list[LoadedCapsule]
    issues: list[CompositionIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def by_name(self) -> dict[str, LoadedCapsule]:
        return {c.name: c for c in self.capsules}


def compose(capsules: list[LoadedCapsule]) -> Composition:
    """Cross-check a set of capsules and report missing/conflicting deps."""
    comp = Composition(capsules=capsules)
    by_name: dict[str, LoadedCapsule] = {}
    for c in capsules:
        if c.name in by_name:
            comp.issues.append(
                CompositionIssue(
                    capsule=c.name,
                    severity="error",
                    message=f"duplicate capsule name (also at {by_name[c.name].path})",
                )
            )
            continue
        by_name[c.name] = c

    for c in capsules:
        _check_capsule_deps(c, by_name, comp)
        _check_interface_requires(c, by_name, comp)

    return comp


def _check_capsule_deps(
    c: LoadedCapsule,
    by_name: dict[str, LoadedCapsule],
    comp: Composition,
) -> None:
    for dep in c.capsule.dependencies.capsules:
        target = by_name.get(dep.name)
        if target is None:
            comp.issues.append(
                CompositionIssue(
                    capsule=c.name,
                    severity="error",
                    message=f"depends on capsule '{dep.name}' which is not in the composition",
                )
            )
            continue
        if dep.version:
            _check_version(c, target, dep.version, comp)


def _check_interface_requires(
    c: LoadedCapsule,
    by_name: dict[str, LoadedCapsule],
    comp: Composition,
) -> None:
    for req in c.capsule.interfaces.requires:
        if req.from_capsule is None:
            continue
        target = by_name.get(req.from_capsule)
        if target is None:
            comp.issues.append(
                CompositionIssue(
                    capsule=c.name,
                    severity="error",
                    message=(
                        f"interface '{req.name}' ({req.kind}) requires capsule "
                        f"'{req.from_capsule}' which is not in the composition"
                    ),
                )
            )
            continue
        same_name = [p for p in target.capsule.interfaces.provides if p.name == req.name]
        exact = [p for p in same_name if p.kind == req.kind]
        if not exact:
            if same_name:
                # Typed-pipe error: name matches but kind doesn't.
                # Unix analogue: wiring stdout to a program that wanted stdin
                # of a different file type.
                wrong_kinds = sorted({p.kind for p in same_name})
                comp.issues.append(
                    CompositionIssue(
                        capsule=c.name,
                        severity="error",
                        message=(
                            f"typed-pipe mismatch: requires {req.kind}:{req.name} "
                            f"from '{target.name}', but it provides {req.name} as "
                            f"kind {', '.join(wrong_kinds)}"
                        ),
                    )
                )
            else:
                # Name not found at all — could be a kind we don't model,
                # so stay at warning per the spec's open-kind rule.
                comp.issues.append(
                    CompositionIssue(
                        capsule=c.name,
                        severity="warning",
                        message=(
                            f"capsule '{target.name}' does not declare a provided "
                            f"interface named '{req.name}' of kind '{req.kind}'"
                        ),
                    )
                )
        if req.version:
            _check_version(c, target, req.version, comp)


def _check_version(
    c: LoadedCapsule,
    target: LoadedCapsule,
    spec: str,
    comp: Composition,
) -> None:
    try:
        sset = SpecifierSet(_normalize_specifier(spec))
    except InvalidSpecifier:
        comp.issues.append(
            CompositionIssue(
                capsule=c.name,
                severity="warning",
                message=f"invalid version specifier '{spec}' for dependency on '{target.name}'",
            )
        )
        return
    if Version(target.capsule.version) not in sset:
        comp.issues.append(
            CompositionIssue(
                capsule=c.name,
                severity="error",
                message=(
                    f"depends on '{target.name}' {spec} but composition has "
                    f"version {target.capsule.version}"
                ),
            )
        )


def topo_order(comp: Composition) -> list[LoadedCapsule]:
    """Return capsules in dependency order (deps first). Cycles raise ValueError."""
    by_name = comp.by_name()
    visited: set[str] = set()
    temp: set[str] = set()
    order: list[LoadedCapsule] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in temp:
            raise ValueError(f"dependency cycle involving capsule '{name}'")
        node = by_name.get(name)
        if node is None:
            return
        temp.add(name)
        for dep in node.capsule.dependencies.capsules:
            visit(dep.name)
        for req in node.capsule.interfaces.requires:
            if req.from_capsule:
                visit(req.from_capsule)
        temp.remove(name)
        visited.add(name)
        order.append(node)

    for c in comp.capsules:
        visit(c.name)
    return order
