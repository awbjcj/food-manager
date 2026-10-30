"""Conservative Telegram text bounds, including non-BMP characters."""


def text_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def bounded_text(text: str, limit: int) -> str:
    if text_units(text) <= limit:
        return text
    return text.encode("utf-16-le")[:(limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
