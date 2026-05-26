---
name: capsule-compose
description: Use when the user describes a software project they want to build and either mentions "my capsules", "the registry", or "find me capsules for X" — or when they describe a system whose features are likely covered by existing capsules at https://capsule-registry.pages.dev. This skill discovers relevant capsules in the user's registry, presents the candidates with explicit reasoning about fit and reuse cost, then assembles a chosen subset into a runnable project via `capsule reconstruct`. NOT for one-off code questions or tasks unrelated to assembling reusable subsystems.
---

# capsule-compose

Assemble a working software project from existing capsules in the user's
registry, driven by a natural-language description.

## What a capsule is (just enough)

A **capsule** is a self-contained subsystem packaged as:
- `capsule.yaml` — the contract (purpose, provides, requires, invariants)
- `install.json` — the file mapping (src/* → target paths)
- `src/` — the actual implementation files
- `REUSE.md` — concrete "what to change to use this elsewhere"

Capsules are addressed by `capsule://<owner>/<name>[@<version>]` and
served by the registry at `https://capsule-registry.pages.dev`.

## The CLI you have available

The `capsule` command must be on PATH. If it's missing, install with:

```bash
git clone https://github.com/quake0day/capsule
cd capsule && pip install -e .
```

Then verify with `capsule --version`. Key commands this skill uses:

| Command | Purpose |
|---|---|
| `bash ~/.claude/skills/capsule-compose/scripts/list-capsules.sh` | Fast registry enumeration with summaries (see below) |
| `capsule man <addr>` | Read a capsule's full man-page in the terminal |
| `capsule status <addr>` | One-screen snapshot (env vars, handoff, contract) |
| `capsule pull <addr>` | Fetch into `~/.capsule/cache/...` |
| `capsule reconstruct --from <dir> --out <out>` | Assemble capsules into a runnable project |
| `capsule reconstruct --from <dir> --out <out> --prompt "..."` | + AI customization (needs ANTHROPIC_API_KEY) |

There is also an MCP server at `https://capsule-registry.pages.dev/mcp` —
if it's wired into the user's Claude Code config, prefer its `capsule_list`
/ `capsule_get` / resource APIs (faster, fewer shell round-trips). Confirm
by checking `claude mcp list` before relying on it.

## Workflow — follow this in order

### 1. Understand the user's intent precisely

Before searching, restate the user's intent back to them in one sentence
and confirm the technology + feature scope. The registry has multiple
realtime stacks (Cloudflare RealtimeKit and LiveKit), multiple auth
stories, etc. Forcing the wrong stack wastes effort.

### 2. Enumerate the registry

Run the bundled helper:

```bash
bash ~/.claude/skills/capsule-compose/scripts/list-capsules.sh
```

It returns one line per capsule with `<owner>/<name>@<version>` + the
one-line purpose summary. Use this as your shortlist input.

### 3. Match candidates to the intent — then explain your shortlist

For each plausible candidate, read its `capsule.yaml` (via `capsule man`
or the registry's web view at `https://capsule-registry.pages.dev/c/<owner>/<name>`)
and look at:

- `purpose.summary` and `purpose.owns` — does it cover the user's need?
- `purpose.does_not_own` — is it deliberately NOT what the user wants?
- `interfaces.requires` — what env vars / peer capsules does it need?
- `x-reuse.notes` (or `REUSE.md`) — what does the user have to change?

**Then write a short table for the user** showing:
- Each candidate you picked, with one-line rationale
- Each candidate you rejected, with why (was it the wrong stack? overlap with a better one?)
- The total "compose graph" — who depends on whom

Ask the user to confirm before downloading anything.

### 4. Avoid mixing incompatible stacks

If two candidates assume different backends (e.g. one wants Cloudflare
KV, another assumes Postgres), call this out. Either:
- Drop one, OR
- Tell the user the gap requires a custom adapter

Do not silently mix.

### 5. Pull + reconstruct

Once confirmed, pull each capsule then reconstruct:

```bash
mkdir -p ./assembled
cd ./assembled

# Fetch each into a subdir matching the capsule's name
for addr in capsule://owner/foo capsule://owner/bar; do
  capsule pull "$addr"
done

# Note: pulls land in ~/.capsule/cache/<sha>/repo/<path>/.
# For reconstruction, the capsules need to be co-located in one dir
# with capsule.yaml + install.json visible at each subdir's root.
# Copy them in:
for addr in capsule://owner/foo capsule://owner/bar; do
  # pull prints the path; copy the dir containing capsule.yaml to ./assembled/<name>/
  ...
done

capsule reconstruct \
  --from ./assembled \
  --out ./my-project \
  --clean
```

### 6. Tell the user what's next

After reconstruction:
- List the env vars to set (from each REUSE.md, surfaced by the
  `Set these env vars on Cloudflare Pages before deploying:` block
  `capsule reconstruct` prints)
- Suggest the run command — typically `cd ./my-project && npx wrangler pages dev .`
- Mention any leftover customization the user wanted but no capsule covered

### 7. Honest "no fit"

If no capsules match well, say so. Suggest either:
- Using `capsule decompose <github-url> --register <name>` to extract
  capsules from a similar existing project (this skill is the
  *consumer*; the decomposer is the *producer*).
- Building the missing piece by hand.

Forcing a bad fit is worse than admitting the gap.

## Examples

### "Build me a real-time chat app on Cloudflare"

Plausible shortlist (Cloudflare RealtimeKit stack):
- `quake0day/f4c-realtime-core` (audio/video, RealtimeKit hook)
- `quake0day/f4c-room-api` (KV-backed room metadata + tokens)
- `quake0day/f4c-turnstile-gate` (bot protection at room creation)
- `quake0day/f4c-nextjs-app-template` (the app shell)
- `quake0day/f4c-build-infra` (OpenNext.js → Workers pipeline)

Reject:
- `lkmeet-*` capsules — these use LiveKit, not Cloudflare RealtimeKit
- `f4c-bot-service` — only if the user wants an AI co-host

### "Make me a video conferencing app like Zoom"

Plausible shortlist (LiveKit stack):
- `quake0day/lkmeet-video-conference-core`
- `quake0day/lkmeet-livekit-auth-api`
- `quake0day/lkmeet-livekit-recording-api` (only if user wants recording)
- `quake0day/lkmeet-livekit-ui-components`
- `quake0day/lkmeet-nextjs-app-shell`
- `quake0day/lkmeet-build-config`

### "I need auth for my web app"

The registry currently has only `quake0day/yingjieli-admin-auth` — a
single-user password+HMAC capsule. If the user wants multi-user auth,
say so plainly: "no capsule covers this; you'll need to write one or
adapt the single-user one".

## Critical rules

- **Never silently compose** — always show the shortlist + ask for confirmation
- **Never mix backends** — Cloudflare KV + Postgres, RealtimeKit + LiveKit, etc.
- **Always surface env vars + reuse notes** before pretending the project is "done"
- **Be honest about leftovers** — if 2 of 3 user features have capsules and 1 doesn't, say it
