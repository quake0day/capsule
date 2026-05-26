// PUT /api/v1/capsules/<owner>/<name>@<version>
//
// Body:    { git_url: string, ref: string, path: string }
// Auth:    Authorization: Bearer <github-token>  (or just the token)
// Effect:  validates the token belongs to <owner>, fetches the capsule.yaml
//          at (git_url, ref, path), confirms its name/version match the
//          URL, then writes the entry into the KV namespace.
//
// On success: 200 { ok: true, address, entry }.
// On rejection: 4xx with a JSON error.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import {
  parseAddress,
  putEntry,
  resolveWithKV,
  type RegistryEntry,
} from "../../../_lib/registry";
import { fetchCapsule, CapsuleFetchError } from "../../../_lib/github";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });

const joinSlug = (slug: string | string[] | undefined): string =>
  !slug ? "" : Array.isArray(slug) ? slug.join("/") : slug;


// ---------------------------------------------------------------------------
// PUT — push
// ---------------------------------------------------------------------------

export const onRequestPut: PagesFunction<Env> = async ({ request, params, env }) => {
  if (!env.CAPSULE_REGISTRY) {
    return json({ error: "server misconfigured: no KV binding" }, 500);
  }

  const slug = joinSlug(params.slug);
  const addr = parseAddress(slug);
  if (!addr || !addr.version) {
    return json(
      { error: `invalid address '${slug}'. Push requires explicit version: <owner>/<name>@<version>.` },
      400,
    );
  }

  const token = extractToken(request.headers.get("Authorization"));
  if (!token) {
    return json(
      { error: "missing Authorization header. Send `Authorization: Bearer <github-token>`." },
      401,
    );
  }

  const ghLogin = await verifyGithubToken(token);
  if (!ghLogin) {
    return json({ error: "GitHub token did not validate (rejected by api.github.com/user)." }, 401);
  }
  if (ghLogin.toLowerCase() !== addr.owner.toLowerCase()) {
    return json(
      { error: `token belongs to '${ghLogin}', cannot push to owner '${addr.owner}'.` },
      403,
    );
  }

  let body: { git_url?: string; ref?: string; path?: string; visibility?: string };
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const { git_url, ref, path } = body;
  if (!git_url || !ref || !path) {
    return json(
      { error: "body must include git_url, ref, path" },
      400,
    );
  }
  if (!/^https:\/\/github\.com\//i.test(git_url)) {
    return json(
      { error: `only github.com repos are accepted (got ${git_url})` },
      400,
    );
  }
  const visibility: "public" | "private" =
    body.visibility === "private" ? "private" : "public";

  const candidate: RegistryEntry = {
    owner: addr.owner,
    name: addr.name,
    version: addr.version,
    git_url,
    ref,
    path,
    visibility,
  };

  // Round-trip fetch: confirm the capsule.yaml is reachable, parses, and
  // its own declared name/version match the address being claimed. For
  // private entries we use the pushing token (already proven owned by
  // `addr.owner` above) to authenticate the fetch.
  let parsed;
  try {
    parsed = await fetchCapsule(candidate, visibility === "private" ? token : undefined);
  } catch (err) {
    const msg = err instanceof CapsuleFetchError ? err.message : String(err);
    return json({ error: `could not fetch the proposed capsule: ${msg}` }, 422);
  }
  const c = parsed.capsule;
  if (c.name !== addr.name) {
    return json(
      { error: `capsule.yaml declares name '${c.name}', does not match URL name '${addr.name}'.` },
      422,
    );
  }
  if (c.version !== addr.version) {
    return json(
      { error: `capsule.yaml declares version '${c.version}', does not match URL version '${addr.version}'.` },
      422,
    );
  }

  await putEntry(env.CAPSULE_REGISTRY, candidate);

  return json({
    ok: true,
    address: `capsule://${candidate.owner}/${candidate.name}@${candidate.version}`,
    entry: candidate,
    view_url: `/c/${candidate.owner}/${candidate.name}@${candidate.version}`,
  });
};


// ---------------------------------------------------------------------------
// GET — lookup (read-side; thin wrapper, mostly for "what's there now")
// ---------------------------------------------------------------------------

export const onRequestGet: PagesFunction<Env> = async ({ params, env }) => {
  const slug = joinSlug(params.slug);
  const addr = parseAddress(slug);
  if (!addr) {
    return json({ error: `invalid address '${slug}'` }, 400);
  }
  const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
  if (!entry) return json({ error: "not found" }, 404);
  return json({ entry });
};


// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function extractToken(authHeader: string | null): string | null {
  if (!authHeader) return null;
  const m = /^Bearer\s+(.+)$/i.exec(authHeader);
  return m ? m[1].trim() : authHeader.trim();
}

async function verifyGithubToken(token: string): Promise<string | null> {
  try {
    const resp = await fetch("https://api.github.com/user", {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "capsule-registry/0.3",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!resp.ok) return null;
    const u = (await resp.json()) as { login?: string };
    return u.login || null;
  } catch {
    return null;
  }
}
