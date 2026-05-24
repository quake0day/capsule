# Capsule

**Self-verifying context capsules for AI-native software development.**

> Git made code portable. Capsules make agent work state portable.

---

## Vision

AI coding agents are getting good at writing code and bad at **handing off
work**. When a task moves between Claude Code, Codex, Cursor, Devin, a
teammate, or "yesterday-you", the agent on the other side starts cold: it
reads the diff but not the *intent*, the README but not the *current task*,
the tests but not the *invariants the tests assume*. The result is wasted
context windows, broken hidden assumptions, and large systems that no one —
human or AI — fully trusts.

Capsule is the portable, AI-readable, **self-verifying** unit of a software
system that fixes this. One capsule. Four parts:

> **Capsule = Context + Contract + Code + Verification**

A capsule tells any agent — current, future, or one that does not exist yet —
what a subsystem is, what it promises, what it must not do, where the last
agent left off, and how to *prove* it still works. Capsules **compose**:
declare what you depend on, and a composer cross-checks the graph so a
multi-agent team (or one agent across many sessions) cannot silently break
things.

Stated as a market position:

- **GitHub** stores code.
- **Docker Hub** stores runtime images.
- **Hugging Face** stores models.
- **Capsule** stores composable, self-verifying AI-readable software subsystems.

### Where Capsule fits next to MCP

| | |
|---|---|
| **MCP** | How an agent connects to tools and data. |
| **Capsule** | What an agent *knows*, what work is *in flight*, and whether a subsystem is *still correct* after agent changes. |

The two are complementary. A future capsule registry can expose every
capsule as an MCP resource (`capsule://name/handoff`, `capsule://name/contract`,
…) so any MCP-aware agent reads them natively.

---

## The four parts of a capsule

Every `capsule.yaml` carries:

1. **Context** — `purpose`, `agent.summary_for_ai`, `agent.avoid`, `glossary`,
   and a `handoff` block for in-flight work.
2. **Contract** — `interfaces.provides`, `interfaces.requires`,
   `dependencies`, `compatibility`.
3. **Code** — the capsule's own directory: reference implementation, tests,
   scaffolding (optional but typical).
4. **Verification** — declarative `health_checks`, `functional_tests`,
   `integration_tests`, and human-readable `invariants` that future
   AI-generated tests will enforce.

Spec is in [`SPEC.md`](SPEC.md). It is intentionally small in v0.1 and will
grow only with concrete need.

---

## Roadmap (three layers)

The product has three layers. v0.1 ships the foundation; later layers turn it
into a platform.

```
L1  Local CLI + Spec + Verify        ← v0.1 (this repo, today)
    capsule.yaml, validate, verify, compose, graph, bundle

L2  Portable Handoff URLs + Adapters ← next
    capsule push      → shareable URL  (handoff.dev/p/abc123)
    capsule pull <url> --for claude|codex|cursor|mcp
    web viewer for humans
    MCP server exposing capsule resources
    GitHub issue / PR adapter

L3  Capsule Registry + Memory Graph  ← later
    multi-capsule project memory across teams
    diff between capsule versions ("what changed in the AI's understanding?")
    AI-generated regression tests from invariants
    permissions, audit, secret redaction
    self-hosted enterprise mode
```

The v0.1 spec is the load-bearing piece. Layers 2 and 3 are valuable
*because* the on-disk format underneath them is structured, validated, and
self-verifying — not a free-text prompt.

---

## Install

Requires Python 3.10+.

```bash
git clone <this-repo>
cd capsule
python -m venv .venv
.venv/Scripts/activate          # on Windows
# source .venv/bin/activate     # on macOS/Linux
pip install -e .
```

You now have a `capsule` command on your PATH (inside the venv).

## Examples

Two reference example sets ship in the repo:

- [`examples/wolfctf/`](examples/wolfctf/) — synthetic three-capsule
  scenario (`auth-core`, `lab-runtime-docker`, `ai-report`) used as the
  quickstart below.
- [`examples/yingjieli/`](examples/yingjieli/) — real-world case study
  decomposing the deployed site [yingjieliartist.com](https://yingjieliartist.com)
  into six capsules (admin-auth, content-store, image-store, public-site,
  admin-ui, cloudflare-deploy). All six validate, compose, graph, and
  bundle out of the box; `verify` requires the yingjieli source repo
  cloned alongside — see the example's README.

## Quickstart: the WolfCTF example

`ai-report` depends on the other two; `lab-runtime-docker` depends on
`auth-core`. The composer resolves the diamond and the bundler renders the
whole picture as one document an agent can read cold.

```bash
# 1. Validate each capsule against the spec.
capsule validate examples/wolfctf/auth-core examples/wolfctf/lab-runtime examples/wolfctf/ai-report

# 2. Cross-check that the three fit together.
capsule compose examples/wolfctf

# 3. Render the dependency graph (text or Graphviz DOT).
capsule graph examples/wolfctf
capsule graph examples/wolfctf --format dot | dot -Tpng -o graph.png

# 4. Run every capsule's verification suite.
capsule verify examples/wolfctf

# 5. Produce a CLAUDE.md so an agent can pick the work up.
capsule bundle examples/wolfctf --for claude  -o CLAUDE.md

# 6. Or an AGENTS.md for Codex / Cursor / generic agent tools.
capsule bundle examples/wolfctf --for codex   -o AGENTS.md

# 7. Or a GitHub issue / PR body.
capsule bundle examples/wolfctf --for github  -o ISSUE.md
```

`lab-runtime-docker/capsule.yaml` carries a `handoff` block — running
`capsule bundle` lifts it into the final document so the next agent knows
exactly where to pick up (the *Demo 1: Claude Code → Codex continuation*
shape from the strategy doc).

## Start your own capsule

```bash
capsule init my-subsystem
capsule validate my-subsystem
capsule verify my-subsystem
```

Then edit `my-subsystem/capsule.yaml` and replace the placeholders.

---

## Commands

| Command            | What it does                                                    |
| ------------------ | --------------------------------------------------------------- |
| `capsule init`     | Scaffold a new capsule directory.                               |
| `capsule validate` | Check `capsule.yaml` against the spec.                          |
| `capsule verify`   | Run `health_checks`, `functional_tests`, `integration_tests`.   |
| `capsule compose`  | Cross-check a set of capsules; report missing/wrong-version deps. |
| `capsule graph`    | Render the dependency graph as text or Graphviz DOT.            |
| `capsule bundle`   | Render the composed capsules as CLAUDE.md / AGENTS.md / GitHub. |

Every command accepts a single capsule path, a `capsule.yaml` file, or a
parent directory that will be walked for `capsule.yaml` files.

## Bundle targets

| `--for`   | Output                                                       |
| --------- | ------------------------------------------------------------ |
| `claude`  | CLAUDE.md, oriented at Claude Code's hard-boundary semantics |
| `codex`   | AGENTS.md, the de-facto agent contract file                  |
| `agents`  | alias of `codex`                                             |
| `github`  | markdown body suitable for a GitHub issue or PR comment      |
| `prompt`  | a single plain-text prompt (no markdown chrome)              |

---

## Status

**v0.1.** The spec, the CLI, and three reference capsules. Not yet on PyPI;
install from source. The spec is allowed to break before v1.0.

Layer 2 (web viewer + MCP server + GitHub adapter) is next.

## License

Apache-2.0.
