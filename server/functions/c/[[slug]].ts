// GET /c/<owner>/<name>[@<version>] → the man-page HTML.
// Also accepts /c/_/<name> as a placeholder for cross-capsule links where
// the owner isn't known — falls back to "first match" in the registry.

import type { PagesFunction } from "@cloudflare/workers-types";

import { parseAddress, resolve, allEntries } from "../_lib/registry";
import type { RegistryEntry } from "../_lib/registry";
import { fetchCapsule, CapsuleFetchError } from "../_lib/github";
import { renderCapsule, layout } from "../_lib/render";

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });

const joinSlug = (slug: string | string[] | undefined): string =>
  !slug ? "" : Array.isArray(slug) ? slug.join("/") : slug;

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

const escape = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

export const onRequestGet: PagesFunction = async ({ params }) => {
  const slug = joinSlug(params.slug);
  if (!slug) return errorPage("Bad request", "Missing capsule address.", 400);

  let addr = parseAddress(slug);

  // /c/_/<name> shorthand: find first capsule with this name across owners.
  if (!addr && slug.startsWith("_/")) {
    const bareName = slug.slice(2).split("@")[0];
    const versionPart = slug.includes("@") ? slug.split("@")[1] : undefined;
    const candidate = allEntries().find((e: RegistryEntry) => e.name === bareName);
    if (candidate) {
      addr = { owner: candidate.owner, name: bareName, version: versionPart };
    }
  }

  if (!addr) {
    return errorPage(
      "Invalid address",
      `Could not parse '${slug}'. Expected <owner>/<name>[@<version>].`,
      400,
    );
  }

  const entry = resolve(addr);
  if (!entry) {
    const v = addr.version ? "@" + addr.version : "";
    return errorPage(
      "Capsule not found",
      `No capsule in the registry for ${addr.owner}/${addr.name}${v}.`,
      404,
    );
  }

  try {
    const { capsule } = await fetchCapsule(entry);
    const title = `${capsule.name} v${capsule.version}`;
    return html(layout(title, renderCapsule(entry, capsule)));
  } catch (err) {
    if (err instanceof CapsuleFetchError) {
      return errorPage(
        "Upstream fetch failed",
        err.message,
        502,
      );
    }
    throw err;
  }
};
