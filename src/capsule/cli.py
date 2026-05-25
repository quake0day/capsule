"""Capsule CLI."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from capsule import __version__
from capsule.bundle import render as render_bundle
from capsule.client import (
    CapsuleClientError,
    parse_address,
    pull as pull_capsule,
)
from capsule.aigen import AIGenError, generate_tests
from capsule.push import PushError, push as push_capsule
from capsule.diff import (
    diff as compute_diff,
    render_markdown as diff_markdown,
    render_text as diff_text,
    to_json_dict as diff_json,
)
from capsule.compose import compose as compose_capsules
from capsule.compose import topo_order
from capsule.graph import render_dot, render_text
from capsule.loader import CapsuleLoadError, LoadedCapsule, discover, load
from capsule.manpage import render_man
from capsule.schema import warnings_for
from capsule.status import build as build_status
from capsule.status import print_status, to_json_dict
from capsule.templates import STARTER_CAPSULE_YAML, STARTER_README
from capsule.verify import Status, verify

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Self-verifying context capsules for AI-native software development.",
)
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"capsule {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """capsule — manage context capsules."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    name: str = typer.Argument(..., help="Capsule name (kebab-case)."),
    directory: Path = typer.Option(
        Path("."),
        "--dir",
        "-d",
        help="Parent directory; the capsule is created at <dir>/<name>/.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing capsule.yaml."),
) -> None:
    """Scaffold a new capsule at <dir>/<name>/."""
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name):
        err_console.print(f"[red]invalid capsule name '{name}': must be kebab-case[/red]")
        raise typer.Exit(code=2)
    target_dir = directory.expanduser().resolve() / name
    target_dir.mkdir(parents=True, exist_ok=True)
    capsule_path = target_dir / "capsule.yaml"
    readme_path = target_dir / "README.md"

    if capsule_path.exists() and not force:
        err_console.print(f"[red]{capsule_path} already exists (use --force to overwrite)[/red]")
        raise typer.Exit(code=1)
    capsule_path.write_text(STARTER_CAPSULE_YAML.format(name=name), encoding="utf-8")
    if not readme_path.exists():
        readme_path.write_text(STARTER_README.format(name=name), encoding="utf-8")
    console.print(f"[green]created[/green] {capsule_path}")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    paths: list[Path] = typer.Argument(..., help="Capsule directories, capsule.yaml files, or a parent dir."),
) -> None:
    """Validate capsule.yaml files against the spec. Auto-discovers under parent dirs."""
    any_failed = False
    targets: list[Path] = []
    for p in paths:
        resolved = p.expanduser().resolve()
        if resolved.is_dir() and not (resolved / "capsule.yaml").exists() and not (resolved / "capsule.yml").exists():
            found = list(resolved.rglob("capsule.yaml"))
            kept = [
                f for f in sorted(found)
                if not any(part.startswith(".") for part in f.relative_to(resolved).parts[:-1])
            ]
            if not kept:
                err_console.print(f"[red]✗[/red] no capsule.yaml found under {resolved}")
                any_failed = True
                continue
            targets.extend(kept)
        else:
            targets.append(resolved)

    for p in targets:
        try:
            lc = load(p)
        except CapsuleLoadError as exc:
            any_failed = True
            err_console.print(f"[red]✗[/red] {exc}")
            continue
        warns = warnings_for(lc.capsule)
        console.print(f"[green]✓[/green] {lc.path}  ({lc.name} v{lc.capsule.version})")
        for w in warns:
            console.print(f"  [yellow]warning[/yellow]: {w}")
    if any_failed:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@app.command(name="verify")
def verify_command(
    paths: list[Path] = typer.Argument(
        ...,
        metavar="PATHS",
        help="Capsule directories, capsule.yaml files, or a parent directory to discover capsules in.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit a JSON report to stdout."),
    skip_integration: bool = typer.Option(
        False, "--skip-integration", help="Skip integration tests."
    ),
) -> None:
    """Run each capsule's verification suite."""
    capsules = _resolve_capsules(paths)
    report = verify(capsules, include_integration=not skip_integration)

    if json_out:
        print(json.dumps(report.to_dict(), indent=2))
        raise typer.Exit(code=0 if report.ok else 1)

    table = Table(title="Capsule Verification Report", show_lines=False)
    table.add_column("capsule", style="cyan")
    table.add_column("category")
    table.add_column("check")
    table.add_column("status")
    table.add_column("ms", justify="right")
    table.add_column("notes", overflow="fold")

    for r in report.results:
        notes = r.skip_reason or ""
        if not notes and r.status == Status.FAIL and r.stderr_tail:
            notes = r.stderr_tail.strip().splitlines()[-1][:120]
        table.add_row(
            r.capsule,
            r.category,
            r.id,
            _color_status(r.status),
            str(r.duration_ms),
            notes,
        )
    console.print(table)

    summary = report.summary()
    console.print(
        f"[bold]Summary:[/bold] "
        f"[green]{summary['pass']} pass[/green]  "
        f"[red]{summary['fail']} fail[/red]  "
        f"[yellow]{summary['timeout']} timeout[/yellow]  "
        f"[yellow]{summary['error']} error[/yellow]  "
        f"[blue]{summary['skipped']} skipped[/blue]"
    )
    raise typer.Exit(code=0 if report.ok else 1)


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------


@app.command(name="compose")
def compose_command(
    paths: list[Path] = typer.Argument(..., metavar="PATHS"),
    json_out: bool = typer.Option(
        False, "--json", help="Emit a JSON description of the composition."
    ),
) -> None:
    """Cross-check a set of capsules and report missing/conflicting dependencies."""
    capsules = _resolve_capsules(paths)
    comp = compose_capsules(capsules)

    if json_out:
        out = {
            "ok": comp.ok,
            "capsules": [
                {"name": c.name, "version": c.capsule.version, "path": str(c.path)}
                for c in comp.capsules
            ],
            "issues": [
                {"capsule": i.capsule, "severity": i.severity, "message": i.message}
                for i in comp.issues
            ],
        }
        print(json.dumps(out, indent=2))
        raise typer.Exit(code=0 if comp.ok else 1)

    console.print(f"[bold]Composition:[/bold] {len(comp.capsules)} capsules")
    for c in comp.capsules:
        console.print(f"  • {c.name} v{c.capsule.version}  [dim]{c.path}[/dim]")

    if comp.issues:
        console.print()
        for i in comp.issues:
            tag = "[red]error[/red]" if i.severity == "error" else "[yellow]warn[/yellow]"
            console.print(f"  {tag}  {i.capsule}: {i.message}")
    else:
        console.print("[green]no issues[/green]")

    raise typer.Exit(code=0 if comp.ok else 1)


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@app.command()
def graph(
    paths: list[Path] = typer.Argument(..., metavar="PATHS"),
    fmt: str = typer.Option("text", "--format", "-f", help="text | dot"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout."
    ),
) -> None:
    """Render the capsule dependency graph."""
    capsules = _resolve_capsules(paths)
    comp = compose_capsules(capsules)
    if fmt == "text":
        rendered = render_text(comp)
    elif fmt == "dot":
        rendered = render_dot(comp)
    else:
        err_console.print(f"[red]unknown format '{fmt}'. Use 'text' or 'dot'.[/red]")
        raise typer.Exit(code=2)

    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
        console.print(
            f"[green]wrote[/green] {output} ({len(rendered)} chars, format={fmt})"
        )
    else:
        print(rendered)


# ---------------------------------------------------------------------------
# bundle
# ---------------------------------------------------------------------------


@app.command()
def bundle(
    paths: list[Path] = typer.Argument(..., metavar="PATHS"),
    for_target: str = typer.Option(
        "claude",
        "--for",
        help="Output target: claude | codex | agents | github | prompt",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout."
    ),
) -> None:
    """Render the composed capsules as an agent-ready document."""
    capsules = _resolve_capsules(paths)
    comp = compose_capsules(capsules)
    try:
        ordered = topo_order(comp)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        text = render_bundle(for_target, ordered)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if output:
        output.write_text(text, encoding="utf-8")
        console.print(
            f"[green]wrote[/green] {output} ({len(text)} chars, target={for_target})"
        )
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


@app.command()
def pull(
    address: str = typer.Argument(..., help="capsule://<owner>/<name>[@<version>]"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-clone even if cached."),
) -> None:
    """Resolve + fetch a capsule via the registry; print its local path."""
    try:
        addr = parse_address(address)
        path = pull_capsule(addr, refresh=refresh)
    except CapsuleClientError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]pulled[/green] {addr}  →  {path}")


# ---------------------------------------------------------------------------
# man
# ---------------------------------------------------------------------------


@app.command()
def man(
    target: str = typer.Argument(..., help="capsule://<owner>/<name>[@<v>] or local path"),
) -> None:
    """Render a single capsule's man page to the terminal."""
    try:
        lc = _load_one(target)
    except (CapsuleClientError, CapsuleLoadError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    render_man(lc.capsule, console)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    target: str = typer.Argument(..., help="capsule://<owner>/<name>[@<v>] or local path"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """One-screen snapshot: version, contract surface, env satisfaction, handoff."""
    addr_str: str | None = None
    try:
        if _looks_like_address(target):
            addr_str = str(parse_address(target))
        lc = _load_one(target)
    except (CapsuleClientError, CapsuleLoadError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    report = build_status(lc, address=addr_str)
    if json_out:
        print(json.dumps(to_json_dict(report), indent=2))
    else:
        print_status(report, console)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command(name="diff")
def diff_command(
    a: str = typer.Argument(..., help="First capsule (address or path)."),
    b: str = typer.Argument(..., help="Second capsule (address or path)."),
    fmt: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="text | markdown | json",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout."
    ),
) -> None:
    """Show what changed between two capsule versions.

    Compares handoff, invariants, contracts and dependencies — the things
    that change an agent's understanding of the subsystem. Not a code diff;
    use `git diff` for that.
    """
    try:
        lc_a = _load_one(a)
        lc_b = _load_one(b)
    except (CapsuleClientError, CapsuleLoadError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    d = compute_diff(lc_a.capsule, lc_b.capsule)

    if fmt == "text":
        rendered = diff_text(d)
    elif fmt == "markdown":
        rendered = diff_markdown(d)
    elif fmt == "json":
        rendered = json.dumps(diff_json(d), indent=2) + "\n"
    else:
        err_console.print(f"[red]unknown format '{fmt}'. Use text | markdown | json.[/red]")
        raise typer.Exit(code=2)

    if output:
        output.write_text(rendered, encoding="utf-8")
        console.print(
            f"[green]wrote[/green] {output} ({len(rendered)} chars, format={fmt})"
        )
    else:
        sys.stdout.write(rendered)
    raise typer.Exit(code=0 if d.empty else 0)  # diff is informational, not a check


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


@app.command()
def push(
    directory: Path = typer.Argument(
        Path("."),
        help="Directory containing capsule.yaml (default: current dir).",
    ),
    git_url: Optional[str] = typer.Option(
        None, "--git-url", help="Override the inferred github.com URL."
    ),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Override the inferred git ref (branch/tag/sha)."
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="Override CAPSULE_TOKEN / `gh auth token`."
    ),
) -> None:
    """Publish a capsule to the registry.

    Auth is the existing `gh` CLI's token (or CAPSULE_TOKEN env var, or
    --token). The server validates the token against api.github.com/user
    and only accepts pushes whose owner matches the GitHub username.
    """
    try:
        lc = load(directory)
    except CapsuleLoadError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        result = push_capsule(lc, git_url=git_url, ref=ref, token=token)
    except PushError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]pushed[/green] {result.address}\n"
        f"  source: {result.git_url}@{result.ref}  {result.path}\n"
        f"  view:   {result.view_url}"
    )


# ---------------------------------------------------------------------------
# generate-tests
# ---------------------------------------------------------------------------


@app.command(name="generate-tests")
def generate_tests_command(
    target: str = typer.Argument(..., help="capsule://<owner>/<name>[@<v>] or local path"),
    model: str = typer.Option(
        "claude-haiku-4-5-20251001",
        "--model",
        help="Anthropic model to use.",
    ),
) -> None:
    """Draft pytest scaffolds from the capsule's invariants (calls Claude API).

    Requires ANTHROPIC_API_KEY in the environment. Output goes to
    <capsule_dir>/tests/ai_generated/<name>_<timestamp>.py — pytest-skipped
    stubs the human implements.
    """
    try:
        lc = _load_one(target)
    except (CapsuleClientError, CapsuleLoadError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        result = generate_tests(lc.capsule, capsule_dir=lc.root, model=model)
    except AIGenError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]wrote[/green] {result.file_path}\n"
        f"  {result.invariant_count} invariant(s) → {result.bytes_written} bytes  "
        f"(model={result.model})"
    )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    port: int = typer.Option(8788, "--port", "-p", help="Port for wrangler pages dev."),
    server_dir: Path = typer.Option(
        Path("server"),
        "--server-dir",
        help="Directory containing the Pages project (default: ./server).",
    ),
) -> None:
    """Run the registry server locally (shells out to `wrangler pages dev`)."""
    target = server_dir.expanduser().resolve()
    if not (target / "wrangler.toml").exists():
        err_console.print(
            f"[red]no wrangler.toml at {target}[/red].  "
            f"Run from a checkout of the capsule repo, or use --server-dir."
        )
        raise typer.Exit(code=1)
    npx = shutil.which("npx")
    if not npx:
        err_console.print("[red]npx is not on PATH[/red]. Install Node.js 18+.")
        raise typer.Exit(code=1)
    console.print(
        f"[green]starting[/green] wrangler pages dev {target} on port {port}\n"
        f"   (set CAPSULE_REGISTRY=http://127.0.0.1:{port} in another shell)"
    )
    try:
        subprocess.run(
            [npx, "wrangler", "pages", "dev", str(target), "--port", str(port), "--ip", "127.0.0.1"],
            check=False,
        )
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _looks_like_address(s: str) -> bool:
    return s.startswith("capsule://") or (
        "/" in s
        and not s.startswith(".")
        and not s.startswith("/")
        and not re.match(r"^[a-zA-Z]:", s)  # not a Windows drive path
        and not Path(s).exists()
    )


def _load_one(target: str) -> LoadedCapsule:
    """Accept either a `capsule://` address or a local file/dir path."""
    if _looks_like_address(target):
        addr = parse_address(target)
        path = pull_capsule(addr)
        return load(path)
    return load(Path(target))


def _resolve_capsules(paths: list[Path]) -> list[LoadedCapsule]:
    """Accept a mix of files, capsule dirs, and parent dirs (auto-discover)."""
    out: list[LoadedCapsule] = []
    seen: set[Path] = set()
    for p in paths:
        try:
            resolved = p.expanduser().resolve()
            if resolved.is_dir() and not (resolved / "capsule.yaml").exists():
                found = discover(resolved)
                if not found:
                    raise CapsuleLoadError(f"no capsules found under {resolved}")
                for lc in found:
                    if lc.path not in seen:
                        out.append(lc)
                        seen.add(lc.path)
            else:
                lc = load(resolved)
                if lc.path not in seen:
                    out.append(lc)
                    seen.add(lc.path)
        except CapsuleLoadError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
    if not out:
        err_console.print("[red]no capsules to operate on[/red]")
        raise typer.Exit(code=1)
    return out


def _color_status(s: Status) -> str:
    return {
        Status.PASS: "[green]pass[/green]",
        Status.FAIL: "[red]fail[/red]",
        Status.TIMEOUT: "[yellow]timeout[/yellow]",
        Status.SKIPPED: "[blue]skipped[/blue]",
        Status.ERROR: "[yellow]error[/yellow]",
    }[s]


if __name__ == "__main__":
    app()
