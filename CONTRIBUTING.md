# Contributing to Capsule

Thanks for being interested. Capsule is small enough that one person can
read the whole thing in an afternoon — and the spec / CLI / registry are
all open precisely so the next change can come from anyone.

## What kinds of contribution help most

In rough priority order:

1. **Publish a capsule.** Run `capsule push` from any project of yours
   that has reusable subsystems, or `capsule decompose --register` on
   any public github repo. Every capsule in the registry makes the next
   one easier to find. This is the highest-leverage contribution.
2. **File issues.** Pull is broken on Windows behind a corporate proxy?
   `capsule diff` misses a field that matters in your domain? The spec
   is missing a `type:` value (this is how `library` got added)? Open
   an issue.
3. **Open spec RFCs.** Anything that changes `capsule.yaml`'s on-disk
   shape needs an RFC issue first — describe the use case, the proposed
   field, and one example. Tagged labels: `rfc`, `spec`.
4. **Adapter PRs.** Add a new MCP transport, a new bundle target
   (`--for cursor` etc.), a new git host beyond github, a new auth
   provider. Self-contained changes are easiest to merge.
5. **Decomposer improvements.** Better prompts, multi-pass mode for
   bigger repos, a heuristic-only fallback for offline use, support for
   non-Anthropic / non-Gemini providers.

## Local development

The repo has two top-level codebases:

```
src/capsule/    Python CLI (Apache 2.0)
server/         Cloudflare Pages registry server in TypeScript (Apache 2.0)
```

### Python CLI

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -e .[dev]
capsule --help
pytest tests/
```

### Registry server

```bash
cd server
npm install
npm run typecheck            # tsc --noEmit
npx wrangler pages dev .     # starts local dev on http://127.0.0.1:8788
```

Smoke-test against the local server:

```bash
export CAPSULE_REGISTRY=http://127.0.0.1:8788
capsule pull capsule://capsule-examples/auth-core@0.3.0
```

(Unset `CAPSULE_REGISTRY` to go back to the hosted instance at
`https://capsule-registry.pages.dev`.)

## Spec changes

The on-disk `capsule.yaml` shape is defined by `SPEC.md`. Everything
else (CLI, registry server, examples) flows from it. To change the spec:

1. Open an issue tagged `rfc:spec` describing the motivation and the
   proposed shape.
2. Wait for one round of discussion. Most issues either get a quick
   "yes / approved" or a request for a different framing.
3. Once approved, the PR should touch four places:
   - `SPEC.md` — the human-readable spec text
   - `src/capsule/schema.py` — Pydantic models
   - `server/functions/_lib/schema.ts` — TypeScript mirror
   - At least one of the example capsules (so the change is exercised)
4. New keys must be optional, or behind a new `apiVersion`. Removing
   keys requires a new `apiVersion` and a deprecation note.

`x-`-prefixed keys are reserved for unofficial extensions and never
break validation.

## Pull requests

- One concern per PR. A fix and a feature in the same PR get split on
  request.
- New CLI commands should land with `--help` text, a couple of golden
  tests in `tests/`, and a mention in the README's command table.
- New server endpoints should land with the existing TypeScript types
  (no `any`) and an entry in the man-page / api routes section of the
  server README.
- A green `pytest` + `npm run typecheck` is the merge bar.

## Commit messages

Free-form. The existing history uses subject-line summaries with
optional longer bodies; follow that or don't. Co-author trailers are
welcome.

## License

By contributing, you agree your contribution will be licensed under
Apache 2.0 (the same license as the rest of the repo).
