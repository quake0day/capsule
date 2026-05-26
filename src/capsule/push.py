"""capsule push — publish a capsule to the registry.

v0.3 reuses the user's existing `gh` CLI auth as the credential source:

  1. CAPSULE_TOKEN env var (explicit override)
  2. `gh auth token` (if `gh` is installed and logged in)

The token is sent to the server's PUT endpoint with Authorization: Bearer.
The server validates the token against api.github.com/user, confirms the
authenticated user matches the capsule's owner namespace, fetches the
proposed capsule.yaml from the supplied git source, and writes the entry
into KV.

push tells the server WHERE the capsule.yaml lives in git — not the bytes.
That keeps the registry a naming layer over git, not a content host.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from capsule.client import registry_base
from capsule.loader import LoadedCapsule


class PushError(Exception):
    """Anything that goes wrong publishing a capsule."""


@dataclass
class PushSource:
    """Where the capsule.yaml lives in git."""
    git_url: str
    ref: str
    path: str  # repo-relative


@dataclass
class PushResult:
    address: str
    git_url: str
    ref: str
    path: str
    view_url: str


def push(
    lc: LoadedCapsule,
    *,
    git_url: str | None = None,
    ref: str | None = None,
    token: str | None = None,
    private: bool = False,
) -> PushResult:
    """Publish a loaded capsule to the registry.

    git_url / ref are inferred from the local git checkout if not given.
    path is computed as the capsule.yaml's path relative to the repo root.
    If `private` is True, the entry is registered with visibility=private
    and subsequent reads require Authorization with a token that can read
    the source repo.
    """
    source = _resolve_source(lc.path, git_url=git_url, ref=ref)
    creds = token or _find_token()
    if not creds:
        raise PushError(
            "no auth token found. Tried:\n"
            "  - CAPSULE_TOKEN env var\n"
            "  - `gh auth token` (gh CLI not installed or not logged in)\n"
            "Set one of them, or pass --token."
        )

    # v0.3 convention: the namespace == the GitHub login of the pushing
    # user. The server enforces this — its only auth model is "you can push
    # under your own gh username." (Org namespaces are a v0.4 problem.)
    owner = _gh_login(creds)
    if not owner:
        raise PushError(
            "could not determine your GitHub username from the token. "
            "Run `gh auth status` to verify your auth is valid."
        )

    name = lc.capsule.name
    version = lc.capsule.version
    address = f"capsule://{owner}/{name}@{version}"
    url = f"{registry_base()}/api/v1/capsules/{owner}/{name}@{version}"
    body_dict = {
        "git_url": source.git_url,
        "ref": source.ref,
        "path": source.path,
    }
    if private:
        body_dict["visibility"] = "private"
    body = json.dumps(body_dict).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {creds}",
            "Content-Type": "application/json",
            "User-Agent": "capsule-cli/0.3 (+https://github.com/quake0day/capsule)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            msg = err_body.get("error", str(exc))
        except Exception:
            msg = f"{exc.code} {exc.reason}"
        raise PushError(f"registry rejected push: {msg}") from exc
    except urllib.error.URLError as exc:
        raise PushError(
            f"could not reach registry at {registry_base()}: {exc.reason}"
        ) from exc

    return PushResult(
        address=payload.get("address", address),
        git_url=source.git_url,
        ref=source.ref,
        path=source.path,
        view_url=f"{registry_base()}{payload.get('view_url', '')}",
    )


# ---------------------------------------------------------------------------
# git source detection
# ---------------------------------------------------------------------------


def _resolve_source(
    capsule_yaml_path: Path,
    *,
    git_url: str | None,
    ref: str | None,
) -> PushSource:
    """Figure out (git_url, ref, repo-relative path) for a capsule.yaml."""
    repo_root = _git_dir(capsule_yaml_path)
    if not repo_root:
        if not git_url:
            raise PushError(
                f"{capsule_yaml_path} is not inside a git repository. "
                f"Either move it into one and commit, or pass --git-url + --ref + --path."
            )
        if not ref:
            raise PushError("--git-url given without --ref")
        # No repo, so caller-supplied path is taken as-is.
        return PushSource(git_url=git_url, ref=ref, path=str(capsule_yaml_path).replace("\\", "/"))

    git_url = git_url or _git_remote(repo_root)
    if not git_url:
        raise PushError(
            f"could not find a github.com remote in {repo_root}. "
            f"Set one (`git remote add origin https://github.com/...`) or pass --git-url."
        )

    ref = ref or _git_branch(repo_root) or "main"
    rel = capsule_yaml_path.resolve().relative_to(repo_root.resolve())
    return PushSource(git_url=git_url, ref=ref, path=str(rel).replace("\\", "/"))


def _git_dir(path: Path) -> Path | None:
    p = path.resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git_remote(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    url = out.stdout.strip()
    if not url:
        return None
    # Normalise SSH-form to HTTPS.
    m = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}"
    return url.rstrip("/")


def _git_branch(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    branch = out.stdout.strip()
    return branch if branch and branch != "HEAD" else None


# ---------------------------------------------------------------------------
# auth source detection
# ---------------------------------------------------------------------------


def _find_token() -> str | None:
    env = os.environ.get("CAPSULE_TOKEN")
    if env:
        return env.strip()
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        out = subprocess.run(
            [gh, "auth", "token"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        return None
    tok = out.stdout.strip()
    return tok or None


def _gh_login(token: str) -> str | None:
    """Resolve token → github username via api.github.com/user.

    We do this on the client too (not just the server) so we can produce a
    nice address string before pushing, and fail early on a bad token.
    """
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "capsule-cli/0.3",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    login = data.get("login")
    return login if isinstance(login, str) else None
