// GET /api/v1/resolve/<owner>/<name>[@<version>]
// → { owner, name, version, git_url, ref, path, raw_url, source: "registry", visibility }
//
// The pure naming layer: do not fetch the capsule itself, just translate a
// `capsule://` address into a concrete git source. Cheap, cacheable.
//
// For private entries, requires Authorization (header or cookie) and verifies
// the token can read the underlying repo before disclosing git_url/path. We
// don't want to leak repo names of private capsules to anonymous callers.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { parseAddress, resolveWithKV, isPrivate } from "../../../_lib/registry";
import { rawUrl, parseGithubUrl } from "../../../_lib/github";
import { extractToken } from "../../../_lib/auth";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

const json = (body: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extraHeaders },
  });

const joinSlug = (slug: string | string[] | undefined): string => {
  if (!slug) return "";
  return Array.isArray(slug) ? slug.join("/") : slug;
};

export const onRequestGet: PagesFunction<Env> = async ({ params, env, request }) => {
  const slug = joinSlug(params.slug);
  if (!slug) return json({ error: "missing slug" }, 400);

  const addr = parseAddress(slug);
  if (!addr) {
    return json(
      { error: `invalid address '${slug}'. Expected <owner>/<name>[@<version>].` },
      400,
    );
  }

  const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
  if (!entry) {
    const v = addr.version ? "@" + addr.version : "";
    return json({ error: `no capsule found for ${addr.owner}/${addr.name}${v}` }, 404);
  }

  // Private entries: token gate. We also confirm the token actually has read
  // access by hitting the GitHub Contents API for the capsule's directory.
  // GitHub answers 404 (not 401) when a token can't see a private repo, so
  // the check + the gate are the same call.
  if (isPrivate(entry)) {
    const token = extractToken(request);
    if (!token) {
      return json(
        { error: "private capsule: send Authorization: Bearer <github-token>", visibility: "private" },
        401,
        { "WWW-Authenticate": "Bearer realm=\"capsule-private\"" },
      );
    }
    const repo = parseGithubUrl(entry.git_url);
    if (!repo) return json({ error: "private capsule: unsupported git_url" }, 502);
    const probe = `https://api.github.com/repos/${repo.owner}/${repo.repo}?ref=${encodeURIComponent(entry.ref)}`;
    const resp = await fetch(probe, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "capsule-registry/0.4",
      },
    });
    if (resp.status === 401) {
      return json({ error: "GitHub rejected the token (401)" }, 401);
    }
    if (resp.status === 404 || resp.status === 403) {
      return json({ error: "token has no access to this private repo" }, 403);
    }
    if (!resp.ok) {
      return json({ error: `GitHub probe ${resp.status}` }, 502);
    }
  }

  const body = {
    owner: entry.owner,
    name: entry.name,
    version: entry.version,
    git_url: entry.git_url,
    ref: entry.ref,
    path: entry.path,
    raw_url: isPrivate(entry) ? null : rawUrl(entry),
    visibility: entry.visibility ?? "public",
    source: "registry",
  };
  return json(body, 200, {
    "Cache-Control": isPrivate(entry) ? "private, no-store" : "public, max-age=60",
  });
};
