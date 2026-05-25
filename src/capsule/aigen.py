"""capsule generate-tests — AI-drafted regression tests from invariants.

This is the "self-verifying" claim made concrete: every capsule ships a list
of invariants ("a revoked token must never authenticate", "a lab must belong
to exactly one student"). We ask Claude to draft one pytest function per
invariant — a scaffold the human then fills in. The output is intentionally
*skip*ped tests with TODO hints, not magic auto-passing tests; the value is
the structured starting point, not pretending the work is done.

The Anthropic API is called with no SDK dependency — plain urllib so we
don't add weight to the CLI's dependency tree.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from capsule.schema import Capsule

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
API_VERSION = "2023-06-01"


class AIGenError(Exception):
    """Anything that goes wrong calling Anthropic or parsing its reply."""


@dataclass
class GenerateResult:
    file_path: Path
    invariant_count: int
    bytes_written: int
    model: str


def generate_tests(
    c: Capsule,
    *,
    capsule_dir: Path,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
) -> GenerateResult:
    """Generate pytest scaffolds for every invariant in the capsule.

    Writes to <capsule_dir>/tests/ai_generated/<name>_<timestamp>.py.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AIGenError(
            "ANTHROPIC_API_KEY is not set. Export it before running:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )

    invariants = c.verification.invariants
    if not invariants:
        raise AIGenError(
            f"capsule {c.name} has no invariants — nothing to generate tests for."
        )

    prompt = _build_prompt(c, invariants)
    body = _call_anthropic(prompt, api_key=key, model=model)
    code = _extract_python(body)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = capsule_dir / "tests" / "ai_generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{c.name.replace('-', '_')}_{ts}.py"

    header = _file_header(c, model, ts, len(invariants))
    out_file.write_text(header + code.rstrip() + "\n", encoding="utf-8")

    return GenerateResult(
        file_path=out_file,
        invariant_count=len(invariants),
        bytes_written=out_file.stat().st_size,
        model=model,
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _build_prompt(c: Capsule, invariants: list[str]) -> str:
    provides_lines = [
        f"  - {p.kind}:{p.name}" + (f" — {p.description}" if p.description else "")
        for p in c.interfaces.provides
    ]
    requires_lines = [
        f"  - {r.kind}:{r.name}"
        + (f" from {r.from_capsule}" if r.from_capsule else "")
        + (f" — {r.description}" if r.description else "")
        for r in c.interfaces.requires
    ]
    invariant_block = "\n".join(f"  {i+1}. {inv}" for i, inv in enumerate(invariants))

    return f"""You are drafting Python pytest test scaffolds for a software subsystem
described by a capsule.yaml document. A capsule is a self-describing,
self-verifying unit of a larger system. Each invariant is a property that
must always hold.

For each invariant below, generate ONE pytest test function. The function
must:
  - have a clear snake_case name that describes what it protects
  - have a one-line docstring that quotes (or paraphrases) the invariant
  - call pytest.skip("not yet implemented: <hint>") near the top, because
    real implementation requires capsule-specific setup the AI cannot infer
  - include a short comment block above the skip with concrete starting
    hints (which file to touch, which API to call, what fixture is needed)

Constraints on your output:
  - Output ONLY a valid Python module
  - Start with `import pytest`
  - No markdown, no code fences, no prose explanation before or after
  - No assertions that would auto-pass; the skip is the contract

----- CAPSULE -----
name:     {c.name}
version:  {c.version}
type:     {c.type}
purpose:  {c.purpose.summary.strip()}

provides:
{chr(10).join(provides_lines) if provides_lines else "  (none declared)"}

requires:
{chr(10).join(requires_lines) if requires_lines else "  (none declared)"}

----- INVARIANTS -----
{invariant_block}

Generate the pytest module now."""


def _call_anthropic(prompt: str, *, api_key: str, model: str) -> dict:
    body = {
        "model": model,
        "max_tokens": 2000,
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
            "user-agent": "capsule-cli/0.3 (+https://github.com/quake0day/capsule)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise AIGenError(f"Anthropic API {exc.code}: {text}") from exc
    except urllib.error.URLError as exc:
        raise AIGenError(f"could not reach Anthropic API: {exc.reason}") from exc


def _extract_python(response: dict) -> str:
    """Pull the text out of a Messages API response, strip any code fences."""
    content = response.get("content") or []
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    if not text_parts:
        raise AIGenError(f"no text content in response: {json.dumps(response)[:300]}")
    text = "\n".join(text_parts).strip()

    # If the model wrapped output in ```python ... ``` despite instructions,
    # strip the fence.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text.startswith(("import", "from", "#")):
        raise AIGenError(
            "model output does not look like a Python module — first 200 chars:\n"
            + text[:200]
        )
    return text


def _file_header(c: Capsule, model: str, timestamp: str, invariant_count: int) -> str:
    return f'''"""AI-generated test scaffolds for {c.name}@{c.version}.

Generated {timestamp} by capsule generate-tests using {model}.
One test per declared invariant ({invariant_count} total). Every test calls
pytest.skip() — the human implementing them must replace each skip with the
real assertions. Do NOT delete the skip without implementing the test:
auto-passing tests are worse than no tests.

See capsule.yaml `verification.invariants` for the canonical list this
file was generated from.
"""

'''
