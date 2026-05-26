// GET /research — the research project landing page.
//
// Editorial layout: arXiv-style abstract up top, numbered sections below.
// Pulls live counts from the same data sources the registry uses (KV-backed
// registry + results.json) so numbers update on each deploy.

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
  const entries = await uniqueLatestWithKV(env.CAPSULE_REGISTRY);
  const capsuleCount = entries.length;
  const ownerCount = new Set(entries.map((e) => e.owner)).size;

  const runs = (benchData as { runs: BenchRun[] }).runs ?? [];
  const distinctRepos = new Set(runs.map((r) => r.repo_name)).size;
  const distinctModels = new Set(runs.map((r) => r.model_full)).size;
  const successfulRuns = runs.filter((r) => r.success).length;
  const decomposedRepos = countDecomposedRepos(entries);

  return html(layout("Research memo", render(
    capsuleCount, ownerCount, decomposedRepos,
    runs.length, distinctModels, distinctRepos, successfulRuns,
  ), {
    ogTitle: "Capsule — a composable subsystem layer for AI software engineering",
    ogDescription: `Research memo. ${capsuleCount} capsules, ${decomposedRepos} decomposed OSS repos, ${distinctModels} LLMs benchmarked.`,
  }));
};


function countDecomposedRepos(entries: Array<{ name: string }>): number {
  const prefixes = new Set<string>();
  for (const e of entries) {
    const m = /^([a-z0-9]+)-/.exec(e.name);
    if (m) prefixes.add(m[1]);
  }
  for (const p of ["mp", "cftest", "test"]) prefixes.delete(p);
  return prefixes.size;
}


function render(
  capsuleCount: number, ownerCount: number, decomposedRepos: number,
  totalRuns: number, distinctModels: number, distinctRepos: number, successfulRuns: number,
): string {
  const successRate = totalRuns > 0 ? Math.round((successfulRuns * 100) / totalRuns) : 0;
  return `
<main class="memo">

  <article class="memo-paper">

    <header class="memo-head">
      <p class="memo-eyebrow">Capsule &nbsp;·&nbsp; Research Memo &nbsp;·&nbsp; v0.2 &nbsp;·&nbsp; May 2026</p>
      <h1 class="memo-title">
        Capsule: a composable subsystem layer<br>
        <em>for AI software engineering.</em>
      </h1>
      <p class="memo-byline">
        Si Chen &nbsp;·&nbsp; West Chester University &nbsp;·&nbsp;
        <a href="mailto:schen@wcupa.edu">schen@wcupa.edu</a>
        &nbsp;&nbsp;<span class="memo-byline-sep">&middot;</span>&nbsp;&nbsp;
        <span class="memo-byline-date">May 26, 2026</span>
      </p>
    </header>

    <section class="memo-abstract" aria-label="Abstract">
      <p class="memo-abstract-label">Abstract.</p>
      <p>
        Current AI coding agents re-derive software architecture from raw
        source on every task, producing context overflow, reinvented
        contracts, and silent failures at subsystem boundaries. We argue the
        bottleneck is not model capability but the absence of a unit AI
        agents can <em>read, compose, and verify</em>. We introduce
        <strong>the capsule</strong> &mdash; a self-contained subsystem
        packaged as a typed contract (provides, requires, invariants), an
        implementation, an executable verification suite, and an
        AI-readable orientation. Capsules are addressable
        (<code>capsule://owner/name@version</code>), bidirectional
        (decomposed from existing repositories by an LLM; reconstructed
        into runnable systems by a mechanical pipeline), and verifiable
        (every capsule carries declarative invariants and contract tests).
        We instantiate the abstraction as a public registry (currently
        <strong>${capsuleCount} capsules</strong> across <strong>${ownerCount} namespaces</strong>),
        a CLI implementing the round-trip
        (<code>capsule decompose</code>, <code>capsule reconstruct</code>),
        and an open benchmark comparing <strong>${distinctModels} LLM providers</strong>
        on the decomposition task across <strong>${distinctRepos} real OSS repositories</strong>
        (${totalRuns} runs, ${successRate}% overall success). Initial
        results suggest that subsystem-level units, when generated and
        consumed by AI agents through a uniform protocol, materially
        reduce context pressure and integration error in multi-agent
        software workflows.
      </p>
      <p class="memo-keywords">
        <span>Keywords</span> &nbsp;&middot;&nbsp; LLMs for software engineering &nbsp;&middot;&nbsp;
        AI-assisted code reuse &nbsp;&middot;&nbsp; subsystem abstractions &nbsp;&middot;&nbsp;
        verification &nbsp;&middot;&nbsp; developer tools.
      </p>
    </section>

    <section class="memo-numbers" aria-label="Live numbers">
      <div class="memo-num">
        <div class="memo-num-v">${capsuleCount}</div>
        <div class="memo-num-l">capsules in the live registry</div>
      </div>
      <div class="memo-num">
        <div class="memo-num-v">${decomposedRepos}</div>
        <div class="memo-num-l">OSS repositories auto-decomposed</div>
      </div>
      <div class="memo-num">
        <div class="memo-num-v">${distinctModels}</div>
        <div class="memo-num-l">LLM providers benchmarked head-to-head</div>
      </div>
      <div class="memo-num">
        <div class="memo-num-v">${totalRuns}</div>
        <div class="memo-num-l">runs in the open benchmark dataset</div>
      </div>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 1</p>
      <h2>Background &mdash; the bottleneck is no longer the model.</h2>
      <p>
        Every coding agent in production today &mdash; Claude Code, Cursor,
        Codex, Devin, Copilot Agents &mdash; reads source files, infers
        intent, and produces edits. Each treats the repository as a flat
        bag of bytes. The agent re-discovers, on every task, what an
        engineer already knows: where the auth subsystem ends; what the
        billing module promises to its callers; which invariants must hold
        before the deploy is safe.
      </p>
      <p>
        Three failure modes recur. <em>Context overflow:</em> a mid-sized
        service exceeds any practical context window, forcing truncation
        and lossy summarisation. <em>Contract drift:</em> agents reinvent
        types at every call site, producing subtly incompatible code that
        passes unit tests and fails at the boundary. <em>Invariant erosion:</em>
        non-obvious constraints (session-token storage, rate-limit
        coupling, ordering guarantees) get encoded only in human reviewers'
        heads, never re-derived by the next agent.
      </p>
      <p>
        Existing remedies sit at the wrong level. <strong>Package managers</strong>
        (npm, PyPI) version code but ignore subsystem-level contracts.
        <strong>Container images</strong> (Docker, OCI) ship runtimes but
        say nothing about how a unit should be composed with another.
        <strong>Tool protocols</strong> (MCP, function calling) wire
        agents to existing services but do not give the agent a stable
        unit to <em>build with</em>. <strong>Prompt files</strong>
        (CLAUDE.md, AGENTS.md) carry per-repo guidance but do not
        compose, version, or verify.
      </p>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 2</p>
      <h2>The capsule abstraction.</h2>
      <p>
        A <strong>capsule</strong> is the smallest unit a software
        subsystem can occupy and remain useful to an agent. It is one
        directory with six declared fields:
      </p>
      <ol class="memo-list">
        <li><strong>Purpose.</strong> Why the subsystem exists, in one line, plus what it does and does <em>not</em> own.</li>
        <li><strong>Interfaces.</strong> Typed <code>provides</code> and <code>requires</code> &mdash; what the unit exposes, what it expects.</li>
        <li><strong>Invariants.</strong> Constraints that must hold for the unit to be correct; expressed declaratively, checked executably.</li>
        <li><strong>Implementation.</strong> The actual code, packaged so it can be installed mechanically into a target tree.</li>
        <li><strong>Verification.</strong> A first-class field, not an afterthought; tests the capsule runs against itself.</li>
        <li><strong>Agent orientation.</strong> A compact, AI-readable brief: what to think about, what to avoid, where the extension points are.</li>
      </ol>
      <p>
        Capsules are addressable
        (<code>capsule://owner/name@version</code>), composable, and
        bidirectional &mdash; they can be authored by humans, decomposed from
        existing repositories by an LLM, and reconstructed back into
        runnable systems on demand. The abstraction sits one rung above
        the file (too small to carry intent) and one rung below the
        container (too coarse to compose at the granularity an agent
        reasons about).
      </p>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 3</p>
      <h2>A bidirectional pipeline.</h2>
      <p>
        The same protocol is read in two directions. <strong>Decomposition</strong>
        (<code>capsule decompose &lt;repo&gt;</code>) sends a structured
        prompt to a chosen LLM &mdash; tree, READMEs, head of each source
        file, capped at ~90KB &mdash; and parses the response into a set
        of candidate capsules. The parser is tolerant by design: a
        strict-JSON pass first, a regex-cleanup fallback for trailing
        commas and single-quoted keys, and a multi-pass mode (skeleton
        first, per-capsule contract second) for models with smaller
        context windows.
      </p>
      <p>
        <strong>Reconstruction</strong>
        (<code>capsule reconstruct &lt;set&gt;</code>) walks the same
        contracts in reverse: it materialises capsule code into a fresh
        directory tree, wires <code>requires</code> against
        <code>provides</code> via a deterministic resolver, and emits a
        runnable scaffold. The round-trip works end-to-end: a real
        artist-portfolio website (~3k LOC) decomposed by Gemini into six
        capsules and reconstructed by the CLI runs and serves traffic
        without manual intervention.
      </p>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 4</p>
      <h2>Registry &amp; verification.</h2>
      <p>
        Capsules live in an HTTP-addressable registry. The current
        instance (<a href="/">${capsuleCount} live capsules</a>) is
        deployed on Cloudflare Pages with KV-backed indexing and serves
        a man-page view at <code>/c/&lt;owner&gt;/&lt;name&gt;</code> for
        every entry &mdash; rendered server-side, no JavaScript on the
        critical path. Each man page is a faithful projection of the
        underlying <code>capsule.yaml</code>: purpose, interfaces,
        invariants, source files, and the AI orientation block.
      </p>
      <p>
        Verification is treated as a first-class field rather than a
        documentation artifact. Every capsule carries declarative
        invariants the registry surfaces; an auxiliary command
        (<code>capsule generate-tests</code>) reads those invariants and
        drafts a <code>pytest</code> scaffold, providing a deterministic
        starting point for AI-generated regression coverage that is
        explicitly tied to the contract rather than to the current
        implementation.
      </p>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 5</p>
      <h2>Live evaluation.</h2>
      <p>
        An open benchmark harness compares <strong>${distinctModels} LLM
        providers</strong> &mdash; including Anthropic Claude, Google
        Gemini, and five Cloudflare Workers AI models (Llama&nbsp;3.3
        70B, gpt-oss, Mistral, Kimi, Qwen) &mdash; on the decomposition
        task across ${distinctRepos} real open-source repositories.
        ${totalRuns} runs are recorded to date with an overall success
        rate of ${successRate}%. The harness clones each repository,
        constructs the production prompt verbatim, calls the model once,
        parses the response with the tolerant parser, and scores
        capsule count, file coverage, wall-clock, and an estimated
        token cost.
      </p>
      <p>
        The numbers are reproducible &mdash; the harness lives in the
        public repository and the raw results are committed as JSON. The
        full per-model and per-(repo, model) tables are at
        <a href="/benchmarks">/benchmarks</a>.
      </p>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 6</p>
      <h2>Live artifacts.</h2>
      <p>Everything below is real, public, and reproducible from source.</p>
      <ul class="memo-links">
        <li><a href="/">Registry index</a> &mdash; browse the ${capsuleCount} live capsules across ${ownerCount} namespaces.</li>
        <li><a href="/c/quake0day/yingjieli-admin-auth">Example capsule (man page)</a> &mdash; <code>yingjieli-admin-auth</code>: HMAC sessions, rate-limit, source files clickable.</li>
        <li><a href="/benchmarks">Benchmarks page</a> &mdash; head-to-head LLM comparison, per-run cost, coverage, wall-clock.</li>
        <li><a href="https://github.com/quake0day/capsule">Source code on GitHub</a> &mdash; Apache-2.0; Python CLI (15 commands) plus a Cloudflare Pages registry (TypeScript).</li>
        <li><a href="https://github.com/quake0day/capsule/blob/main/SPEC.md"><code>capsule.yaml</code> spec</a> &mdash; the on-disk format, v0.1.</li>
        <li><a href="https://github.com/quake0day/capsule/blob/main/docs/L2-DESIGN.md">L2 design document</a> &mdash; the Unix-philosophy framing and registry architecture.</li>
        <li><a href="https://github.com/quake0day/yingjieli-capsules">yingjieli-capsules</a> &mdash; a real artist portfolio site decomposed into six reconstructable capsules.</li>
        <li><a href="https://github.com/quake0day/cyberverse-capsules">cyberverse-capsules</a> &mdash; the largest case: 387 source files reduced to 13 capsules, including an adapter pattern Gemini correctly recovered.</li>
        <li><a href="/install-skill.sh">Claude Code skill installer</a> &mdash; one-line install for the discovery and composition skill.</li>
      </ul>
    </section>

    <section class="memo-section">
      <p class="memo-sec-num">§ 7</p>
      <h2>Three commands to try it.</h2>
      <pre class="memo-demo"><code><span class="memo-c"># 1.  Install the CLI</span>
<span class="memo-p">$</span> pip install -e git+https://github.com/quake0day/capsule.git#egg=capsule-cli

<span class="memo-c"># 2.  Read any registered capsule's man page locally</span>
<span class="memo-p">$</span> capsule man capsule://quake0day/yingjieli-admin-auth

<span class="memo-c"># 3.  Decompose a github repository into capsules and register them</span>
<span class="memo-p">$</span> export GEMINI_API_KEY=...
<span class="memo-p">$</span> capsule decompose https://github.com/&lt;owner&gt;/&lt;repo&gt; \\
       --out ./out --namespace mystuff --register mystuff-capsules</code></pre>
    </section>

    <footer class="memo-footer">
      <p class="memo-foot-rule"></p>
      <p class="memo-foot-line">
        <span class="memo-foot-tag">capsule</span>
        <span class="memo-foot-sep">/</span>
        <a href="https://github.com/quake0day/capsule">github.com/quake0day/capsule</a>
        <span class="memo-foot-sep">/</span>
        <a href="https://capsule-registry.pages.dev">capsule-registry.pages.dev</a>
        <span class="memo-foot-sep">/</span>
        <a href="mailto:quake0day@gmail.com">quake0day@gmail.com</a>
      </p>
      <p class="memo-foot-line memo-foot-dim">
        Apache-2.0 &nbsp;·&nbsp; rendered server-side, no JavaScript &nbsp;·&nbsp;
        numbers above pulled live from the registry and benchmark dataset on each request.
      </p>
    </footer>

  </article>

</main>`;
}
