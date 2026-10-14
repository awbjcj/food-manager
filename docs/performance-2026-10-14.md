# Pantry read-path performance

The statistics and shopping-name reads now ask SQLite for the values callers
need. They retain their existing interfaces and keep household, eligibility,
date, and counting rules inside the pantry modules.

- `compute_stats` calculates counts and integer cost sums in SQLite. It avoids
  loading receipt records, pantry records, correction payloads, and recipe JSON.
  Its five queries return at most six aggregate rows.
- `count_confirmed` counts matching cooked meals directly.
- `active_pantry_names` selects only eligible names, preserving duplicates and
  the existing expiry/id order.

No callers, schema, or dependencies changed. The implementation retains
unknown-versus-zero costs, integer average rounding, inclusive cutoffs, and
caller-owned transactions. This fits [ADR-0001](adr/0001-long-running-python-bot-process.md).

## Measurements

Local synthetic measurements on Windows, October 14, 2026:

| Read | Before median | After median | ORM objects before → after | SELECTs before → after |
| --- | ---: | ---: | ---: | ---: |
| `compute_stats` | 159.707 ms | 14.022 ms | 9,000 → 0 | 5 → 5 |
| `count_confirmed` | 9.261 ms | 1.365 ms | 1,000 → 0 | 1 → 1 |
| `active_pantry_names` | 27.643 ms | 2.475 ms | 2,000 → 0 | 1 → 1 |

The fixture has 2,000 rows in each of five tables: Receipt, PantryItem,
PendingCorrection, CookSession, and CookedMeal. Half the active pantry items
are expired; half the meals are confirmed. Proposal payloads contain about
4 KiB and recipe payloads about 16 KiB of synthetic text. The benchmark uses
in-memory SQLite, a warm-up, and seven timed calls with fresh sessions;
fixture creation is excluded.

Before and after ran sequentially after validation. The before process loaded
the original read functions from Git revision `e282fee`, using the same
fixture, models, and installed dependencies as the after process. Timings vary
by machine and load; these figures are not production latency or an end-to-end
bot speedup. The stable improvement is bounded aggregate results and no ORM
history materialization.

Run the current benchmark from the repository root:

```powershell
uv run python -m bin.benchmark_pantry_reads
```

The [benchmark](../bin/benchmark_pantry_reads.py) creates its own in-memory
database and requires no bot credentials or network calls.

## Validation

Baseline: 973 tests passed. After: 980 tests passed, with the same three
pre-existing SQLModel query deprecation warnings. Ruff and Pyright passed.

[Regression tests](../tests/test_pantry_read_queries.py) verify exact results,
household isolation, date cutoffs, integer precision, unknown costs, duplicate
name ordering, autoflush/rollback, query counts, and zero history model loads.
The model-load guard failed on the original implementation before the change.

The architecture review also identified overlapping personal/household
preference-history reads. That candidate remains deferred until measured;
this change introduces no preference cache or new persistence abstraction.
