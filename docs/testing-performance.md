# Test suite performance

The ordinary full-suite command benefits from the optimized database setup:

```bash
uv run pytest
uv run pytest -q --durations=15
```

CI uses the second command to expose future slowdowns. Every test is collected
and executed; there are no new exclusions, retries, or test dependencies.
For a single file or more detailed timings:

```bash
uv run pytest tests/test_core_services.py
uv run pytest -q --durations=30
```

## Measured results

Measured on Windows with Python 3.13.2 on 2026-10-30, using an isolated copy of
commit `bf34294972b0dfe5110f625e55ea0930e4eaef14` and then applying only these
test-performance changes. Concurrent application edits were excluded. Both
serial runs used the same existing application dependencies and included all
original tests; the final run also included two new fixture isolation cases.

| Measurement | Before | After |
| --- | ---: | ---: |
| Full suite, pytest-reported elapsed time | 176.25 s | 164.59 s |
| Full command wall time | 183.74 s | 172.66 s |
| Seven affected modules, summed test phase durations | 43.97 s | 14.17 s |
| Passing tests | 1,244 | 1,246 |
| Failed / skipped tests | 0 / 0 | 0 / 0 |

The affected modules improved by 67.8%; total pytest runtime improved by 6.6%.
Migration and Git subprocess durations varied between runs and still account
for much of the remaining time. These are local observations, not a promised
CI runtime. JUnit test identities were compared: no original cases were lost.

## Database setup

Profiling identified repeated `SQLModel.metadata.create_all()` calls against
temporary files as a significant cost: a 34-test API/batch sample spent 9.95
seconds creating schemas, with 9.20 seconds inside SQLite cursor execution.

`tests/conftest.py::sqlite_db_path` supplies a separate SQLite file for each test.
It copies a schema-only template built once per pytest process. The template is
created with the normal rollback journal and its engine is disposed before any
copy; no open connections or WAL sidecars are copied. The template is rebuilt
from the current model metadata on every run, so model changes cannot leave a
persistent stale cache.

Each test still seeds its own data and creates its own engine. File-backed
transactions, independent connections, real commits/rollbacks, production
connection pragmas where used, and reopening a database remain available.
The fixture isolation tests verify empty starting data and persistence after
disposing and reopening an engine, including rollback behavior.

Use this fixture for service/HTTP tests that need an initialized file database.
Keep migration tests on fresh, empty paths: they must continue to execute the
actual migrations. Existing in-memory fixtures do not need to change.

## Tracking regressions

All three CI test entry points print the slowest 15 phases. Inspect setup time
as well as call time when investigating a slowdown. Compare runs on the same
machine and stable source/dependencies, with the same test count. Use serial
runs to attribute fixture improvements separately from parallel scheduling.
Do not enable parallel workers without measuring on the intended runner: SDK
imports and process startup can outweigh the gain. A two-worker experiment on
the benchmark machine passed all tests but took 198.80 seconds against a
176.25-second baseline, so parallel execution was not adopted.
Absolute CI timing assertions are intentionally avoided because shared-runner
load varies; the isolation tests guard the behavior that the optimization must
preserve.
