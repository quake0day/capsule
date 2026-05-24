// GET /  →  registry index, server-rendered.
//
// Lists every capsule in the registry (highest version per (owner, name))
// with name + version + a one-line excerpt of its purpose. Click-through
// goes to /c/<owner>/<name>.

import type { PagesFunction } from "@cloudflare/workers-types";

import { uniqueLatest } from "./_lib/registry";
import { fetchCapsule } from "./_lib/github";
import { layout } from "./_lib/render";

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });

const escape = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

interface IndexCard {
  owner: string;
  name: string;
  version: string;
  summary: string;
  type: string;
}

async function buildCard(entry: ReturnType<typeof uniqueLatest>[number]): Promise<IndexCard> {
  try {
    const { capsule } = await fetchCapsule(entry);
    const firstLine = (capsule.purpose?.summary ?? "").trim().split(/\r?\n/)[0] ?? "";
    return {
      owner: entry.owner,
      name: entry.name,
      version: entry.version,
      summary: firstLine,
      type: capsule.type,
    };
  } catch {
    return {
      owner: entry.owner,
      name: entry.name,
      version: entry.version,
      summary: "(failed to fetch capsule.yaml)",
      type: "?",
    };
  }
}

export const onRequestGet: PagesFunction = async () => {
  const entries = uniqueLatest();
  const cards = await Promise.all(entries.map(buildCard));

  const list = cards.map((c) => `
  <a class="capsule-card" href="/c/${escape(c.owner)}/${escape(c.name)}">
    <div class="name">${escape(c.owner)}/${escape(c.name)} <span class="version">v${escape(c.version)}</span> <span class="badge">${escape(c.type)}</span></div>
    <div class="summary">${escape(c.summary)}</div>
  </a>`).join("\n");

  return html(layout("Registry", `
<main class="index">
  <h1>Registry</h1>
  <p class="lede">
    ${entries.length} capsule${entries.length === 1 ? "" : "s"} registered.
    Click into one to read its man page. Pull any of them with
    <code>capsule pull capsule://&lt;owner&gt;/&lt;name&gt;</code>.
  </p>
  ${list}
</main>`));
};
