// GET /research — the research project landing page.
//
// One long server-rendered page meant for sharing with collaborators,
// reviewers, and conference reads. Pulls live counts from the same data
// sources the registry uses (KV-backed registry + results.json) so the
// numbers update whenever those do.

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import { uniqueLatestWithKV } from "./_lib/registry";
import { layout } from "./_lib/render";
import benchData from "../benchmarks/results.json";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

interface BenchRun {
  repo_name: string;
  model: string;
  model_full: string;
  success: boolean;
  passes?: string;
}

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });


export const onRequestGet: PagesFunction<Env> = async ({ env }) => {
  // Live counts: registry capsules (latest per owner/name) + benchmark stats.
  const entries = await uniqueLatestWithKV(env.CAPSULE_REGISTRY);
  const capsuleCount = entries.length;
  const ownerCount = new Set(entries.map((e) => e.owner)).size;

  const runs = (benchData as { runs: BenchRun[] }).runs ?? [];
  const distinctRepos = new Set(runs.map((r) => r.repo_name)).size;
  const distinctModels = new Set(runs.map((r) => r.model_full)).size;
  const multiRuns = runs.filter((r) => r.passes === "multi").length;

  // Distinct source repos that have been decomposed into capsules.
  // Pull from the unique owner/source-prefix patterns we already use.
  const decomposedRepos = countDecomposedRepos(entries);

  return html(layout("Research project", render(
    capsuleCount, ownerCount, decomposedRepos,
    runs.length, distinctModels, distinctRepos, multiRuns,
  ), {
    ogTitle: "Capsule — a composable subsystem layer for AI software engineering",
    ogDescription: `${capsuleCount} live capsules across ${decomposedRepos} decomposed OSS repos, benchmarked across ${distinctModels} LLMs.`,
  }));
};


function countDecomposedRepos(entries: Array<{ name: string }>): number {
  // Heuristic: namespace prefixes we've seen so far (yingjieli-, f4c-,
  // lkmeet-, ext-, scnmnt-, bt-, cverse-, mp-, cftest-).
  const prefixes = new Set<string>();
  for (const e of entries) {
    const m = /^([a-z0-9]+)-/.exec(e.name);
    if (m) prefixes.add(m[1]);
  }
  // Exclude prefixes that are clearly experimental (test-, mp-, cftest-).
  for (const p of ["mp", "cftest", "test"]) prefixes.delete(p);
  return prefixes.size;
}


function render(
  capsuleCount: number, ownerCount: number, decomposedRepos: number,
  totalRuns: number, distinctModels: number, distinctRepos: number, multiRuns: number,
): string {
  return `
<main class="research">

  <section class="r-hero">
    <img class="r-logo" src="/assets/logo.png" alt="" width="96" height="96">
    <p class="r-eyebrow">Capsule · research project · 2026</p>
    <h1>The composable subsystem layer<br>for AI software engineering</h1>
    <p class="r-tagline">
      Unix piped <em>text</em> between programs.<br>
      Capsule pipes <em>engineering context</em> between AI agents.
    </p>
    <div class="r-hero-cta">
      <a class="btn btn-primary" href="https://github.com/quake0day/capsule">View on GitHub</a>
      <a class="btn btn-ghost" href="/benchmarks">See the benchmarks &rarr;</a>
    </div>
    <figure class="r-hero-figure">
      <img src="/assets/hero.png" alt="A glowing mint-green capsule surrounded by a quiet constellation of dots and lines">
    </figure>
  </section>

  <section class="r-stats">
    <div class="r-stat">
      <div class="r-stat-num">${capsuleCount}</div>
      <div class="r-stat-lbl">capsules in the live registry</div>
    </div>
    <div class="r-stat">
      <div class="r-stat-num">${decomposedRepos}</div>
      <div class="r-stat-lbl">real OSS repos auto-decomposed</div>
    </div>
    <div class="r-stat">
      <div class="r-stat-num">${distinctModels}</div>
      <div class="r-stat-lbl">LLMs benchmarked head-to-head</div>
    </div>
    <div class="r-stat">
      <div class="r-stat-num">${totalRuns}</div>
      <div class="r-stat-lbl">benchmark runs in the dataset</div>
    </div>
  </section>

  <section class="r-section">
    <h2>The idea</h2>
    <p>
      Every AI coding agent today re-derives software architecture from raw
      source on every task. Context windows overflow, contracts get re-invented,
      and integration work silently breaks at subsystem boundaries.
    </p>
    <p>
      <strong>A capsule</strong> is a self-contained subsystem packaged as
      four things: a contract (provides + requires + invariants), the
      code that implements it, a verification suite that proves it still
      works, and an AI-readable orientation that tells any agent how to
      think about it. Capsules are addressable
      (<code>capsule://&lt;owner&gt;/&lt;name&gt;@&lt;version&gt;</code>),
      composable, and bidirectional — they can be authored by humans,
      <em>decomposed</em> from existing repos by an LLM, and
      <em>reconstructed</em> back into runnable systems on demand.
    </p>
    <p>
      The pitch in one image: GitHub stores code, Docker Hub stores
      runtime images, Hugging Face stores models. Nothing stores the
      <strong>subsystem</strong> — the unit AI agents actually need to
      reason about. Capsule fills that gap.
    </p>
  </section>

  <section class="r-section">
    <h2>What is novel</h2>
    <ol class="r-contrib">
      <li>
        <strong>A new abstraction.</strong> capsule.yaml v0.1 is a
        subsystem-level unit between "module" and "container" with
        explicit AI orientation + reusability notes baked into the
        contract. Different unit from MCP (tool connectivity) and from
        CLAUDE.md / AGENTS.md (single-shot prompts).
      </li>
      <li>
        <strong>Bidirectional pipeline.</strong> An LLM-driven decomposer
        (<code>capsule decompose</code>) extracts capsules from arbitrary
        github repos in one command; a mechanical reconstructor
        (<code>capsule reconstruct</code>) rebuilds runnable systems
        from capsule sets + data. The round-trip works end-to-end.
      </li>
      <li>
        <strong>Verification as a first-class field.</strong> Every
        capsule carries declarative invariants + executable checks; the
        registry surfaces them; AI-generated regression tests
        (<code>capsule generate-tests</code>) read invariants and draft
        pytest scaffolds.
      </li>
      <li>
        <strong>Live evaluation.</strong> An open benchmark harness
        compares 7 LLM providers (Anthropic, Gemini, 5 Cloudflare Workers
        AI models including gpt-oss, Llama, Mistral, Kimi) on the
        decomposition task across ${distinctRepos} real OSS repos.
        ${multiRuns > 0 ? `Single-pass and multi-pass modes are compared (${multiRuns} multi-pass runs).` : ""}
      </li>
    </ol>
  </section>

  <section class="r-section">
    <h2>Live artifacts</h2>
    <p>
      Everything below is real, public, and reproducible from the source.
      No screenshots, no mocks.
    </p>
    <ul class="r-links">
      <li>
        <a href="/">Registry index</a>
        — browse the ${capsuleCount} live capsules across ${ownerCount} namespaces
      </li>
      <li>
        <a href="/c/quake0day/yingjieli-admin-auth">Example capsule (man page)</a>
        — yingjieli-admin-auth: HMAC sessions, rate-limit, source files clickable
      </li>
      <li>
        <a href="/benchmarks">Benchmarks page</a>
        — head-to-head LLM comparison, per-run cost + coverage + wall-clock
      </li>
      <li>
        <a href="https://github.com/quake0day/capsule">Source code on GitHub</a>
        — Apache-2.0; Python CLI (15 commands) + Cloudflare Pages registry (TypeScript)
      </li>
      <li>
        <a href="https://github.com/quake0day/capsule/blob/main/SPEC.md">capsule.yaml spec</a>
        — the on-disk format, v0.1
      </li>
      <li>
        <a href="https://github.com/quake0day/capsule/blob/main/docs/L2-DESIGN.md">L2 design doc</a>
        — the Unix-philosophy framing + registry architecture
      </li>
      <li>
        <a href="https://github.com/quake0day/yingjieli-capsules">yingjieli-capsules</a>
        — a real artist portfolio site, decomposed into 6 reconstructable capsules
      </li>
      <li>
        <a href="https://github.com/quake0day/cyberverse-capsules">cyberverse-capsules</a>
        — the largest case: 387 files → 13 capsules including an adapter pattern Gemini correctly recovered
      </li>
      <li>
        <a href="/install-skill.sh">Claude Code skill installer</a>
        — one-line install for the discovery+compose skill
      </li>
    </ul>
  </section>

  <section class="r-section">
    <h2>Try it in three commands</h2>
    <pre class="r-demo"><code># 1. Install the CLI
pip install -e git+https://github.com/quake0day/capsule.git#egg=capsule-cli

# 2. Read any registered capsule's man page locally
capsule man capsule://quake0day/yingjieli-admin-auth

# 3. Decompose a github repo of your choice + register it
GEMINI_API_KEY=...
capsule decompose https://github.com/&lt;owner&gt;/&lt;repo&gt; \\
  --out ./out --namespace mystuff --register mystuff-capsules</code></pre>
  </section>

  <section class="r-section">
    <h2>Paper plan</h2>
    <p>
      Primary targets <strong>ICSE 2027 Demonstrations Track</strong> +
      <strong>ICSE 2027 NIER</strong> — same submission deadline
      (October 23, 2026), 4-page format, overlapping ~80% of content.
      Demo paper presents the system; NIER frames the abstraction as a
      new direction.
    </p>
    <p>
      What's needed beyond what's shipped: a more rigorous evaluation
      (target N=100+ repos for the decomposer; a small N=10-15 developer
      study comparing capsule-armed vs vanilla agent prompts on
      reconstruction tasks); a tighter related-work positioning vs MCP /
      CLAUDE.md / AGENTS.md / RAG / package managers; ablations isolating
      the abstraction's contribution from the underlying LLM's.
    </p>
  </section>

  <section class="r-section r-collab">
    <h2>Collaborators welcome</h2>
    <p>
      If any of this aligns with your interests — LLMs for software
      engineering, developer tools, programming language design,
      empirical SE, or AI-assisted code reuse — I'd love to talk. The
      system is far enough along that there's a real artifact to evaluate,
      and the deadline window leaves room for one solid empirical
      contribution.
    </p>
    <p>
      Contact: <a href="mailto:quake0day@gmail.com">quake0day@gmail.com</a>
      · <a href="https://github.com/quake0day/capsule">repo</a>
      · <a href="https://github.com/quake0day/capsule/issues">open issues</a>
    </p>
  </section>

</main>`;
}
