# Versions and releases

[English](releases.md) | [简体中文](releases.zh-CN.md)

Production versions use `vMAJOR.MINOR.PATCH` Git tags. The README's **version**
badge shows the highest stable tag; **release** shows the latest published
GitHub release. All versioned commits come from `master`, which Railway deploys.
`dev` remains the default development branch.

## Automatic version labels

After a successful **CI/CD** push run on the current `master` commit,
[Version and release](../.github/workflows/release.yml) examines commits since
the highest stable ancestor tag. The largest applicable bump wins:

| Commit signal | Version change | Example |
| --- | --- | --- |
| `type!:` or a `BREAKING CHANGE:` / `BREAKING-CHANGE:` footer | Major | `v6.14.1` → `v7.0.0` |
| `feat:` or `feat(scope):` | Minor | `v6.14.1` → `v6.15.0` |
| Fix, performance, refactor, revert, dependency update, or an unclassified commit | Patch | `v6.14.1` → `v6.14.2` |
| Only `docs:`, `test:`, `ci:`, `style:`, non-dependency `chore:` / `build:` | No tag | Version stays the same |

Use Conventional Commit subjects so feature and breaking changes receive the
right bump. A batch gets one tag, rather than a tag for every internal commit.
Failed, cancelled, pending, PR, fork, and superseded production CI runs cannot
authorize a new tag. Existing version tags are never moved or replaced.

## Batched GitHub releases

Every Friday at **14:23 UTC**, the workflow checks current production CI, labels
any unversioned production changes, and publishes the newest version if needed.
Notes include every commit since the preceding **published release**, including
changes covered by intermediate tags. No release is created for an already
published version. A seven-day minimum between public releases also applies to
manual runs, so re-running automation cannot flood the Releases page.

In GitHub Actions, **Version and release → Run workflow** offers `tag` (repair or
catch up version labels) and `publish` (consider the weekly batch). Because
schedules and manual dispatch are registered on GitHub's default branch, the
workflow is kept on both `dev` and `master`; it always checks out `master`.
The workflow uses the repository's `GITHUB_TOKEN` with `contents: write` and
`actions: read`, without an additional personal access token.

This automation records source versions. Railway deployment health and live
provider/payment acceptance remain separate checks. It does not publish to PyPI
or change the Python distribution's independent package metadata.

## Historical backfill

[version-history.json](../.github/version-history.json) records reviewed
completion points from the initial bot through the current production history.
All labels use three numeric components. Only **six** entries publish GitHub
releases, consolidating each major generation; the other milestones get tags.
The repository's older roadmap numbers were sometimes completed out of order
(for example, the full v4.9 recipe chain followed v5.0 stability work).
These legacy labels identify feature completion snapshots, rather than a
strict chronological release sequence.

Uncompleted or ambiguous roadmap slots are not invented: there is no separate
v4.3–v4.5 or v5.4 release checkpoint. Work originally planned as **v5.5** landed
after v6.0 and is recorded as **v6.1.0**, keeping subsequent production numbering
increasing. Later v6 minor versions group related completed feature sets, and
patch checkpoints cover intervening maintenance.

Preview the entire historical plan before writing:

```text
python bin/release.py backfill --repo awbjcj/food-manager
python bin/release.py backfill --repo awbjcj/food-manager --apply
```

The script validates every target against production ancestry and checks every
existing tag for conflicts before making any remote write. Re-running skips
existing matching tags and releases. It refuses to overwrite a tag or publish
more than six historical releases. The one-time historical backfill is exempt
from the weekly publication interval.

Implementation: [bin/release.py](../bin/release.py). Regression checks:
`uv run pytest tests/test_release.py`.
