"""The container backup is the only copy of the pre-edit state.

`--write` on a container rewrites it in place, so the sidecar backup is the
sole record of what the file was. That made the backup itself worth
protecting, and it was not: `Path.write_bytes` follows a symlink at the
destination and truncates whatever it finds.

Two confirmed defects, both reproduced in an isolated temporary directory
with dummy files before the fix:

1. a `book.xlsx.bak` symlink pointing at an unrelated file overwrote THAT
   file with the workbook's bytes;
2. a second `--write` silently replaced the existing backup, destroying the
   only copy of the original.

No real user file is touched by these tests - every path is under `tmp_path`.
"""

from __future__ import annotations

import base64
import io
import os
import struct
import zipfile
from pathlib import Path

import pytest

from pqtools.cli import main

M_SOURCE = "section Section1;\nshared Q1 = 1;\n"


def _blob(m_text: str = M_SOURCE) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Config/Package.xml", "<Package/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("Formulas/Section1.m", m_text.encode("utf-8"))
    parts = buffer.getvalue()
    out = [struct.pack("<I", 0)]
    for segment in (
        parts,
        b"\xef\xbb\xbf<permissions/>",
        b"\x00\x00\x00\x00\xef\xbb\xbf<metadata/>",
        b"\x01\x02\x03\x04binding",
    ):
        out.append(struct.pack("<I", len(segment)))
        out.append(segment)
    return b"".join(out)


def _workbook(tmp_path: Path, name: str = "book.xlsx") -> Path:
    xml = (
        b'<?xml version="1.0"?><DataMashup xmlns="x">'
        + base64.b64encode(_blob())
        + b"</DataMashup>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("customXml/item1.xml", xml)
    path = tmp_path / name
    path.write_bytes(buffer.getvalue())
    return path


# Both callers that create a backup: `--write` on a transform, and `add`.
_WRITE = pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["format", "{path}", "--write"], id="transform-write"),
        pytest.param(
            ["add", "{path}", "--name", "Q2", "--source", "2", "--write"], id="add"
        ),
    ],
)


def _run(argv: list[str], path: Path) -> int:
    return main([arg.format(path=str(path)) for arg in argv])


# --- defect 1: a symlink at the backup path ------------------------------


@_WRITE
def test_a_backup_symlink_never_overwrites_its_target(
    tmp_path: Path, argv: list[str]
) -> None:
    victim = tmp_path / "unrelated-important.txt"
    victim.write_text("KEEP ME", encoding="utf-8")
    book = _workbook(tmp_path)
    (tmp_path / "book.xlsx.bak").symlink_to(victim)

    assert _run(argv, book) == 0

    # the whole point: the symlink's target is untouched
    assert victim.read_text(encoding="utf-8") == "KEEP ME"
    # and the real backup went somewhere else, holding the workbook
    sidecar = tmp_path / "book.xlsx.bak.1"
    assert sidecar.exists()
    assert sidecar.read_bytes().startswith(b"PK")


@_WRITE
def test_a_dangling_backup_symlink_does_not_create_its_target(
    tmp_path: Path, argv: list[str]
) -> None:
    # O_EXCL fails on a symlink whatever it points at, so a dangling link
    # must not be resolved into a newly created file either.
    target = tmp_path / "never-created.txt"
    book = _workbook(tmp_path)
    (tmp_path / "book.xlsx.bak").symlink_to(target)

    assert _run(argv, book) == 0
    assert not target.exists()


# --- defect 2: an existing backup is the original, and must survive ------


@_WRITE
def test_an_existing_backup_is_preserved_not_replaced(
    tmp_path: Path, argv: list[str]
) -> None:
    book = _workbook(tmp_path)
    original = tmp_path / "book.xlsx.bak"
    original.write_bytes(b"ORIGINAL-PRE-EDIT-STATE")

    assert _run(argv, book) == 0

    assert original.read_bytes() == b"ORIGINAL-PRE-EDIT-STATE"
    assert (tmp_path / "book.xlsx.bak.1").read_bytes().startswith(b"PK")


def test_repeated_edits_keep_every_earlier_backup(tmp_path: Path) -> None:
    book = _workbook(tmp_path)
    for _ in range(3):
        assert main(["format", str(book), "--write"]) == 0
    names = sorted(p.name for p in tmp_path.glob("book.xlsx.bak*"))
    assert names == ["book.xlsx.bak", "book.xlsx.bak.1", "book.xlsx.bak.2"]


# --- a failed backup must leave the container untouched ------------------


@_WRITE
def test_a_failed_backup_leaves_the_container_unmodified(
    tmp_path: Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # A read-only directory is the simplest real backup failure: the sidecar
    # cannot be created, so nothing may be written to the container either.
    # `main` turns the typed error into a non-zero exit and a named message,
    # which is the contract a caller actually sees.
    book = _workbook(tmp_path)
    before = book.read_bytes()
    os.chmod(tmp_path, 0o500)
    try:
        assert _run(argv, book) != 0
    finally:
        os.chmod(tmp_path, 0o700)
    assert "backup" in capsys.readouterr().err
    assert book.read_bytes() == before
    assert not list(tmp_path.glob("book.xlsx.bak*"))


def test_exhausted_backup_names_refuse_rather_than_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pqtools import cli

    monkeypatch.setattr(cli, "_MAX_BACKUPS", 2)
    book = _workbook(tmp_path)
    before = book.read_bytes()
    (tmp_path / "book.xlsx.bak").write_bytes(b"one")
    (tmp_path / "book.xlsx.bak.1").write_bytes(b"two")

    assert main(["format", str(book), "--write"]) != 0
    assert "was not modified" in capsys.readouterr().err

    assert book.read_bytes() == before
    assert (tmp_path / "book.xlsx.bak").read_bytes() == b"one"
    assert (tmp_path / "book.xlsx.bak.1").read_bytes() == b"two"


def test_the_backup_is_a_faithful_copy_and_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _workbook(tmp_path)
    before = book.read_bytes()
    assert main(["format", str(book), "--write"]) == 0
    backup = tmp_path / "book.xlsx.bak"
    assert backup.read_bytes() == before
    # the path actually used is reported, so a sidestepped name is visible
    assert str(backup) in capsys.readouterr().err
