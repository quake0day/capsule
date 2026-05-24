#!/usr/bin/env python3
"""Generate deck images via Gemini Imagen 3.

Usage:
    GEMINI_API_KEY=... python tools/gen_deck_images.py [slot ...]

If no slots are passed, generates all of them. Each slot writes a PNG to
deck/assets/<slot>.png. The script never prints the API key; if you see
a key in this file's output it's a bug, file an issue.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "deck" / "assets"

ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/imagen-4.0-generate-001:predict"
)

# Each slot: (filename_stem, aspect_ratio, prompt). The prompts are tuned for
# the deck's terminal-developer aesthetic — near-black background, warm off-
# white type, single mint/terminal-green accent (#8ef3a8), JetBrains Mono +
# IBM Plex Sans pairing.
SLOTS: dict[str, tuple[str, str]] = {
    "problem": (
        "16:9",
        "A minimalist abstract artwork painted in a flat illustration style. Deep "
        "matte black background. On the left half of the canvas: a tightly packed "
        "field of identical tiny pale-beige paper squares, all the same size, "
        "arranged like a quiet wall of postage stamps. On the right half: five large "
        "smooth horizontal pill-capsule shapes stacked vertically with even gaps "
        "between them; each pill is just a fine pale-beige outline, hollow inside; "
        "the left tip of each pill softly glows a quiet mint green. A thin vertical "
        "column of faint dots runs down the middle, separating the two halves. "
        "Generous black negative space. No letters, no numbers, no symbols, no "
        "writing of any kind anywhere in the image. No people, no faces, no logos. "
        "The atmosphere is calm and sophisticated, the look of a luxury developer "
        "tool brand."
    ),
    "unix": (
        "16:9",
        "Minimal abstract diagram on a deep black background. Three identical rounded "
        "horizontal pill shapes arranged in a row from left to right, each drawn in "
        "fine pale beige outline with a soft mint glow on one edge. Between each pair "
        "of pills, a single thin horizontal line with a small arrow tip, evoking a "
        "Unix pipe. Generous empty space above and below. Flat vector style. No text "
        "anywhere in the image, no captions, no labels, no annotations, no code, no "
        "people, no logos, no shadows. Sparse, calm, schematic."
    ),
    "positioning": (
        "16:9",
        "A minimalist abstract artwork painted in a flat illustration style. Deep "
        "matte black background. Five large smooth horizontal pill-capsule shapes "
        "are arranged vertically with even spacing between them. The top, second, "
        "fourth, and bottom pills are each drawn as a fine pale-beige outline, "
        "hollow inside. The middle pill is highlighted: its outline is a bright "
        "mint green, its interior is filled with a soft mint-green tint, and it is "
        "slightly taller than the four other pills. A small mint-green dot floats "
        "just to the left of the highlighted middle pill. Generous black negative "
        "space surrounds the composition. No letters, no numbers, no symbols, no "
        "writing of any kind anywhere in the image. No people, no faces, no logos. "
        "The atmosphere is calm and sophisticated, the look of a luxury developer "
        "tool brand."
    ),
    "closing": (
        "16:9",
        "An expansive hero visual on a dark near-black background. A single large "
        "rounded-rectangular capsule shape in the lower third, drawn in fine pale "
        "beige linework with a soft inner mint glow. Above it, thin ghosted "
        "constellations of dotted connecting lines suggesting many agents and tools "
        "radiating outward into the upper darkness, like a network of interfaces and "
        "contracts. Empty negative space dominates the upper two thirds. Sparse, "
        "atmospheric, technical, calm. No people, no faces, no logos, no text."
    ),
}


def _post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {exc.reason}: {body}") from exc


def generate_one(slot: str, aspect_ratio: str, prompt: str, *, api_key: str) -> Path:
    url = f"{ENDPOINT}?key={api_key}"
    body = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": aspect_ratio,
            "safetyFilterLevel": "block_only_high",
            "personGeneration": "dont_allow",
        },
    }
    resp = _post_json(url, body)
    predictions = resp.get("predictions") or []
    if not predictions:
        raise RuntimeError(f"no predictions for {slot}: {json.dumps(resp)[:400]}")
    b64 = predictions[0].get("bytesBase64Encoded")
    if not b64:
        raise RuntimeError(f"no bytesBase64Encoded for {slot}: {json.dumps(predictions[0])[:400]}")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    out = ASSETS_DIR / f"{slot}.png"
    out.write_bytes(base64.b64decode(b64))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate deck images with Gemini Imagen 3.")
    ap.add_argument("slots", nargs="*", help="Slot names to generate. Default: all.")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("error: set GEMINI_API_KEY environment variable", file=sys.stderr)
        return 2

    targets = args.slots or list(SLOTS.keys())
    unknown = [s for s in targets if s not in SLOTS]
    if unknown:
        print(f"error: unknown slot(s): {unknown}. Known: {list(SLOTS)}", file=sys.stderr)
        return 2

    failures: list[tuple[str, str]] = []
    for slot in targets:
        aspect, prompt = SLOTS[slot]
        try:
            out = generate_one(slot, aspect, prompt, api_key=api_key)
            print(f"  ✓ {slot}  →  {out}  ({out.stat().st_size} bytes)")
        except Exception as exc:
            print(f"  ✗ {slot}  →  {exc}", file=sys.stderr)
            failures.append((slot, str(exc)))
    if failures:
        print(f"\n{len(failures)} of {len(targets)} failed.", file=sys.stderr)
        return 1
    print(f"\nall {len(targets)} generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
