// Registry: parse the static registry.json and look up entries by address.

import registry from "../../registry.json";

export interface RegistryEntry {
  owner: string;
  name: string;
  version: string;
  git_url: string;
  ref: string;
  path: string;
}

interface RegistryFile {
  entries: RegistryEntry[];
}

const REGISTRY = registry as unknown as RegistryFile;

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

/** All entries currently in the registry. */
export function allEntries(): RegistryEntry[] {
  return REGISTRY.entries;
}

/** Resolve an address. Without a version, returns the highest semver match. */
export function resolve(addr: CapsuleAddress): RegistryEntry | null {
  const matches = REGISTRY.entries.filter(
    (e) => e.owner === addr.owner && e.name === addr.name,
  );
  if (matches.length === 0) return null;
  if (addr.version) {
    return matches.find((e) => e.version === addr.version) ?? null;
  }
  // No version → pick highest semver.
  matches.sort((a, b) => -compareSemver(a.version, b.version));
  return matches[0];
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

/** Group entries by `(owner, name)`, returning the highest-version representative
 *  for each. Used by the index page. */
export function uniqueLatest(): RegistryEntry[] {
  const byKey = new Map<string, RegistryEntry>();
  for (const e of REGISTRY.entries) {
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
