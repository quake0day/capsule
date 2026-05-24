# yingjieli — case study

These six capsules decompose a real, deployed website
([yingjieliartist.com](https://yingjieliartist.com)) — an artist portfolio
running on Cloudflare Pages with a Pages-Functions backend (KV + R2).

They are included in this repo as a **case study**, not a runnable demo:
the verification commands inside each `capsule.yaml` expect the actual
yingjieli source tree at `../..` relative to the capsule directory. To run
`capsule verify` against them, clone the source repo alongside this one:

```bash
git clone https://github.com/quake0day/yingjieli.git
# then either move these capsules into yingjieli/capsules/
# or run capsule with cwd-adjusted paths from inside that repo
```

What works out-of-the-box against this directory **without** the yingjieli
source:

```bash
capsule validate examples/yingjieli       # all 6 ✓
capsule compose  examples/yingjieli       # no issues
capsule graph    examples/yingjieli       # diamond deps resolve cleanly
capsule bundle   examples/yingjieli --for claude -o CLAUDE.md
```

What requires the yingjieli source checked out:

```bash
capsule verify examples/yingjieli         # runs real `node --check` + behavioral probes
```

## What the six capsules carve up

```
admin-ui                cloudflare-deploy        public-site
   │                         (adapter)              │
   ├── admin-auth                                   ├── content-store ──► admin-auth
   ├── content-store ──► admin-auth                 └── image-store   ──► admin-auth
   └── image-store   ──► admin-auth
```

| Capsule | What it owns |
|---|---|
| `yingjieli-admin-auth` | HMAC sessions, login, rate-limit; the only thing that says "request is admin" |
| `yingjieli-content-store` | KV-backed site content (hero, bio, works, exhibitions, contact) |
| `yingjieli-image-store` | R2-backed image upload + serve + delete |
| `yingjieli-public-site` | Static frontend; read-only consumer |
| `yingjieli-admin-ui` | Admin panel; write consumer |
| `yingjieli-cloudflare-deploy` | Pages config: `_headers`, `_redirects`, `wrangler.toml`, bindings |

## Why this case study is in the repo

It is the first real-world dogfood of the capsule format. Decomposing
yingjieli surfaced three product bugs that have since been fixed:

1. `capsule validate` did not auto-discover capsules under a parent
   directory (now does, matching `verify` / `compose` / `graph` / `bundle`).
2. `capsule graph` had no `--output` flag (now does).
3. PyYAML's "mapping values are not allowed here" error around unquoted
   `: ` (colon-space) trapped me three times while authoring the capsules.
   The loader now surfaces a copy-pasteable hint with the offending line.

The dogfood ratio: 6 capsules, **22 / 22** verification checks pass against
the real source — including real HMAC roundtrip and tampered-token
rejection probes, not synthetic placeholders.
