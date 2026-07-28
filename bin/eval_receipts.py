"""Run private receipt photos through the live Anthropic API and diff expected JSON.

Usage: `uv run python bin/eval_receipts.py`

Reads photos from tests/fixtures/private_receipts/<name>.jpg or .png and
expected results from tests/fixtures/expected/<name>.json. This is manual
only and should not run in CI.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import anthropic

from app.llm import AnthropicLLMClient
from app.settings import Settings

FIXTURES = Path("tests/fixtures/private_receipts")
EXPECTED = Path("tests/fixtures/expected")


def _guess_media_type(photo: Path) -> str:
    suffix = photo.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/jpeg"


async def _evaluate_one(client: AnthropicLLMClient, photo: Path) -> tuple[bool, str]:
    expected_path = EXPECTED / (photo.stem + ".json")
    if not expected_path.exists():
        return False, f"no expected file at {expected_path}"
    expected = json.loads(expected_path.read_text())

    result = await client.extract_items_from_image(
        photo.read_bytes(),
        image_media_type=_guess_media_type(photo),
    )
    actual_items = [
        {"name": item.name, "est_shelf_life_days": item.est_shelf_life_days}
        for item in result.parse.items
        if item.is_food
    ]

    diffs = []
    for expected_item in expected.get("items", []):
        match = next(
            (
                actual
                for actual in actual_items
                if actual["name"].lower() == expected_item["name"].lower()
            ),
            None,
        )
        if match is None:
            diffs.append(f"missing item: {expected_item['name']!r}")
            continue
        if abs(match["est_shelf_life_days"] - expected_item["est_shelf_life_days"]) > 1:
            diffs.append(
                f"{expected_item['name']}: shelf life {match['est_shelf_life_days']} "
                f"vs expected {expected_item['est_shelf_life_days']}"
            )
    if diffs:
        return False, "  " + "\n  ".join(diffs)
    return True, f"{len(actual_items)} items matched"


async def _amain() -> int:
    settings = Settings.load()
    sdk = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    client = AnthropicLLMClient(sdk=sdk, model=settings.anthropic_model)

    photos = sorted(FIXTURES.glob("*.jpg")) + sorted(FIXTURES.glob("*.png"))
    if not photos:
        print(f"no photos in {FIXTURES}/")
        return 0

    failures = 0
    for photo in photos:
        ok, detail = await _evaluate_one(client, photo)
        print(f"{'PASS' if ok else 'FAIL'} {photo.name}\n{detail}\n")
        if not ok:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
