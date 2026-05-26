#!/usr/bin/env python3
"""Benchmark capsule decompose across (repo, provider, model) combos.

Captures, per run:
  - wall_clock_s
  - input_chars  (prompt size)
  - output_chars (LLM response size)
  - capsule_count
  - leftover_count
  - files_total / file_coverage_pct
  - cost_usd_est (using per-model pricing where known)
  - success / error_msg / error_class

Writes results to server/benchmarks/results.json so the live /benchmarks
page on the registry can render comparisons.

Usage:
  GEMINI_API_KEY=... \\
  python tools/benchmark_decompose.py \\
    --repos https://github.com/quake0day/yingjieli,https://github.com/i365dev/free4chat \\
    --runs gemini=gemini-2.5-flash,workers-ai=gpt-oss-20b,workers-ai=gpt-oss-120b

Re-running appends to results.json with fresh timestamps; the page shows
the newest run per (repo, model) pair (older history is preserved for
audit but not surfaced).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make `capsule.*` importable when running from a clone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from capsule.decompose import (
    DecomposeError,
    acquire_repo,
    walk_repo,
    build_context,
    build_prompt,
    call_anthropic,
    call_gemini,
    extract_json,
    parse_plan,
    DEFAULT_MODEL as DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
)
from capsule.cf_workers_ai import (
    WorkersAIError,
    call_workers_ai,
    estimate_cost_usd,
    resolve_credentials as cf_creds,
    resolve_model as cf_model,
    MODEL_PRICING,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "server" / "benchmarks" / "results.json"


# ---------------------------------------------------------------------------
# data shapes
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    repo: str
    repo_name: str
    provider: str
    model: str
    model_full: str
    started_at: str
    wall_clock_s: float
    success: bool
    capsule_count: int = 0
    leftover_count: int = 0
    files_total: int = 0
    file_coverage_pct: int = 0
    input_chars: int = 0
    output_chars: int = 0
    input_tokens_est: int = 0
    output_tokens_est: int = 0
    cost_usd_est: float = 0.0
    error: Optional[str] = None
    error_class: Optional[str] = None


@dataclass
class ResultsDoc:
    generated_at: str
    runs: list[BenchResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------


def run_one(repo_url: str, provider: str, model: str) -> BenchResult:
    """Execute one (repo, provider, model) combo. Always returns a BenchResult,
    even on failure (success=False, error set)."""
    repo_name = repo_url.rstrip("/").split("/")[-1]
    started_at = datetime.now(timezone.utc).isoformat()
    model_full = _model_full(provider, model)

    result = BenchResult(
        repo=repo_url,
        repo_name=repo_name,
        provider=provider,
        model=model,
        model_full=model_full,
        started_at=started_at,
        wall_clock_s=0.0,
        success=False,
    )

    t0 = time.monotonic()
    try:
        repo_root, is_temp = acquire_repo(repo_url)
        try:
            files = walk_repo(repo_root)
            result.files_total = len(files)
            if not files:
                raise DecomposeError("no files in repo")

            context = build_context(repo_root, files)
            prompt = build_prompt(repo_url, context, namespace=None, user_hint=None)
            result.input_chars = len(prompt)
            result.input_tokens_est = result.input_chars // 4  # rough

            response = _call(provider, model, prompt)
            result.output_chars = len(response.get("text", ""))
            result.output_tokens_est = result.output_chars // 4

            payload = extract_json(response)
            plan = parse_plan(repo_url, payload)

            placed = sum(len(c.files) for c in plan.capsules)
            leftover = len(plan.leftover_files)
            covered = placed + leftover
            result.capsule_count = len(plan.capsules)
            result.leftover_count = leftover
            result.file_coverage_pct = (covered * 100 // result.files_total) if result.files_total else 0

            if provider == "workers-ai":
                result.cost_usd_est = estimate_cost_usd(
                    model_full, result.input_tokens_est, result.output_tokens_est,
                )
            else:
                # Rough Gemini/Anthropic pricing (we don't track precisely in v1)
                result.cost_usd_est = _estimate_cost_other(
                    provider, model, result.input_tokens_est, result.output_tokens_est,
                )

            result.success = True
        finally:
            if is_temp:
                import shutil
                shutil.rmtree(repo_root, ignore_errors=True)
    except Exception as exc:
        result.error = str(exc)[:500]
        result.error_class = exc.__class__.__name__
        # Keep partial stats if we got far enough to compute them.
    finally:
        result.wall_clock_s = round(time.monotonic() - t0, 2)

    return result


def _model_full(provider: str, model: str) -> str:
    if provider == "workers-ai":
        try:
            return cf_model(model)
        except WorkersAIError:
            return model
    return model


def _call(provider: str, model: str, prompt: str) -> dict:
    if provider == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]
        return call_anthropic(prompt, api_key=key, model=model or DEFAULT_ANTHROPIC_MODEL)
    if provider == "gemini":
        key = os.environ["GEMINI_API_KEY"]
        return call_gemini(prompt, api_key=key, model=model or DEFAULT_GEMINI_MODEL)
    if provider == "workers-ai":
        creds = cf_creds()
        return call_workers_ai(prompt, model=model, creds=creds)
    raise ValueError(f"unknown provider: {provider}")


def _estimate_cost_other(provider: str, model: str, in_tok: int, out_tok: int) -> float:
    """Rough cost for Anthropic / Gemini. Public prices, not tracked precisely."""
    if provider == "anthropic":
        # claude-haiku-4-5 ≈ $1.00 / $5.00 per M tokens. Underestimates for sonnet.
        if "sonnet" in model.lower():
            return in_tok / 1e6 * 3.0 + out_tok / 1e6 * 15.0
        return in_tok / 1e6 * 1.0 + out_tok / 1e6 * 5.0
    if provider == "gemini":
        # gemini-2.5-flash ≈ $0.075 / $0.30 per M tokens (paid tier).
        return in_tok / 1e6 * 0.075 + out_tok / 1e6 * 0.30
    return 0.0


# ---------------------------------------------------------------------------
# results store
# ---------------------------------------------------------------------------


def load_results(path: Path) -> ResultsDoc:
    if not path.exists():
        return ResultsDoc(generated_at=datetime.now(timezone.utc).isoformat())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        runs = [BenchResult(**r) for r in raw.get("runs", [])]
        return ResultsDoc(generated_at=raw.get("generated_at", ""), runs=runs)
    except Exception:
        return ResultsDoc(generated_at=datetime.now(timezone.utc).isoformat())


def save_results(path: Path, doc: ResultsDoc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": [asdict(r) for r in doc.runs],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_run_spec(spec: str) -> list[tuple[str, str]]:
    """`gemini=gemini-2.5-flash,workers-ai=gpt-oss-20b,workers-ai=gpt-oss-120b`
    → [(gemini, gemini-2.5-flash), (workers-ai, gpt-oss-20b), ...]"""
    out: list[tuple[str, str]] = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"bad --runs piece '{piece}', expected provider=model")
        provider, model = piece.split("=", 1)
        out.append((provider.strip(), model.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--repos", required=True,
        help="comma-separated list of git URLs to decompose",
    )
    ap.add_argument(
        "--runs", required=True,
        help="comma-separated provider=model pairs (e.g. workers-ai=gpt-oss-20b,gemini=gemini-2.5-flash)",
    )
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"output JSON path (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})",
    )
    args = ap.parse_args()

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    pairs = parse_run_spec(args.runs)
    total = len(repos) * len(pairs)
    print(f"\nRunning {total} benchmarks ({len(repos)} repo(s) × {len(pairs)} model(s))\n")

    doc = load_results(args.out)
    n = 0
    for repo_url in repos:
        for provider, model in pairs:
            n += 1
            label = f"{provider}/{model}"
            print(f"  [{n}/{total}] {repo_url}  via  {label}...", flush=True)
            try:
                r = run_one(repo_url, provider, model)
            except Exception as exc:  # belt and suspenders
                print(f"        ✗ harness crashed: {exc}")
                traceback.print_exc()
                continue
            if r.success:
                print(f"        ✓ {r.wall_clock_s}s · {r.capsule_count} capsules · "
                      f"{r.file_coverage_pct}% coverage · ~${r.cost_usd_est:.4f}")
            else:
                print(f"        ✗ {r.wall_clock_s}s · {r.error_class}: {(r.error or '')[:80]}")
            doc.runs.append(r)
            save_results(args.out, doc)  # incremental save

    print(f"\nWrote {len(doc.runs)} total runs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
