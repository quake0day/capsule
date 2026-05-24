# L2 design — "Capsule, the AI-native Unix"

> Status: approved 2026-05-24. Implementation tracked under capsule repo
> task list (#23 and follow-ups).

This document captures the L2 (v0.2) design after the unix.txt re-framing.
It is the source of truth for what L2 ships and what it deliberately defers.

---

## 1. Framing

Capsule is the **Unix-like composition layer for AI software engineering**.
We are not building an OS, we are building the *shell + filesystem + man +
package manager* for AI agents working on real codebases. The deliberate
philosophical analogue:

| Unix concept | Capsule concept |
|---|---|
| filesystem path | capsule address — `capsule://owner/name@version` |
| shell command | `capsule <verb>` — init, validate, verify, compose, graph, bundle, **pull**, **man**, **status**, **serve** |
| pipe `\|` | typed interface composition (a `requires` of kind/name pipes from a matching `provides`) |
| man page | `capsule man <addr>` — terminal + web rendering of the AI-readable card |
| process status | `capsule status <addr>` — version, verify result, contract surface, unmet env |
| package manager | `capsule pull / pin` (push deferred) |
| daemon | hosted registry + MCP server (deferred to L3) |

**Strategic tagline (now primary):**
*Unix piped text between programs. Capsule pipes engineering context between AI agents.*

**Handoff sub-tagline (kept):**
*Git made code portable. Capsules make agent work state portable.*

## 2. Capsule Philosophy (project manifesto, lifted from unix.txt)

1. Do one subsystem well.
2. Declare clear contracts.
3. Make context machine-readable.
4. Compose through standard interfaces.
5. Verify every capsule independently.
6. Preserve handoff state.
7. Prefer small composable systems over monolithic agent prompts.

These appear at the top of README.md, are linked from this doc, and become
the rubric for accepting/rejecting future features ("does it serve a
principle? if not, defer").

## 3. v0.2 scope (what L2 ships)

Five deliverables, in build order:

### 3.1 Registry server (Cloudflare Pages, TypeScript)

A small Pages app that serves both the web viewer and the resolver API.

```
server/
  wrangler.toml
  _headers, _redirects
  index.html                              the / index of registered capsules
  assets/style.css
  capsule.yaml                            ← yes, the server is itself a capsule
  registry.yaml                           static seed: name → { git_url, ref, path }
  tsconfig.json
  functions/
    api/v1/
      resolve/[[slug]].ts                 GET /api/v1/resolve/<owner>/<name>@<v>
      capsule/[[slug]].ts                 GET /api/v1/capsule/<owner>/<name>@<v>
    c/[[slug]].ts                         GET /c/<owner>/<name>[@<v>]  — man-page HTML
    _lib/
      registry.ts                         parse + look up registry.yaml (KV later)
      github.ts                           fetch raw capsule.yaml from a github tree
      schema.ts                           thin TS mirror of capsule.yaml v0.1 shape
      render.ts                           server-side HTML rendering
```

**Endpoints (v0.2):**

| Method | Path | Returns |
|---|---|---|
| GET | `/` | Index of every capsule in registry |
| GET | `/c/<owner>/<name>` | Man-page HTML for latest version |
| GET | `/c/<owner>/<name>@<version>` | Man-page HTML for specific version |
| GET | `/api/v1/resolve/<owner>/<name>@<v>` | `{ git_url, ref, path, version }` |
| GET | `/api/v1/capsule/<owner>/<name>@<v>` | The parsed capsule.yaml as JSON |

**Out of v0.2:** `/compose/<id>` (lands in v0.3), search, auth, push.

**Why TypeScript / Pages Functions:** the user's stack of record
(yingjieliartist.com proved it). Same runtime, same KV/R2 primitives,
same wrangler dev loop. Eats own dogfood.

### 3.2 `capsule pull <addr>`

Python CLI command:

1. Parse `capsule://<owner>/<name>[@<v>]` (or accept a raw git URL or local path).
2. Hit `$CAPSULE_REGISTRY/api/v1/resolve/...` (default `http://localhost:8788`).
3. `git clone --depth=1 --filter=blob:none` (sparse-checkout if `path` is set) into `~/.capsule/cache/<commit_sha>/`.
4. Print the resulting local path (other commands consume the path).

Cache is content-addressed by commit SHA; re-pulls of the same version are
no-ops after the first hit.

### 3.3 `capsule man <addr>`

Renders the capsule's man page to the terminal — Rich panels for:

```
NAME           yingjieli-admin-auth (v1.0.0, subsystem)
PURPOSE        <summary>
OWNS           ...
DOES NOT OWN   ...
AI ORIENTATION ...
AVOID          ...
PROVIDES       http_api:auth-login, ...
REQUIRES       env:ADMIN_PASSWORD, ...
INVARIANTS     ...
HANDOFF        — (at rest)  | OR  the objective + remaining if present
STATUS         PASS 4/4 verify, last 2026-05-24 14:02   (cached)
```

Single capsule only. The web equivalent at `/c/<owner>/<name>` shows the
same content with cross-links to required/provided capsules.

### 3.4 `capsule status <addr>`

One-screen snapshot, machine- and human-readable. JSON via `--json`.

```
name           yingjieli-admin-auth
version        1.0.0
verified       PASS (4/4)  2026-05-24 14:02   (cached)
provides       3 http_api, 1 library
requires       3 env  (1 unsatisfied: SESSION_SECRET)
compatible     content-store >=1.0.0, image-store >=1.0.0
handoff        — (at rest)
```

The single command an agent runs before deciding to trust a capsule.

### 3.5 `capsule serve`

Thin wrapper that shells out to `npx wrangler pages dev server/`. Reason for
shelling out instead of re-implementing: the Pages Functions runtime is
non-trivial and `wrangler pages dev` is the canonical local dev story. We
let Cloudflare own that.

## 4. Typed-pipe compose upgrade (small, in scope)

Today `compose` checks that `requires.from_capsule.name` matches some
`provides.name`. L2 also enforces **kind matching**:

```
requires:
  - kind: http_api
    name: auth-introspect
    from_capsule: auth-core
```

…only resolves if `auth-core` has a `provides` with **both** `kind: http_api`
**and** `name: auth-introspect`. Today it only checks the name. The fix is
~10 lines in `compose.py`. It makes the Unix-pipe analogy real: a kind
mismatch is the equivalent of `command | another-command` where the second
expects JSON and the first emits PNG.

## 5. Deferred to L3 / L4 (explicit)

- `capsule push` (use `git push` for v0.2; PR to `server/registry.yaml` to register)
- Hosted registry behind a real domain
- KV-backed registry mutations (registry currently rebuilt from yaml on deploy)
- R2-backed snapshot bundles
- MCP server exposing `capsule://` resources
- GitHub PR/issue body adapter
- AI-generated regression tests from invariants
- Capsule Score / search / discovery
- Multi-user auth + permissions + audit

## 6. v0.2 build order (concrete tasks)

1. README + philosophy update (this commit)
2. `server/` scaffold: wrangler.toml, registry.yaml schema, _headers
3. `functions/_lib/registry.ts` + `functions/_lib/github.ts`
4. `functions/api/v1/resolve/[[slug]].ts`
5. `functions/api/v1/capsule/[[slug]].ts`
6. `functions/c/[[slug]].ts` + `_lib/render.ts` + `assets/style.css`
7. `index.html` (static, hydrated by `/api/v1/...` calls)
8. `server/capsule.yaml` (the registry server *is* a capsule)
9. CLI: `capsule pull` + `capsule man` + `capsule status` + `capsule serve`
10. Typed-pipe upgrade in `compose.py`
11. End-to-end smoke test: register the yingjieli capsules in `registry.yaml`,
    `wrangler pages dev`, then `capsule pull capsule://quake0day/yingjieli-admin-auth@1.0.0`
    + `capsule man ...` + `capsule status ...`

## 7. Risks and decision-reversal cost

| Risk | Mitigation | If we have to reverse |
|---|---|---|
| TS server adds polyglot complexity to the repo | Keep `server/` self-contained; no shared code with Python | Drop `server/` and switch CLI to local-file mode |
| `wrangler pages dev` flakiness on Windows | Document the workaround (use `npx`); fall back to deploying preview branches | Same; the prod server is independent of local dev |
| Static `registry.yaml` does not scale | Move to KV in v0.3 with the same JSON schema | One Functions file edits |
| `capsule://` URL scheme conflicts with another tool | The scheme is unregistered; if needed, fall back to `https://capsule.dev/c/<owner>/<name>@<v>` as the canonical form | Find-and-replace; not a data migration |
