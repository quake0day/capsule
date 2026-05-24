"""Starter content for `capsule init`."""

from __future__ import annotations

STARTER_CAPSULE_YAML = """\
apiVersion: capsule.dev/v0.1
kind: Capsule

name: {name}
version: 0.1.0
type: subsystem
domain: example

purpose:
  summary: |
    One or two sentences describing what this capsule is for.
  owns:
    - the thing this capsule is responsible for
  does_not_own:
    - the thing it deliberately leaves to other capsules

interfaces:
  provides: []
  requires: []

dependencies:
  capsules: []
  runtime: []

agent:
  summary_for_ai: |
    Tell an AI agent — one that has never seen this codebase — what this
    capsule is, what to assume about its environment, and what NOT to do.
  avoid:
    - bypassing the interfaces this capsule provides
  glossary: {{}}

verification:
  health_checks:
    - id: smoke
      command: echo "replace me with a real health check"
  functional_tests: []
  integration_tests: []
  invariants:
    - state an invariant that must always hold for this capsule

# Optional. Populate when work is mid-flight and you want another agent to
# pick it up.
# handoff:
#   objective: ...
#   completed: []
#   remaining: []
#   next_agent_should: []
"""

STARTER_README = """\
# {name}

A capsule scaffold created with `capsule init`.

- See `capsule.yaml` for the contract.
- Run `capsule validate` to check the spec.
- Run `capsule verify` to execute the verification suite.
- Run `capsule bundle --for claude` to produce a CLAUDE.md for AI agents.
"""
