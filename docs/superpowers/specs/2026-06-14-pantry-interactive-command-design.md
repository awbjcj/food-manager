# `/pantry` Interactive Command — Design Spec
_Date: 2026-06-14_

## Problem

Managing pantry items requires knowing item IDs and typing separate commands
(`/ate 5`, `/toss 12`, `/correct 3 expires tomorrow`, etc.). The daily digest
already renders an interactive inline-keyboard UI where the user can tap to act
on items. This feature brings that same UI on demand via a single `/pantry`
command with three optional modes.

## Command Syntax

```
/pantry           → full pantry (all active items, uncapped)
/pantry digest    → urgency-due items only (7-day window, same as daily digest)
/pantry <id>      → jump directly to the item card for that item
```

Legacy text commands (`/ate`, `/toss`, `/delete`, `/snooze`, `/correct`) remain
registered. They are not removed — just no longer the primary UX path.

## Argument Parser

New function in `app/commands.py`:

```python
def parse_pantry_arg(args: Sequence[str]) -> Literal["all", "digest"] | int
```

- No args → `"all"`
- `"digest"` → `"digest"`
- `"5"` or `"#5"` → `5` (integer item id, via `parse_item_id_arg`)
- Anything else → `CommandError`

## Callback Data — Stateless View Origin Encoding

`ItemAction` (in `commands.py`) gains one field:

```python
@dataclass(frozen=True)
class ItemAction:
    kind: ItemKind
    item_id: Optional[int] = None
    nudge_code: Optional[str] = None
    back_to: str = "digest"          # ← new; "digest" | "all"
```

New callback data forms handled by `parse_item_callback`:

| Callback string        | `back_to` | Meaning                                     |
|------------------------|-----------|---------------------------------------------|
| `item:open:<id>`       | `digest`  | Open card; Back returns to digest (existing)|
| `item:open:<id>:all`   | `all`     | Open card; Back returns to full pantry (new)|
| `item:list`            | `digest`  | Back to digest list (existing)              |
| `item:list:all`        | `all`     | Back to full-pantry list (new)              |

All other item callback forms (`corr`, `nudge`, `rm`, `rmok`, `ctext`) are
unchanged and always default `back_to="digest"`.

### Known v1 limitation

If the user drills into a correction sub-menu (`item:corr:<id>`) and presses
Back → item card (`item:open:<id>`), the `back_to` context resets to `"digest"`
for that one hop. Threading `:all` through every sub-screen callback is left for
a future pass.

## Renderer / Keyboard Changes (`app/renderer.py`)

Two functions gain an optional `back_to: str = "digest"` parameter — default
unchanged, so all existing callers are unaffected:

### `build_digest_keyboard(..., back_to: str = "digest")`

| `back_to`  | Item button `callback_data`  |
|------------|------------------------------|
| `"digest"` | `item:open:<id>` (existing)  |
| `"all"`    | `item:open:<id>:all` (new)   |

### `build_item_card_keyboard(item, *, lang, back_to: str = "digest")`

| `back_to`  | Back button `callback_data` |
|------------|-----------------------------|
| `"digest"` | `item:list` (existing)      |
| `"all"`    | `item:list:all` (new)       |

### Rendering approach

Both modes reuse `render_digest` (urgency-bucketed: Expired / Today / Tomorrow /
This week). In full-pantry mode, items with distant expiry dates land in "This
week" — functionally correct, cosmetically imprecise. Acceptable for v1.
Full-pantry mode is rendered with `cap=None` (no truncation).

## Bot Handler Changes (`app/bot.py`)

### New: `handle_pantry`

```
mode = parse_pantry_arg(args)
```

| mode       | Data source       | Keyboard                                  |
|------------|-------------------|-------------------------------------------|
| `"all"`    | `list_active(..., f=ListFilter.default())` | `build_digest_keyboard(back_to="all")`    |
| `"digest"` | `list_digest_due` | `build_digest_keyboard(back_to="digest")` |
| `int`      | `session.get`     | `build_item_card_keyboard(back_to="all")` |

For `int` mode: validates item exists and belongs to household; if item is not
active, shows `"#N is {status}; cannot manage"`.

### New: `_refresh_pantry_message`

Mirror of `_refresh_digest_message` for the full-pantry context:

- Data: `list_active(session, household_id=household_id, f=ListFilter.default(), today=today)`
- Keyboard: `build_digest_keyboard(..., back_to="all")`
- Empty fallback: `t("pantry.all_clear", lang)`

### Updated: `handle_item_callback`

When `action.kind == "list"`:
- `back_to == "digest"` → `_refresh_digest_message` (unchanged)
- `back_to == "all"` → `_refresh_pantry_message` (new)

When rendering `open`, `corr`, `rm` screens: passes `action.back_to` to
`build_item_card_keyboard` so the Back button round-trips correctly.

### Registration

`/pantry` added to `_MESSAGE_COMMANDS` with deps:
`("session_factory", "now_provider", "on_user_created", "translation_llm")`

## i18n (`app/i18n.py`)

One new key:

```python
"pantry.all_clear": {
    "en": "Your pantry is clear.",
    "zh": "您的食品储藏室已清空。",
    "fr": "Votre garde-manger est vide.",
    "es": "Tu despensa está vacía.",
}
```

All other strings (digest title, section headers, action buttons) are reused
unchanged.

## Files Changed

| File                  | Change                                                      |
|-----------------------|-------------------------------------------------------------|
| `app/commands.py`     | `parse_pantry_arg`; `ItemAction.back_to`; extend `parse_item_callback` |
| `app/renderer.py`     | `back_to` param on `build_digest_keyboard` + `build_item_card_keyboard` |
| `app/bot.py`          | `handle_pantry`; `_refresh_pantry_message`; update `handle_item_callback`; register `/pantry` |
| `app/i18n.py`         | `pantry.all_clear` key (4 languages)                        |

**No database changes. No migration required.**

## Out of Scope (v1)

- Threading `back_to` through correction/remove sub-menus
- A "later" bucket in full-pantry view for items beyond 7 days
- Pagination / "show more" in full-pantry mode (cap=None shows all)
