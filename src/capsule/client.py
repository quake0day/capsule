"""Client for the capsule registry server: resolve, fetch, cache.

The cache lives under `~/.capsule/cache/` and is content-addressed by commit
SHA (or, for the lightweight v0.2, by the resolved ref string — once we add
a /api/v1/resolve response that includes the commit SHA, we can tighten
this).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REGISTRY = "http://127.0.0.1:8788"
CACHE_ROOT = Path.home() / ".capsule" / "cache"

ADDR_RE = re.compile(r"^(?:capsule://)?([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9-]*)(?:@(.+))?$", re.IGNORECASE)


class CapsuleClientError(Exception):
    """Anything that goes wrong talking to the registry or git."""


@dataclass(frozen=True)
class Address:
    owner: str
    name: str
    version: str | None = None

    def __str__(self) -> str:
        v = f"@{self.version}" if self.version else ""
        return f"capsule://{self.owner}/{self.name}{v}"


@dataclass(frozen=True)
class Resolved:
    owner: str
    name: str
    version: str
    git_url: str
    ref: str
    path: str
    raw_url: str | None


def parse_address(s: str) -> Address:
    m = ADDR_RE.match(s.strip())
    if not m:
        raise CapsuleClientError(
            f"invalid capsule address '{s}'. Expected capsule://<owner>/<name>[@<version>]."
        )
    return Address(owner=m.group(1), name=m.group(2), version=m.group(3))


def registry_base() -> str:
    return os.environ.get("CAPSULE_REGISTRY", DEFAULT_REGISTRY).rstrip("/")


def resolve(addr: Address) -> Resolved:
    """Ask the registry server to translate an address into a git source."""
    v = f"@{addr.version}" if addr.version else ""
    url = f"{registry_base()}/api/v1/resolve/{addr.owner}/{addr.name}{v}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            msg = payload.get("error", str(exc))
        except Exception:
            msg = str(exc)
        raise CapsuleClientError(f"registry returned {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise CapsuleClientError(
            f"could not reach registry at {registry_base()}: {exc.reason}. "
            f"Set CAPSULE_REGISTRY or run `capsule serve` in another shell."
        ) from exc
    return Resolved(
        owner=body["owner"],
        name=body["name"],
        version=body["version"],
        git_url=body["git_url"],
        ref=body["ref"],
        path=body["path"],
        raw_url=body.get("raw_url"),
    )


def pull(addr: Address, *, refresh: bool = False) -> Path:
    """Resolve + cache the capsule. Returns the local path to capsule.yaml.

    Strategy: shallow clone the whole repo (or pull updates if cached) into
    `~/.capsule/cache/<owner>__<name>__<version>/`, then return the path to
    the specific capsule.yaml inside.
    """
    r = resolve(addr)
    # Cache key includes a hash of (git_url, ref, path) so that re-pointing
    # the registry at a different source invalidates automatically instead
    # of silently reusing a stale clone.
    fingerprint = hashlib.sha256(
        f"{r.git_url}\n{r.ref}\n{r.path}".encode("utf-8")
    ).hexdigest()[:12]
    cache_dir = CACHE_ROOT / f"{r.owner}__{r.name}__{r.version}__{fingerprint}"
    repo_dir = cache_dir / "repo"
    capsule_path = repo_dir / r.path

    if cache_dir.exists() and refresh:
        shutil.rmtree(cache_dir)

    cache_dir.mkdir(parents=True, exist_ok=True)
    if not repo_dir.exists():
        _git(["clone", "--depth", "1", "--branch", r.ref, r.git_url, str(repo_dir)], check_msg=f"clone {r.git_url}@{r.ref}")
    elif refresh:
        # We removed the dir above; this branch is unreachable unless something
        # else races us. Keep for safety.
        _git(["clone", "--depth", "1", "--branch", r.ref, r.git_url, str(repo_dir)])

    if not capsule_path.exists():
        raise CapsuleClientError(
            f"resolved capsule path does not exist after clone: {capsule_path}"
        )
    return capsule_path


def _git(args: list[str], *, check_msg: str | None = None) -> None:
    try:
        subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise CapsuleClientError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        what = check_msg or " ".join(args)
        raise CapsuleClientError(
            f"git failed ({what}): {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc
