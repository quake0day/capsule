"""capsule reconstruct --prompt — AI-driven customization layer.

After the mechanical reconstruction has produced a working site, this
module routes the assembled output + the user's natural-language prompt
+ the capsule contracts to Claude. Claude returns a list of small file
patches (full-file rewrites of a handful of files) which we apply. The
prompt + capsules + data are the *recipe*; Claude is the *cook* that
seasons the dish.

We bound the surface deliberately:
  - Claude can only rewrite files that already exist in the output
  - The list of editable files is enumerated up-front (frontend assets
    + admin UI files, not the auth or content-store backends, which are
    contracts we don't want the LLM rewriting silently)
  - Each patch is a full-file replacement returned as a fenced block
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from capsule.aigen import AIGenError  # re-use the same error type

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"   # bigger model — multi-file edits
API_VERSION = "2023-06-01"

# Whitelist of file paths inside the output directory that Claude may
# rewrite. Backend functions and auth code are intentionally excluded.
EDITABLE_GLOBS = [
    "index.html",
    "assets/styles.css",
    "assets/app.js",
    "admin/index.html",
    "admin/admin.css",
    "admin/admin.js",
]


@dataclass
class Patch:
    path: str           # relative to out_dir
    new_content: str
    rationale: str


def customize(
    *,
    capsules_dir: Path,
    out_dir: Path,
    data: dict | None,
    prompt: str,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> list[Patch]:
    """Ask Claude to customize the reconstructed site per the user's prompt."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AIGenError(
            "ANTHROPIC_API_KEY is not set. Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )

    contracts = _collect_contracts(capsules_dir)
    editable = _collect_editable_files(out_dir)
    if not editable:
        raise AIGenError(
            f"no editable frontend files found under {out_dir}. "
            f"Run the mechanical reconstruction first."
        )

    user_msg = _build_prompt(prompt, contracts, editable, data)
    response = _call_anthropic(user_msg, api_key=key, model=model)
    patches = _parse_patches(response, editable_set={p[0] for p in editable})
    if not patches:
        raise AIGenError(
            "Claude returned a response but no patches were found. "
            "Re-run with a more specific prompt, or check the model output."
        )

    # Apply.
    for p in patches:
        dst = (out_dir / p.path).resolve()
        if not dst.is_file():
            # Defensive: should not happen because we already enumerated.
            raise AIGenError(f"patch target outside whitelist: {p.path}")
        dst.write_text(p.new_content, encoding="utf-8")
    return patches


# ---------------------------------------------------------------------------
# inputs to the prompt
# ---------------------------------------------------------------------------


def _collect_contracts(capsules_dir: Path) -> str:
    """Concatenate every capsule.yaml as plain text for context."""
    parts: list[str] = []
    for y in sorted(capsules_dir.rglob("capsule.yaml")):
        rel = y.relative_to(capsules_dir)
        parts.append(f"# {rel}\n{y.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def _collect_editable_files(out_dir: Path) -> list[tuple[str, str]]:
    """Return (rel_path, content) for every whitelisted file present."""
    out: list[tuple[str, str]] = []
    for rel in EDITABLE_GLOBS:
        p = (out_dir / rel).resolve()
        if p.is_file():
            try:
                out.append((rel, p.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                # Skip binary files silently.
                continue
    return out


def _build_prompt(
    user_prompt: str,
    contracts: str,
    editable: list[tuple[str, str]],
    data: dict | None,
) -> str:
    files_block = "\n\n".join(
        f"----- FILE: {rel} -----\n{content}"
        for rel, content in editable
    )

    data_block = ""
    if data is not None:
        data_excerpt = json.dumps({k: data[k] for k in list(data.keys())[:3]}, indent=2)[:1500]
        data_block = f"\n----- DATA EXCERPT (first 3 keys, truncated) -----\n{data_excerpt}\n"

    return f"""You are helping customize an already-assembled Cloudflare Pages site.
The site has been mechanically reconstructed from a set of self-contained
capsules. Your job: apply the user's prompt by rewriting one or more of
the editable frontend files below. Backend code (auth, data store, image
store) is OUT OF SCOPE — do not touch it, do not reference fields that
don't exist in the data, and do not break the runtime contracts the
capsules declare.

USER PROMPT:
{user_prompt}

CONSTRAINTS (hard):
  - Output ONE or more full-file rewrites. No partial diffs.
  - Use this exact fenced block format for each file you change:

        <<<FILE: <relative-path>>>>
        <full new file contents, no truncation, no ellipsis>
        <<<END FILE>>>

  - Only change files that appear below as "FILE: ..." headers.
  - Do not produce a file that already matches the input — skip files
    that don't need to change.
  - Before EACH file block, write one line starting with "RATIONALE:"
    explaining in a sentence why the change is needed.
  - Do not add new files; the reconstruction owns the file set.
  - Do not break the JS module structure (the page is 0-dep ESM) or the
    /api/data → render → DOM data flow.

CAPSULE CONTRACTS (read-only context):
{contracts[:6000]}{"..." if len(contracts) > 6000 else ""}

EDITABLE FILES (you may rewrite any subset of these):
{files_block}
{data_block}
Now produce your patches."""


# ---------------------------------------------------------------------------
# anthropic call + parse
# ---------------------------------------------------------------------------


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> str:
    body = {
        "model": model,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        ANTHROPIC_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
            "user-agent": "capsule-cli/0.4 (+https://github.com/quake0day/capsule)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise AIGenError(f"Anthropic API {exc.code}: {text}") from exc
    except urllib.error.URLError as exc:
        raise AIGenError(f"could not reach Anthropic API: {exc.reason}") from exc

    content = payload.get("content") or []
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    if not text_parts:
        raise AIGenError(f"no text content in Claude response: {json.dumps(payload)[:300]}")
    return "\n".join(text_parts)


# Parser: matches blocks like
#   RATIONALE: <text>
#   <<<FILE: <path>>>>
#   <body>
#   <<<END FILE>>>
_PATCH_RE = re.compile(
    r"RATIONALE:\s*(?P<rationale>.+?)\s*"
    r"<<<FILE:\s*(?P<path>[^>]+?)\s*>>>>\s*\n"
    r"(?P<body>.*?)"
    r"\n\s*<<<END FILE>>>",
    re.DOTALL,
)


def _parse_patches(text: str, *, editable_set: set[str]) -> list[Patch]:
    out: list[Patch] = []
    for m in _PATCH_RE.finditer(text):
        path = m.group("path").strip()
        if path not in editable_set:
            # Silently drop attempts to write files outside the whitelist.
            continue
        out.append(Patch(
            path=path,
            new_content=m.group("body"),
            rationale=m.group("rationale").strip(),
        ))
    return out
