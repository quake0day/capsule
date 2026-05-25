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

/** Directory portion of entry.path — the prefix all sibling files live under. */
export function capsuleDirPath(entry: RegistryEntry): string {
  const path = entry.path.replace(/^\/+/, "");
  const lastSlash = path.lastIndexOf("/");
  return lastSlash === -1 ? "" : path.slice(0, lastSlash + 1);
}

/** Build a raw URL for an arbitrary file inside the capsule's directory.
 *  `relPath` is relative to the directory holding capsule.yaml. */
export function rawSiblingUrl(entry: RegistryEntry, relPath: string): string | null {
  const repo = parseGithubUrl(entry.git_url);
  if (!repo) return null;
  // Hard reject anything that escapes the capsule dir or is absolute.
  const clean = relPath.replace(/^\/+/, "");
  if (clean.includes("..") || clean.includes("\\")) return null;
  const dir = capsuleDirPath(entry);
  return `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${entry.ref}/${dir}${clean}`;
}

export interface FetchResult {
  capsule: Capsule;
  raw: string;
  source_url: string;
}

export class CapsuleFetchError extends Error {
  constructor(message: string, public readonly status?: number) { super(message); }
}

/** Fetch raw text from a github raw URL, with edge cache.
 *  Returns null on 404; raises CapsuleFetchError on other failures. */
export async function fetchRawText(
  url: string,
  opts: { contentType?: string } = {},
): Promise<string | null> {
  const contentType = opts.contentType ?? "text/plain; charset=utf-8";
  const cache = caches.default;
  const cacheKey = new Request(url, { method: "GET" });
  const cached = await cache.match(cacheKey);
  if (cached) {
    if (cached.status === 404) return null;
    if (!cached.ok) throw new CapsuleFetchError(`cached failure ${cached.status} for ${url}`);
    return await cached.text();
  }
  const res = await fetch(url, {
    headers: { "User-Agent": "capsule-registry/0.4" },
    cf: { cacheTtl: 60, cacheEverything: true },
  });
  if (res.status === 404) {
    // Memoise the 404 briefly so repeated probes don't hammer github.
    void cache.put(cacheKey, new Response(null, {
      status: 404,
      headers: { "Cache-Control": "public, max-age=30" },
    }));
    return null;
  }
  if (!res.ok) {
    throw new CapsuleFetchError(`failed to fetch ${url}: HTTP ${res.status}`, res.status);
  }
  const text = await res.text();
  void cache.put(cacheKey, new Response(text, {
    headers: {
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=60",
    },
  }));
  return text;
}


/** Fetch and parse the capsule.yaml referenced by a registry entry. */
export async function fetchCapsule(entry: RegistryEntry): Promise<FetchResult> {
  const url = rawUrl(entry);
  if (!url) {
    throw new CapsuleFetchError(`unsupported git_url (only github.com supported in v0.2): ${entry.git_url}`);
  }
  const raw = await fetchRawText(url, { contentType: "text/yaml; charset=utf-8" });
  if (raw === null) {
    throw new CapsuleFetchError(`${url}: 404 (capsule.yaml not found at that path/ref)`, 404);
  }
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


// ---------------------------------------------------------------------------
// install.json + arbitrary sibling files
// ---------------------------------------------------------------------------

export interface InstallFile { from: string; to: string; }

export interface InstallPlan {
  files: InstallFile[];
  env_required?: string[];
  // We don't model the full install.json schema here; the registry only
  // needs the file list. Reconstruction-side validation is the CLI's job.
}

export interface InstallFetchResult {
  install: InstallPlan;
  source_url: string;
}

/** Fetch install.json (sibling of capsule.yaml). Returns null if not present. */
export async function fetchInstall(entry: RegistryEntry): Promise<InstallFetchResult | null> {
  const repo = parseGithubUrl(entry.git_url);
  if (!repo) return null;
  const dir = capsuleDirPath(entry);
  const url = `https://raw.githubusercontent.com/${repo.owner}/${repo.repo}/${entry.ref}/${dir}install.json`;
  const raw = await fetchRawText(url, { contentType: "application/json; charset=utf-8" });
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new CapsuleFetchError(`install.json parse failed for ${url}: ${(err as Error).message}`);
  }
  const filesArr = Array.isArray((parsed as { files?: unknown[] })?.files)
    ? ((parsed as { files: unknown[] }).files as unknown[])
    : [];
  const files: InstallFile[] = filesArr
    .map((f) => f as { from?: unknown; to?: unknown })
    .filter((f) => typeof f.from === "string" && typeof f.to === "string")
    .map((f) => ({ from: f.from as string, to: f.to as string }));
  const envReq = Array.isArray((parsed as { env_required?: unknown[] })?.env_required)
    ? ((parsed as { env_required: unknown[] }).env_required.filter((x) => typeof x === "string") as string[])
    : [];
  return { install: { files, env_required: envReq }, source_url: url };
}

/** Fetch the raw bytes (as text) of any file inside the capsule's directory. */
export async function fetchSibling(
  entry: RegistryEntry,
  relPath: string,
): Promise<{ text: string; source_url: string } | null> {
  const url = rawSiblingUrl(entry, relPath);
  if (!url) return null;
  const text = await fetchRawText(url);
  if (text === null) return null;
  return { text, source_url: url };
}
