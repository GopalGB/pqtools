"""Native review-tree materialization helper tests.

The existing review wrapper must materialize a target commit into a BASE checkout
without changing HEAD so that `git diff --cached` remains BASE..TARGET.

This file reproduces the stale-file and case-only-rename failure shapes on
applicable filesystems and validates the new helper contract with real git
content comparisons.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.test_end_to_end import _git_env

REPO_ROOT = Path(__file__).resolve().parent.parent
MATERIALIZE = REPO_ROOT / "scripts" / "materialize_review_tree.sh"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**_git_env(), "PATH": os.environ.get("PATH", "")},
        check=False,
    )


def _repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    assert _git(["init", "-q", "."], path).returncode == 0
    assert _git(["config", "user.email", "t@example.invalid"], path).returncode == 0
    assert _git(["config", "user.name", "t"], path).returncode == 0


def _materialize(path: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(MATERIALIZE), target],
        cwd=path,
        capture_output=True,
        text=True,
        env={**_git_env(), "PATH": os.environ.get("PATH", "")},
        check=False,
    )


def _read(path: Path, rel: str) -> str:
    return (path / rel).read_text(encoding="utf-8")


def _is_case_sensitive(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / "CaseProbe.txt"
    probe.write_text("x", encoding="utf-8")
    try:
        return not (path / "caseprobe.txt").exists()
    finally:
        probe.unlink()


def _fixture(repo: Path, *files: str) -> None:
    for rel in files:
        file = repo / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{rel}\n", encoding="utf-8")
        _git(["add", str(rel)], repo)
    assert _git(["commit", "-qm", "fixture"], repo).returncode == 0


def test_materialize_populates_target_tree_in_disposable_review_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "basic"
    _repo(repo)
    _fixture(
        repo,
        "src/root.txt",
    )
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    (repo / "src" / "root.txt").write_text("target\n", encoding="utf-8")
    (repo / "src" / "extra.txt").write_text("added\n", encoding="utf-8")
    assert _git(["add", "src/root.txt", "src/extra.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    expected = _git(["diff", "--name-status", "--no-renames", base, target], repo).stdout
    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(["rev-parse", "HEAD"], repo).stdout.strip() == base
    assert _git(["diff", "--name-status", "--no-renames", "--cached"], repo).stdout == expected
    assert _read(repo, "src/root.txt") == "target\n"
    assert _read(repo, "src/extra.txt") == "added\n"


def test_materialize_handles_deleted_paths(tmp_path: Path) -> None:
    repo = tmp_path / "delete"
    _repo(repo)
    _fixture(repo, "a.txt", "b.txt")
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    assert _git(["rm", "b.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "b.txt").exists()
    assert (repo / "a.txt").exists()
    assert _git(["diff", "--name-status", "--no-renames", "--cached"], repo).stdout == _git(
        ["diff", "--name-status", "--no-renames", base, target],
        repo,
    ).stdout


def test_materialize_replays_file_renames(tmp_path: Path) -> None:
    repo = tmp_path / "rename"
    _repo(repo)
    _fixture(repo, "old.txt")
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    assert _git(["mv", "old.txt", "new.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "old.txt").exists()
    assert _read(repo, "new.txt") == "old.txt\n"


def test_materialize_handles_nested_and_non_ascii_paths(tmp_path: Path) -> None:
    repo = tmp_path / "nested"
    _repo(repo)
    _fixture(repo, "root.txt", "nested/path.txt")
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    (repo / "nested" / "nested.txt").write_text("nested\n", encoding="utf-8")
    (repo / "Éclair.txt").write_text("accented\n", encoding="utf-8")
    assert _git(["add", "nested/nested.txt", "Éclair.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _read(repo, "nested/nested.txt") == "nested\n"
    assert _read(repo, "Éclair.txt") == "accented\n"
    assert (repo / "root.txt").exists()


def test_materialize_refuses_missing_target_commit(tmp_path: Path) -> None:
    repo = tmp_path / "bad"
    _repo(repo)
    _fixture(repo, "a.txt")
    result = _materialize(repo, "0000000000000000000000000000000000000000")
    assert result.returncode != 0
    assert "not found" in result.stdout + result.stderr


def test_materialize_replaces_content_without_changing_path(tmp_path: Path) -> None:
    repo = tmp_path / "content"
    _repo(repo)
    _fixture(repo, "a.txt")
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    (repo / "a.txt").write_text("replacement\n", encoding="utf-8")
    assert _git(["add", "a.txt"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _read(repo, "a.txt") == "replacement\n"
    expected = _git(["diff", "--name-status", "--no-renames", base, target], repo).stdout
    assert _git(["diff", "--cached", "--name-status", "--no-renames"], repo).stdout == expected


def test_materialize_handles_case_only_rename_when_possible(tmp_path: Path) -> None:
    if not _is_case_sensitive(tmp_path / "case_probe"):
        pytest.skip("case-only rename fixture requires a case-sensitive filesystem")

    repo = tmp_path / "case"
    _repo(repo)
    _fixture(repo, "Foo.md")
    base = _git(["rev-parse", "HEAD"], repo).stdout.strip()

    assert _git(["mv", "Foo.md", "foo.md"], repo).returncode == 0
    assert _git(["commit", "-qm", "target"], repo).returncode == 0
    target = _git(["rev-parse", "HEAD"], repo).stdout.strip()
    assert _git(["checkout", "-q", base], repo).returncode == 0

    result = _materialize(repo, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repo / "Foo.md").exists()
    assert _read(repo, "foo.md") == "Foo.md\n"
    cached = _git(["diff", "--cached", "--name-status", "--no-renames"], repo).stdout
    expected = _git(["diff", "--name-status", "--no-renames", base, target], repo).stdout
    assert cached == expected
