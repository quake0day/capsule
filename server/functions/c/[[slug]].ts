// GET /c/<owner>/<name>[@<version>]                 → the man-page HTML
// GET /c/<owner>/<name>[@<version>]/blob/<path>     → per-file source view
//
// Also accepts /c/_/<name> as a placeholder for cross-capsule links where
// the owner isn't known — falls back to "first match" in the registry.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import {
  parseAddress,
  resolveWithKV,
  allEntriesWithKV,
  type RegistryEntry,
} from "../_lib/registry";
import {
  fetchCapsule,
  fetchInstall,
  fetchSibling,
  CapsuleFetchError,
} from "../_lib/github";
import { renderCapsule, renderFile, layout } from "../_lib/render";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });

const escape = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

const errorPage = (title: string, message: string, status: number): Response =>
  html(
    layout(title, `
<main class="error">
  <h1>${escape(title)}</h1>
  <p>${escape(message)}</p>
  <p><a href="/">← back to registry</a></p>
</main>`),
    status,
  );

const joinSlug = (slug: string | string[] | undefined): string =>
  !slug ? "" : Array.isArray(slug) ? slug.join("/") : slug;


// Detect /blob/ split. Returns { addrSlug, filePath } if the slug encodes a
// per-file view, or null otherwise.
function splitBlob(slug: string): { addrSlug: string; filePath: string } | null {
  const BLOB_RE = /\/blob\//;
  const m = BLOB_RE.exec(slug);
  if (!m) return null;
  return {
    addrSlug: slug.slice(0, m.index),
    filePath: slug.slice(m.index + "/blob/".length),
  };
}


export const onRequestGet: PagesFunction<Env> = async ({ params, env }) => {
  const slug = joinSlug(params.slug);
  if (!slug) return errorPage("Bad request", "Missing capsule address.", 400);

  const blob = splitBlob(slug);
  if (blob) {
    return await handleBlob(env, blob.addrSlug, blob.filePath);
  }
  return await handleManPage(env, slug);
};


// ---------------------------------------------------------------------------
// man-page (now with Source-files section)
// ---------------------------------------------------------------------------

async function handleManPage(env: Env, slug: string): Promise<Response> {
  const addr = await resolveSlug(env, slug);
  if (!addr.ok) return addr.response;

  try {
    const { capsule } = await fetchCapsule(addr.entry);
    const installResult = await fetchInstall(addr.entry);
    const files = installResult?.install.files ?? [];
    const title = `${capsule.name} v${capsule.version}`;
    return html(layout(title, renderCapsule(addr.entry, capsule, { files })));
  } catch (err) {
    if (err instanceof CapsuleFetchError) {
      return errorPage("Upstream fetch failed", err.message, 502);
    }
    throw err;
  }
}


// ---------------------------------------------------------------------------
// per-file blob view
// ---------------------------------------------------------------------------

async function handleBlob(
  env: Env,
  addrSlug: string,
  filePath: string,
): Promise<Response> {
  const addr = await resolveSlug(env, addrSlug);
  if (!addr.ok) return addr.response;

  if (!filePath || filePath.includes("..")) {
    return errorPage("Invalid file path", `Refusing to serve '${filePath}'.`, 400);
  }

  // Confirm the file is actually declared by the capsule's install.json.
  // (We allow capsule.yaml and install.json themselves as a convenience.)
  let allowed = filePath === "capsule.yaml" || filePath === "install.json";
  const install = await fetchInstall(addr.entry);
  const declared = install?.install.files.map((f) => f.from) ?? [];
  if (!allowed && declared.includes(filePath)) allowed = true;

  if (!allowed) {
    return errorPage(
      "File not in this capsule",
      `'${filePath}' is not listed in install.json. ` +
        (declared.length ? `Declared files: ${declared.join(", ")}` : "(this capsule has no install.json.)"),
      404,
    );
  }

  try {
    let text: string | null;
    let sourceUrl: string;
    if (filePath === "capsule.yaml") {
      const { raw, source_url } = await fetchCapsule(addr.entry);
      text = raw;
      sourceUrl = source_url;
    } else if (filePath === "install.json" && install) {
      text = JSON.stringify(install.install, null, 2);
      sourceUrl = install.source_url;
    } else {
      const result = await fetchSibling(addr.entry, filePath);
      if (!result) {
        return errorPage("File not found", `${filePath} returned 404 from the source repo.`, 404);
      }
      text = result.text;
      sourceUrl = result.source_url;
    }
    const title = `${addr.entry.name} · ${filePath}`;
    return html(layout(title, renderFile(addr.entry, filePath, text, sourceUrl)));
  } catch (err) {
    if (err instanceof CapsuleFetchError) {
      return errorPage("Upstream fetch failed", err.message, 502);
    }
    throw err;
  }
}


// ---------------------------------------------------------------------------
// shared address resolution
// ---------------------------------------------------------------------------

type ResolveOk = { ok: true; entry: RegistryEntry };
type ResolveFail = { ok: false; response: Response };

async function resolveSlug(env: Env, slug: string): Promise<ResolveOk | ResolveFail> {
  let addr = parseAddress(slug);

  // /c/_/<name> shorthand: find first capsule with this name across owners.
  if (!addr && slug.startsWith("_/")) {
    const bareName = slug.slice(2).split("@")[0];
    const versionPart = slug.includes("@") ? slug.split("@")[1] : undefined;
    const entries = await allEntriesWithKV(env.CAPSULE_REGISTRY);
    const candidate = entries.find((e) => e.name === bareName);
    if (candidate) {
      addr = { owner: candidate.owner, name: bareName, version: versionPart };
    }
  }

  if (!addr) {
    return {
      ok: false,
      response: errorPage(
        "Invalid address",
        `Could not parse '${slug}'. Expected <owner>/<name>[@<version>].`,
        400,
      ),
    };
  }

  const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
  if (!entry) {
    const v = addr.version ? "@" + addr.version : "";
    return {
      ok: false,
      response: errorPage(
        "Capsule not found",
        `No capsule in the registry for ${addr.owner}/${addr.name}${v}.`,
        404,
      ),
    };
  }
  return { ok: true, entry };
}
