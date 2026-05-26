#!/usr/bin/env python3
"""Generate brand assets (logo, hero, OG card) via Gemini Imagen 4.

Usage:
    GEMINI_API_KEY=... python tools/gen_brand_images.py [slot ...]

Writes PNGs to server/assets/. Slots: logo, hero, og. Mirrors the pattern
in tools/gen_deck_images.py but writes to the deployed Pages assets dir.
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
ASSETS_DIR = REPO_ROOT / "server" / "assets"

ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/imagen-4.0-generate-001:predict"
)

# Each slot: (filename_stem, aspect_ratio, prompt).
# Aesthetic: warm pale beige outline + quiet mint-green accent on near-black,
# matching the dark code blocks used on the registry. Text-free prompts so
# the model can't hallucinate ugly captions.
SLOTS: dict[str, tuple[str, str]] = {
    "logo": (
        "1:1",
        "Minimal vector logo mark of a single side-view medicine pill capsule "
        "floating in the center of a dark charcoal square. The capsule is "
        "horizontal, drawn as a clean cream-colored thin outline. Its left half "
        "is filled with a glowing mint green color, fading to empty on the right. "
        "Sophisticated and quiet, like a high-end app icon. Symmetric empty space "
        "around the capsule. Flat 2D, no text, no shadows."
    ),
    "hero": (
        "16:9",
        "Wide minimalist art piece on a dark charcoal background. A single "
        "horizontal cream-outlined pill capsule glows softly in mint green and "
        "hovers slightly left of center. Around it, scattered thin cream-colored "
        "dots and gentle line traces suggest a quiet constellation. Generous "
        "empty dark space fills most of the canvas. Calm, contemplative, "
        "schematic. Flat 2D illustration, no text, no people."
    ),
    "og": (
        "16:9",
        "Wide minimalist illustration. Dark charcoal background. On the left "
        "third, a single horizontal cream-outlined pill capsule glows softly in "
        "mint green. A few faint cream dots float nearby. The right two thirds "
        "are pure dark empty space. Calm, sophisticated, schematic. Flat 2D, "
        "no text, no people."
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
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{exc.code} {exc.reason}: {err}") from exc


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
    ap = argparse.ArgumentParser(description="Generate brand assets with Gemini Imagen 4.")
    ap.add_argument("slots", nargs="*", help="Slot names. Default: all.")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("error: set GEMINI_API_KEY", file=sys.stderr)
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
            print(f"  ok {slot}  ->  {out}  ({out.stat().st_size} bytes)")
        except Exception as exc:
            print(f"  fail {slot}  ->  {exc}", file=sys.stderr)
            failures.append((slot, str(exc)))
    if failures:
        print(f"\n{len(failures)} of {len(targets)} failed.", file=sys.stderr)
        return 1
    print(f"\nall {len(targets)} generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
