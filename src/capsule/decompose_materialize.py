"""Turn a DecompositionPlan into a directory tree of capsules on disk."""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml


def _rmtree_force(path: Path) -> None:
    """shutil.rmtree that survives read-only files (the git/objects/* case on
    Windows). Quietly chmod 0o666 and retry on PermissionError."""
    def _on_error(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_on_error)

from capsule.decompose import (
    DecompositionPlan,
    ProposedCapsule,
    ProposedFile,
    ProposedInterface,
)


@dataclass
class MaterializeResult:
    out_dir: Path
    capsules_written: list[str]
    files_copied: int
    leftover_files: int
    overall_summary_path: Path


def materialize(
    plan: DecompositionPlan,
    repo_root: Path,
    out_dir: Path,
    *,
    clean: bool = False,
) -> MaterializeResult:
    """Realise `plan` against the cloned source at `repo_root`, into `out_dir`."""
    out = out_dir.expanduser().resolve()
    if clean and out.exists():
        _rmtree_force(out)
    out.mkdir(parents=True, exist_ok=True)

    used_source_paths: set[str] = set()
    capsules_written: list[str] = []
    files_copied = 0

    for cap in plan.capsules:
        if not cap.name:
            continue
        cap_dir = out / cap.name
        cap_dir.mkdir(parents=True, exist_ok=True)

        # Copy source files.
        for f in cap.files:
            files_copied += _copy_one(repo_root, cap_dir, f, used_source_paths, capsule_name=cap.name)

        # Write capsule.yaml.
        (cap_dir / "capsule.yaml").write_text(
            yaml.safe_dump(_capsule_yaml_doc(cap), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        # Write install.json.
        (cap_dir / "install.json").write_text(
            json.dumps(_install_json_doc(cap), indent=2),
            encoding="utf-8",
        )

        # Write REUSE.md (a focused "what does the consumer need to do" doc).
        (cap_dir / "REUSE.md").write_text(_reuse_md(cap), encoding="utf-8")

        capsules_written.append(cap.name)

    # Leftover bucket.
    leftover_count = 0
    if plan.leftover_files:
        leftover_dir = out / "_leftover"
        leftover_dir.mkdir(parents=True, exist_ok=True)
        for rel in plan.leftover_files:
            src = repo_root / rel
            if not src.is_file():
                continue
            dst = leftover_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            leftover_count += 1
            used_source_paths.add(rel)
        (leftover_dir / "WHY.md").write_text(
            _leftover_md(plan),
            encoding="utf-8",
        )

    # Overall summary at the root.
    summary_path = out / "DECOMPOSITION.md"
    summary_path.write_text(_summary_md(plan, repo_root, capsules_written, leftover_count), encoding="utf-8")

    return MaterializeResult(
        out_dir=out,
        capsules_written=capsules_written,
        files_copied=files_copied,
        leftover_files=leftover_count,
        overall_summary_path=summary_path,
    )


# ---------------------------------------------------------------------------
# per-capsule file generation
# ---------------------------------------------------------------------------


def _copy_one(
    repo_root: Path,
    cap_dir: Path,
    f: ProposedFile,
    used_source_paths: set[str],
    *,
    capsule_name: str,
) -> int:
    src = (repo_root / f.from_).resolve()
    if not src.is_file():
        # The LLM hallucinated a path. Skip but record nothing — the
        # capsule.yaml still describes the intent, just with a hole.
        return 0
    rel = f.from_.replace("\\", "/")
    if rel in used_source_paths:
        # Same file claimed by two capsules. First-write wins; we still
        # produce a copy here so each capsule is self-contained.
        pass
    used_source_paths.add(rel)
    # Bundle under src/<relative-original-path> inside the capsule.
    dst = (cap_dir / "src" / rel).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return 1


def _capsule_yaml_doc(cap: ProposedCapsule) -> dict:
    doc: dict = {
        "apiVersion": "capsule.dev/v0.1",
        "kind": "Capsule",
        "name": cap.name,
        "version": "0.1.0",
        "type": cap.type or "subsystem",
    }
    purpose: dict = {"summary": cap.purpose_summary.strip() + "\n"}
    if cap.owns:
        purpose["owns"] = cap.owns
    if cap.does_not_own:
        purpose["does_not_own"] = cap.does_not_own
    doc["purpose"] = purpose

    interfaces: dict = {}
    if cap.provides:
        interfaces["provides"] = [_iface_doc(i) for i in cap.provides]
    if cap.requires:
        interfaces["requires"] = [_iface_doc(i) for i in cap.requires]
    if interfaces:
        doc["interfaces"] = interfaces

    if cap.depends_on or cap.env_required:
        deps: dict = {}
        if cap.depends_on:
            deps["capsules"] = [{"name": d, "version": ">=0.1.0"} for d in cap.depends_on]
        if cap.env_required:
            # Env reqs go under interfaces.requires as well so consumers see them
            # at the contract surface, but we also note them here for tooling.
            pass
        if deps:
            doc["dependencies"] = deps

    agent: dict = {}
    if cap.agent_summary:
        agent["summary_for_ai"] = cap.agent_summary.strip() + "\n"
    if cap.avoid:
        agent["avoid"] = cap.avoid
    if agent:
        doc["agent"] = agent

    if cap.invariants:
        doc["verification"] = {"invariants": cap.invariants}

    # Reuse hint as a custom x-* key (preserved by the loader as extra).
    if cap.reusability_notes:
        doc["x-reuse"] = {"notes": cap.reusability_notes.strip() + "\n"}

    doc["x-reconstruct"] = {"install": "install.json"}
    return doc


def _iface_doc(i: ProposedInterface) -> dict:
    out: dict = {"kind": i.kind, "name": i.name}
    if i.from_capsule:
        out["from_capsule"] = i.from_capsule
    if i.version:
        out["version"] = i.version
    if i.description:
        out["description"] = i.description
    return out


def _install_json_doc(cap: ProposedCapsule) -> dict:
    return {
        "$schema": "https://capsule-registry.pages.dev/schemas/install.v1.json",
        "files": [
            {"from": f"src/{f.from_.replace(chr(92), '/')}", "to": f.to}
            for f in cap.files if f.from_ and f.to
        ],
        "env_required": cap.env_required,
    }


# ---------------------------------------------------------------------------
# human-readable docs
# ---------------------------------------------------------------------------


def _reuse_md(cap: ProposedCapsule) -> str:
    lines = [
        f"# {cap.name} — reusability notes",
        "",
        f"**Type:** `{cap.type}`",
        "",
        "## What the consumer must change to use this elsewhere",
        "",
        cap.reusability_notes.strip() or "_(none documented — please review the source code before adopting.)_",
        "",
    ]
    if cap.env_required:
        lines.extend([
            "## Required environment variables",
            "",
            *[f"- `{e}`" for e in cap.env_required],
            "",
        ])
    if cap.depends_on:
        lines.extend([
            "## Required peer capsules",
            "",
            *[f"- `{d}`" for d in cap.depends_on],
            "",
        ])
    if cap.invariants:
        lines.extend([
            "## Invariants you must not break",
            "",
            *[f"- {inv}" for inv in cap.invariants],
            "",
        ])
    return "\n".join(lines)


def _leftover_md(plan: DecompositionPlan) -> str:
    return (
        "# Leftover files\n\n"
        "These files were copied verbatim from the source repo because the\n"
        "decomposer judged them to be project-specific (not reusable).\n\n"
        f"## Why\n\n{plan.leftover_explanation.strip() or '_(no explanation provided)_'}\n\n"
        "## Files\n\n"
        + "\n".join(f"- `{p}`" for p in plan.leftover_files)
        + "\n"
    )


def _summary_md(
    plan: DecompositionPlan,
    repo_root: Path,
    capsules_written: list[str],
    leftover_count: int,
) -> str:
    return (
        "# Decomposition\n\n"
        f"**Source:** `{plan.source}`\n\n"
        f"**Plan summary.** {plan.summary.strip()}\n\n"
        f"## Capsules produced ({len(capsules_written)})\n\n"
        + "\n".join(f"- [`{n}`]({n}/) — see [REUSE.md]({n}/REUSE.md)" for n in capsules_written)
        + f"\n\n## Leftover files: {leftover_count}\n\n"
        f"{plan.leftover_explanation.strip() or '_(none)_'}\n\n"
        "## Next steps\n\n"
        "```bash\n"
        "# Validate every produced capsule\n"
        "capsule validate .\n\n"
        "# Reconstruct the whole composition into a runnable site\n"
        "capsule reconstruct --from . --out ./out\n"
        "```\n"
    )


def validate_completeness(
    plan: DecompositionPlan, repo_root: Path,
) -> tuple[int, int, list[str]]:
    """Sanity check: how many source files did the plan account for?"""
    # Walk the repo same way decompose.walk_repo does (cheap re-import).
    from capsule.decompose import walk_repo
    files = walk_repo(repo_root)
    total = len(files)
    claimed: set[str] = set()
    for cap in plan.capsules:
        for f in cap.files:
            claimed.add(f.from_.replace("\\", "/"))
    for rel in plan.leftover_files:
        claimed.add(rel.replace("\\", "/"))
    missed = [
        p.relative_to(repo_root).as_posix()
        for p in files
        if p.relative_to(repo_root).as_posix() not in claimed
    ]
    return total, total - len(missed), missed
