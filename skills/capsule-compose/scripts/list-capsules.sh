#!/usr/bin/env bash
# list-capsules.sh — print one line per registered capsule.
#
# Format: "<owner>/<name>@<version>  [<type>]  <one-line purpose>"
#
# Output is designed for a coding agent (Claude) to skim — short,
# deterministic, no formatting noise. Reads from the production
# capsule registry by default; override with CAPSULE_REGISTRY env var.
#
# Usage:
#   bash list-capsules.sh
#   bash list-capsules.sh --owner quake0day      # filter by owner
#   bash list-capsules.sh --search "auth"         # case-insensitive grep on purpose
set -euo pipefail

REGISTRY="${CAPSULE_REGISTRY:-https://capsule-registry.pages.dev}"

# Resolve a usable Python interpreter — `python3` on Windows is often the
# Microsoft Store shim, which is flaky under load. Prefer the real install.
PY="$(command -v python 2>/dev/null || command -v python3 2>/dev/null || true)"
if [[ -z "$PY" ]]; then
  echo "error: no python interpreter on PATH (tried 'python', 'python3')" >&2
  exit 2
fi

# Auth discovery — same precedence as the Python CLI's _read_token():
#   1. $CAPSULE_TOKEN (explicit override)
#   2. `gh auth token` (existing gh CLI session)
# When set, every registry call carries Authorization: Bearer, which is
# how private capsules become visible to this script. Public-only mode
# still works (the server treats anonymous requests as before).
AUTH_HEADER=()
if [[ -n "${CAPSULE_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${CAPSULE_TOKEN}")
elif command -v gh >/dev/null 2>&1; then
  _tok="$(gh auth token 2>/dev/null || true)"
  if [[ -n "$_tok" ]]; then
    AUTH_HEADER=(-H "Authorization: Bearer ${_tok}")
  fi
fi

owner_filter=""
search_term=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)   owner_filter="$2"; shift 2 ;;
    --search)  search_term="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 1. Fetch the list of every capsule from MCP tools/call (most reliable
#    cross-version). Falls back to scraping the HTML index if MCP is
#    unreachable.
list_via_mcp() {
  curl -fsS -X POST "$REGISTRY/mcp" "${AUTH_HEADER[@]}" \
    -H "content-type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"capsule_list"}}' \
    | "$PY" -c "
import sys, json
r = json.load(sys.stdin)
items = json.loads(r['result']['content'][0]['text'])
for it in items:
    print(f\"{it['owner']}/{it['name']}@{it['version']}\")
"
}

list_via_html() {
  # Sending the cookie path is the only way the homepage will include the
  # caller's own private capsules. AUTH_HEADER alone (header form) is also
  # accepted server-side; we send both forms for symmetry.
  local _tok=""
  if [[ ${#AUTH_HEADER[@]} -gt 0 ]]; then
    _tok="${AUTH_HEADER[1]#Authorization: Bearer }"
  fi
  if [[ -n "$_tok" ]]; then
    curl -fsS "$REGISTRY/" "${AUTH_HEADER[@]}" --cookie "capsule_token=${_tok}" \
      | grep -oE 'href=\"/c/[^\"@]+\"' \
      | sed -E 's#href=\"/c/##;s#\"$##'
  else
    curl -fsS "$REGISTRY/" \
      | grep -oE 'href=\"/c/[^\"@]+\"' \
      | sed -E 's#href=\"/c/##;s#\"$##'
  fi
}

addresses="$(list_via_mcp 2>/dev/null || list_via_html)"

# 2. For each capsule, fetch its rendered JSON to get type + purpose.
#    Done sequentially for predictable output ordering.
echo "$addresses" | while IFS= read -r addr; do
  # Strip trailing CR — Python on Windows emits \r\n; bash `read` keeps the \r,
  # which makes the URL invalid (Cloudflare 404s on the dangling %0D).
  addr="${addr%$'\r'}"
  [[ -z "$addr" ]] && continue
  if [[ -n "$owner_filter" ]]; then
    case "$addr" in
      "$owner_filter"/*) : ;;
      *) continue ;;
    esac
  fi

  # `</dev/null` is critical: without it curl inherits the outer
  # `while read` pipe and silently consumes the rest of the address list.
  # AUTH_HEADER is passed through so private capsules are fetchable.
  resp="$(curl -fsS "${AUTH_HEADER[@]}" "$REGISTRY/api/v1/capsule/$addr" </dev/null 2>/dev/null || true)"
  if [[ -z "$resp" ]]; then
    printf "%-60s  [?]  (fetch failed)\n" "$addr"
    continue
  fi

  line="$(echo "$resp" | "$PY" -c "
import sys, json
r = json.load(sys.stdin)
c = r.get('capsule', {})
purpose = (c.get('purpose', {}).get('summary') or '').strip().split(chr(10))[0]
t = c.get('type', '?')
print(f\"{t}|{purpose}\")
" 2>/dev/null || echo "?|(unparseable)")"

  ctype="${line%%|*}"
  purpose="${line#*|}"

  if [[ -n "$search_term" ]]; then
    if ! grep -qi "$search_term" <<<"$addr $purpose"; then
      continue
    fi
  fi

  printf "%-60s  [%s]  %s\n" "$addr" "$ctype" "$purpose"
done
