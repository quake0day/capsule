// GET /  →  registry index, server-rendered.
//
// Lists every capsule in the registry (highest version per (owner, name))
// with name + version + a one-line excerpt of its purpose. Click-through
// goes to /c/<owner>/<name>.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { uniqueLatestWithKV } from "./_lib/registry";
import { fetchCapsule } from "./_lib/github";
import { extractToken, verifyGithubToken } from "./_lib/auth";
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
  visibility: "public" | "private";
}

async function buildCard(
  entry: Awaited<ReturnType<typeof uniqueLatestWithKV>>[number],
  token: string | null,
): Promise<IndexCard> {
  const visibility: "public" | "private" = entry.visibility === "private" ? "private" : "public";
  try {
    const { capsule } = await fetchCapsule(entry, token ?? undefined);
    const firstLine = (capsule.purpose?.summary ?? "").trim().split(/\r?\n/)[0] ?? "";
    return {
      owner: entry.owner,
      name: entry.name,
      version: entry.version,
      summary: firstLine,
      type: capsule.type,
      visibility,
    };
  } catch {
    return {
      owner: entry.owner,
      name: entry.name,
      version: entry.version,
      summary: "(failed to fetch capsule.yaml)",
      type: "?",
      visibility,
    };
  }
}

export const onRequestGet: PagesFunction<Env> = async ({ env, request }) => {
  // Anonymous view: public capsules only.
  // Signed-in view (cookie / Authorization): public + caller's own private.
  const token = extractToken(request);
  const login = token ? await verifyGithubToken(token) : null;
  const showPrivate = !!login;

  const allEntries = await uniqueLatestWithKV(env.CAPSULE_REGISTRY, { includePrivate: showPrivate });
  const entries = showPrivate
    ? allEntries.filter((e) => e.visibility !== "private" || e.owner.toLowerCase() === login!.toLowerCase())
    : allEntries;

  const cards = await Promise.all(entries.map((e) => buildCard(e, token)));
  cards.sort((a, b) => {
    // private (yours) first, then alphabetical
    if (a.visibility !== b.visibility) return a.visibility === "private" ? -1 : 1;
    return `${a.owner}/${a.name}`.localeCompare(`${b.owner}/${b.name}`);
  });

  const myPrivateCount = cards.filter((c) => c.visibility === "private").length;
  const publicCount = cards.length - myPrivateCount;

  const list = cards.map((c) => `
  <a class="capsule-card${c.visibility === "private" ? " card-private" : ""}" href="/c/${escape(c.owner)}/${escape(c.name)}">
    <div class="name">${escape(c.owner)}/${escape(c.name)} <span class="version">v${escape(c.version)}</span> <span class="badge">${escape(c.type)}</span>${c.visibility === "private" ? ` <span class="badge badge-private">private</span>` : ""}</div>
    <div class="summary">${escape(c.summary)}</div>
  </a>`).join("\n");

  const authBar = login
    ? `<p class="auth-bar">Signed in as <strong>@${escape(login)}</strong> · seeing ${myPrivateCount} of your private capsule${myPrivateCount === 1 ? "" : "s"} alongside ${publicCount} public · <a href="/auth/logout">sign out</a></p>`
    : `<p class="auth-bar"><a href="/auth?return=/">Sign in</a> with a GitHub token to also list your private capsules here.</p>`;

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
      <a href="/research">Research project</a>
      <span class="sep">·</span>
      <a href="/benchmarks">LLM benchmarks</a>
      <span class="sep">·</span>
      <a href="/install-skill.sh">View install script</a>
      <span class="sep">·</span>
      <a href="https://github.com/quake0day/capsule">GitHub (Apache-2.0)</a>
      <span class="sep">·</span>
      <a href="https://github.com/quake0day/capsule/blob/main/SPEC.md">spec</a>
    </p>
  </section>

  <section class="capsules">
    <h2>Registry <span class="count">· ${cards.length} capsule${cards.length === 1 ? "" : "s"}${myPrivateCount > 0 ? ` (${myPrivateCount} private)` : ""}</span></h2>
    ${authBar}
    <p class="lede">
      Click any capsule for its man page. Pull from the command line with
      <code>capsule pull capsule://&lt;owner&gt;/&lt;name&gt;</code>.
    </p>
    ${list}
  </section>
</main>`));
};
