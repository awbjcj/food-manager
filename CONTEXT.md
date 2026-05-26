# Food Manager

Food Manager tracks groceries from receipt capture through expiry reminders.

## Language

**Pantry Item**:
A stock lot from one receipt line that is tracked and acted on as a whole.
_Avoid_: Individual serving, partially consumed quantity

**Pantry-Relevant Receipt Item**:
A purchased food item from a receipt that should be tracked for expiry.
_Avoid_: Receipt line, transaction line, accounting line

**Purchase Date**:
The date pantry-relevant receipt items were bought, read from the receipt when possible and otherwise assumed from scan date.
_Avoid_: Freshness date

**Active Pantry Item**:
A pantry item that remains in the pantry, whether or not reminders are temporarily snoozed.
_Avoid_: Unsnoozed item

**Snooze**:
A temporary suppression of reminders for an active pantry item.
_Avoid_: Shelf-life extension, expiry extension

**Tossed Pantry Item**:
A pantry item discarded as food waste.
_Avoid_: Deleted item, duplicate item

**Removed Pantry Item**:
A pantry item excluded because it was logged incorrectly or as a duplicate.
_Avoid_: Tossed item

**Shelf Life**:
The number of days a pantry item is expected to keep from its purchase date.
_Avoid_: Days remaining, reminder offset

**Shelf-Life Correction**:
A user correction that fixes one pantry item's expiry and teaches that user's future estimates for the same normalized food.
_Avoid_: One-off expiry edit

## Relationships

- A **Pantry Item** comes from one receipt line.
- A **Pantry-Relevant Receipt Item** becomes a **Pantry Item** when logged.
- A **Purchase Date** is shared by the pantry-relevant receipt items from the same receipt.
- Multiple **Pantry Items** can have the same normalized food name when they come from separate purchases.
- A **Pantry Item** is consumed, tossed, or snoozed as a whole in v1.
- An **Active Pantry Item** may have reminders suppressed until a future date.
- A **Snooze** does not change a pantry item's expiry date or shelf life.
- A **Shelf Life** determines a pantry item's expiry date from its purchase date.
- A **Shelf-Life Correction** applies to one pantry item and that user's future pantry items with the same normalized food.
- A **Tossed Pantry Item** counts as waste; a **Removed Pantry Item** does not.

## Example dialogue

> **Dev:** "If a receipt says bananas x6, does tapping eaten mean one banana was eaten?"
> **Domain expert:** "No - that **Pantry Item** is treated as one stock lot, so the whole lot leaves the active pantry."

> **Dev:** "If you buy milk before finishing the previous milk, should those rows merge?"
> **Domain expert:** "No - they are separate **Pantry Items** with separate purchase and expiry dates."

> **Dev:** "Do we keep receipt discounts, totals, or non-food lines?"
> **Domain expert:** "No - only **Pantry-Relevant Receipt Items** are tracked."

> **Dev:** "If you scan an old receipt today, do groceries get a fresh shelf life from today?"
> **Domain expert:** "No - their **Shelf Life** starts from the **Purchase Date** on the receipt."

> **Dev:** "If the receipt date is unreadable, should ingestion stop?"
> **Domain expert:** "No - use the scan date as the assumed **Purchase Date** and make that assumption visible."

> **Dev:** "Does snoozing remove a **Pantry Item** from the pantry?"
> **Domain expert:** "No - it remains an **Active Pantry Item**; snoozing only hides it from reminders until later."

> **Dev:** "Does Remind +2d mean the food lasts two days longer?"
> **Domain expert:** "No - that **Snooze** only delays reminders."

> **Dev:** "When you correct milk to 10 days, is that 10 days from today?"
> **Domain expert:** "No - that is the **Shelf Life**, counted from when the milk was purchased."

> **Dev:** "If you correct strawberries once, should future strawberries use that value too?"
> **Domain expert:** "Yes - a **Shelf-Life Correction** teaches the estimate for that normalized food."

> **Dev:** "If an item was imported twice, should I mark it tossed?"
> **Domain expert:** "No - make it a **Removed Pantry Item** so waste stats stay accurate."

## Flagged ambiguities

- Partial consumption of a **Pantry Item** is intentionally out of scope for v1.
- Receipt accounting and receipt-line archival are out of scope; only **Pantry-Relevant Receipt Items** are tracked.
- Snoozing does not create a separate pantry item state; it temporarily suppresses reminders for an **Active Pantry Item**.
- A **Snooze** does not extend **Shelf Life**.
- Corrections use **Shelf Life**, not days remaining.
- A **Shelf-Life Correction** is intentionally broader than a one-off expiry edit in v1.
- **Tossed Pantry Item** is reserved for food waste; import cleanup uses **Removed Pantry Item**.
