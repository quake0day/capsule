// GET  /auth          → form for pasting a GitHub PAT
// POST /auth          → validate via /user, set HttpOnly cookie, redirect
// GET  /auth/logout   → clear cookie
//
// Browsers cannot attach Authorization headers to plain GET navigation, so
// the cookie path is how a human reads private capsules from the web view.
// The token is HttpOnly + Secure + SameSite=Strict; it never leaves the
// edge for anything other than the api.github.com calls we proxy.

import type { PagesFunction } from "@cloudflare/workers-types";
import { verifyGithubToken } from "./_lib/auth";

const HTML = (body: string, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...headers },
  });

const escape = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c] as string));

function form(returnTo: string, error?: string): string {
  const safeReturn = escape(returnTo);
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — capsule</title>
<link rel="stylesheet" href="/assets/style.css">
<link rel="icon" type="image/png" href="/assets/logo.png">
</head>
<body>
<header class="topbar">
  <a class="brand" href="/">
    <img class="brand-mark" src="/assets/logo.png" alt="" width="28" height="28">
    <span class="brand-name">capsule</span>
  </a>
  <span class="tagline">private capsule access</span>
</header>
<main class="manpage" style="max-width:640px">
  <h1>Sign in with a GitHub PAT</h1>
  <p class="purpose">
    Private capsules require proof that you have read access to the
    underlying private GitHub repo. Paste a personal access token below;
    we forward it to <code>api.github.com</code> for each fetch and store
    it server-side only as an HttpOnly cookie scoped to this site.
  </p>
  ${error ? `<p class="avoid" style="padding:10px 14px">${escape(error)}</p>` : ""}
  <form method="POST" action="/auth" autocomplete="off">
    <input type="hidden" name="return" value="${safeReturn}">
    <p>
      <label for="token" style="display:block;font-weight:600;margin-bottom:6px">GitHub token</label>
      <input type="password" id="token" name="token" required
        placeholder="ghp_xxxx or github_pat_xxxx"
        style="width:100%;font-family:ui-monospace,Menlo,monospace;padding:10px 12px;border:1px solid #ccc;border-radius:6px;font-size:13px">
    </p>
    <p>
      <button type="submit"
        style="padding:10px 22px;border:none;border-radius:6px;background:#0a0a0a;color:#fafaf7;font-weight:500;font-size:14px;cursor:pointer">
        Sign in
      </button>
      <a href="/" style="margin-left:14px">cancel</a>
    </p>
  </form>
  <h2 style="margin-top:32px">What scopes?</h2>
  <ul>
    <li>For repos you own — <code>repo</code> on a classic PAT is the simplest.</li>
    <li>For a fine-grained PAT — just read-only "Contents" on the specific private repos.</li>
  </ul>
  <p class="hint">
    From the CLI you don't need this — <code>capsule pull</code> already uses your <code>gh auth token</code>.
  </p>
</main>
</body>
</html>`;
}

export const onRequestGet: PagesFunction = async ({ request }) => {
  const url = new URL(request.url);
  if (url.pathname === "/auth/logout") {
    return HTML(form("/"), 200, {
      "Set-Cookie": "capsule_token=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
    });
  }
  const ret = sanitiseReturn(url.searchParams.get("return"));
  return HTML(form(ret));
};

export const onRequestPost: PagesFunction = async ({ request }) => {
  const ct = request.headers.get("Content-Type") ?? "";
  let token = "";
  let returnTo = "/";
  if (ct.includes("application/x-www-form-urlencoded")) {
    const text = await request.text();
    const params = new URLSearchParams(text);
    token = (params.get("token") ?? "").trim();
    returnTo = sanitiseReturn(params.get("return"));
  } else {
    const body = await request.json().catch(() => ({}));
    token = String((body as { token?: string }).token ?? "").trim();
    returnTo = sanitiseReturn((body as { return?: string }).return ?? "/");
  }
  if (!token) return HTML(form(returnTo, "Token is required."), 400);
  const login = await verifyGithubToken(token);
  if (!login) {
    return HTML(form(returnTo, "GitHub rejected the token (401 from api.github.com/user)."), 401);
  }
  // 12-hour cookie — short enough to age out, long enough for a session.
  const cookie =
    `capsule_token=${encodeURIComponent(token)}; ` +
    `Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${12 * 3600}`;
  return new Response(null, {
    status: 303,
    headers: { Location: returnTo, "Set-Cookie": cookie },
  });
};

function sanitiseReturn(raw: string | null): string {
  if (!raw) return "/";
  // Only allow same-site paths — never absolute URLs (open-redirect).
  if (!raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}
