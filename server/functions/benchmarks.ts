// GET /benchmarks — live comparison of LLM providers on the decompose task.
//
// Reads server/benchmarks/results.json (committed; future: KV-backed) and
// renders two tables: per-model summary, then per-(repo, model) detail.
// No client JS — server-side HTML so the page is scrape-friendly and works
// without JS enabled.

import type { PagesFunction } from "@cloudflare/workers-types";

import { layout } from "./_lib/render";
// The results file is imported at build time. Updates require a redeploy
// (which the benchmark CLI triggers via git push → Pages auto-deploy).
import resultsDoc from "../benchmarks/results.json";

interface BenchRun {
  repo: string;
  repo_name: string;
  provider: string;
  model: string;
  model_full: string;
  started_at: string;
  wall_clock_s: number;
  success: boolean;
  capsule_count: number;
  leftover_count: number;
  files_total: number;
  file_coverage_pct: number;
  input_chars: number;
  output_chars: number;
  input_tokens_est: number;
  output_tokens_est: number;
  cost_usd_est: number;
  error: string | null;
  error_class: string | null;
}

interface ResultsDoc { generated_at: string; runs: BenchRun[] }

const DOC = resultsDoc as unknown as ResultsDoc;

const h = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

const html = (body: string, status = 200): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });


export const onRequestGet: PagesFunction = async () => {
  const runs = DOC.runs ?? [];
  return html(layout("Benchmarks", render(runs, DOC.generated_at)));
};


// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

function render(runs: BenchRun[], generated_at: string): string {
  if (runs.length === 0) {
    return `
<main class="benchmarks">
  <h1>Benchmarks</h1>
  <p class="lede">No benchmark runs recorded yet. Run
  <code>python tools/benchmark_decompose.py</code> from the capsule repo to
  produce the first batch.</p>
</main>`;
  }

  // Keep only the newest run per (repo_name, model_full) so the table
  // shows the freshest comparison. Older runs are still in the JSON for
  // audit, just not surfaced here.
  const newest = new Map<string, BenchRun>();
  for (const r of runs) {
    const key = `${r.repo_name}::${r.model_full}`;
    const existing = newest.get(key);
    if (!existing || existing.started_at < r.started_at) {
      newest.set(key, r);
    }
  }
  const latest = [...newest.values()];

  return `
<main class="benchmarks">
  <h1>Benchmarks</h1>
  <p class="lede">
    Same decompose prompt across <strong>${countRepos(latest)} repos</strong>
    × <strong>${countModels(latest)} models</strong>. Newest run per pair shown.
    Source data: <a href="https://github.com/quake0day/capsule/blob/main/server/benchmarks/results.json">results.json</a>
    · Generated ${h(generated_at.split("T")[0] ?? generated_at)}.
  </p>

  ${renderModelSummary(latest)}
  ${renderDetailTable(latest)}

  <section class="bench-method">
    <h2>Method</h2>
    <p>For each (repo, model) pair, the harness:</p>
    <ol>
      <li>Clones the repo shallowly</li>
      <li>Builds the same prompt the production <code>capsule decompose</code> uses (tree + first 200 lines of each text file, capped at ~90KB)</li>
      <li>Calls the model exactly once, no retries on the LLM side</li>
      <li>Parses the JSON response with the tolerant parser (strict JSON first; relaxes trailing commas + single-quoted keys if needed)</li>
      <li>Counts capsules, leftover files, file coverage; estimates cost from per-model published pricing × ⌈chars/4⌉ token estimate</li>
    </ol>
    <p class="hint">
      The harness lives at <a href="https://github.com/quake0day/capsule/blob/main/tools/benchmark_decompose.py">tools/benchmark_decompose.py</a>.
      Anyone can re-run it locally — same inputs, same scoring.
    </p>
  </section>
</main>`;
}


function renderModelSummary(latest: BenchRun[]): string {
  const byModel = new Map<string, BenchRun[]>();
  for (const r of latest) {
    const k = r.model_full;
    const arr = byModel.get(k) ?? [];
    arr.push(r);
    byModel.set(k, arr);
  }

  const rows = [...byModel.entries()].map(([model_full, items]) => {
    const ok = items.filter((i) => i.success);
    const successRate = items.length > 0 ? Math.round((ok.length * 100) / items.length) : 0;
    const avgWall = avg(ok.map((i) => i.wall_clock_s));
    const avgCov = avg(ok.map((i) => i.file_coverage_pct));
    const avgCost = avg(ok.map((i) => i.cost_usd_est));
    const example = items[0];
    const shortModel = example?.model ?? model_full;
    const provider = example?.provider ?? "?";
    return { model_full, shortModel, provider, items, ok, successRate, avgWall, avgCov, avgCost };
  }).sort((a, b) => b.successRate - a.successRate || a.avgWall - b.avgWall);

  return `
<section class="bench-summary">
  <h2>Per-model summary</h2>
  <table>
    <thead>
      <tr>
        <th>Model</th>
        <th>Provider</th>
        <th class="num">Runs</th>
        <th class="num">Success</th>
        <th class="num">Avg time</th>
        <th class="num">Avg coverage</th>
        <th class="num">Avg cost / run</th>
      </tr>
    </thead>
    <tbody>
      ${rows.map((r) => `
      <tr class="${r.successRate === 100 ? "row-ok" : r.successRate === 0 ? "row-fail" : "row-partial"}">
        <td><code>${h(r.shortModel)}</code></td>
        <td><span class="prov-pill prov-${h(r.provider)}">${h(r.provider)}</span></td>
        <td class="num">${r.items.length}</td>
        <td class="num">${r.successRate}%</td>
        <td class="num">${r.avgWall ? r.avgWall.toFixed(1) + "s" : "—"}</td>
        <td class="num">${r.avgCov ? r.avgCov.toFixed(0) + "%" : "—"}</td>
        <td class="num">${r.avgCost ? "$" + r.avgCost.toFixed(4) : "$0.0000"}</td>
      </tr>`).join("")}
    </tbody>
  </table>
</section>`;
}


function renderDetailTable(latest: BenchRun[]): string {
  // Sort by repo, then by wall_clock (fastest first within a repo).
  const sorted = [...latest].sort((a, b) =>
    a.repo_name.localeCompare(b.repo_name)
    || (a.success === b.success ? a.wall_clock_s - b.wall_clock_s : (a.success ? -1 : 1))
  );

  return `
<section class="bench-detail">
  <h2>All runs (newest per pair)</h2>
  <table>
    <thead>
      <tr>
        <th>Repo</th>
        <th>Model</th>
        <th>Status</th>
        <th class="num">Capsules</th>
        <th class="num">Coverage</th>
        <th class="num">Wall-clock</th>
        <th class="num">Input chars</th>
        <th class="num">Output chars</th>
        <th class="num">Est cost</th>
      </tr>
    </thead>
    <tbody>
      ${sorted.map((r) => `
      <tr class="${r.success ? "row-ok" : "row-fail"}">
        <td><a href="${h(r.repo)}"><code>${h(r.repo_name)}</code></a></td>
        <td><code>${h(r.model)}</code></td>
        <td>${r.success
          ? '<span class="status-ok">ok</span>'
          : `<span class="status-fail" title="${h(r.error ?? "")}">${h(r.error_class ?? "fail")}</span>`}</td>
        <td class="num">${r.success ? r.capsule_count : "—"}</td>
        <td class="num">${r.success ? r.file_coverage_pct + "%" : "—"}</td>
        <td class="num">${r.wall_clock_s.toFixed(1)}s</td>
        <td class="num">${r.input_chars.toLocaleString()}</td>
        <td class="num">${r.success ? r.output_chars.toLocaleString() : "—"}</td>
        <td class="num">${r.success ? "$" + r.cost_usd_est.toFixed(4) : "—"}</td>
      </tr>`).join("")}
    </tbody>
  </table>
</section>`;
}


function avg(xs: number[]): number {
  if (xs.length === 0) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function countRepos(rs: BenchRun[]): number {
  return new Set(rs.map((r) => r.repo_name)).size;
}

function countModels(rs: BenchRun[]): number {
  return new Set(rs.map((r) => r.model_full)).size;
}
