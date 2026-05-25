// GET /api/v1/capsule/<owner>/<name>[@<version>]
// → { resolved, capsule } — the registry entry plus the fully-parsed capsule.yaml.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { parseAddress, resolveWithKV } from "../../../_lib/registry";
import { fetchCapsule, CapsuleFetchError, rawUrl } from "../../../_lib/github";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });

const joinSlug = (slug: string | string[] | undefined): string =>
  !slug ? "" : Array.isArray(slug) ? slug.join("/") : slug;

export const onRequestGet: PagesFunction<Env> = async ({ params, env }) => {
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

  try {
    const { capsule, source_url } = await fetchCapsule(entry);
    return json({
      resolved: {
        owner: entry.owner,
        name: entry.name,
        version: entry.version,
        git_url: entry.git_url,
        ref: entry.ref,
        path: entry.path,
        raw_url: rawUrl(entry),
        source_url,
      },
      capsule,
    });
  } catch (err) {
    if (err instanceof CapsuleFetchError) {
      const status = err.status === 404 ? 502 : 502;
      return json({ error: err.message }, status);
    }
    throw err;
  }
};
