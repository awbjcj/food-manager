"""Run workflow shell gates against local repositories and fake validation tools."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def bash_path(path):
    value = path.as_posix()
    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value


@pytest.fixture
def bash():
    if os.name == "nt":
        git = shutil.which("git")
        candidates = [Path(git).resolve().parents[1] / "bin/bash.exe"] if git else []
    else:
        candidates = [Path(shutil.which("bash") or "/missing-bash")]
    executable = next((str(path) for path in candidates if path.is_file()), None)
    if executable is None:
        pytest.skip("Bash is needed to execute GitHub Actions shell gates")
    return executable


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("branch", "repository", "allowed"), [
    ("dev", "awbjcj/food-manager", True),
    ("hotfix/repair", "awbjcj/food-manager", True),
    ("feature/new", "awbjcj/food-manager", False),
    ("dev", "fork/food-manager", False),
    ("hotfix/repair", "fork/food-manager", False),
    ("dev", "", False),
])
def test_merge_source_policy_binds_the_branch_to_its_repository(bash, branch, repository, allowed):
    step = workflow("guard-main-merge.yml")["jobs"]["check-source-branch"]["steps"][0]
    result = subprocess.run([bash, "-c", step["run"]], capture_output=True, text=True, check=False,
        env={**os.environ, "HEAD_REF": branch, "HEAD_REPOSITORY": repository,
             "BASE_REPOSITORY": "awbjcj/food-manager"})
    assert (result.returncode == 0) == allowed, result.stdout + result.stderr


@pytest.mark.parametrize("validation_succeeds", [False, True])
def test_rebased_dev_is_published_only_after_validation_passes(bash, tmp_path, validation_succeeds):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "checkout"

    def git(*args, cwd=repo):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--initial-branch=master", str(repo)], check=True, capture_output=True)
    git("config", "user.name", "Workflow Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", str(tmp_path / "no-hooks"))
    (repo / "base").write_text("base")
    git("add", ".")
    git("commit", "-m", "base")
    git("remote", "add", "origin", remote.as_posix())
    git("push", "origin", "master")
    git("switch", "-c", "dev")
    (repo / "feature").write_text("new dev work")
    git("add", ".")
    git("commit", "-m", "dev feature")
    git("push", "origin", "dev")
    before = git("rev-parse", "HEAD")
    git("switch", "master")
    (repo / "hotfix").write_text("master hotfix")
    git("add", ".")
    git("commit", "-m", "hotfix")
    git("push", "origin", "master")
    git("switch", "dev")

    steps = workflow("sync-dev-with-master.yml")["jobs"]["sync"]["steps"]
    rebase = next(s for s in steps if s.get("id") == "rebase")
    validation = next(s for s in steps if s.get("name") == "Validate the rebased tree")
    publish = next(s for s in steps if s.get("name") == "Publish validated dev")
    assert steps.index(rebase) < steps.index(validation) < steps.index(publish)
    assert "always()" not in publish["if"] and "continue-on-error" not in validation
    output = tmp_path / "outputs"
    environment = {**os.environ, "GITHUB_OUTPUT": bash_path(output)}
    subprocess.run([bash, "-c", rebase["run"]], cwd=repo, env=environment,
                   check=True, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert outputs["changed"] == "true"
    assert (repo / "feature").exists() and (repo / "hotfix").exists()
    assert git("rev-parse", "refs/heads/dev", cwd=remote) == before

    stubs = tmp_path / "tools"
    stubs.mkdir()
    uv = stubs / "uv"
    uv.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$VALIDATION_LOG"\n'
                  'if [ "$*" = "run pytest -q" ] && [ "$FAIL_VALIDATION" = "1" ]; then exit 1; fi\n')
    uv.chmod(0o755)
    log = tmp_path / "validation.log"
    environment.update(VALIDATION_LOG=bash_path(log),
                       FAIL_VALIDATION="0" if validation_succeeds else "1")
    result = subprocess.run([bash, "-c", f"export PATH={shlex.quote(bash_path(stubs))}:$PATH\n" + validation["run"]],
                            cwd=repo, env=environment, capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == validation_succeeds, result.stderr
    assert log.read_text().splitlines() == [
        "sync --all-groups", "run ruff check app tests bin migrations", "run pytest -q",
    ]
    if result.returncode == 0:
        environment.update(BEFORE=outputs["before"], AFTER=outputs["after"])
        subprocess.run([bash, "-c", publish["run"]], cwd=repo, env=environment,
                       check=True, capture_output=True, text=True)
    assert git("rev-parse", "refs/heads/dev", cwd=remote) == (
        outputs["after"] if validation_succeeds else before
    )
