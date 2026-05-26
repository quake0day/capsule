"""Cloudflare Workers AI provider for capsule decompose.

Reads credentials in this order:
  1. CF_API_TOKEN + CF_ACCOUNT_ID env vars (explicit override)
  2. wrangler OAuth token from ~/.wrangler/config/default.toml (or the
     Windows xdg-style path under %APPDATA%/xdg.config/.wrangler/) — works
     out of the box for anyone who has run `wrangler login`

This is the magic that lets the decompose feature work without asking the
user to create a new API token: if they have wrangler installed and authed
(which everyone deploying Pages does), they can use Workers AI for free.

Per-model pricing table is the same one the registry exposes on
/benchmarks; sourced from cloudflare.com/workers-ai/platform/pricing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# Per-model pricing in USD per M tokens (input, output) — from the
# Cloudflare Workers AI pricing page. Used by the benchmark harness to
# estimate cost per decompose run.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI open-weight
    "@cf/openai/gpt-oss-120b":                  (0.35, 0.75),
    "@cf/openai/gpt-oss-20b":                   (0.20, 0.30),
    # Meta Llama
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": (0.29, 2.25),
    "@cf/meta/llama-3.1-70b-instruct":          (0.59, 0.79),
    "@cf/meta/llama-3.1-8b-instruct":           (0.28, 0.83),
    "@cf/meta/llama-4-scout-17b-16e-instruct":  (0.27, 0.85),
    "@cf/meta/llama-3.2-11b-vision-instruct":   (0.05, 0.68),
    # Alibaba Qwen
    "@cf/qwen/qwen2.5-coder-32b-instruct":      (0.66, 1.00),
    "@cf/qwen/qwq-32b":                         (0.66, 1.00),
    # DeepSeek
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": (0.50, 4.88),
    # Mistral
    "@cf/mistralai/mistral-small-3.1-24b-instruct": (0.35, 0.56),
    # Google
    "@cf/google/gemma-3-12b-it":                (0.35, 0.56),
    # Moonshot Kimi — frontier-scale, 1T params, 262K context
    "@cf/moonshotai/kimi-k2.6":                 (0.00, 0.00),  # pricing TBD
    "@cf/moonshotai/kimi-k2.5":                 (0.00, 0.00),
}


# Short aliases → full CF model IDs. Lets the user say --model llama-3.3-70b
# instead of --model @cf/meta/llama-3.3-70b-instruct-fp8-fast.
MODEL_ALIASES: dict[str, str] = {
    "gpt-oss-120b":       "@cf/openai/gpt-oss-120b",
    "gpt-oss-20b":        "@cf/openai/gpt-oss-20b",
    "llama-3.3-70b":      "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "llama-3.1-70b":      "@cf/meta/llama-3.1-70b-instruct",
    "llama-3.1-8b":       "@cf/meta/llama-3.1-8b-instruct",
    "llama-4-scout-17b":  "@cf/meta/llama-4-scout-17b-16e-instruct",
    "llama-3.2-11b-vision": "@cf/meta/llama-3.2-11b-vision-instruct",
    "qwen2.5-coder-32b":  "@cf/qwen/qwen2.5-coder-32b-instruct",
    "qwen-qwq-32b":       "@cf/qwen/qwq-32b",
    "deepseek-r1-32b":    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "mistral-small-3.1":  "@cf/mistralai/mistral-small-3.1-24b-instruct",
    "mistral-small":      "@cf/mistralai/mistral-small-3.1-24b-instruct",
    "gemma-3-12b":        "@cf/google/gemma-3-12b-it",
    "kimi":               "@cf/moonshotai/kimi-k2.6",
    "kimi-k2.6":          "@cf/moonshotai/kimi-k2.6",
    "kimi-k2.5":          "@cf/moonshotai/kimi-k2.5",
}


DEFAULT_WORKERS_AI_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


# Possible locations for wrangler's OAuth config. Wrangler uses the XDG-
# style "xdg.config" subdir on Windows under %APPDATA%, and ~/.wrangler/
# on Unix.
_WRANGLER_CONFIG_CANDIDATES = [
    Path.home() / ".wrangler" / "config" / "default.toml",
    Path.home() / ".config" / ".wrangler" / "config" / "default.toml",
    # Windows
    Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "default.toml",
]


@dataclass
class CFCredentials:
    account_id: str
    token: str
    source: str  # "env" | "wrangler"


def resolve_credentials() -> CFCredentials:
    """Find CF credentials. Tries env vars first, then wrangler OAuth."""
    token = os.environ.get("CF_API_TOKEN")
    account_id = os.environ.get("CF_ACCOUNT_ID")
    if token and account_id:
        return CFCredentials(account_id=account_id, token=token, source="env")

    # Fall back to wrangler OAuth.
    wr_token = _wrangler_oauth_token()
    if not wr_token:
        raise WorkersAIError(
            "No Cloudflare credentials found. Either:\n"
            "  - Set CF_API_TOKEN + CF_ACCOUNT_ID env vars, or\n"
            "  - Run `wrangler login` once (anywhere) — capsule will reuse the OAuth token."
        )
    if not token:
        token = wr_token
    if not account_id:
        account_id = _query_account_id(token)
    # If the cached token has expired (~1h lifetime), prod wrangler to
    # refresh on disk, then re-read + retry once.
    if not account_id:
        if _wrangler_refresh():
            wr_token = _wrangler_oauth_token()
            if wr_token:
                token = wr_token
                account_id = _query_account_id(token)
    if not account_id:
        raise WorkersAIError(
            "Found a Cloudflare token but could not resolve an account id "
            "(may be expired). Run `wrangler login` to re-auth, or set "
            "CF_ACCOUNT_ID + CF_API_TOKEN env vars explicitly."
        )
    return CFCredentials(account_id=account_id, token=token, source="wrangler")


def resolve_model(name: str | None) -> str:
    """Expand alias → full CF model id. Pass-through if already full."""
    if not name:
        return DEFAULT_WORKERS_AI_MODEL
    if name.startswith("@cf/"):
        return name
    if name in MODEL_ALIASES:
        return MODEL_ALIASES[name]
    raise WorkersAIError(
        f"Unknown model alias '{name}'. Known aliases: {', '.join(sorted(MODEL_ALIASES))}, "
        "or pass the full `@cf/...` id."
    )


class WorkersAIError(Exception):
    """Anything that goes wrong calling Cloudflare Workers AI."""


def call_workers_ai(
    prompt: str,
    *,
    model: str,
    creds: CFCredentials,
    max_tokens: int = 16000,
    temperature: float = 0.4,
) -> dict:
    """Single-shot call to Workers AI. Returns the normalised response shape
    used by decompose.extract_json — { text, raw, provider, usage? }."""
    full_model = resolve_model(model)
    url = f"https://api.cloudflare.com/client/v4/accounts/{creds.account_id}/ai/run/{full_model}"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "User-Agent": "capsule-cli/0.4 (+https://github.com/quake0day/capsule)",
        },
    )
    try:
        # Generous timeout — large-output generations on bigger CF models
        # can take 2-5 minutes.
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WorkersAIError(f"Workers AI {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise WorkersAIError(f"could not reach Workers AI: {exc.reason}") from exc

    # Workers AI returns one of two shapes depending on the model family:
    #   - CF-native:        {"success": true, "result": {"response": "..."}}
    #   - OpenAI-compatible (gpt-oss-*, some others):
    #     {"result": {"choices": [{"message": {"content": "..."}}]}}
    # The top-level `success` key is also missing on OpenAI-shaped responses,
    # so don't bail on its absence — only bail if explicit success=false.
    # Optional debug dump.
    debug_path = os.environ.get("CAPSULE_DEBUG_CF_PATH")
    if debug_path:
        try:
            Path(debug_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    if payload.get("success") is False:
        errors = payload.get("errors", [])
        raise WorkersAIError(f"Workers AI returned success=false: {errors}")

    result = payload.get("result") or {}
    text = _extract_text(result)
    if not text:
        # Surface the keys we found so the next failure mode is easier to fix.
        keys = _summarize_shape(payload)
        raise WorkersAIError(
            f"Workers AI returned no response text. Top-level shape: {keys}. "
            f"Tried CF-native (`result.response`), OpenAI (`result.choices[0]"
            f".message.content`), and reasoning-model variants. "
            f"Set CAPSULE_DEBUG_CF_PATH=/tmp/cf.json to dump full payload. "
            f"First 400 chars: {json.dumps(payload)[:400]}"
        )

    usage = result.get("usage") or {}
    return {
        "text": text,
        "raw": payload,
        "provider": "workers-ai",
        "model": full_model,
        "usage": usage,
    }


def _extract_text(result: dict) -> str:
    """Find the response text across all CF Workers AI response shapes:

    1. CF-native:                              result.response
    2. OpenAI chat completion:                 result.choices[0].message.content
    3. gpt-oss reasoning models (sometimes):   result.choices[0].message.reasoning_content + content
    4. Some streaming-style payloads:          result.delta / result.text
    """
    # 1. CF native
    text = result.get("response")
    if text:
        return text

    # 2 + 3: choices array
    choices = result.get("choices") or []
    if choices:
        msg = (choices[0] or {}).get("message") or {}
        # Prefer content; if absent, fall back to reasoning_content (some
        # gpt-oss responses put the JSON output in reasoning_content when the
        # model "talked itself into" treating it as chain-of-thought).
        for key in ("content", "reasoning_content", "reasoning", "text"):
            v = msg.get(key)
            if v:
                return v
        # Some responses tuck output into the top of the choice instead.
        for key in ("text", "delta"):
            v = (choices[0] or {}).get(key)
            if v:
                return v

    # 4. miscellaneous
    for key in ("text", "delta", "output_text"):
        v = result.get(key)
        if v:
            return v
    return ""


def _summarize_shape(payload: dict, depth: int = 2) -> str:
    """Return a compact dotted path of keys for diagnostic messages."""
    def walk(o, prefix=""):
        if depth == 0 or not isinstance(o, dict):
            return [prefix.rstrip(".")]
        out = []
        for k in list(o.keys())[:8]:
            out.extend(walk(o[k], f"{prefix}{k}."))
        return out
    return ", ".join(walk(payload))


# ---------------------------------------------------------------------------
# wrangler OAuth helpers
# ---------------------------------------------------------------------------


def _wrangler_oauth_token() -> str | None:
    """Read wrangler's cached OAuth token. Wrangler rotates these ~hourly;
    if the cached one is stale, the caller will get a 403 and should call
    `_wrangler_refresh()` to trigger a refresh on disk."""
    for path in _WRANGLER_CONFIG_CANDIDATES:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
            else:
                continue
        except OSError:
            continue
        # Tiny TOML reader for just the oauth_token key — avoids a tomllib
        # dependency on older Pythons.
        m = re.search(r'^oauth_token\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def _wrangler_refresh() -> bool:
    """Force wrangler to refresh its OAuth token by invoking a cheap
    authenticated command. Returns True if the refresh ran cleanly."""
    import shutil
    import subprocess
    npx = shutil.which("npx")
    if not npx:
        return False
    try:
        subprocess.run(
            [npx, "wrangler", "whoami"],
            check=True, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def _query_account_id(token: str) -> str | None:
    """List accounts visible to this token; return the first account id.

    Diagnostics on failure are surfaced via CAPSULE_DEBUG_CF=1 (stderr).
    """
    debug = os.environ.get("CAPSULE_DEBUG_CF") == "1"
    try:
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4/accounts",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "capsule-cli/0.4",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if debug:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"[cf debug] /accounts → {exc.code}: {body[:200]}", file=__import__("sys").stderr)
        return None
    except urllib.error.URLError as exc:
        if debug:
            print(f"[cf debug] /accounts URL error: {exc.reason}", file=__import__("sys").stderr)
        return None
    if not payload.get("success"):
        if debug:
            print(f"[cf debug] /accounts success=false: {payload.get('errors')}", file=__import__("sys").stderr)
        return None
    results = payload.get("result") or []
    if not results:
        if debug:
            print("[cf debug] /accounts returned 0 accounts", file=__import__("sys").stderr)
        return None
    return results[0].get("id")


# ---------------------------------------------------------------------------
# cost estimation (for the benchmark harness)
# ---------------------------------------------------------------------------


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD given prompt + completion token counts."""
    full_model = resolve_model(model)
    if full_model not in MODEL_PRICING:
        return 0.0
    in_per_m, out_per_m = MODEL_PRICING[full_model]
    return (input_tokens / 1_000_000.0) * in_per_m + (output_tokens / 1_000_000.0) * out_per_m
