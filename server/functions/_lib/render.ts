// HTML rendering for /c/<owner>/<name>[@<version>] — the man page view.
//
// Server-side templating. No client JS required. The output is one self-
// contained <article> per capsule, ready to be wrapped in the standard
// layout (see layout()).

import type {
  Capsule,
  ExtensionPoint,
  InterfaceProvides,
  InterfaceRequires,
} from "./schema";
import type { RegistryEntry } from "./registry";

const h = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c] as string));

/** Wrap rendered <main> content in the standard page chrome. */
export function layout(title: string, main: string, opts: { canonical?: string } = {}): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${h(title)} — capsule</title>
<link rel="stylesheet" href="/assets/style.css">
${opts.canonical ? `<link rel="canonical" href="${h(opts.canonical)}">` : ""}
</head>
<body>
<header class="topbar">
  <a class="brand" href="/">capsule</a>
  <span class="tagline">AI-native Unix-like composition layer</span>
</header>
${main}
<footer class="footer">
  <p>capsule v0.2 · <a href="https://github.com/quake0day/capsule">github.com/quake0day/capsule</a></p>
</footer>
</body>
</html>`;
}

/** Render the man page for a single capsule. */
export function renderCapsule(entry: RegistryEntry, capsule: Capsule): string {
  const addr = `capsule://${h(entry.owner)}/${h(entry.name)}@${h(entry.version)}`;
  const sourceLink = entry.git_url + (entry.git_url.endsWith("/") ? "" : "/") + "blob/" + entry.ref + "/" + entry.path;

  return `
<main class="manpage">
  <section class="title">
    <h1>${h(capsule.name)} <span class="version">v${h(capsule.version)}</span></h1>
    <p class="type-line"><span class="badge">${h(capsule.type)}</span>${capsule.domain ? ` <span class="domain">${h(capsule.domain)}</span>` : ""}</p>
    <p class="addr"><code>${addr}</code></p>
    <p class="purpose">${h(capsule.purpose.summary).replace(/\n/g, "<br>")}</p>
  </section>

  ${section("Owns", capsule.purpose.owns)}
  ${section("Does not own", capsule.purpose.does_not_own)}
  ${capsule.agent?.summary_for_ai ? aiOrientation(capsule.agent.summary_for_ai) : ""}
  ${section("Avoid", capsule.agent?.avoid, "avoid")}
  ${extensionPoints(capsule.agent?.extension_points)}

  ${provides(capsule.interfaces?.provides)}
  ${requires(capsule.interfaces?.requires)}
  ${dependencies(capsule)}

  ${section("Invariants (must always hold)", capsule.verification?.invariants, "invariants")}
  ${handoff(capsule.handoff)}
  ${glossary(capsule.agent?.glossary)}

  <section class="meta">
    <h2>Source</h2>
    <p><a href="${h(sourceLink)}">${h(sourceLink)}</a></p>
    <p class="hint">Pull this capsule locally:</p>
    <pre><code>capsule pull ${addr}</code></pre>
    <p class="hint">Or render to your terminal:</p>
    <pre><code>capsule man ${addr}</code></pre>
  </section>
</main>`;
}

function section(title: string, items: string[] | undefined, cls = ""): string {
  if (!items || items.length === 0) return "";
  return `
  <section${cls ? ` class="${cls}"` : ""}>
    <h2>${h(title)}</h2>
    <ul>
      ${items.map((it) => `<li>${h(it)}</li>`).join("\n      ")}
    </ul>
  </section>`;
}

function aiOrientation(text: string): string {
  return `
  <section class="ai">
    <h2>AI orientation</h2>
    <p>${h(text).replace(/\n\n+/g, "</p><p>").replace(/\n/g, "<br>")}</p>
  </section>`;
}

function extensionPoints(eps: ExtensionPoint[] | undefined): string {
  if (!eps || eps.length === 0) return "";
  return `
  <section>
    <h2>Extension points</h2>
    <dl class="ext-points">
      ${eps.map((e) => `
      <dt><code>${h(e.id)}</code> <span class="hint">at <code>${h(e.where)}</code></span></dt>
      <dd>${h(e.contract).replace(/\n/g, "<br>")}</dd>`).join("\n      ")}
    </dl>
  </section>`;
}

function provides(items: InterfaceProvides[] | undefined): string {
  if (!items || items.length === 0) return "";
  return `
  <section>
    <h2>Provides</h2>
    <ul class="iface">
      ${items.map((p) => `<li><code>${h(p.kind)}:${h(p.name)}</code>${p.description ? ` — ${h(p.description)}` : ""}</li>`).join("\n      ")}
    </ul>
  </section>`;
}

function requires(items: InterfaceRequires[] | undefined): string {
  if (!items || items.length === 0) return "";
  return `
  <section>
    <h2>Requires</h2>
    <ul class="iface">
      ${items.map((r) => {
        const tag = `<code>${h(r.kind)}:${h(r.name)}</code>`;
        const from = r.from_capsule
          ? ` from <a href="/c/_/${h(r.from_capsule)}"><code>${h(r.from_capsule)}</code></a>${r.version ? ` <span class="hint">(${h(r.version)})</span>` : ""}`
          : "";
        return `<li>${tag}${from}${r.description ? ` — ${h(r.description)}` : ""}</li>`;
      }).join("\n      ")}
    </ul>
  </section>`;
}

function dependencies(c: Capsule): string {
  const caps = c.dependencies?.capsules ?? [];
  const runtime = c.dependencies?.runtime ?? [];
  if (caps.length === 0 && runtime.length === 0) return "";
  return `
  <section>
    <h2>Dependencies</h2>
    ${caps.length > 0 ? `<h3>Capsules</h3><ul>${caps.map((d) => `<li><a href="/c/_/${h(d.name)}"><code>${h(d.name)}</code></a>${d.version ? ` <span class="hint">${h(d.version)}</span>` : ""}</li>`).join("")}</ul>` : ""}
    ${runtime.length > 0 ? `<h3>Runtime</h3><ul>${runtime.map((r) => Object.entries(r).map(([k, v]) => `<li><code>${h(k)}</code> <span class="hint">${h(String(v))}</span></li>`).join("")).join("")}</ul>` : ""}
  </section>`;
}

function handoff(h0: Capsule["handoff"]): string {
  if (!h0) return "";
  return `
  <section class="handoff">
    <h2>Handoff <span class="hint">— work in progress</span></h2>
    <p class="objective"><strong>Objective.</strong> ${h(h0.objective)}</p>
    ${h0.completed?.length ? `<h3>Completed</h3><ul>${h0.completed.map((x) => `<li>${h(x)}</li>`).join("")}</ul>` : ""}
    ${h0.remaining?.length ? `<h3>Remaining</h3><ul>${h0.remaining.map((x) => `<li>${h(x)}</li>`).join("")}</ul>` : ""}
    ${h0.next_agent_should?.length ? `<h3>Next agent should</h3><ul>${h0.next_agent_should.map((x) => `<li>${h(x)}</li>`).join("")}</ul>` : ""}
    ${h0.do_not_touch?.length ? `<h3>Do not touch</h3><ul>${h0.do_not_touch.map((x) => `<li>${h(x)}</li>`).join("")}</ul>` : ""}
    ${h0.open_questions?.length ? `<h3>Open questions</h3><ul>${h0.open_questions.map((x) => `<li>${h(x)}</li>`).join("")}</ul>` : ""}
  </section>`;
}

function glossary(g: Record<string, string> | undefined): string {
  if (!g || Object.keys(g).length === 0) return "";
  return `
  <section>
    <h2>Glossary</h2>
    <dl class="glossary">
      ${Object.entries(g).map(([k, v]) => `<dt><code>${h(k)}</code></dt><dd>${h(v)}</dd>`).join("")}
    </dl>
  </section>`;
}
