"""Convert raw LLM item names to canonical cache keys.

TODO(user): tune the rules in `normalize()` and seed `ALIASES`. See
spec §10 - this function determines cache hit rate over time. Examples
to honor:

  - lowercase + collapse whitespace
  - strip trailing size/qty suffixes (`1 gal`, `12 oz`, `6 ct`, `dozen`)
  - drop marketing adjectives that don't change shelf life
    (`organic`, `fresh`, `large`, `family size`)
  - PRESERVE form/state words that DO change shelf life
    (`frozen`, `cut`, `sliced`, `cooked`, `raw`)
  - apply ALIASES last
"""

import re

ALIASES: dict[str, str] = {
    # TODO(user): seed from your typical receipt vocabulary.
}

_ADJECTIVES_TO_STRIP = {
    "organic", "fresh", "large", "small", "medium",
    "family", "size", "natural", "premium",
}

_SIZE_SUFFIX = re.compile(
    r"\s*(?:\d+(?:\.\d+)?\s*"
    r"(?:gal|gallon|gallons|oz|lb|lbs|g|kg|ml|l|ct|count|pk|pack|bunch)"
    r"|dozen)\s*$",
    flags=re.IGNORECASE,
)


def normalize(raw: str) -> str:
    normalized = raw.lower().strip().replace(",", " ")
    while True:
        without_suffix = _SIZE_SUFFIX.sub("", normalized).strip()
        if without_suffix == normalized:
            break
        normalized = without_suffix
    tokens = [t for t in re.split(r"\s+", normalized) if t]
    tokens = [t for t in tokens if t not in _ADJECTIVES_TO_STRIP]
    normalized = " ".join(tokens)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return ALIASES.get(normalized, normalized)
