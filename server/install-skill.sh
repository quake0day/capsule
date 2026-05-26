#!/usr/bin/env bash
# install-skill.sh — install the capsule-compose skill for Claude Code.
#
# Served from https://capsule-registry.pages.dev/install-skill.sh.
# Source of truth for the script content: this file in the capsule repo,
# at server/install-skill.sh (Cloudflare Pages serves it as a static file).
#
# What it does:
#   - Creates ~/.claude/skills/capsule-compose/
#   - Downloads SKILL.md and scripts/list-capsules.sh from the public
#     github mirror (no auth required, repo is Apache-2.0 OSS)
#   - Makes the helper script executable
#
# Inspect first if you don't want to pipe-to-bash:
#   curl -fsSL https://capsule-registry.pages.dev/install-skill.sh
#
set -euo pipefail

SKILL_DIR="${HOME}/.claude/skills/capsule-compose"
RAW="https://raw.githubusercontent.com/quake0day/capsule/main/skills/capsule-compose"

# Pretty output — use unicode where possible, fall back to ASCII.
ok()    { printf "  \033[32m✓\033[0m %s\n" "$*"; }
info()  { printf "  • %s\n" "$*"; }
warn()  { printf "  \033[33m!\033[0m %s\n" "$*" >&2; }
die()   { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required (not found on PATH)"

printf "\n\033[1mInstalling capsule-compose skill\033[0m\n"
printf "  target: %s\n\n" "$SKILL_DIR"

mkdir -p "$SKILL_DIR/scripts"

info "downloading SKILL.md"
curl -fsSL "$RAW/SKILL.md" -o "$SKILL_DIR/SKILL.md" \
  || die "failed to fetch SKILL.md from $RAW"
ok "wrote $SKILL_DIR/SKILL.md"

info "downloading scripts/list-capsules.sh"
curl -fsSL "$RAW/scripts/list-capsules.sh" -o "$SKILL_DIR/scripts/list-capsules.sh" \
  || die "failed to fetch list-capsules.sh from $RAW/scripts/"
chmod +x "$SKILL_DIR/scripts/list-capsules.sh"
ok "wrote $SKILL_DIR/scripts/list-capsules.sh (executable)"

# Friendly health check: can we hit the registry?
if curl -fsSL --max-time 5 -o /dev/null https://capsule-registry.pages.dev/ 2>/dev/null; then
  ok "registry reachable at https://capsule-registry.pages.dev"
else
  warn "registry not reachable right now — skill will still work, the helper just won't enumerate until the registry responds"
fi

# Soft check: is the capsule CLI on PATH? (Not required for skill install,
# but the skill calls capsule pull / man / status / reconstruct at runtime.)
if command -v capsule >/dev/null 2>&1; then
  ok "capsule CLI on PATH ($(command -v capsule))"
else
  warn "capsule CLI not on PATH yet — the skill will recommend installing it on first use:"
  printf "        git clone https://github.com/quake0day/capsule && cd capsule && pip install -e .\n"
fi

printf "\n\033[1m\033[32mDone.\033[0m\n\n"
printf "Next:\n"
printf "  1. Restart Claude Code (skills load at session start).\n"
printf "  2. Try a prompt like:\n"
printf "       \"find me capsules from the registry for a video chat app\"\n"
printf "       \"build a real-time chat starter using these capsules\"\n\n"
printf "The skill will discover relevant capsules, present a shortlist with\n"
printf "reasoning about fit + reuse cost, and ask before pulling + reconstructing.\n\n"
printf "Browse the registry:   https://capsule-registry.pages.dev/\n"
printf "Read the spec:         https://github.com/quake0day/capsule/blob/main/SPEC.md\n"
printf "Re-run this installer: curl -fsSL https://capsule-registry.pages.dev/install-skill.sh | bash\n\n"
