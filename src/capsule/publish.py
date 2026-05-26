"""Publish a directory of decomposed capsules: github + registry, one shot.

Used by `capsule decompose --register <repo>`. Given a local directory of
already-materialized capsules, this:

  1. Initialises git in the directory (if not already a repo).
  2. Creates a public github repo at <gh-user>/<repo>.
  3. Commits + pushes the capsules there (so the registry server can
     fetch them via raw.githubusercontent.com).
  4. For each capsule subdirectory, calls capsule.push.push() which
     hits the registry's PUT /api/v1/capsules endpoint and registers
     the entry in KV.

If the github repo already exists, the local directory is force-aligned
to the existing remote (same repo name, same owner). No destructive
operations on the user's other repos.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from capsule.loader import load as load_capsule
from capsule.push import PushError, push as push_capsule


class PublishError(Exception):
    """Anything that goes wrong creating the repo or pushing entries."""


@dataclass
class PublishResult:
    github_url: str
    branch: str
    capsules_pushed: list[str] = field(default_factory=list)
    capsules_failed: list[tuple[str, str]] = field(default_factory=list)


def publish(
    capsules_dir: Path,
    repo_name: str,
    *,
    source_note: str = "",
    initial_commit_message: str | None = None,
    force: bool = False,
) -> PublishResult:
    """Create the github repo + push + register every capsule.

    Idempotent on retry. If `force` is True (typically because the caller
    used --clean), the push uses --force-with-lease so a fresh local
    history can replace a stale remote that we created on a previous run.
    """
    capsules_dir = capsules_dir.expanduser().resolve()
    if not capsules_dir.is_dir():
        raise PublishError(f"--out directory does not exist: {capsules_dir}")

    if not shutil.which("git"):
        raise PublishError("git is not installed or not on PATH")
    if not shutil.which("gh"):
        raise PublishError(
            "gh CLI is not installed. Required for `--register` (used to create + push the github repo). "
            "Install from https://cli.github.com/."
        )

    gh_login = _gh_user()
    if not gh_login:
        raise PublishError("`gh auth status` failed — run `gh auth login` first.")

    github_url = f"https://github.com/{gh_login}/{repo_name}"

    _init_git_if_needed(capsules_dir)
    _ensure_remote(capsules_dir, github_url, repo_name)
    branch = _ensure_branch(capsules_dir)

    msg = initial_commit_message or (
        f"Capsules decomposed from {source_note}".rstrip() if source_note
        else "Initial commit of decomposed capsules"
    )
    _stage_and_commit(capsules_dir, msg)
    _push(capsules_dir, branch, force=force)

    # Now register every capsule in the registry.
    result = PublishResult(github_url=github_url, branch=branch)
    for child in sorted(capsules_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(("_", ".")):
            continue
        capsule_yaml = child / "capsule.yaml"
        if not capsule_yaml.is_file():
            continue
        try:
            lc = load_capsule(capsule_yaml)
        except Exception as exc:
            result.capsules_failed.append((child.name, f"load failed: {exc}"))
            continue
        try:
            push_capsule(lc, ref=branch)
        except PushError as exc:
            result.capsules_failed.append((child.name, str(exc)))
            continue
        result.capsules_pushed.append(child.name)

    return result


# ---------------------------------------------------------------------------
# git / gh helpers
# ---------------------------------------------------------------------------


def _gh_user() -> str | None:
    """Return the github login of the currently-authenticated gh user, or None."""
    try:
        out = subprocess.run(
            ["gh", "api", "/user", "-q", ".login"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    login = out.stdout.strip()
    return login or None


def _init_git_if_needed(dir: Path) -> None:
    if (dir / ".git").is_dir():
        return
    _git(dir, ["init", "-b", "main"], errmsg="git init failed")


def _ensure_remote(dir: Path, github_url: str, repo_name: str) -> None:
    """Set origin to github_url; create the repo via gh if it does not exist."""
    # If `origin` already points at our target, nothing to do.
    current = _git(dir, ["remote", "get-url", "origin"], check=False)
    if current and current.strip() == github_url:
        return
    if current:
        # Replace any existing origin with ours (we own this directory).
        _git(dir, ["remote", "set-url", "origin", github_url], errmsg="git remote set-url failed")
    else:
        _git(dir, ["remote", "add", "origin", github_url], errmsg="git remote add failed")

    # If the repo does not exist on github, create it.
    if not _gh_repo_exists(github_url):
        try:
            subprocess.run(
                ["gh", "repo", "create", repo_name,
                 "--public",
                 "--description", "Decomposed by capsule decompose.",
                 "--confirm"],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise PublishError(
                f"`gh repo create {repo_name}` failed:\n  {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc


def _gh_repo_exists(github_url: str) -> bool:
    # Extract owner/name from URL.
    parts = github_url.rstrip("/").split("/")
    if len(parts) < 5:
        return False
    owner_repo = f"{parts[-2]}/{parts[-1]}"
    out = subprocess.run(
        ["gh", "repo", "view", owner_repo],
        capture_output=True, text=True, check=False,
    )
    return out.returncode == 0


def _ensure_branch(dir: Path) -> str:
    """Switch to (or create) `main`, return the branch name actually in use."""
    # If a branch is already checked out, use it.
    current = _git(dir, ["branch", "--show-current"], check=False)
    branch = (current or "").strip()
    if branch:
        return branch
    # No branch yet (fresh `git init`). Force the initial branch to main.
    _git(dir, ["checkout", "-b", "main"], check=False)
    return "main"


def _stage_and_commit(dir: Path, message: str) -> None:
    _git(dir, ["add", "-A"], errmsg="git add failed")
    # Check whether there's anything to commit. If not, this is a no-op.
    status = _git(dir, ["status", "--porcelain"], check=False) or ""
    if not status.strip():
        return
    try:
        subprocess.run(
            ["git", "-C", str(dir), "commit", "-m", message],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        # "nothing to commit" is fine; anything else is a real error.
        stderr = (exc.stderr or "").lower()
        if "nothing to commit" in stderr or "working tree clean" in stderr:
            return
        raise PublishError(
            f"git commit failed:\n  {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc


def _push(dir: Path, branch: str, *, force: bool = False) -> None:
    args = ["git", "-C", str(dir), "push", "-u", "origin", branch]
    if force:
        # The caller passed --clean; we want to overwrite the remote with
        # the fresh local history. --force-with-lease requires a fetch
        # baseline that a freshly-init'd repo doesn't have, so plain --force
        # is the right semantic here.
        args.append("--force")
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise PublishError(
            f"git push failed:\n  {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc


def _git(dir: Path, args: list[str], *, check: bool = True, errmsg: str | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(dir), *args],
            check=check, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        if errmsg:
            raise PublishError(
                f"{errmsg}:\n  {exc.stderr.strip() or exc.stdout.strip()}"
            ) from exc
        return None
    return out.stdout
