// GET /compose?c=<owner>/<name>[@v],<owner>/<name>[@v],...
//
// Renders a composition view: the topology graph (via Mermaid), per-capsule
// cards, the bundled handoff block, and the joined avoid/invariants list.
// Server-rendered HTML; the only client JS is the Mermaid CDN script.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { parseAddress, resolveWithKV, type RegistryEntry } from "./_lib/registry";
import { fetchCapsule, CapsuleFetchError } from "./_lib/github";
import { layout } from "./_lib/render";
import type { Capsule } from "./_lib/schema";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });

const h = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

const errorPage = (title: string, message: string, status: number): Response =>
  html(
    layout(title, `
<main class="error">
  <h1>${h(title)}</h1>
  <p>${h(message)}</p>
  <p><a href="/">← back to registry</a></p>
</main>`),
    status,
  );

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url);
  const raw = url.searchParams.get("c") || url.searchParams.get("capsules") || "";
  if (!raw) {
    return errorPage(
      "Missing composition",
      "Pass ?c=<owner>/<name>[@v],<owner>/<name>[@v],...",
      400,
    );
  }

  const slugs = raw.split(",").map((s) => s.trim()).filter(Boolean);
  if (slugs.length === 0) {
    return errorPage("Empty composition", "Pass at least one capsule.", 400);
  }
  if (slugs.length > 12) {
    return errorPage(
      "Too many capsules",
      `Limit is 12 capsules per composition (got ${slugs.length}).`,
      400,
    );
  }

  type Resolved = { slug: string; entry: RegistryEntry; capsule: Capsule };
  type Failed = { slug: string; error: string };

  const resolved: Resolved[] = [];
  const failed: Failed[] = [];

  for (const slug of slugs) {
    const addr = parseAddress(slug);
    if (!addr) {
      failed.push({ slug, error: "invalid address" });
      continue;
    }
    const entry = await resolveWithKV(addr, env.CAPSULE_REGISTRY);
    if (!entry) {
      failed.push({ slug, error: "not in registry" });
      continue;
    }
    try {
      const { capsule } = await fetchCapsule(entry);
      resolved.push({ slug, entry, capsule });
    } catch (err) {
      const msg = err instanceof CapsuleFetchError ? err.message : String(err);
      failed.push({ slug, error: msg });
    }
  }

  const title = `Composition: ${resolved.length} capsule${resolved.length === 1 ? "" : "s"}`;
  return html(layout(title, renderCompose(resolved, failed)));
};


// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

interface Resolved {
  slug: string;
  entry: RegistryEntry;
  capsule: Capsule;
}
interface Failed { slug: string; error: string }

function renderCompose(resolved: Resolved[], failed: Failed[]): string {
  const mermaid = buildMermaid(resolved);
  const handoffs = resolved.filter((r) => r.capsule.handoff);

  return `
<main class="compose">
  <h1>${h(`Composition · ${resolved.length} capsule${resolved.length === 1 ? "" : "s"}`)}</h1>
  <p class="lede">
    Pull this composition locally:
    <code>capsule pull ${resolved.map((r) => `capsule://${h(r.entry.owner)}/${h(r.entry.name)}`).join(" ")}</code>
  </p>

  ${failed.length > 0 ? `
  <section class="failed">
    <h2>Could not resolve</h2>
    <ul>
      ${failed.map((f) => `<li><code>${h(f.slug)}</code> — ${h(f.error)}</li>`).join("")}
    </ul>
  </section>` : ""}

  ${resolved.length > 1 ? `
  <section class="topology">
    <h2>Topology</h2>
    <pre class="mermaid">${h(mermaid)}</pre>
    <p class="hint">Dashed = interface require; solid = capsule dependency.</p>
  </section>` : ""}

  <section class="cards">
    <h2>Capsules</h2>
    <div class="card-grid">
      ${resolved.map((r) => capsuleCard(r)).join("\n")}
    </div>
  </section>

  ${handoffs.length > 0 ? `
  <section class="handoff-stack">
    <h2>Handoff — work in progress (${handoffs.length})</h2>
    ${handoffs.map((r) => handoffBlock(r)).join("\n")}
  </section>` : ""}

  ${renderInvariants(resolved)}

  <section class="meta">
    <h2>Bundle these locally</h2>
    <pre><code>${h(`CAPSULE_REGISTRY=https://capsule-registry.pages.dev \\
capsule bundle ${resolved.map((r) => `capsule://${r.entry.owner}/${r.entry.name}`).join(" ")} --for claude -o CLAUDE.md`)}</code></pre>
  </section>
</main>

<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({
    startOnLoad: true,
    theme: "dark",
    themeVariables: { darkMode: true, background: "#0a0b0d" },
    flowchart: { useMaxWidth: true, htmlLabels: false }
  });
</script>`;
}


function capsuleCard(r: Resolved): string {
  const c = r.capsule;
  const summary = (c.purpose?.summary || "").split(/\r?\n/)[0] || "";
  return `
  <a class="capsule-card" href="/c/${h(r.entry.owner)}/${h(r.entry.name)}">
    <div class="name">${h(r.entry.owner)}/${h(r.entry.name)} <span class="version">v${h(r.entry.version)}</span> <span class="badge">${h(c.type)}</span></div>
    <div class="summary">${h(summary)}</div>
    <div class="meta-line">
      ${c.interfaces?.provides?.length ?? 0} provide · ${c.interfaces?.requires?.length ?? 0} require · ${c.verification?.invariants?.length ?? 0} invariant · ${c.handoff ? "<span class=\"hint-mint\">handoff in progress</span>" : "at rest"}
    </div>
  </a>`;
}


function handoffBlock(r: Resolved): string {
  const c = r.capsule;
  const h0 = c.handoff!;
  return `
  <article class="handoff-card">
    <header>
      <strong>${h(r.entry.owner)}/${h(r.entry.name)}</strong>
      <span class="hint">v${h(r.entry.version)}</span>
    </header>
    <p class="objective"><em>Objective.</em> ${h(h0.objective)}</p>
    ${h0.remaining?.length ? `<details open><summary>Remaining (${h0.remaining.length})</summary><ul>${h0.remaining.map((x) => `<li>${h(x)}</li>`).join("")}</ul></details>` : ""}
    ${h0.next_agent_should?.length ? `<details><summary>Next agent should</summary><ul>${h0.next_agent_should.map((x) => `<li>${h(x)}</li>`).join("")}</ul></details>` : ""}
    ${h0.do_not_touch?.length ? `<details><summary>Do not touch</summary><ul>${h0.do_not_touch.map((x) => `<li>${h(x)}</li>`).join("")}</ul></details>` : ""}
  </article>`;
}


function renderInvariants(resolved: Resolved[]): string {
  const all = resolved.flatMap((r) =>
    (r.capsule.verification?.invariants || []).map((inv) => ({
      owner: r.entry.owner,
      name: r.entry.name,
      text: inv,
    })),
  );
  if (all.length === 0) return "";
  return `
  <section class="invariants">
    <h2>Invariants the composition must preserve (${all.length})</h2>
    <ul>
      ${all.map((i) =>
        `<li><code class="hint">${h(i.owner)}/${h(i.name)}</code> · ${h(i.text)}</li>`,
      ).join("")}
    </ul>
  </section>`;
}


function buildMermaid(resolved: Resolved[]): string {
  // Build a flowchart with one node per capsule and edges from the
  // requires.from_capsule / dependencies.capsules links.
  const present = new Set(resolved.map((r) => r.entry.name));
  const lines: string[] = ["flowchart LR"];

  for (const r of resolved) {
    const safeId = mermaidId(r.entry.name);
    const label = `"${r.entry.name}\\nv${r.entry.version}"`;
    lines.push(`  ${safeId}[${label}]`);
  }

  for (const r of resolved) {
    const fromId = mermaidId(r.entry.name);
    for (const dep of r.capsule.dependencies?.capsules || []) {
      if (present.has(dep.name)) {
        lines.push(`  ${fromId} --> ${mermaidId(dep.name)}`);
      }
    }
    for (const req of r.capsule.interfaces?.requires || []) {
      if (req.from_capsule && present.has(req.from_capsule)) {
        const label = `${req.kind}:${req.name}`.replace(/[|]/g, "/");
        lines.push(`  ${fromId} -.->|${label}| ${mermaidId(req.from_capsule)}`);
      }
    }
  }
  return lines.join("\n");
}


function mermaidId(name: string): string {
  return name.replace(/[^a-zA-Z0-9_]/g, "_");
}
