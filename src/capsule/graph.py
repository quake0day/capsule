"""Render the capsule dependency graph as text or Graphviz DOT."""

from __future__ import annotations

from capsule.compose import Composition


def render_text(comp: Composition) -> str:
    """Plain indented tree, one root per top-level capsule (no incoming deps)."""
    by_name = comp.by_name()
    incoming: dict[str, list[str]] = {n: [] for n in by_name}
    for c in comp.capsules:
        for dep in c.capsule.dependencies.capsules:
            if dep.name in incoming:
                incoming[dep.name].append(c.name)
        for req in c.capsule.interfaces.requires:
            if req.from_capsule and req.from_capsule in incoming:
                incoming[req.from_capsule].append(c.name)

    roots = sorted([n for n, parents in incoming.items() if not parents])
    if not roots:
        roots = sorted(by_name.keys())

    lines: list[str] = []
    seen: set[str] = set()

    def walk(name: str, prefix: str = "", last: bool = True, stack: tuple[str, ...] = ()) -> None:
        connector = "└── " if last else "├── "
        node = by_name.get(name)
        suffix = f" v{node.capsule.version}" if node else " (missing)"
        if name in stack:
            mark = "  (cycle)"
        elif name in seen:
            mark = "  (see above)"
        else:
            mark = ""
        lines.append(f"{prefix}{connector}{name}{suffix}{mark}")
        if node is None or name in seen or name in stack:
            return
        seen.add(name)
        children: list[str] = []
        for dep in node.capsule.dependencies.capsules:
            children.append(dep.name)
        for req in node.capsule.interfaces.requires:
            if req.from_capsule and req.from_capsule not in children:
                children.append(req.from_capsule)
        child_prefix = prefix + ("    " if last else "│   ")
        new_stack = (*stack, name)
        for i, child in enumerate(children):
            walk(child, child_prefix, i == len(children) - 1, new_stack)

    for i, root in enumerate(roots):
        walk(root, "", i == len(roots) - 1)
    return "\n".join(lines)


def render_dot(comp: Composition) -> str:
    """Graphviz DOT, suitable for `dot -Tpng`."""
    lines = ["digraph capsules {", '  rankdir="LR";', '  node [shape=box, style="rounded,filled", fillcolor="#f5f5f5"];']
    for c in comp.capsules:
        label = f"{c.name}\\nv{c.capsule.version}"
        lines.append(f'  "{c.name}" [label="{label}"];')
    for c in comp.capsules:
        for dep in c.capsule.dependencies.capsules:
            lines.append(f'  "{c.name}" -> "{dep.name}";')
        for req in c.capsule.interfaces.requires:
            if req.from_capsule:
                lines.append(
                    f'  "{c.name}" -> "{req.from_capsule}" '
                    f'[style=dashed, label="{req.kind}:{req.name}"];'
                )
    lines.append("}")
    return "\n".join(lines)
