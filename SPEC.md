# Capsule Spec v0.1

> **Capsule = Context + Contract + Code + Verification**
>
> A capsule is a portable, AI-readable, self-verifying unit of a software system.
> It captures *what a subsystem is*, *what it promises*, *how to integrate with it*,
> *what its current work state is*, and *how to prove it still works*.

This document defines `capsule.yaml` — the on-disk format that every capsule
ships. The format is intentionally small in v0.1; it will grow with concrete
need, not speculation.

---

## 1. File location

A capsule lives in a directory. The directory MUST contain a file named
`capsule.yaml` at its root. Everything else in the directory (source code,
tests, fixtures, docs) is owned by the capsule.

```
my-capsule/
  capsule.yaml          # required
  README.md             # optional, human-facing
  src/                  # capsule's reference implementation (optional)
  tests/                # capsule's verification suite (optional)
```

A repository may contain many capsules at any depth. Tools discover capsules
by walking the tree for `capsule.yaml` files.

## 2. Top-level fields

```yaml
apiVersion: capsule.dev/v0.1   # required
kind: Capsule                  # required

name: lab-runtime-docker        # required, kebab-case, unique within a registry
version: 0.2.0                  # required, semver
type: subsystem                 # required, one of: subsystem | adapter | template | bundle
domain: education.ctf           # optional, dotted path for grouping
maintainers:                    # optional
  - name: Quake
    email: quake0day@gmail.com

purpose:        { ... }   # required
interfaces:     { ... }   # optional
dependencies:   { ... }   # optional
agent:          { ... }   # required (this is the AI-readable layer)
verification:   { ... }   # optional but strongly recommended
compatibility:  { ... }   # optional
handoff:        { ... }   # optional, current work-state for next agent
```

Unknown top-level keys are rejected by `capsule validate`. Forward-compatible
extension lives under `x-` prefixed keys.

## 3. `purpose`

What this capsule is for. Aimed at both humans and agents.

```yaml
purpose:
  summary: |
    Runs per-student lab containers on a single Docker host and exposes
    a browser-accessible terminal.
  owns:
    - container lifecycle (create / pause / destroy)
    - per-lab metadata persisted to disk
    - browser terminal proxy
  does_not_own:
    - authentication of students
    - scoring or grading
    - long-term storage of student artifacts
```

`owns` / `does_not_own` is what makes a capsule a *subsystem* rather than a
grab-bag. Agents use these lists to decide whether a change belongs in this
capsule or somewhere else.

## 4. `interfaces`

The contract surface. Two sides: what this capsule offers, and what it needs.

```yaml
interfaces:
  provides:
    - kind: http_api
      name: lab-control
      spec: openapi/lab-control.yaml          # path relative to capsule dir
    - kind: cli
      name: labctl
      entrypoint: bin/labctl
    - kind: event
      name: lab.created
      payload_schema: schemas/lab-created.json

  requires:
    - kind: http_api
      name: auth-introspect
      from_capsule: auth-core                 # symbolic reference
      version: ">=0.2 <1.0"
    - kind: env
      name: DOCKER_HOST
      description: Docker daemon socket; defaults to unix:///var/run/docker.sock
```

`kind` is open-ended in v0.1; common values: `http_api`, `cli`, `event`,
`mcp_resource`, `env`, `volume`, `library`. Tools should ignore kinds they do
not understand rather than error.

## 5. `dependencies`

Composition-level dependencies on other capsules and on the runtime.

```yaml
dependencies:
  capsules:
    - name: auth-core
      version: ">=0.2.0 <1.0.0"
    - name: postgres-storage
      version: ">=14"
  runtime:
    - docker: ">=24"
    - python: ">=3.11"
```

Capsule dependencies are *symbolic*, not URLs. Resolution to a concrete
capsule directory (or a registry pull) is the composer's job, not the
capsule's.

## 6. `agent` (the AI-readable layer)

This is the part that makes the capsule worth more than a README. It is
written for an LLM that has never seen the system before.

```yaml
agent:
  summary_for_ai: |
    You are looking at the lab-runtime capsule. It owns container lifecycle
    for student CTF labs. It does NOT own authentication — assume an
    upstream auth-core capsule has already authorized the caller and passed
    a verified student_id in the request context.

  extension_points:
    - id: lab-image-resolver
      where: src/runtime/images.py:resolve_image
      contract: |
        Given a (challenge_id, student_id), return a Docker image reference.
        Must be deterministic for a given challenge_id.

  avoid:
    - Do not call the Docker socket from request handlers directly;
      always go through src/runtime/docker_client.py.
    - Do not persist student secrets in lab metadata; metadata is world-
      readable to instructors.

  glossary:
    lab: a single container instance owned by exactly one student
    challenge: a reusable CTF problem definition; spawns labs
```

`summary_for_ai`, `avoid`, and `glossary` are the highest-leverage fields.
Capsule consumers (Claude Code, Codex, Cursor, MCP clients) lift these
straight into their system prompt via `capsule bundle`.

## 7. `verification`

What it means for this capsule to be "working right now". Declarative; each
entry is a shell command plus what it proves.

```yaml
verification:
  health_checks:
    - id: docker-daemon-reachable
      command: docker info
      timeout_seconds: 10

  functional_tests:
    - id: create-student-lab
      command: pytest tests/functional/test_create_lab.py -q
      proves:
        - A lab container can be created end-to-end.
        - The lab's browser terminal is reachable after creation.

  integration_tests:
    - id: auth-required-for-lab-create
      requires_capsules: [auth-core]
      command: pytest tests/integration/test_auth_required.py -q
      proves:
        - Unauthenticated requests are rejected before any container starts.

  invariants:
    - A lab instance must always belong to exactly one student.
    - A student must not access another student's lab container.
    - Destroying a lab must revoke all browser-terminal sessions for it.
    - Lab metadata must not contain plaintext secrets.
```

### Verification semantics

- A check **passes** if its command exits 0 within `timeout_seconds`
  (default 60s).
- A check **fails** if it exits non-zero, times out, or is not runnable
  (missing binary).
- `invariants` are not executed in v0.1 — they are human/AI-readable
  contracts that future verifiers (AI-generated regression tests) will
  consume.
- `integration_tests` MAY declare `requires_capsules`; the runner refuses
  to run them unless those capsules are part of the current composition.

### Report

`capsule verify` produces a structured report (JSON + pretty terminal
output) with one row per check: `id`, `status`, `duration_ms`, `stdout_tail`,
`stderr_tail`. Exit code is non-zero if any required check failed.

## 8. `compatibility`

Tested combinations with other capsules. Optional but valuable in a registry.

```yaml
compatibility:
  tested_with:
    - capsule: auth-core
      versions: ">=0.2.0 <1.0.0"
      verification: pytest tests/integration/test_auth_core.py -q
    - capsule: postgres-storage
      versions: ">=14"
      verification: pytest tests/integration/test_postgres.py -q
```

## 9. `handoff` (current work state)

The piece that makes a capsule *transferable mid-flight*. Optional — most
released capsules will not carry handoff state. Working capsules under
active development will.

```yaml
handoff:
  generated_at: 2026-05-24T08:00:00Z
  generated_by: claude-code@0.4.2
  objective: |
    Wire the lab-runtime capsule to emit lab.created events that the
    ai-report capsule can consume.
  completed:
    - Event schema drafted at schemas/lab-created.json.
    - Emitter stub in src/runtime/events.py.
  remaining:
    - Hook emitter into lifecycle.create_lab.
    - Add functional test that asserts event payload shape.
  open_questions:
    - Should events be fire-and-forget or persisted to an outbox table?
  next_agent_should:
    - Start at src/runtime/lifecycle.py:create_lab and emit the event
      after the container reaches RUNNING.
  do_not_touch:
    - src/runtime/docker_client.py — recently stabilised, no changes
      needed for this objective.
```

A capsule with no `handoff` block is assumed to be at rest; one with a
handoff block is "in progress" and the next agent should read it before
making changes.

## 10. Validation rules (v0.1)

`capsule validate` rejects a file if:

1. `apiVersion` is not `capsule.dev/v0.1`.
2. `kind` is not `Capsule`.
3. `name` is missing, empty, or contains characters outside `[a-z0-9-]`.
4. `version` does not parse as semver.
5. `type` is not one of the documented values.
6. Required sections (`purpose`, `agent`) are missing.
7. A verification check is missing `id` or `command`.
8. An `interfaces.requires[].from_capsule` references a name that is not a
   valid capsule name (format-only; existence is checked by `compose`).
9. Any unknown top-level key not prefixed with `x-`.

Warnings (do not fail validation):

- Missing `verification` section.
- Missing `agent.summary_for_ai`.
- `handoff` present but older than 14 days.

## 11. Versioning of the spec itself

The spec follows the same kind of semver discipline it asks of capsules.
v0.x is allowed to break. v1.0 freezes the field set; later additions go
under additive minor versions or `x-` extension keys.
