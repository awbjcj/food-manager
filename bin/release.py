"""SemVer tags and deliberately batched GitHub releases (stdlib + git + gh)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
CONVENTIONAL = re.compile(r"^(\w+)(?:\([^\r\n)]+\))?(!)?:\s")
COOLDOWN = timedelta(days=7)


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def api(repo: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", f"repos/{repo}/{endpoint}"]
    if payload is None:
        command.extend(["--method", "GET"])
    else:
        command.extend(["--method", "POST", "--input", "-"])
    try:
        result = subprocess.run(
            command,
            input=None if payload is None else json.dumps(payload),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.stderr.strip()}") from exc
    return json.loads(result.stdout)


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, tag: str) -> Version:
        match = SEMVER.fullmatch(tag)
        if match is None:
            raise ValueError(f"Not a stable SemVer tag: {tag!r}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"


def next_version(current: Version, messages: list[str]) -> Version | None:
    """Highest Conventional Commit bump wins; non-conventional changes get a patch."""
    level = 0
    for message in messages:
        subject = message.splitlines()[0]
        match = CONVENTIONAL.match(subject)
        if (match and match[2]) or re.search(
            r"^BREAKING[ -]CHANGE:\s", message, re.MULTILINE
        ):
            level = 3
        elif match and match[1] == "feat":
            level = max(level, 2)
        elif (
            not match
            or match[1] not in {"docs", "test", "ci", "style", "chore", "build"}
            or subject.startswith(("chore(deps):", "build(deps):"))
        ):
            level = max(level, 1)
    if level == 3:
        return Version(current.major + 1, 0, 0)
    if level == 2:
        return Version(current.major, current.minor + 1, 0)
    if level == 1:
        return Version(current.major, current.minor, current.patch + 1)
    return None


def stable_tags(target: str) -> list[Version]:
    return sorted(
        Version.parse(tag)
        for tag in git("tag", "--merged", target).splitlines()
        if SEMVER.fullmatch(tag)
    )


def tag_commit(tag: str) -> str:
    return git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")


def require_ci(repo: str, target: str) -> None:
    query = urlencode(
        {"branch": "master", "event": "push", "head_sha": target, "per_page": 1}
    )
    runs = api(repo, f"actions/workflows/ci.yml/runs?{query}")["workflow_runs"]
    if (
        not runs
        or runs[0]["status"] != "completed"
        or runs[0]["conclusion"] != "success"
    ):
        raise ValueError(f"Production CI has not succeeded for {target}")


def releases(repo: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(repo, f"releases?per_page=100&page={page}")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
        page += 1


def create_tag(repo: str, tag: str, target: str, *, apply: bool) -> None:
    print(f"Tag {tag} -> {target}")
    if apply:
        api(repo, "git/refs", {"ref": f"refs/tags/{tag}", "sha": target})
        git("fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}")


def ensure_version(repo: str, target: str, *, apply: bool) -> str:
    tags = stable_tags(target)
    if not tags:
        raise ValueError(
            "Backfill the reviewed version history before enabling automation"
        )
    current = tags[-1]
    if tag_commit(str(current)) == target:
        print(f"{current} already labels this commit")
        return str(current)
    messages = [
        text
        for text in git("log", "--format=%B%x00", f"{current}..{target}").split("\0")
        if text.strip()
    ]
    version = next_version(current, [text.strip() for text in messages])
    if version is None:
        print("No version change: only documentation, tests, or build/CI maintenance")
        return str(current)
    tag = str(version)
    all_tags = set(git("tag", "--list").splitlines())
    if tag in all_tags:
        raise ValueError(f"Refusing to reuse existing tag {tag} from another history")
    create_tag(repo, tag, target, apply=apply)
    return tag


def release_due(rows: list[dict[str, Any]], now: datetime) -> bool:
    published = [
        datetime.fromisoformat(row["published_at"])
        for row in rows
        if not row["draft"] and not row["prerelease"] and row.get("published_at")
    ]
    return not published or now - max(published) >= COOLDOWN


def publish(repo: str, tag: str, rows: list[dict[str, Any]], *, apply: bool) -> None:
    if any(row["tag_name"] == tag for row in rows):
        print(f"Release {tag} already exists")
        return
    if not release_due(rows, datetime.now(UTC)):
        print("Release skipped: the previous publication is less than seven days old")
        return
    target = tag_commit(tag)
    ancestors = set(stable_tags(target))
    previous = sorted(
        Version.parse(row["tag_name"])
        for row in rows
        if not row["draft"]
        and not row["prerelease"]
        and SEMVER.fullmatch(row["tag_name"])
        and Version.parse(row["tag_name"]) in ancestors
    )
    if previous and previous[-1] >= Version.parse(tag):
        print("No newer version to publish")
        return
    revision_range = f"{previous[-1]}..{target}" if previous else target
    entries = git("log", "--format=%H%x09%s", revision_range).splitlines()
    body = "Changes since the previous published release:\n\n" + "\n".join(
        f"- {subject} ([{sha[:7]}](https://github.com/{repo}/commit/{sha}))"
        for sha, subject in (entry.split("\t", 1) for entry in entries)
    )
    if previous:
        body += f"\n\n[Full comparison](https://github.com/{repo}/compare/{previous[-1]}...{tag})"
    print(f"Publish weekly release {tag}")
    if apply:
        api(
            repo,
            "releases",
            {
                "tag_name": tag,
                "target_commitish": target,
                "name": tag,
                "body": body,
                "make_latest": "true",
            },
        )


def backfill(repo: str, target: str, *, apply: bool) -> None:
    history = json.loads(
        ROOT.joinpath(".github", "version-history.json").read_text(encoding="utf-8")
    )
    existing = set(git("tag", "--list").splitlines())
    seen: set[str] = set()
    # Validate the entire plan before the first remote write. Tags are immutable.
    for entry in history:
        tag = entry["tag"]
        Version.parse(tag)
        if tag in seen:
            raise ValueError(f"Duplicate historical tag {tag}")
        seen.add(tag)
        sha = git("rev-parse", "--verify", f"{entry['commit']}^{{commit}}")
        git("merge-base", "--is-ancestor", sha, target)
        entry["sha"] = sha
        if tag in existing and tag_commit(tag) != sha:
            raise ValueError(f"Refusing to move historical tag {tag}")
    rows = releases(repo)
    published = {row["tag_name"] for row in rows}
    selected = [entry for entry in history if entry.get("release")]
    if len(selected) > 6:
        raise ValueError("Historical backfill is limited to six consolidated releases")
    newest = max(
        (
            Version.parse(row["tag_name"])
            for row in rows
            if not row["draft"]
            and not row["prerelease"]
            and SEMVER.fullmatch(row["tag_name"])
        ),
        default=Version(0, 0, 0),
    )
    for entry in history:
        if entry["tag"] not in existing:
            create_tag(repo, entry["tag"], entry["sha"], apply=apply)
    for entry in selected:
        tag = entry["tag"]
        if tag in published:
            continue
        major = Version.parse(tag).major
        milestones = [
            row for row in history if Version.parse(row["tag"]).major == major
        ]
        body = "Retrospective source milestones, reconstructed from repository history.\n\n"
        body += "\n".join(
            f"- [{row['tag']}](https://github.com/{repo}/tree/{row['tag']}): {row['title']}"
            for row in milestones
        )
        body += "\n\nThese tags record source history; they do not attest to a hosted deployment."
        print(f"Publish historical release {tag}")
        if apply:
            api(
                repo,
                "releases",
                {
                    "tag_name": tag,
                    "target_commitish": entry["sha"],
                    "name": f"{tag} — {entry['title']}",
                    "body": body,
                    "make_latest": "true"
                    if entry is selected[-1] and Version.parse(tag) > newest
                    else "false",
                },
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backfill", "tag", "publish"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument(
        "--apply", action="store_true", help="Write tags/releases; otherwise preview"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        parser.error("--repo must be OWNER/REPO")
    git("fetch", "--tags", "origin", "master")
    target = api(args.repo, "branches/master")["commit"]["sha"]
    if git("rev-parse", "origin/master") != target:
        raise ValueError("master changed during fetch; retry on its current commit")
    if args.expected_sha and args.expected_sha != target:
        print("Skipping superseded CI run: master has advanced")
        return
    if args.command == "backfill":
        backfill(args.repo, target, apply=args.apply)
        return
    require_ci(args.repo, target)
    tag = ensure_version(args.repo, target, apply=args.apply)
    if args.command == "publish":
        if not args.apply and tag not in git("tag", "--list").splitlines():
            print(f"Would consider {tag} for weekly publication after creating its tag")
            return
        publish(args.repo, tag, releases(args.repo), apply=args.apply)


if __name__ == "__main__":
    main()
