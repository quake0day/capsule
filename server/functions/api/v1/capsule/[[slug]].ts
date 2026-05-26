// GET /api/v1/capsule/<owner>/<name>[@<version>]
//    → { resolved, capsule } — the registry entry plus the fully-parsed capsule.yaml.
//
// GET /api/v1/capsule/<owner>/<name>[@<version>]/files
//    → { resolved, files: [{ from, to, blob_url }, ...], env_required }
//
// GET /api/v1/capsule/<owner>/<name>[@<version>]/blob/<path>
//    → raw bytes of the named file (text/plain; charset=utf-8 by default;
//      JSON / YAML / HTML / CSS / JS get their natural Content-Type)

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { parseAddress, resolveWithKV, isPrivate, type RegistryEntry } from "../../../_lib/registry";
import {
  fetchCapsule,
  fetchInstall,
  fetchSibling,
  CapsuleFetchError,
  CapsuleAuthError,
  rawUrl,
} from "../../../_lib/github";
import { extractToken } from "../../../_lib/auth";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

function privateJson(slug: string, message?: string): Response {
  return new Response(JSON.stringify({
    error: message ?? "private capsule: send Authorization: Bearer <github-token>",
    visibility: "private",
    address: `capsule://${slug}`,
    hint: "use the CLI (capsule pull / capsule man), which already uses your gh auth token",
  }, null, 2), {
    status: 401,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "WWW-Authenticate": "Bearer realm=\"capsule-private\"",
    },
  });
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });

const joinSlug = (slug: string | string[] | undefined): string =>
  !slug ? "" : Array.isArray(slug) ? slug.join("/") : slug;

function splitBlob(slug: string): { addrSlug: string; filePath: string } | null {
  const idx = slug.indexOf("/blob/");
  if (idx === -1) return null;
  return { addrSlug: slug.slice(0, idx), filePath: slug.slice(idx + "/blob/".length) };
}

function splitFilesTail(slug: string): string | null {
  if (slug.endsWith("/files")) return slug.slice(0, -"/files".length);
  return null;
}

function contentTypeFor(path: string): string {
  const lower = path.toLowerCase();
  if (lower.endsWith(".json")) return "application/json; charset=utf-8";
  if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return "text/yaml; charset=utf-8";
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return "text/html; charset=utf-8";
  if (lower.endsWith(".css")) return "text/css; charset=utf-8";
  if (lower.endsWith(".js") || lower.endsWith(".mjs")) return "application/javascript; charset=utf-8";
  if (lower.endsWith(".ts")) return "application/typescript; charset=utf-8";
  if (lower.endsWith(".toml")) return "application/toml; charset=utf-8";
  if (lower.endsWith(".md")) return "text/markdown; charset=utf-8";
  return "text/plain; charset=utf-8";
}


export const onRequestGet: PagesFunction<Env> = async ({ params, env, request }) => {
  const slug = joinSlug(params.slug);
  if (!slug) return json({ error: "missing slug" }, 400);

  const token = extractToken(request);
  const blob = splitBlob(slug);
  if (blob) return await serveBlob(env, blob.addrSlug, blob.filePath, token);

  const filesSlug = splitFilesTail(slug);
  if (filesSlug !== null) return await serveFilesList(env, filesSlug, token);

  return await serveCapsule(env, slug, token);
};


// ---------------------------------------------------------------------------
// existing capsule JSON
// ---------------------------------------------------------------------------

async function serveCapsule(env: Env, slug: string, token: string | null): Promise<Response> {
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
  if (isPrivate(entry) && !token) return privateJson(slug);
  try {
    const { capsule, source_url } = await fetchCapsule(entry, token ?? undefined);
    return json({
      resolved: resolvedJson(entry, source_url),
      capsule,
    });
  } catch (err) {
    if (err instanceof CapsuleAuthError) return privateJson(slug, err.message);
    if (err instanceof CapsuleFetchError) return json({ error: err.message }, 502);
    throw err;
  }
}


// ---------------------------------------------------------------------------
// new: files list
// ---------------------------------------------------------------------------

async function serveFilesList(env: Env, slug: string, token: string | null): Promise<Response> {
  const addr = parseAddress(slug);
  if (!addr) {
    return json({ error: `invalid address '${slug}' (for /files)` }, 400);
  }
  const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
  if (!entry) {
    return json({ error: `no capsule found for ${addr.owner}/${addr.name}` }, 404);
  }
  if (isPrivate(entry) && !token) return privateJson(slug);
  try {
    const install = await fetchInstall(entry, token ?? undefined);
    if (!install) {
      return json({
        resolved: resolvedJson(entry, null),
        files: [],
        env_required: [],
        note: "this capsule has no install.json (descriptor-only)",
      });
    }
    const blobBase = `/api/v1/capsule/${entry.owner}/${entry.name}@${entry.version}/blob`;
    return json({
      resolved: resolvedJson(entry, install.source_url),
      files: install.install.files.map((f) => ({
        from: f.from,
        to: f.to,
        blob_url: `${blobBase}/${f.from}`,
      })),
      env_required: install.install.env_required ?? [],
    });
  } catch (err) {
    if (err instanceof CapsuleAuthError) return privateJson(slug, err.message);
    if (err instanceof CapsuleFetchError) return json({ error: err.message }, 502);
    throw err;
  }
}


// ---------------------------------------------------------------------------
// new: blob (raw bytes)
// ---------------------------------------------------------------------------

async function serveBlob(env: Env, addrSlug: string, filePath: string, token: string | null): Promise<Response> {
  const addr = parseAddress(addrSlug);
  if (!addr) return new Response(`invalid address '${addrSlug}'`, { status: 400 });
  if (!filePath || filePath.includes("..")) {
    return new Response(`bad file path '${filePath}'`, { status: 400 });
  }
  const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
  if (!entry) {
    return new Response(`no capsule found for ${addr.owner}/${addr.name}`, { status: 404 });
  }
  if (isPrivate(entry) && !token) return privateJson(`${addrSlug}/blob/${filePath}`);

  // Whitelist same as the HTML view: any file declared in install.json,
  // plus capsule.yaml + install.json themselves.
  let allowed = filePath === "capsule.yaml" || filePath === "install.json";
  const install = await fetchInstall(entry, token ?? undefined);
  if (!allowed && (install?.install.files.some((f) => f.from === filePath) ?? false)) {
    allowed = true;
  }
  if (!allowed) {
    const declared = install?.install.files.map((f) => f.from).join(", ") ?? "(none)";
    return new Response(
      `'${filePath}' not in this capsule. Declared: ${declared}`,
      { status: 404 },
    );
  }

  try {
    let text: string;
    if (filePath === "capsule.yaml") {
      text = (await fetchCapsule(entry, token ?? undefined)).raw;
    } else if (filePath === "install.json" && install) {
      text = JSON.stringify(install.install, null, 2);
    } else {
      const result = await fetchSibling(entry, filePath, token ?? undefined);
      if (!result) return new Response(`${filePath}: 404 upstream`, { status: 404 });
      text = result.text;
    }
    return new Response(text, {
      headers: {
        "Content-Type": contentTypeFor(filePath),
        "Cache-Control": isPrivate(entry)
          ? "private, no-store"
          : "public, max-age=60, stale-while-revalidate=300",
      },
    });
  } catch (err) {
    if (err instanceof CapsuleAuthError) return privateJson(`${addrSlug}/blob/${filePath}`, err.message);
    if (err instanceof CapsuleFetchError) return new Response(err.message, { status: 502 });
    throw err;
  }
}


// ---------------------------------------------------------------------------

function resolvedJson(entry: RegistryEntry, source_url: string | null) {
  return {
    owner: entry.owner,
    name: entry.name,
    version: entry.version,
    git_url: entry.git_url,
    ref: entry.ref,
    path: entry.path,
    raw_url: rawUrl(entry),
    source_url,
  };
}
