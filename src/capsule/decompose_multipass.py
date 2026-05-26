"""Multi-pass decomposition.

Single-shot decompose works well for repos that fit in (input + output)
under a model's context window. For repos that don't — either because
prompt input is too big (CyberVerse, bubbletea) or because the JSON
output the model would need to emit exceeds a per-request token budget
or wall-clock timeout (CF Workers AI is hard-capped around 4 min) — we
need to break the work apart.

The pattern here is:

  Pass 1  →  ask LLM only for the SKELETON (capsule list + file
              assignment + a one-line purpose each). Output stays small
              even on big repos.

  Pass 2  →  for each capsule in the skeleton, ask LLM to fill in the
              contract (provides, requires, invariants, REUSE notes)
              from JUST that capsule's file excerpts. Each call is small.

Output shape is the same DecompositionPlan as single-pass, so the
materializer doesn't care which path produced it. The benchmark page
distinguishes them via the `passes` field on each run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from capsule.decompose import (
    DecomposeError,
    DecompositionPlan,
    MAX_EXCERPT_LINES,
    MAX_FILE_BYTES,
    ProposedCapsule,
    ProposedFile,
    ProposedInterface,
    _BINARY_EXTS,
    _str_list,
    extract_json,
    walk_repo,
)


# Pass-1 needs the LLM to pick BOUNDARIES, not write detailed contracts.
# Filenames + READMEs are typically enough for that. Keeping pass-1
# context small is what makes multi-pass unblock small-context models
# (e.g. Llama 3.3 70B on CF has only 24K total context).
def build_short_context(repo_root: Path, files: list[Path]) -> str:
    """Tree of every file + full text of README / top-level config files only.
    Aims for ~5-15 KB total so pass-1 fits comfortably in 24K-context windows."""
    rels = [p.relative_to(repo_root).as_posix() for p in files]
    tree_block = (
        "REPO TREE (" + str(len(rels)) + " files):\n" + "\n".join(rels)
    )

    # Surface only files that are HIGH-SIGNAL for boundary detection.
    SIGNAL_PATTERNS = (
        "readme",        # README, README.md, README.rst
        "package.json",
        "pyproject.toml",
        "go.mod",
        "cargo.toml",
        "tsconfig",
        "wrangler.toml",
        "dockerfile",
        "compose.yml",
        "compose.yaml",
        "_lib",
    )

    excerpts: list[str] = []
    budget = 12_000
    for path in files:
        rel = path.relative_to(repo_root).as_posix()
        rel_lower = rel.lower()
        # Pick files that announce the project's structure.
        if not any(p in rel_lower for p in SIGNAL_PATTERNS):
            # Also include any file at the repo root that isn't a binary.
            if "/" in rel.strip("/"):
                continue
        if path.suffix.lower() in _BINARY_EXTS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 20_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        head = "\n".join(lines[:60])
        truncation = "" if len(lines) <= 60 else f"\n# ... ({len(lines) - 60} more lines)"
        block = f"\n----- FILE: {rel} -----\n{head}{truncation}\n"
        if len(block) > budget:
            continue
        excerpts.append(block)
        budget -= len(block)

    return tree_block + (
        "\n\nKEY FILE EXCERPTS (READMEs + configs + root-level files):\n"
        if excerpts else "\n"
    ) + "".join(excerpts)


# ---------------------------------------------------------------------------
# Pass 1 — skeleton (small output)
# ---------------------------------------------------------------------------


def build_pass1_prompt(
    source: str,
    context: str,
    namespace: str | None,
    user_hint: str | None,
) -> str:
    """Same input context as single-pass, but asks for a stripped-down
    JSON: capsule names + file assignments + one-line purposes only."""
    ns_block = (
        f"TARGET NAMESPACE: {namespace}\n"
        f"  Every capsule name MUST start with `{namespace}-`."
        if namespace else
        "TARGET NAMESPACE: (none — pick generic, reusable names that do NOT "
        "mention the source project's brand or domain.)"
    )
    hint_block = f"USER HINT:\n  {user_hint}" if user_hint else "USER HINT: (none)"

    return f"""You are decomposing a repository into reusable capsules. This is
PASS 1 of 2 — you will draft the SKELETON only (capsule names + file
assignments + one-line purposes). PASS 2 will fill in the detailed
contract per capsule separately.

OUTPUT STRICT JSON, in one ```json``` block, with this minimal schema:

{{
  "summary": "<one-paragraph description of the repo and your decomposition>",
  "capsules": [
    {{
      "name": "<kebab-case, generic, namespace-prefixed if a namespace was given>",
      "type": "subsystem" | "adapter" | "template" | "library",
      "purpose_summary": "<one sentence — what this capsule owns>",
      "files": [
        {{"from": "<path in source repo>", "to": "<path in reconstructed site>"}}
      ]
    }}
  ],
  "leftover_files": ["<path>", "..."],
  "leftover_explanation": "<why these don't fit a reusable capsule>"
}}

HARD CONSTRAINTS:
  - Every file in REPO TREE appears EXACTLY ONCE: either in some capsule's
    files[] or in leftover_files. No file twice. No file missing.
  - Source paths in files[].from must match the REPO TREE entries verbatim.
  - Output JSON only, inside ONE ```json``` fence, nothing before or after.

REUSE PRINCIPLES (same as single-pass):
  - PREFER GENERIC NAMES. Bad: `acmecorp-billing-svc`. Good: `stripe-billing-gateway`.
  - If a file is fundamentally project-specific (artwork list, brand HTML),
    put it in leftover_files. An honest leftover bucket is FAR better than
    a fake-reusable capsule.

----- SOURCE: {source} -----

{ns_block}

{hint_block}

{context}

Produce the SKELETON JSON now."""


# ---------------------------------------------------------------------------
# Pass 2 — per-capsule contract (small input, small output)
# ---------------------------------------------------------------------------


def build_pass2_prompt(
    *,
    skeleton: "Pass1Skeleton",
    capsule: "Pass1Capsule",
    repo_root: Path,
    namespace: str | None,
) -> str:
    """Generate the per-capsule pass-2 prompt.

    Includes only the files that belong to this capsule (excerpted), plus
    sibling-capsule names + summaries so the model can declare cross-
    capsule dependencies + requires.from_capsule.
    """
    siblings = [c for c in skeleton.capsules if c.name != capsule.name]
    sibling_block = "\n".join(
        f"  - {c.name} ({c.type}): {c.purpose_summary.strip()[:160]}"
        for c in siblings
    ) or "  (none)"

    file_excerpts = _per_capsule_excerpts(repo_root, capsule.files)

    ns_block = (
        f"  - For depends_on / requires.from_capsule fields, every reference "
        f"to a sibling capsule MUST use its full name (already namespace-"
        f"prefixed with `{namespace}-`)."
        if namespace else ""
    )

    return f"""You are filling in PASS 2 of 2 — the detailed contract for a
SINGLE capsule that was already proposed in PASS 1. Use the file excerpts
below to ground every claim (avoid hallucinating fields that aren't
backed by the code).

CAPSULE BEING DETAILED
  name:            {capsule.name}
  type:            {capsule.type}
  purpose:         {capsule.purpose_summary}
  file count:      {len(capsule.files)}

SIBLING CAPSULES (for depends_on / requires references):
{sibling_block}

OUTPUT STRICT JSON, in ONE ```json``` block, with this schema:

{{
  "owns": ["<bullet>", "..."],
  "does_not_own": ["<bullet>", "..."],
  "provides": [
    {{"kind": "http_api|library|event|cli|env", "name": "...", "description": "..."}}
  ],
  "requires": [
    {{"kind": "...", "name": "...", "from_capsule": "<sibling name or null>", "description": "..."}}
  ],
  "depends_on": ["<sibling capsule name>", "..."],
  "env_required": ["<env var the capsule reads at runtime>", "..."],
  "agent_summary": "<what an AI agent needs to know to safely work on this capsule>",
  "avoid": ["<hard rule: do not X>", "..."],
  "invariants": ["<property that must always hold>", "..."],
  "reusability_notes": "<concrete: what does a consumer need to rename / configure / replace to use this elsewhere?>"
}}

HARD CONSTRAINTS:
  - JSON only, inside ONE ```json``` fence.
  - Only reference sibling capsules that appear in the SIBLING CAPSULES list above.
  - reusability_notes MUST name specific hardcoded values (env var names,
    binding names, brand strings, paths) that a consumer will need to change.
  {ns_block}

----- FILE EXCERPTS FOR THIS CAPSULE -----

{file_excerpts}

Produce the contract JSON now."""


def _per_capsule_excerpts(repo_root: Path, files: list["Pass1File"]) -> str:
    """First ~120 lines of each file the capsule owns. Capped at ~30KB
    total so even capsules with many files fit in tiny-context models."""
    MAX_LINES_PER_FILE = 120
    MAX_TOTAL = 30_000
    out: list[str] = []
    budget = MAX_TOTAL
    for f in files:
        path = (repo_root / f.from_).resolve()
        if not path.is_file():
            continue
        if path.suffix.lower() in _BINARY_EXTS:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        head = "\n".join(lines[:MAX_LINES_PER_FILE])
        truncation = "" if len(lines) <= MAX_LINES_PER_FILE else f"\n# ... ({len(lines) - MAX_LINES_PER_FILE} more lines)"
        block = f"\n----- {f.from_} ({size}b, {len(lines)} lines) -----\n{head}{truncation}\n"
        if len(block) > budget:
            break
        out.append(block)
        budget -= len(block)
    return "".join(out) or "(no readable files)"


# ---------------------------------------------------------------------------
# Skeleton parsing (intermediate shape)
# ---------------------------------------------------------------------------


from dataclasses import dataclass, field


@dataclass
class Pass1File:
    from_: str
    to: str


@dataclass
class Pass1Capsule:
    name: str
    type: str
    purpose_summary: str
    files: list[Pass1File] = field(default_factory=list)


@dataclass
class Pass1Skeleton:
    summary: str
    capsules: list[Pass1Capsule]
    leftover_files: list[str]
    leftover_explanation: str


def parse_pass1(payload: dict) -> Pass1Skeleton:
    if not isinstance(payload, dict) or "capsules" not in payload:
        raise DecomposeError(
            f"Pass-1 payload missing 'capsules': {json.dumps(payload)[:300]}"
        )
    caps: list[Pass1Capsule] = []
    for raw in payload.get("capsules", []):
        files = [
            Pass1File(from_=str(f.get("from", "")), to=str(f.get("to", "")))
            for f in raw.get("files", [])
            if isinstance(f, dict) and f.get("from") and f.get("to")
        ]
        caps.append(Pass1Capsule(
            name=str(raw.get("name", "")),
            type=str(raw.get("type", "subsystem")) or "subsystem",
            purpose_summary=str(raw.get("purpose_summary", "")),
            files=files,
        ))
    return Pass1Skeleton(
        summary=str(payload.get("summary", "")),
        capsules=caps,
        leftover_files=_str_list(payload, "leftover_files"),
        leftover_explanation=str(payload.get("leftover_explanation", "")),
    )


# ---------------------------------------------------------------------------
# Merging pass-2 detail back into ProposedCapsule
# ---------------------------------------------------------------------------


def parse_pass2_into(p1: Pass1Capsule, payload: dict) -> ProposedCapsule:
    """Combine pass-1 skeleton + pass-2 contract → full ProposedCapsule."""
    provides = [
        ProposedInterface(
            kind=str(i.get("kind", "")),
            name=str(i.get("name", "")),
            from_capsule=i.get("from_capsule") or None,
            description=i.get("description") or None,
        )
        for i in payload.get("provides", []) or []
    ]
    requires = [
        ProposedInterface(
            kind=str(i.get("kind", "")),
            name=str(i.get("name", "")),
            from_capsule=i.get("from_capsule") or None,
            description=i.get("description") or None,
        )
        for i in payload.get("requires", []) or []
    ]
    return ProposedCapsule(
        name=p1.name,
        type=p1.type or "subsystem",
        purpose_summary=p1.purpose_summary,
        owns=_str_list(payload, "owns"),
        does_not_own=_str_list(payload, "does_not_own"),
        files=[ProposedFile(from_=f.from_, to=f.to) for f in p1.files],
        env_required=_str_list(payload, "env_required"),
        depends_on=_str_list(payload, "depends_on"),
        provides=provides,
        requires=requires,
        agent_summary=str(payload.get("agent_summary", "")),
        avoid=_str_list(payload, "avoid"),
        invariants=_str_list(payload, "invariants"),
        reusability_notes=str(payload.get("reusability_notes", "")),
    )
