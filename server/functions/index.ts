// GET /  →  registry index, server-rendered.
//
// Lists every capsule in the registry (highest version per (owner, name))
// with name + version + a one-line excerpt of its purpose. Click-through
// goes to /c/<owner>/<name>.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { uniqueLatestWithKV } from "./_lib/registry";
import { fetchCapsule } from "./_lib/github";
import { layout } from "./_lib/render";

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

interface IndexCard {
  owner: string;
  name: string;
  version: string;
  summary: string;
  type: string;
}

async function buildCard(entry: Awaited<ReturnType<typeof uniqueLatestWithKV>>[number]): Promise<IndexCard> {
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

export const onRequestGet: PagesFunction<Env> = async ({ env }) => {
  const entries = await uniqueLatestWithKV(env.CAPSULE_REGISTRY);
  const cards = await Promise.all(entries.map(buildCard));

  const list = cards.map((c) => `
  <a class="capsule-card" href="/c/${escape(c.owner)}/${escape(c.name)}">
    <div class="name">${escape(c.owner)}/${escape(c.name)} <span class="version">v${escape(c.version)}</span> <span class="badge">${escape(c.type)}</span></div>
    <div class="summary">${escape(c.summary)}</div>
  </a>`).join("\n");

  return html(layout("Registry", `
<main class="index">
  <section class="quickstart">
    <h1>Use these capsules with Claude</h1>
    <p class="quickstart-lede">
      Install the <code>capsule-compose</code> skill once, then describe
      what you want to build. Claude reads this registry, picks fitting
      capsules, asks you to confirm, and reconstructs a runnable project.
    </p>
    <pre class="install-cmd"><code>curl -fsSL https://capsule-registry.pages.dev/install-skill.sh | bash</code></pre>
    <p class="quickstart-hint">
      After installing, restart Claude Code and try a prompt like:<br>
      <em>"find me capsules for a video chat app with auth and recording"</em><br>
      <em>"build a chat starter from these capsules"</em>
    </p>
    <p class="quickstart-meta">
      <a href="/install-skill.sh">View the install script first</a>
      <span class="sep">·</span>
      <a href="https://github.com/quake0day/capsule">capsule on GitHub (Apache-2.0)</a>
      <span class="sep">·</span>
      <a href="https://github.com/quake0day/capsule/blob/main/SPEC.md">capsule.yaml spec</a>
    </p>
  </section>

  <section class="capsules">
    <h2>Registry <span class="count">· ${entries.length} capsule${entries.length === 1 ? "" : "s"}</span></h2>
    <p class="lede">
      Click any capsule for its man page. Pull from the command line with
      <code>capsule pull capsule://&lt;owner&gt;/&lt;name&gt;</code>.
    </p>
    ${list}
  </section>
</main>`));
};
