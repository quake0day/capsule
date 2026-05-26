// Registry: parse the static registry.json and look up entries by address.
//
// In L2 we shipped a static, committed registry: editing meant a PR to
// registry.json + a redeploy. In L3 we add a KV overlay (binding name
// CAPSULE_REGISTRY in wrangler.toml). At lookup time:
//
//   1. Try the KV namespace first. KV entries shadow / override static ones.
//   2. Fall back to the static registry.json bundle.
//
// The static file remains useful as a deterministic seed: even with an
// empty KV namespace, the same demo capsules resolve.
//
// All exported functions accept an optional KVNamespace; if undefined, the
// resolver behaves exactly as in v0.2 (static-only). This lets the same
// helpers serve both edge functions (KV available) and CLI tests.

import type { KVNamespace } from "@cloudflare/workers-types";

import registry from "../../registry.json";

export interface RegistryEntry {
  owner: string;
  name: string;
  version: string;
  git_url: string;
  ref: string;
  path: string;
  /** "public" (default) lists in the open index and serves via raw.githubusercontent.com.
   *  "private" is hidden from public listings and requires Authorization on every read;
   *  GitHub itself acts as the access oracle (we just proxy a Contents-API call with
   *  the user's token). */
  visibility?: "public" | "private";
}

/** Convenience: true when entry should be treated as private. */
export function isPrivate(e: RegistryEntry): boolean {
  return e.visibility === "private";
}

interface RegistryFile {
  entries: RegistryEntry[];
}

const STATIC_REGISTRY = registry as unknown as RegistryFile;

const kvKey = (owner: string, name: string, version: string): string =>
  `entry:${owner}/${name}@${version}`;
const KV_PREFIX = "entry:";

/** Address parsed from `capsule://<owner>/<name>[@<version>]`. */
export interface CapsuleAddress {
  owner: string;
  name: string;
  version?: string; // undefined → "latest"
}

const ADDR_RE = /^([a-z0-9][a-z0-9-]*)\/([a-z0-9][a-z0-9-]*)(?:@(.+))?$/i;

/** Parse a slug like `quake0day/yingjieli-admin-auth@1.0.0`. */
export function parseAddress(slug: string): CapsuleAddress | null {
  // Strip a leading `capsule://` if present.
  const cleaned = slug.replace(/^capsule:\/\//, "");
  const m = ADDR_RE.exec(cleaned);
  if (!m) return null;
  return { owner: m[1], name: m[2], version: m[3] };
}

/** Static-only entry list (no KV overlay). Cheap; safe to call anywhere. */
export function allEntries(): RegistryEntry[] {
  return STATIC_REGISTRY.entries;
}

/** KV-aware entry list. KV entries override static entries on collision. */
export async function allEntriesWithKV(
  kv: KVNamespace | undefined,
): Promise<RegistryEntry[]> {
  if (!kv) return STATIC_REGISTRY.entries;
  const overlay = await readAllKV(kv);
  return mergeEntries(STATIC_REGISTRY.entries, overlay);
}

/** Synchronous resolve against the static registry only. */
export function resolve(addr: CapsuleAddress): RegistryEntry | null {
  return resolveFrom(STATIC_REGISTRY.entries, addr);
}

/** KV-aware resolve: KV overlay first, then static fallback. */
export async function resolveWithKV(
  addr: CapsuleAddress,
  kv: KVNamespace | undefined,
): Promise<RegistryEntry | null> {
  if (!kv) return resolve(addr);
  const merged = await allEntriesWithKV(kv);
  return resolveFrom(merged, addr);
}

function resolveFrom(
  entries: readonly RegistryEntry[],
  addr: CapsuleAddress,
): RegistryEntry | null {
  const matches = entries.filter(
    (e) => e.owner === addr.owner && e.name === addr.name,
  );
  if (matches.length === 0) return null;
  if (addr.version) {
    return matches.find((e) => e.version === addr.version) ?? null;
  }
  // No version → pick highest semver.
  const sorted = [...matches].sort((a, b) => -compareSemver(a.version, b.version));
  return sorted[0];
}

async function readAllKV(kv: KVNamespace): Promise<RegistryEntry[]> {
  const out: RegistryEntry[] = [];
  let cursor: string | undefined;
  // KV list is paginated; in v0.3 we expect <1000 entries so one pass is fine.
  do {
    const page = await kv.list({ prefix: KV_PREFIX, cursor });
    for (const k of page.keys) {
      const raw = await kv.get(k.name);
      if (!raw) continue;
      try {
        const e = JSON.parse(raw) as RegistryEntry;
        if (e?.owner && e?.name && e?.version && e?.git_url && e?.ref && e?.path) {
          if (e.visibility !== "private") e.visibility = "public";
          out.push(e);
        }
      } catch {
        // Skip malformed entries; do not crash the whole registry.
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

function mergeEntries(
  staticEntries: readonly RegistryEntry[],
  overlay: readonly RegistryEntry[],
): RegistryEntry[] {
  const key = (e: RegistryEntry) => `${e.owner}/${e.name}@${e.version}`;
  const map = new Map<string, RegistryEntry>();
  for (const e of staticEntries) map.set(key(e), e);
  for (const e of overlay) map.set(key(e), e); // overlay wins on collision
  return [...map.values()];
}

/** Write a new (or replacement) entry into KV. */
export async function putEntry(kv: KVNamespace, entry: RegistryEntry): Promise<void> {
  await kv.put(kvKey(entry.owner, entry.name, entry.version), JSON.stringify(entry));
}

/** Strict semver comparison (`1.2.3` vs `1.10.0`). Returns -1 / 0 / 1. */
export function compareSemver(a: string, b: string): number {
  const parse = (s: string) =>
    s.split(".").map((p) => parseInt(p, 10));
  const [aa = 0, ab = 0, ac = 0] = parse(a);
  const [ba = 0, bb = 0, bc = 0] = parse(b);
  if (aa !== ba) return aa < ba ? -1 : 1;
  if (ab !== bb) return ab < bb ? -1 : 1;
  if (ac !== bc) return ac < bc ? -1 : 1;
  return 0;
}

/** Static-only highest-version-per-name. */
export function uniqueLatest(): RegistryEntry[] {
  return uniqueLatestFrom(STATIC_REGISTRY.entries);
}

/** KV-aware highest-version-per-name. Excludes private entries by default;
 *  pass { includePrivate: true } to include them (e.g. for an authed listing). */
export async function uniqueLatestWithKV(
  kv: KVNamespace | undefined,
  opts: { includePrivate?: boolean } = {},
): Promise<RegistryEntry[]> {
  const entries = await allEntriesWithKV(kv);
  const filtered = opts.includePrivate
    ? entries
    : entries.filter((e) => e.visibility !== "private");
  return uniqueLatestFrom(filtered);
}

function uniqueLatestFrom(entries: readonly RegistryEntry[]): RegistryEntry[] {
  const byKey = new Map<string, RegistryEntry>();
  for (const e of entries) {
    const key = `${e.owner}/${e.name}`;
    const existing = byKey.get(key);
    if (!existing || compareSemver(e.version, existing.version) > 0) {
      byKey.set(key, e);
    }
  }
  return [...byKey.values()].sort((a, b) =>
    `${a.owner}/${a.name}`.localeCompare(`${b.owner}/${b.name}`),
  );
}
