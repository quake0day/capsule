"""capsule reconstruct — assemble a system from code-bundled capsules.

Each capsule in a "reconstructable" set carries three things:

  1. capsule.yaml         the contract (purpose / interfaces / invariants / ...)
  2. install.json         the per-capsule install plan (file mapping + injections)
  3. src/                 the actual implementation files

`capsule reconstruct` walks every capsule, applies its install.json against
an output directory, performs declared data injections and template
substitutions, and writes a complete runnable site.

The mechanical mode (this module) is intentionally deterministic — same
inputs, byte-identical output. The AI-driven mode (capsule.aigen_recon)
sits on top of it: it lets the user pass `--prompt "..."` to ask Claude
to *customize* the output after the mechanical assembly.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ReconstructError(Exception):
    """Anything that goes wrong assembling the output."""


@dataclass
class InstallFile:
    from_: str
    to: str


@dataclass
class DataInjection:
    file: str
    marker: str
    replace_with: str
    format: str = "json-pretty"


@dataclass
class TemplateSub:
    file: str
    vars: dict[str, dict[str, Any]]  # var → { default, from_arg }


@dataclass
class InstallPlan:
    files: list[InstallFile]
    data_injections: list[DataInjection] = field(default_factory=list)
    template_substitutions: list[TemplateSub] = field(default_factory=list)
    env_required: list[str] = field(default_factory=list)


@dataclass
class CapsuleSource:
    name: str
    root: Path  # directory holding capsule.yaml + install.json + src/
    plan: InstallPlan


@dataclass
class ReconstructResult:
    capsules: list[str]
    files_written: int
    out: Path
    env_required: list[str]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# discovery + parsing
# ---------------------------------------------------------------------------


def discover_capsules(root: Path) -> list[CapsuleSource]:
    """Find every dir under `root` that has both capsule.yaml and install.json."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ReconstructError(f"--from must be a directory: {root}")
    out: list[CapsuleSource] = []
    for capsule_yaml in sorted(root.rglob("capsule.yaml")):
        cdir = capsule_yaml.parent
        install_json = cdir / "install.json"
        if not install_json.exists():
            continue
        try:
            spec = yaml.safe_load(capsule_yaml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ReconstructError(f"{capsule_yaml}: YAML parse error: {exc}") from exc
        name = spec.get("name") or cdir.name
        plan = _parse_install_json(install_json)
        out.append(CapsuleSource(name=name, root=cdir, plan=plan))
    if not out:
        raise ReconstructError(
            f"no reconstructable capsules under {root} "
            f"(need both capsule.yaml and install.json in each dir)"
        )
    return out


def _parse_install_json(path: Path) -> InstallPlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReconstructError(f"{path}: invalid JSON: {exc}") from exc

    files = [
        InstallFile(from_=f["from"], to=f["to"])
        for f in raw.get("files", [])
        if isinstance(f, dict) and "from" in f and "to" in f
    ]
    injections = [
        DataInjection(
            file=di["file"],
            marker=di["marker"],
            replace_with=di["replace_with"],
            format=di.get("format", "json-pretty"),
        )
        for di in raw.get("data_injections", [])
    ]
    tsubs = [
        TemplateSub(file=ts["file"], vars=ts.get("vars", {}))
        for ts in raw.get("template_substitutions", [])
    ]
    return InstallPlan(
        files=files,
        data_injections=injections,
        template_substitutions=tsubs,
        env_required=list(raw.get("env_required", [])),
    )


# ---------------------------------------------------------------------------
# reconstruction
# ---------------------------------------------------------------------------


def reconstruct(
    capsules_dir: Path,
    out_dir: Path,
    *,
    data: dict | None = None,
    template_args: dict[str, str] | None = None,
    clean: bool = False,
) -> ReconstructResult:
    """Assemble a site from code-bundled capsules into out_dir."""
    capsules = discover_capsules(capsules_dir)
    out_dir = out_dir.expanduser().resolve()

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files_written = 0
    warnings: list[str] = []
    env_required: set[str] = set()

    for c in capsules:
        for f in c.plan.files:
            src = (c.root / f.from_).resolve()
            dst = (out_dir / f.to).resolve()
            if not src.exists():
                raise ReconstructError(
                    f"{c.name}: install.json references missing file {f.from_} "
                    f"(absolute: {src})"
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            files_written += 1

        for inj in c.plan.data_injections:
            if data is None:
                warnings.append(
                    f"{c.name}: data_injection on {inj.file} requested but no --data "
                    f"was supplied; the marker {inj.marker!r} was left in place."
                )
                continue
            dst = (out_dir / inj.file).resolve()
            if not dst.exists():
                raise ReconstructError(
                    f"{c.name}: data_injection target {inj.file} not produced by files[]"
                )
            _apply_data_injection(dst, inj, data)

        for tsub in c.plan.template_substitutions:
            dst = (out_dir / tsub.file).resolve()
            if not dst.exists():
                raise ReconstructError(
                    f"{c.name}: template_substitution target {tsub.file} not produced by files[]"
                )
            _apply_template_subs(dst, tsub, template_args or {})

        env_required.update(c.plan.env_required)

    return ReconstructResult(
        capsules=[c.name for c in capsules],
        files_written=files_written,
        out=out_dir,
        env_required=sorted(env_required),
        warnings=warnings,
    )


def _apply_data_injection(file: Path, inj: DataInjection, data: dict) -> None:
    text = file.read_text(encoding="utf-8")
    if inj.marker not in text:
        raise ReconstructError(
            f"{file}: marker {inj.marker!r} not found "
            f"(needed for data_injections.replace_with='{inj.replace_with}')"
        )
    if inj.replace_with != "${data}":
        raise ReconstructError(
            f"{file}: unsupported replace_with '{inj.replace_with}' "
            f"(only ${{data}} is supported in v1)"
        )
    if inj.format == "json-pretty":
        rendered = json.dumps(data, indent=2, ensure_ascii=False)
    elif inj.format == "json-compact":
        rendered = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        raise ReconstructError(f"{file}: unsupported injection format '{inj.format}'")
    file.write_text(text.replace(inj.marker, rendered), encoding="utf-8")


def _apply_template_subs(file: Path, tsub: TemplateSub, args: dict[str, str]) -> None:
    text = file.read_text(encoding="utf-8")
    for var, spec in tsub.vars.items():
        from_arg = spec.get("from_arg")
        default = spec.get("default", "")
        value = (args.get(from_arg) if from_arg else None) or default
        text = text.replace("${" + var + "}", str(value))
    file.write_text(text, encoding="utf-8")
