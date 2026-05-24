// Fetch raw capsule.yaml from a github repo at a given ref + path.

import { parse as parseYaml } from "yaml";
import type { Capsule } from "./schema";
import type { RegistryEntry } from "./registry";

const GH_REPO_RE = /^https?:\/\/github\.com\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i;

interface GHRepo { owner: string; repo: string; }

export function parseGithubUrl(url: string): GHRepo | null {
  const m = GH_REPO_RE.exec(url);
  if (!m) return null;
  return { owner: m[1], repo: m[2] };
}

/** Build the raw.githubusercontent.com URL for a registry entry. */
export function rawUrl(entry: RegistryEntry): string | null {
  const repo = parseGithubUrl(entry.git_url);
  if (!repo) return null;
  const path = entry.path.replace(/^\/+/, "");
  return `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${entry.ref}/${path}`;
}

export interface FetchResult {
  capsule: Capsule;
  raw: string;
  source_url: string;
}

export class CapsuleFetchError extends Error {
  constructor(message: string, public readonly status?: number) { super(message); }
}

/** Fetch and parse the capsule.yaml referenced by a registry entry. */
export async function fetchCapsule(entry: RegistryEntry): Promise<FetchResult> {
  const url = rawUrl(entry);
  if (!url) {
    throw new CapsuleFetchError(`unsupported git_url (only github.com supported in v0.2): ${entry.git_url}`);
  }

  // Edge cache for GitHub responses — capsule.yaml at a given ref+path is
  // effectively immutable for a tagged ref, and changes slowly on `main`.
  const cache = caches.default;
  const cacheKey = new Request(url, { method: "GET" });
  const cached = await cache.match(cacheKey);
  let res: Response;
  if (cached) {
    res = cached;
  } else {
    res = await fetch(url, {
      headers: { "User-Agent": "capsule-registry/0.2" },
      cf: { cacheTtl: 60, cacheEverything: true },
    });
    // Re-clone before caching to keep the original body readable.
    const clone = res.clone();
    if (res.ok) {
      // 60s edge cache; cf already applies it, but we also cache the parsed
      // Response so subsequent requests skip the fetch entirely.
      const cacheable = new Response(await clone.text(), {
        headers: {
          "Content-Type": "text/yaml; charset=utf-8",
          "Cache-Control": "public, max-age=60",
        },
      });
      // waitUntil isn't available here; fire-and-forget is fine since failures
      // just mean we'll re-fetch next time.
      void cache.put(cacheKey, cacheable);
    }
  }

  if (!res.ok) {
    throw new CapsuleFetchError(
      `failed to fetch ${url}: HTTP ${res.status}`,
      res.status,
    );
  }

  const raw = await res.text();
  let parsed: unknown;
  try {
    parsed = parseYaml(raw);
  } catch (err) {
    throw new CapsuleFetchError(
      `YAML parse failed for ${url}: ${(err as Error).message}`,
    );
  }
  if (!parsed || typeof parsed !== "object") {
    throw new CapsuleFetchError(`${url}: top-level must be a mapping`);
  }
  return { capsule: parsed as Capsule, raw, source_url: url };
}
