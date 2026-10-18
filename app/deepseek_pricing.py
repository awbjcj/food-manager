"""DeepSeek USD token estimates, checked against first-party prices 2026-10-18.

https://api-docs.deepseek.com/quick_start/pricing/
Rates are integer micro-USD per 1,000 tokens. Chinese public holidays are not
tracked: estimates conservatively use peak rates during weekday peak windows.
"""

from datetime import UTC, datetime
from fractions import Fraction

# cache miss, cache hit, output; legacy Flash IDs now bill as deepseek-flash.
_FLASH = (300, 6, 1200)
_PEAK_RATES = {
    "deepseek-flash": _FLASH,
    "deepseek-v4-flash": _FLASH,
    "deepseek-v4-flash-vision-exp": _FLASH,
    "deepseek-v4-pro": (1320, 44, 3960),
}


def deepseek_token_cost_micros(response, model: str) -> int | None:
    rates = _PEAK_RATES.get(model)
    usage = getattr(response, "usage", None)
    if rates is None or usage is None:
        return None
    try:
        inputs, outputs = usage.input_tokens, usage.output_tokens
        details = getattr(usage, "input_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) or 0
        if any(type(n) is not int or n < 0 for n in (inputs, outputs, cached)):
            return None
        if cached > inputs:
            return None
        # created_at is provider request time; never use the local wall clock.
        created_at = getattr(response, "created_at", None)
        off_peak = False
        if created_at is not None:
            when = datetime.fromtimestamp(created_at, UTC)
            off_peak = when.weekday() >= 5 or not (
                1 <= when.hour < 4 or 6 <= when.hour < 10
            )
        miss_rate, hit_rate, output_rate = rates
        total = (
            (inputs - cached) * miss_rate + cached * hit_rate + outputs * output_rate
        )
        # output_tokens already includes reasoning: don't add it a second time.
        return round(Fraction(total, 2000 if off_peak else 1000))
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return None
