"""capsule decompose — turn an existing repo into a set of reusable capsules.

The reverse of `capsule reconstruct`. Given a git URL or local path, this
tool:

  1. Reads the repo (clone if URL, walk if local).
  2. Builds a compact context bundle (tree + key file excerpts).
  3. Asks Claude to propose a decomposition: 1..N capsules, each with a
     clear boundary, a typed contract, and a reusability note explaining
     what a consumer must change to use it elsewhere.
  4. Returns the structured plan. (materializer.py turns it into files.)

The LLM call is single-shot — one prompt in, one JSON response out. The
prompt is engineered to push hard on REUSABILITY: generic naming (not
project-specific), explicit ${VAR} substitutions for hardcoded values,
honest leftover bucket for files that can't be cleanly extracted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"
API_VERSION = "2023-06-01"

# Gemini fallback — used automatically if ANTHROPIC_API_KEY is unset but
# GEMINI_API_KEY is. Model picked for long-context structured-JSON tasks.
GEMINI_ENDPOINT_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Hard caps to keep prompts under control.
MAX_TREE_FILES = 800
MAX_FILE_BYTES = 100_000        # individual file ceiling for excerpt
MAX_EXCERPT_LINES = 200
MAX_TOTAL_CONTEXT = 90_000      # cap on bytes of file excerpts we send

# Anything matching this is silently skipped from the tree + excerpts.
_SKIP_PATTERNS = [
    r"^\.git/", r"^\.git$",
    r"^node_modules/",
    r"^\.venv/", r"^venv/",
    r"__pycache__/",
    r"\.pyc$",
    r"^\.next/", r"^dist/", r"^build/", r"^out/",
    r"\.DS_Store$",
    r"\.lock$", r"^package-lock\.json$", r"^pnpm-lock\.yaml$", r"^yarn\.lock$", r"^Cargo\.lock$",
    r"\.min\.(js|css)$", r"\.map$",
]
_SKIP_RE = re.compile("|".join(_SKIP_PATTERNS))

# Binary extensions we never even attempt to read as text.
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".webm", ".wav",
    ".exe", ".dll", ".so", ".dylib",
}


class DecomposeError(Exception):
    """Anything that goes wrong cloning, prompting, or parsing."""


# ---------------------------------------------------------------------------
# data shapes (mirrors of the JSON the LLM is asked to return)
# ---------------------------------------------------------------------------


@dataclass
class ProposedFile:
    from_: str            # path inside the source repo
    to: str               # target path inside a reconstructed site


@dataclass
class ProposedInterface:
    kind: str
    name: str
    from_capsule: str | None = None
    version: str | None = None
    description: str | None = None


@dataclass
class ProposedCapsule:
    name: str
    type: str             # subsystem | adapter | template
    purpose_summary: str
    owns: list[str]
    does_not_own: list[str]
    files: list[ProposedFile]
    env_required: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    provides: list[ProposedInterface] = field(default_factory=list)
    requires: list[ProposedInterface] = field(default_factory=list)
    agent_summary: str = ""
    avoid: list[str] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    reusability_notes: str = ""


@dataclass
class DecompositionPlan:
    source: str                       # the original repo URL or path
    summary: str
    capsules: list[ProposedCapsule]
    leftover_files: list[str] = field(default_factory=list)
    leftover_explanation: str = ""


# ---------------------------------------------------------------------------
# acquiring the source
# ---------------------------------------------------------------------------


def acquire_repo(source: str, *, keep: bool = False) -> tuple[Path, bool]:
    """Return (path_to_repo, is_temp). For URLs, clones into a temp dir."""
    if _looks_like_url(source):
        tmp = Path(tempfile.mkdtemp(prefix="capsule-decompose-"))
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", source, str(tmp)],
                check=True, capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise DecomposeError("git is not installed or not on PATH") from exc
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            raise DecomposeError(
                f"git clone failed: {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        return tmp, not keep
    path = Path(source).expanduser().resolve()
    if not path.is_dir():
        raise DecomposeError(f"{source}: not a directory and not a recognised URL")
    return path, False


def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "git@", "ssh://"))


# ---------------------------------------------------------------------------
# context bundle
# ---------------------------------------------------------------------------


def walk_repo(repo_root: Path) -> list[Path]:
    """Return a sorted list of files in the repo, with the skip-list applied."""
    out: list[Path] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if _SKIP_RE.search(rel):
            continue
        out.append(path)
        if len(out) >= MAX_TREE_FILES:
            break
    return out


def build_context(repo_root: Path, files: list[Path]) -> str:
    """Compose the LLM context: tree + selected file excerpts.

    File selection rules:
      - All paths go in the tree
      - Excerpt text content for each non-binary file under MAX_FILE_BYTES
      - Cap the *first* MAX_EXCERPT_LINES of each file
      - Stop accumulating excerpts once we hit MAX_TOTAL_CONTEXT
    """
    rels = [p.relative_to(repo_root).as_posix() for p in files]
    tree_block = "REPO TREE (" + str(len(rels)) + " files):\n" + "\n".join(rels)

    excerpts: list[str] = []
    budget = MAX_TOTAL_CONTEXT
    for path in files:
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
        head = "\n".join(lines[:MAX_EXCERPT_LINES])
        truncation = "" if len(lines) <= MAX_EXCERPT_LINES else f"\n# ... ({len(lines) - MAX_EXCERPT_LINES} more lines truncated)"
        rel = path.relative_to(repo_root).as_posix()
        block = f"\n----- FILE: {rel} ({size}b, {len(lines)} lines) -----\n{head}{truncation}\n"
        if len(block) > budget:
            break
        excerpts.append(block)
        budget -= len(block)

    return tree_block + "\n\nFILE EXCERPTS (first " + str(MAX_EXCERPT_LINES) + " lines each, " + str(len(excerpts)) + " files):\n" + "".join(excerpts)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def build_prompt(
    source: str,
    context: str,
    namespace: str | None,
    user_hint: str | None,
) -> str:
    ns_block = (
        f"TARGET NAMESPACE: {namespace}\n"
        f"  Every capsule name MUST start with `{namespace}-` so the output is\n"
        f"  unambiguously yours (e.g. `{namespace}-auth`, `{namespace}-search`)."
        if namespace else
        "TARGET NAMESPACE: (none specified — pick generic, reusable names\n"
        "  that do NOT mention the source project's brand or domain.)"
    )
    hint_block = f"USER HINT:\n  {user_hint}" if user_hint else "USER HINT: (none)"

    return f"""You are decomposing an existing software repository into a set of
**reusable** capsules. A capsule is a self-contained subsystem with a clear
boundary, a typed contract (what it provides / requires), and code that can
be **lifted into a different project with minimal change**.

Your output decides how reusable the result is. Optimise hard for reuse:

  - PREFER GENERIC NAMES. Bad: `acmecorp-billing-svc`. Good: `stripe-billing-gateway`.
    The capsule's name should describe what it *is*, not where it came from.
  - EVERY hardcoded project-specific value (binding names like YL_DATA,
    domain names, KV keys, brand strings) gets noted in `reusability_notes`
    so the consumer knows what to rename / parameterize.
  - DO NOT FORCE a capsule out of code that is fundamentally project-specific
    (e.g. a particular HTML page with hand-written copy, an artwork list, a
    one-of-a-kind dashboard). Put those in `leftover_files` instead. An
    honest leftover bucket is FAR better than a fake-reusable capsule.
  - When a file is half-reusable, half-bespoke (e.g. an admin SPA that
    happens to have one artist's color palette baked in), mark the capsule
    as `type: template` — meaning "starting point, expected to be customized".

OUTPUT STRICT JSON, in a single fenced ```json``` block, with this schema:

{{
  "summary": "<one-paragraph description of the repo and your decomposition>",
  "capsules": [
    {{
      "name": "<kebab-case, generic, namespace-prefixed if a namespace was given>",
      "type": "subsystem" | "adapter" | "template",
      "purpose_summary": "<one-paragraph 'what this owns'>",
      "owns": ["<bullet>", "..."],
      "does_not_own": ["<bullet>", "..."],
      "files": [
        {{"from": "<path in source repo>", "to": "<path in a reconstructed site>"}}
      ],
      "env_required": ["<env var the capsule reads at runtime>"],
      "depends_on": ["<other capsule.name in this output>"],
      "provides": [
        {{"kind": "http_api|library|event|cli|env", "name": "...", "description": "..."}}
      ],
      "requires": [
        {{"kind": "...", "name": "...", "from_capsule": "<dep name or null>", "description": "..."}}
      ],
      "agent_summary": "<what an AI agent needs to know to work on this capsule>",
      "avoid": ["<hard rule: do not X>"],
      "invariants": ["<property that must always hold>"],
      "reusability_notes": "<concrete: what does the consumer need to rename / configure / replace to use this elsewhere?>"
    }}
  ],
  "leftover_files": ["<path>", "..."],
  "leftover_explanation": "<why these files don't fit a reusable capsule>"
}}

Constraints (HARD):
  - JSON only, inside one ```json``` block, nothing before or after.
  - Every file in the source repo appears EXACTLY ONCE: either in some
    capsule's files[], or in leftover_files. No file may appear twice.
  - Source paths in `files[].from` must match the REPO TREE exactly.
  - `to` paths are forward-slash relative paths (no leading slash, no `..`).
  - Use the same `kind` strings the capsule.yaml spec uses (http_api,
    library, event, cli, env). Do NOT invent new top-level kinds.

----- SOURCE: {source} -----

{ns_block}

{hint_block}

{context}

Produce the JSON decomposition now."""


def call_anthropic(prompt: str, *, api_key: str, model: str) -> dict:
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
        with urllib.request.urlopen(req, timeout=180) as resp:
            return _normalise_anthropic(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise DecomposeError(f"Anthropic API {exc.code}: {text}") from exc
    except urllib.error.URLError as exc:
        raise DecomposeError(f"could not reach Anthropic API: {exc.reason}") from exc


def call_gemini(prompt: str, *, api_key: str, model: str) -> dict:
    """Gemini fallback. Returns the text-only normalised shape."""
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            # Gemini 2.5 supports up to 65k output tokens; we use 32k to
            # leave headroom for the long decomposition JSON of mid-sized
            # repos. A truncated response (finishReason=MAX_TOKENS) is
            # detected post-hoc.
            "maxOutputTokens": 32000,
            "temperature": 0.4,
            # Force the model to emit a JSON document instead of prose.
            # Removes the markdown-fenced-block parsing risk and most
            # half-escaped-string failures.
            "responseMimeType": "application/json",
        },
    }
    url = f"{GEMINI_ENDPOINT_TMPL.format(model=model)}?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return _normalise_gemini(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise DecomposeError(f"Gemini API {exc.code}: {text}") from exc
    except urllib.error.URLError as exc:
        raise DecomposeError(f"could not reach Gemini API: {exc.reason}") from exc


def _normalise_anthropic(raw: dict) -> dict:
    """Anthropic Messages API → { text: str, raw: any }."""
    return {"text": _anthropic_text(raw), "raw": raw, "provider": "anthropic"}


def _anthropic_text(raw: dict) -> str:
    content = raw.get("content") or []
    parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    return "\n".join(parts)


def _normalise_gemini(raw: dict) -> dict:
    """Gemini generateContent → { text: str, raw: any }."""
    return {"text": _gemini_text(raw), "raw": raw, "provider": "gemini"}


def _gemini_text(raw: dict) -> str:
    cands = raw.get("candidates") or []
    if not cands:
        return ""
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
    # Surface MAX_TOKENS truncation as an actionable error rather than a
    # confusing JSON parse error downstream.
    if cand.get("finishReason") == "MAX_TOKENS":
        raise DecomposeError(
            "Gemini stopped at maxOutputTokens — the decomposition was truncated "
            "mid-response. Try a smaller repo, narrower --prompt, or raise the "
            "limit (currently 32000)."
        )
    return text


def extract_json(response: dict) -> dict:
    text = response.get("text", "")
    if not text:
        raise DecomposeError(f"empty text in LLM response: {json.dumps(response.get('raw', {}))[:300]}")

    # If a debug dump path is set, drop the raw text there for inspection.
    debug_path = os.environ.get("CAPSULE_DECOMPOSE_DEBUG")
    if debug_path:
        try:
            Path(debug_path).write_text(text, encoding="utf-8")
        except OSError:
            pass

    # Find a ```json fenced block (most reliable).
    m = re.search(r"```json\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # Fallback: try to find a JSON object spanning the response.
        first = text.find("{")
        last = text.rfind("}")
        if first == -1 or last == -1 or last <= first:
            raise DecomposeError(f"could not locate JSON in response. First 300 chars:\n{text[:300]}")
        text = text[first:last + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecomposeError(f"JSON parse failed: {exc}. First 300 chars:\n{text[:300]}") from exc


def parse_plan(source: str, payload: dict) -> DecompositionPlan:
    if not isinstance(payload, dict) or "capsules" not in payload:
        raise DecomposeError(f"LLM payload missing 'capsules' field: {json.dumps(payload)[:300]}")

    capsules: list[ProposedCapsule] = []
    for raw in payload.get("capsules", []):
        capsules.append(ProposedCapsule(
            name=_str(raw, "name"),
            type=raw.get("type", "subsystem") or "subsystem",
            purpose_summary=_str(raw, "purpose_summary"),
            owns=_str_list(raw, "owns"),
            does_not_own=_str_list(raw, "does_not_own"),
            files=[
                ProposedFile(from_=str(f.get("from", "")), to=str(f.get("to", "")))
                for f in raw.get("files", [])
                if isinstance(f, dict) and f.get("from") and f.get("to")
            ],
            env_required=_str_list(raw, "env_required"),
            depends_on=_str_list(raw, "depends_on"),
            provides=[_parse_iface(i) for i in raw.get("provides", [])],
            requires=[_parse_iface(i) for i in raw.get("requires", [])],
            agent_summary=raw.get("agent_summary", "") or "",
            avoid=_str_list(raw, "avoid"),
            invariants=_str_list(raw, "invariants"),
            reusability_notes=raw.get("reusability_notes", "") or "",
        ))

    return DecompositionPlan(
        source=source,
        summary=payload.get("summary", "") or "",
        capsules=capsules,
        leftover_files=_str_list(payload, "leftover_files"),
        leftover_explanation=payload.get("leftover_explanation", "") or "",
    )


def _parse_iface(raw: dict) -> ProposedInterface:
    return ProposedInterface(
        kind=str(raw.get("kind", "")),
        name=str(raw.get("name", "")),
        from_capsule=raw.get("from_capsule") or None,
        version=raw.get("version") or None,
        description=raw.get("description") or None,
    )


def _str(raw: dict, key: str) -> str:
    v = raw.get(key, "")
    return v if isinstance(v, str) else ""


def _str_list(raw: dict, key: str) -> list[str]:
    v = raw.get(key, [])
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str)]


# ---------------------------------------------------------------------------
# top-level orchestration
# ---------------------------------------------------------------------------


def decompose(
    source: str,
    *,
    namespace: str | None = None,
    prompt: str | None = None,
    keep: bool = False,
    model: str | None = None,
    provider: str | None = None,  # "anthropic" | "gemini" | None (auto)
    api_key: str | None = None,
) -> tuple[DecompositionPlan, Path, bool]:
    """Acquire + analyse + plan. Returns (plan, repo_root, is_temp_dir).

    Provider auto-detected from env if not specified:
      ANTHROPIC_API_KEY → anthropic
      GEMINI_API_KEY    → gemini
    """
    chosen_provider, key = _pick_provider(provider, api_key)
    chosen_model = model or (DEFAULT_MODEL if chosen_provider == "anthropic" else DEFAULT_GEMINI_MODEL)

    repo_root, is_temp = acquire_repo(source, keep=keep)
    files = walk_repo(repo_root)
    if not files:
        raise DecomposeError(f"no files found under {repo_root}")
    context = build_context(repo_root, files)
    full_prompt = build_prompt(source, context, namespace, prompt)

    if chosen_provider == "anthropic":
        response = call_anthropic(full_prompt, api_key=key, model=chosen_model)
    else:
        response = call_gemini(full_prompt, api_key=key, model=chosen_model)

    payload = extract_json(response)
    plan = parse_plan(source, payload)
    return plan, repo_root, is_temp


def _pick_provider(provider: str | None, api_key: str | None) -> tuple[str, str]:
    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise DecomposeError("provider=anthropic but ANTHROPIC_API_KEY is unset")
        return "anthropic", key
    if provider == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise DecomposeError("provider=gemini but GEMINI_API_KEY is unset")
        return "gemini", key
    # Auto: prefer Anthropic if its key is set, else Gemini.
    if api_key:
        # Explicit key passed but no provider — assume anthropic.
        return "anthropic", api_key
    anth = os.environ.get("ANTHROPIC_API_KEY")
    if anth:
        return "anthropic", anth
    gem = os.environ.get("GEMINI_API_KEY")
    if gem:
        return "gemini", gem
    raise DecomposeError(
        "no LLM key found. Set ANTHROPIC_API_KEY (preferred) or GEMINI_API_KEY:\n"
        "  export ANTHROPIC_API_KEY=sk-ant-...\n"
        "  export GEMINI_API_KEY=AIza..."
    )
