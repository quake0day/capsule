// POST /mcp — Model Context Protocol (Streamable HTTP transport).
//
// JSON-RPC 2.0 dispatch for a read-only capsule registry. Implements the
// minimum useful surface:
//
//   initialize                  → server capabilities + protocol version
//   notifications/initialized   → ack
//   tools/list                  → three tools (resolve / get / compose)
//   tools/call                  → execute a tool
//   resources/list              → one resource per registered capsule
//   resources/read              → fetch a capsule's parsed content
//
// Wire-up in any MCP client:
//
//   claude mcp add capsule-registry --transport http \
//     https://capsule-registry.pages.dev/mcp
//
// or for the JSON-RPC raw clients:
//
//   curl -X POST https://capsule-registry.pages.dev/mcp \
//     -H "content-type: application/json" \
//     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

import type { PagesFunction, KVNamespace } from "@cloudflare/workers-types";

import {
  parseAddress,
  resolveWithKV,
  uniqueLatestWithKV,
  isPrivate,
  type RegistryEntry,
} from "./_lib/registry";
import {
  fetchCapsule,
  fetchInstall,
  fetchSibling,
  rawUrl,
  CapsuleFetchError,
  CapsuleAuthError,
} from "./_lib/github";
import { extractToken, verifyGithubToken } from "./_lib/auth";

interface Env { CAPSULE_REGISTRY?: KVNamespace }

/** Caller's authenticated identity, if any. Resolved once per request. */
interface AuthCtx {
  token: string | null;
  login: string | null;
}

async function resolveAuth(request: Request): Promise<AuthCtx> {
  const token = extractToken(request);
  if (!token) return { token: null, login: null };
  const login = await verifyGithubToken(token);
  return { token, login };
}

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_NAME = "capsule-registry";
const SERVER_VERSION = "0.3.0";

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id?: number | string | null;
  method: string;
  params?: unknown;
}

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });

const rpcError = (id: number | string | null | undefined, code: number, message: string) =>
  jsonResponse({
    jsonrpc: "2.0",
    id: id ?? null,
    error: { code, message },
  });

const rpcResult = (id: number | string | null | undefined, result: unknown) =>
  jsonResponse({
    jsonrpc: "2.0",
    id: id ?? null,
    result,
  });


// ---------------------------------------------------------------------------
// HTTP entry points
// ---------------------------------------------------------------------------

export const onRequestPost: PagesFunction<Env> = async ({ request, env }) => {
  let body: JsonRpcRequest;
  try {
    body = (await request.json()) as JsonRpcRequest;
  } catch {
    return rpcError(null, -32700, "Parse error: invalid JSON");
  }
  if (!body || body.jsonrpc !== "2.0" || typeof body.method !== "string") {
    return rpcError(body?.id ?? null, -32600, "Invalid Request");
  }

  const auth = await resolveAuth(request);

  try {
    return await dispatch(body, env.CAPSULE_REGISTRY, auth);
  } catch (err) {
    return rpcError(body.id, -32603, `Internal error: ${(err as Error).message}`);
  }
};

export const onRequestGet: PagesFunction<Env> = async () =>
  jsonResponse({
    server: SERVER_NAME,
    version: SERVER_VERSION,
    transport: "streamable-http",
    protocolVersion: PROTOCOL_VERSION,
    hint: "POST a JSON-RPC 2.0 envelope to this URL. See /c/_/capsule-registry-server for full details.",
  });


// ---------------------------------------------------------------------------
// dispatch
// ---------------------------------------------------------------------------

async function dispatch(
  req: JsonRpcRequest,
  kv: KVNamespace | undefined,
  auth: AuthCtx,
): Promise<Response> {
  switch (req.method) {
    case "initialize":
      return rpcResult(req.id, {
        protocolVersion: PROTOCOL_VERSION,
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
        capabilities: {
          tools: {},
          resources: { subscribe: false, listChanged: false },
        },
      });

    case "notifications/initialized":
    case "initialized":
      // Notifications have no id; just ack with 204-ish empty success.
      return new Response(null, { status: 204 });

    case "tools/list":
      return rpcResult(req.id, { tools: TOOL_DESCRIPTORS });

    case "tools/call":
      return await callTool(req, kv, auth);

    case "resources/list":
      return rpcResult(req.id, { resources: await listResources(kv, auth) });

    case "resources/read":
      return await readResource(req, kv, auth);

    case "ping":
      return rpcResult(req.id, {});

    default:
      return rpcError(req.id, -32601, `Method not found: ${req.method}`);
  }
}


// ---------------------------------------------------------------------------
// tools
// ---------------------------------------------------------------------------

const TOOL_DESCRIPTORS = [
  {
    name: "capsule_resolve",
    description:
      "Translate a capsule address (capsule://owner/name[@version]) into the concrete git source where its capsule.yaml lives. Pure naming layer; does not fetch.",
    inputSchema: {
      type: "object",
      properties: {
        address: {
          type: "string",
          description: "capsule://<owner>/<name>[@<version>] (or <owner>/<name>[@<version>])",
        },
      },
      required: ["address"],
    },
  },
  {
    name: "capsule_get",
    description:
      "Resolve, fetch, and return the full parsed capsule.yaml for a given capsule address. Use this when an agent needs the AI orientation, invariants, contracts, or handoff state of a subsystem before working on it.",
    inputSchema: {
      type: "object",
      properties: {
        address: { type: "string" },
      },
      required: ["address"],
    },
  },
  {
    name: "capsule_list",
    description:
      "List every capsule currently registered in this registry (latest version of each owner/name pair).",
    inputSchema: { type: "object", properties: {} },
  },
];


function gateEntry(entry: RegistryEntry, auth: AuthCtx): string | null {
  if (!isPrivate(entry)) return null;
  if (!auth.token) return `private capsule (${entry.owner}/${entry.name}): send Authorization: Bearer <github-token> to MCP`;
  if (!auth.login || auth.login.toLowerCase() !== entry.owner.toLowerCase()) {
    return `private capsule (${entry.owner}/${entry.name}): your token belongs to a different user`;
  }
  return null;
}

async function callTool(req: JsonRpcRequest, kv: KVNamespace | undefined, auth: AuthCtx): Promise<Response> {
  const params = (req.params || {}) as { name?: string; arguments?: Record<string, unknown> };
  const name = params.name;
  const args = params.arguments || {};

  if (name === "capsule_resolve") {
    const addr = String(args.address || "");
    const parsed = parseAddress(addr);
    if (!parsed) return toolError(req.id, `invalid address: ${addr}`);
    const entry = await resolveWithKV(parsed, kv);
    if (!entry) return toolError(req.id, `no capsule in registry for ${addr}`);
    const denied = gateEntry(entry, auth);
    if (denied) return toolError(req.id, denied);
    return toolResult(req.id, JSON.stringify(
      {
        owner: entry.owner,
        name: entry.name,
        version: entry.version,
        git_url: entry.git_url,
        ref: entry.ref,
        path: entry.path,
        raw_url: isPrivate(entry) ? null : rawUrl(entry),
        visibility: entry.visibility ?? "public",
      },
      null,
      2,
    ));
  }

  if (name === "capsule_get") {
    const addr = String(args.address || "");
    const parsed = parseAddress(addr);
    if (!parsed) return toolError(req.id, `invalid address: ${addr}`);
    const entry = await resolveWithKV(parsed, kv);
    if (!entry) return toolError(req.id, `no capsule in registry for ${addr}`);
    const denied = gateEntry(entry, auth);
    if (denied) return toolError(req.id, denied);
    try {
      const { capsule, source_url } = await fetchCapsule(entry, auth.token ?? undefined);
      return toolResult(req.id, JSON.stringify({ source_url, capsule }, null, 2));
    } catch (err) {
      if (err instanceof CapsuleAuthError) return toolError(req.id, err.message);
      const msg = err instanceof CapsuleFetchError ? err.message : String(err);
      return toolError(req.id, `fetch failed: ${msg}`);
    }
  }

  if (name === "capsule_list") {
    // Include private when authed, scoped to caller's owner.
    const entries = await uniqueLatestWithKV(kv, { includePrivate: !!auth.login });
    const visible = auth.login
      ? entries.filter((e) => e.visibility !== "private" || e.owner.toLowerCase() === auth.login!.toLowerCase())
      : entries.filter((e) => e.visibility !== "private");
    const items = visible.map((e) => ({
      address: `capsule://${e.owner}/${e.name}@${e.version}`,
      owner: e.owner,
      name: e.name,
      version: e.version,
      visibility: e.visibility ?? "public",
    }));
    return toolResult(req.id, JSON.stringify(items, null, 2));
  }

  return toolError(req.id, `unknown tool: ${name}`);
}


function toolResult(id: JsonRpcRequest["id"], text: string): Response {
  return rpcResult(id, {
    content: [{ type: "text", text }],
    isError: false,
  });
}

function toolError(id: JsonRpcRequest["id"], text: string): Response {
  return rpcResult(id, {
    content: [{ type: "text", text }],
    isError: true,
  });
}


// ---------------------------------------------------------------------------
// resources
// ---------------------------------------------------------------------------

/** Splits `capsule://owner/name@v/files/<rest>` into (addr-slug, file-path).
 *  Returns null if the URI is not a file URI. */
function splitFileUri(uri: string): { addrSlug: string; filePath: string } | null {
  const cleaned = uri.replace(/^capsule:\/\//, "");
  const idx = cleaned.indexOf("/files/");
  if (idx === -1) return null;
  return {
    addrSlug: cleaned.slice(0, idx),
    filePath: cleaned.slice(idx + "/files/".length),
  };
}


async function listResources(kv: KVNamespace | undefined, auth: AuthCtx) {
  const all = await uniqueLatestWithKV(kv, { includePrivate: !!auth.login });
  const entries = auth.login
    ? all.filter((e) => e.visibility !== "private" || e.owner.toLowerCase() === auth.login!.toLowerCase())
    : all.filter((e) => e.visibility !== "private");
  const out: Array<{ uri: string; name: string; description: string; mimeType: string }> = [];

  // Each capsule contributes one "manifest" resource plus one resource per
  // declared file (capsule.yaml + install.json + every install.json files[]).
  // We fetch install.json in parallel for snappier listing.
  await Promise.all(entries.map(async (e: RegistryEntry) => {
    const baseAddr = `capsule://${e.owner}/${e.name}@${e.version}`;
    out.push({
      uri: baseAddr,
      name: `${e.owner}/${e.name}@${e.version} · manifest${isPrivate(e) ? " · private" : ""}`,
      description: `Top-level capsule.yaml for ${e.owner}/${e.name} at version ${e.version}.`,
      mimeType: "application/yaml",
    });

    let install;
    try {
      install = await fetchInstall(e, auth.token ?? undefined);
    } catch {
      install = null;
    }
    if (!install) return;

    out.push({
      uri: `${baseAddr}/files/install.json`,
      name: `${e.owner}/${e.name}@${e.version} · install.json`,
      description: "Install plan: file mapping + data injections + env requirements.",
      mimeType: "application/json",
    });

    for (const f of install.install.files) {
      out.push({
        uri: `${baseAddr}/files/${f.from}`,
        name: `${e.owner}/${e.name}@${e.version} · ${f.from}`,
        description: `Source file. Installs to '${f.to}' in the reconstructed site.`,
        mimeType: guessMime(f.from),
      });
    }
  }));

  return out;
}


function guessMime(path: string): string {
  const lower = path.toLowerCase();
  if (lower.endsWith(".json")) return "application/json";
  if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return "application/yaml";
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return "text/html";
  if (lower.endsWith(".css")) return "text/css";
  if (lower.endsWith(".js") || lower.endsWith(".mjs")) return "application/javascript";
  if (lower.endsWith(".ts")) return "application/typescript";
  return "text/plain";
}


async function readResource(req: JsonRpcRequest, kv: KVNamespace | undefined, auth: AuthCtx): Promise<Response> {
  const params = (req.params || {}) as { uri?: string };
  const uri = String(params.uri || "");

  // File URI?
  const fileUri = splitFileUri(uri);
  if (fileUri) {
    return await readFileResource(req, kv, fileUri.addrSlug, fileUri.filePath, auth);
  }

  // Otherwise plain capsule manifest URI.
  const parsed = parseAddress(uri);
  if (!parsed) {
    return rpcError(req.id, -32602, `invalid resource URI: ${uri}`);
  }
  const entry = await resolveWithKV(parsed, kv);
  if (!entry) {
    return rpcError(req.id, -32602, `unknown resource: ${uri}`);
  }
  const denied = gateEntry(entry, auth);
  if (denied) return rpcError(req.id, -32001, denied);
  try {
    const { raw } = await fetchCapsule(entry, auth.token ?? undefined);
    return rpcResult(req.id, {
      contents: [{
        uri: `capsule://${entry.owner}/${entry.name}@${entry.version}`,
        mimeType: "application/yaml",
        text: raw,
      }],
    });
  } catch (err) {
    if (err instanceof CapsuleAuthError) return rpcError(req.id, -32001, err.message);
    return rpcError(req.id, -32603, `resource fetch failed: ${(err as Error).message}`);
  }
}


async function readFileResource(
  req: JsonRpcRequest,
  kv: KVNamespace | undefined,
  addrSlug: string,
  filePath: string,
  auth: AuthCtx,
): Promise<Response> {
  const addr = parseAddress(addrSlug);
  if (!addr) return rpcError(req.id, -32602, `invalid file URI: ${addrSlug}/files/${filePath}`);
  if (filePath.includes("..")) return rpcError(req.id, -32602, `bad file path: ${filePath}`);

  const entry = await resolveWithKV(addr, kv);
  if (!entry) return rpcError(req.id, -32602, `unknown capsule: ${addrSlug}`);
  const denied = gateEntry(entry, auth);
  if (denied) return rpcError(req.id, -32001, denied);

  // Allow capsule.yaml + install.json + any file declared by install.json.
  try {
    let text: string;
    let mime: string;
    if (filePath === "capsule.yaml") {
      text = (await fetchCapsule(entry, auth.token ?? undefined)).raw;
      mime = "application/yaml";
    } else if (filePath === "install.json") {
      const install = await fetchInstall(entry, auth.token ?? undefined);
      if (!install) return rpcError(req.id, -32602, `no install.json for ${addrSlug}`);
      text = JSON.stringify(install.install, null, 2);
      mime = "application/json";
    } else {
      const install = await fetchInstall(entry, auth.token ?? undefined);
      const declared = install?.install.files.some((f) => f.from === filePath) ?? false;
      if (!declared) return rpcError(req.id, -32602, `${filePath} not declared in install.json`);
      const result = await fetchSibling(entry, filePath, auth.token ?? undefined);
      if (!result) return rpcError(req.id, -32602, `${filePath}: 404 upstream`);
      text = result.text;
      mime = guessMime(filePath);
    }
    return rpcResult(req.id, {
      contents: [{
        uri: `capsule://${entry.owner}/${entry.name}@${entry.version}/files/${filePath}`,
        mimeType: mime,
        text,
      }],
    });
  } catch (err) {
    if (err instanceof CapsuleAuthError) return rpcError(req.id, -32001, err.message);
    return rpcError(req.id, -32603, `file fetch failed: ${(err as Error).message}`);
  }
}
