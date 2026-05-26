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
  curl -fsS -X POST "$REGISTRY/mcp" \
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
  curl -fsS "$REGISTRY/" \
    | grep -oE 'href=\"/c/[^\"@]+\"' \
    | sed -E 's#href=\"/c/##;s#\"$##'
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
  resp="$(curl -fsS "$REGISTRY/api/v1/capsule/$addr" </dev/null 2>/dev/null || true)"
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
