from __future__ import annotations

from app.normalization import normalize

BLEND_WEIGHTS = {"health": 0.4, "expiry": 0.4, "deliciousness": 0.2}


def violates_exclusions(ingredient_names, *, exclusions) -> bool:
    norm_excl = [normalize(e) for e in exclusions if e.strip()]
    for raw in ingredient_names:
        n = normalize(raw)
        for ex in norm_excl:
            if ex and ex in n:
                return True
    return False


def expiry_utilization(*, recipe_names, urgent_names) -> float:
    if not urgent_names:
        return 0.0
    recipe_norm = {normalize(n) for n in recipe_names}
    used = sum(1 for u in urgent_names if normalize(u) in recipe_norm)
    return used / len(urgent_names)


def blended_score(*, health_0_1: float, expiry_use: float, deliciousness: float) -> float:
    return (
        BLEND_WEIGHTS["health"] * health_0_1
        + BLEND_WEIGHTS["expiry"] * expiry_use
        + BLEND_WEIGHTS["deliciousness"] * deliciousness
    )


def shopping_list(*, recipe_names, pantry_normalized) -> list[str]:
    have = {normalize(n) for n in pantry_normalized}
    missing: list[str] = []
    seen: set[str] = set()
    for raw in recipe_names:
        n = normalize(raw)
        if n in have or n in seen:
            continue
        seen.add(n)
        missing.append(raw)
    return missing
