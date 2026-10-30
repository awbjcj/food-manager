from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from bin import release


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (["fix(pantry): handle empty receipts"], "v6.14.2"),
        (["perf: aggregate pantry statistics"], "v6.14.2"),
        (["refactor: simplify the provider adapter"], "v6.14.2"),
        (["feat(cook): add a new recipe source", "fix: timeout"], "v6.15.0"),
        (["feat(api)!: require the new request format", "feat: add recipes"], "v7.0.0"),
        (
            ["fix: change configuration\n\nBREAKING CHANGE: remove the legacy key"],
            "v7.0.0",
        ),
        (["refactor!: remove a public entry point"], "v7.0.0"),
        (["chore(deps): update the runtime dependencies"], "v6.14.2"),
        (["build(deps): update the base image"], "v6.14.2"),
        (["Support a new provider response shape"], "v6.14.2"),
        (
            [
                "docs: update setup",
                "test: cover the receipt edge case",
                "ci: automate releases",
            ],
            None,
        ),
        (["chore: tidy editor settings", "build: simplify the workflow"], None),
    ],
)
def test_version_bump_precedence(messages: list[str], expected: str | None):
    result = release.next_version(release.Version(6, 14, 1), messages)
    assert (str(result) if result else None) == expected


@pytest.mark.parametrize(
    "tag", ["v1.2", "1.2.3", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-rc.1"]
)
def test_only_stable_canonical_semver_is_accepted(tag: str):
    with pytest.raises(ValueError):
        release.Version.parse(tag)


def test_versions_sort_numerically():
    assert release.Version.parse("v6.10.0") > release.Version.parse("v6.9.9")


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    release.git("init", "--initial-branch=master")
    release.git("config", "user.name", "Release tests")
    release.git("config", "user.email", "release-tests@example.invalid")
    release.git("commit", "--allow-empty", "-m", "feat: original release")
    release.git("tag", "v6.9.0")
    return tmp_path


def test_bump_uses_numeric_latest_ancestor_and_is_repeatable(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    release.git("tag", "v6.10.0")
    release.git("commit", "--allow-empty", "-m", "feat: meal calendar")
    target = release.git("rev-parse", "HEAD")
    writes: list[tuple[str, str]] = []

    def create(_repo: str, tag: str, sha: str, *, apply: bool):
        assert apply
        writes.append((tag, sha))
        release.git("tag", tag, sha)

    monkeypatch.setattr(release, "create_tag", create)
    assert release.ensure_version("owner/repo", target, apply=True) == "v6.11.0"
    assert release.ensure_version("owner/repo", target, apply=True) == "v6.11.0"
    assert writes == [("v6.11.0", target)]


def test_docs_only_push_does_not_create_a_tag(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    release.git("commit", "--allow-empty", "-m", "docs: describe setup")
    create = Mock()
    monkeypatch.setattr(release, "create_tag", create)
    assert release.ensure_version("owner/repo", "HEAD", apply=True) == "v6.9.0"
    create.assert_not_called()


def test_unrelated_history_cannot_reuse_a_version(repository: Path):
    release.git("switch", "-c", "other")
    release.git("commit", "--allow-empty", "-m", "feat: another history")
    release.git("tag", "v6.10.0")
    release.git("switch", "master")
    release.git("commit", "--allow-empty", "-m", "feat: production feature")
    with pytest.raises(ValueError, match="existing tag"):
        release.ensure_version("owner/repo", "HEAD", apply=True)


@pytest.mark.parametrize(
    "status,conclusion",
    [("in_progress", None), ("completed", "failure"), ("completed", "cancelled")],
)
def test_incomplete_or_failed_ci_cannot_authorize_versioning(
    status: str, conclusion: str | None, monkeypatch: pytest.MonkeyPatch
):
    request = Mock(
        return_value={"workflow_runs": [{"status": status, "conclusion": conclusion}]}
    )
    monkeypatch.setattr(release, "api", request)
    with pytest.raises(ValueError, match="CI has not succeeded"):
        release.require_ci("owner/repo", "abc123")
    endpoint = request.call_args.args[1]
    assert "head_sha=abc123" in endpoint
    assert "branch=master" in endpoint
    assert "event=push" in endpoint


def test_missing_ci_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(release, "api", Mock(return_value={"workflow_runs": []}))
    with pytest.raises(ValueError, match="CI has not succeeded"):
        release.require_ci("owner/repo", "abc123")


def test_successful_production_ci_is_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        release,
        "api",
        Mock(
            return_value={
                "workflow_runs": [{"status": "completed", "conclusion": "success"}]
            }
        ),
    )
    release.require_ci("owner/repo", "abc123")


def test_release_cooldown_uses_publication_time_not_old_commit_time():
    now = datetime(2026, 10, 29, tzinfo=UTC)
    rows = [{"draft": False, "prerelease": False, "published_at": now.isoformat()}]
    assert not release.release_due(rows, now + timedelta(days=6, hours=23))
    assert release.release_due(rows, now + timedelta(days=7))
    assert release.release_due([], now)
    rows[0]["draft"] = True
    assert release.release_due(rows, now)


def test_releases_are_paginated(monkeypatch: pytest.MonkeyPatch):
    request = Mock(
        side_effect=[
            [{"tag_name": f"v1.0.{i}"} for i in range(100)],
            [{"tag_name": "v0.1.0"}],
        ]
    )
    monkeypatch.setattr(release, "api", request)
    assert len(release.releases("owner/repo")) == 101
    assert request.call_args.args[1].endswith("page=2")


def test_repeated_or_too_frequent_publication_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
):
    request = Mock()
    monkeypatch.setattr(release, "api", request)
    rows = [
        {
            "tag_name": "v6.9.0",
            "draft": False,
            "prerelease": False,
            "published_at": datetime.now(UTC).isoformat(),
        }
    ]
    release.publish("owner/repo", "v6.9.0", rows, apply=True)
    release.publish("owner/repo", "v6.10.0", rows, apply=True)
    request.assert_not_called()


def test_weekly_notes_include_every_intermediate_version(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    release.git("commit", "--allow-empty", "-m", "feat: add recipes")
    release.git("tag", "v6.10.0")
    release.git("commit", "--allow-empty", "-m", "fix: recipe timeout")
    release.git("tag", "v6.10.1")
    request = Mock()
    monkeypatch.setattr(release, "api", request)
    rows = [
        {
            "tag_name": "v6.9.0",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-01-01T00:00:00Z",
        }
    ]
    release.publish("owner/repo", "v6.10.1", rows, apply=True)
    body = request.call_args.args[2]
    assert "feat: add recipes" in body["body"]
    assert "fix: recipe timeout" in body["body"]
    assert "v6.9.0...v6.10.1" in body["body"]
    assert body["target_commitish"] == release.git("rev-parse", "HEAD")


def test_backfill_validates_every_tag_before_any_write(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    original = release.git("rev-parse", "HEAD")
    release.git("commit", "--allow-empty", "-m", "fix: latest production")
    directory = repository / ".github"
    directory.mkdir()
    directory.joinpath("version-history.json").write_text(
        json.dumps(
            [
                {"tag": "v6.8.0", "commit": original, "title": "Earlier milestone"},
                {"tag": "v6.9.0", "commit": "HEAD", "title": "Conflicting milestone"},
            ]
        ),
        encoding="utf-8",
    )
    request = Mock()
    monkeypatch.setattr(release, "api", request)
    with pytest.raises(ValueError, match="Refusing to move"):
        release.backfill("owner/repo", "HEAD", apply=True)
    request.assert_not_called()
    assert release.git("tag", "--list") == "v6.9.0"


def test_backfill_preview_has_no_remote_writes(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    directory = repository / ".github"
    directory.mkdir()
    directory.joinpath("version-history.json").write_text(
        json.dumps(
            [
                {
                    "tag": "v6.8.0",
                    "commit": "HEAD",
                    "title": "Reviewed milestone",
                    "release": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    request = Mock(return_value=[])
    monkeypatch.setattr(release, "api", request)
    release.backfill("owner/repo", "HEAD", apply=False)
    assert all(len(call.args) == 2 for call in request.call_args_list)
    assert release.git("tag", "--list") == "v6.9.0"


def test_backfill_rejects_commits_outside_production(repository: Path):
    release.git("switch", "-c", "unfinished")
    release.git("commit", "--allow-empty", "-m", "feat: unfinished work")
    unfinished = release.git("rev-parse", "HEAD")
    release.git("switch", "master")
    directory = repository / ".github"
    directory.mkdir()
    directory.joinpath("version-history.json").write_text(
        json.dumps(
            [
                {"tag": "v6.10.0", "commit": unfinished, "title": "Unfinished"},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(subprocess.CalledProcessError):
        release.backfill("owner/repo", "HEAD", apply=True)


def test_superseded_ci_run_exits_before_versioning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release.py",
            "tag",
            "--repo",
            "owner/repo",
            "--expected-sha",
            "old",
            "--apply",
        ],
    )
    monkeypatch.setattr(release, "git", Mock(side_effect=["", "new"]))
    request = Mock(return_value={"commit": {"sha": "new"}})
    monkeypatch.setattr(release, "api", request)
    version = Mock()
    monkeypatch.setattr(release, "ensure_version", version)
    release.main()
    version.assert_not_called()
    request.assert_called_once_with("owner/repo", "branches/master")


def test_backfill_cannot_publish_more_than_six_releases(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    directory = repository / ".github"
    directory.mkdir()
    directory.joinpath("version-history.json").write_text(
        json.dumps(
            [
                {
                    "tag": f"v{i}.0.0",
                    "commit": "HEAD",
                    "title": "Milestone",
                    "release": True,
                }
                for i in range(1, 8)
            ]
        ),
        encoding="utf-8",
    )
    request = Mock(return_value=[])
    monkeypatch.setattr(release, "api", request)
    with pytest.raises(ValueError, match="limited to six"):
        release.backfill("owner/repo", "HEAD", apply=True)
    assert all(len(call.args) == 2 for call in request.call_args_list)


def test_backfill_never_replaces_a_newer_latest_release(
    repository: Path, monkeypatch: pytest.MonkeyPatch
):
    directory = repository / ".github"
    directory.mkdir()
    directory.joinpath("version-history.json").write_text(
        json.dumps(
            [
                {
                    "tag": "v6.8.0",
                    "commit": "HEAD",
                    "title": "Older milestone",
                    "release": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    request = Mock(
        side_effect=[[{"tag_name": "v6.10.0", "draft": False, "prerelease": False}], {}]
    )
    monkeypatch.setattr(release, "api", request)
    monkeypatch.setattr(release, "create_tag", Mock())
    release.backfill("owner/repo", "HEAD", apply=True)
    assert request.call_args.args[2]["make_latest"] == "false"
