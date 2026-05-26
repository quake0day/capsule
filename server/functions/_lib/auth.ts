// Shared request-auth helpers.
//
// Public capsules don't require any auth at all. Private capsules need a
// GitHub PAT that proves the requesting user can read the underlying
// private repo. We accept the token from two places, in priority order:
//
//   1. `Authorization: Bearer <token>`  — for CLI / API clients.
//   2. `capsule_token=<token>` cookie    — for browser navigation, set by
//      a /auth form (HttpOnly, Secure, SameSite=Strict). Browsers cannot
//      attach Authorization headers to plain GET navigation, hence the
//      cookie path.
//
// We never log or persist the token server-side. It is forwarded once to
// api.github.com to authorise the fetch and then dropped.

/** Pull a bearer token off a request. Returns null if neither source has one. */
export function extractToken(request: Request): string | null {
  const header = request.headers.get("Authorization");
  if (header) {
    const m = /^Bearer\s+(.+)$/i.exec(header);
    if (m) return m[1].trim();
    return header.trim();
  }
  const cookie = request.headers.get("Cookie");
  if (cookie) {
    for (const piece of cookie.split(";")) {
      const [k, ...rest] = piece.trim().split("=");
      if (k === "capsule_token" && rest.length > 0) {
        return rest.join("=").trim();
      }
    }
  }
  return null;
}

/** Validate a GitHub token by calling /user. Returns the login on success. */
export async function verifyGithubToken(token: string): Promise<string | null> {
  try {
    const resp = await fetch("https://api.github.com/user", {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "capsule-registry/0.4",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!resp.ok) return null;
    const u = (await resp.json()) as { login?: string };
    return u.login || null;
  } catch {
    return null;
  }
}
