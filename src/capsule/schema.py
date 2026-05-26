"""Pydantic models for capsule.yaml (spec v0.1).

The models mirror SPEC.md one-to-one. Validation messages are deliberately
plain so they surface well in CLI output.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION = "capsule.dev/v0.1"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")

CapsuleType = Literal["subsystem", "adapter", "template", "bundle", "library"]


class _Strict(BaseModel):
    """Base that rejects unknown keys unless they start with x-."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Maintainer(_Strict):
    name: str
    email: str | None = None


class Purpose(_Strict):
    summary: str
    owns: list[str] = Field(default_factory=list)
    does_not_own: list[str] = Field(default_factory=list)


class InterfaceProvides(_Strict):
    kind: str
    name: str
    spec: str | None = None
    entrypoint: str | None = None
    payload_schema: str | None = None
    description: str | None = None


class InterfaceRequires(_Strict):
    kind: str
    name: str
    from_capsule: str | None = None
    version: str | None = None
    description: str | None = None

    @field_validator("from_capsule")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is not None and not NAME_RE.match(v):
            raise ValueError(
                f"from_capsule '{v}' must be kebab-case (lowercase letters, digits, hyphens)"
            )
        return v


class Interfaces(_Strict):
    provides: list[InterfaceProvides] = Field(default_factory=list)
    requires: list[InterfaceRequires] = Field(default_factory=list)


class CapsuleDependency(_Strict):
    name: str
    version: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(f"dependency name '{v}' must be kebab-case")
        return v


class Dependencies(_Strict):
    capsules: list[CapsuleDependency] = Field(default_factory=list)
    runtime: list[dict[str, str]] = Field(default_factory=list)


class ExtensionPoint(_Strict):
    id: str
    where: str
    contract: str


class AgentContext(_Strict):
    summary_for_ai: str | None = None
    extension_points: list[ExtensionPoint] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)


class Check(_Strict):
    id: str
    command: str
    timeout_seconds: int = 60
    proves: list[str] = Field(default_factory=list)
    requires_capsules: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("check id must be non-empty")
        return v


class Verification(_Strict):
    health_checks: list[Check] = Field(default_factory=list)
    functional_tests: list[Check] = Field(default_factory=list)
    integration_tests: list[Check] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)


class CompatibilityEntry(_Strict):
    capsule: str
    versions: str
    verification: str | None = None


class Compatibility(_Strict):
    tested_with: list[CompatibilityEntry] = Field(default_factory=list)


class Handoff(_Strict):
    generated_at: datetime | None = None
    generated_by: str | None = None
    objective: str
    completed: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_agent_should: list[str] = Field(default_factory=list)
    do_not_touch: list[str] = Field(default_factory=list)


class Capsule(BaseModel):
    """Top-level capsule.yaml document."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    apiVersion: str
    kind: Literal["Capsule"]

    name: str
    version: str
    type: CapsuleType
    domain: str | None = None
    maintainers: list[Maintainer] = Field(default_factory=list)

    purpose: Purpose
    interfaces: Interfaces = Field(default_factory=Interfaces)
    dependencies: Dependencies = Field(default_factory=Dependencies)
    agent: AgentContext = Field(default_factory=AgentContext)
    verification: Verification = Field(default_factory=Verification)
    compatibility: Compatibility = Field(default_factory=Compatibility)
    handoff: Handoff | None = None

    @field_validator("apiVersion")
    @classmethod
    def _check_api(cls, v: str) -> str:
        if v != API_VERSION:
            raise ValueError(f"apiVersion must be '{API_VERSION}', got '{v}'")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError(
                f"name '{v}' must be kebab-case (lowercase letters, digits, hyphens)"
            )
        return v

    @field_validator("version")
    @classmethod
    def _check_semver(cls, v: str) -> str:
        try:
            Version(v)
        except InvalidVersion as exc:
            raise ValueError(f"version '{v}' is not valid semver: {exc}") from exc
        return v

    @model_validator(mode="after")
    def _reject_unknown_toplevel(self) -> "Capsule":
        # Pydantic with extra="allow" captures unknown keys in __pydantic_extra__.
        # We accept x-prefixed extensions; everything else is an error.
        extras = self.__pydantic_extra__ or {}
        bad = [k for k in extras if not k.startswith("x-")]
        if bad:
            raise ValueError(
                "unknown top-level keys (use x- prefix for extensions): " + ", ".join(bad)
            )
        return self


def warnings_for(c: Capsule) -> list[str]:
    """Non-fatal warnings, surfaced by `capsule validate`."""
    out: list[str] = []
    if not c.agent.summary_for_ai:
        out.append("agent.summary_for_ai is empty — AI consumers will fall back to purpose.summary")
    if (
        not c.verification.health_checks
        and not c.verification.functional_tests
        and not c.verification.integration_tests
    ):
        out.append("no verification checks defined — capsule cannot prove it works")
    if c.handoff and c.handoff.generated_at:
        age = (datetime.now(c.handoff.generated_at.tzinfo) - c.handoff.generated_at).days
        if age > 14:
            out.append(f"handoff is {age} days old — consider regenerating")
    return out


def from_dict(data: dict[str, Any]) -> Capsule:
    """Validate a raw dict (as parsed from YAML) into a Capsule model."""
    return Capsule.model_validate(data)
