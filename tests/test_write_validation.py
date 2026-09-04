"""Independent verification that a rewritten container is still a valid file.

The point of this file is that none of it is our own code agreeing with
itself. A rewrite verified only by the reader that produced it proves the
round trip, not the file.

Four readers now look at the result:
  1. our own containers.read_sections (the M comes back)
  2. zipfile CRC + XML well-formedness of every part (structural)
  3. openpyxl - an independent Python implementation
  4. python-calamine - an independent *Rust* implementation, sharing no code
     with openpyxl either

Excel itself is still not among them, which is why every --write leaves a
.bak. These are the strongest available substitute, not a replacement.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from pqtools.cli import main


def _container(tmp_path: Path) -> Path:
    """A workbook that is BOTH a real .xlsx and carries Power Query.

    The synthetic fixture in test_containers.py holds only a DataMashup part,
    which is right for testing the DataMashup reader and useless here: neither
    openpyxl nor calamine can open it, so "an independent reader agrees" would
    be vacuously true. This builds a genuine workbook with openpyxl first,
    then injects the customXml part exactly as Excel does, UTF-16 with a BOM.
    """
    import base64
    import codecs
    import io
    import shutil
    import zipfile

    openpyxl = pytest.importorskip("openpyxl")
    from test_containers import _blob

    plain = tmp_path / "plain.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    for row in (["Region", "Amount"], ["North", 10], ["South", 20]):
        sheet.append(row)
    book.save(plain)

    encoded = base64.b64encode(_blob()).decode("ascii")
    xml = (
        '<?xml version="1.0" encoding="utf-16"?>'
        f'<DataMashup xmlns="x">{encoded}</DataMashup>'
    )
    part = codecs.BOM_UTF16_LE + xml.encode("utf-16-le")

    path = tmp_path / "book.xlsx"
    buffer = io.BytesIO()
    with zipfile.ZipFile(plain) as source, zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            out.writestr(info, source.read(info.filename))
        out.writestr("customXml/item1.xml", part)
    path.write_bytes(buffer.getvalue())
    shutil.copyfile(path, tmp_path / "pristine.xlsx")
    return path


def _rewrite(tmp_path: Path) -> tuple[Path, Path]:
    path = _container(tmp_path)
    assert (
        main(
            [
                "add",
                str(path),
                "--name",
                "Added",
                "--source",
                'let S = #table({"a"},{{1}}) in S',
                "--write",
            ]
        )
        == 0
    )
    return path, path.with_suffix(".xlsx.bak")


def test_every_zip_member_survives_its_crc(tmp_path: Path) -> None:
    path, _ = _rewrite(tmp_path)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None


def test_every_xml_part_is_still_well_formed(tmp_path: Path) -> None:
    # A rebuild that corrupts one part while keeping the zip valid is exactly
    # the failure that would surface first in Excel and nowhere else.
    path, _ = _rewrite(tmp_path)
    checked = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.filename.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(info.filename))
                checked += 1
    assert checked > 0, "no XML parts found - the fixture is not a real container"


def test_the_added_query_reads_back(tmp_path: Path) -> None:
    from pqtools import containers

    path, _ = _rewrite(tmp_path)
    sections = containers.read_sections(path)
    assert "Added" in containers.split_shared(sections[0].source, sections[0].container)


def test_openpyxl_opens_the_rewritten_workbook(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path, _ = _rewrite(tmp_path)
    # Compared against the pristine copy rather than the .bak: openpyxl
    # dispatches on the file extension, so it refuses a .bak on sight even
    # though the bytes are a valid workbook.
    before = openpyxl.load_workbook(tmp_path / "pristine.xlsx")
    after = openpyxl.load_workbook(path)
    assert before.sheetnames == after.sheetnames
    for name in before.sheetnames:
        assert list(before[name].iter_rows(values_only=True)) == list(
            after[name].iter_rows(values_only=True)
        )


def test_a_rust_reader_opens_the_rewritten_workbook(tmp_path: Path) -> None:
    # python-calamine shares no code with openpyxl or with us. If all three
    # agree, the agreement is about the file rather than about one parser's
    # tolerance for a malformed one.
    calamine = pytest.importorskip("python_calamine")
    path, _ = _rewrite(tmp_path)
    before = calamine.CalamineWorkbook.from_path(str(tmp_path / "pristine.xlsx"))
    after = calamine.CalamineWorkbook.from_path(str(path))
    assert before.sheet_names == after.sheet_names
    for name in before.sheet_names:
        assert (
            before.get_sheet_by_name(name).to_python()
            == after.get_sheet_by_name(name).to_python()
        )


def test_the_backup_is_byte_identical_to_the_original(tmp_path: Path) -> None:
    # The .bak is the whole recovery story, so it has to be exact.
    path = _container(tmp_path)
    original = path.read_bytes()
    main(
        [
            "add",
            str(path),
            "--name",
            "Added",
            "--source",
            "let S = 1 in S",
            "--write",
        ]
    )
    assert path.with_suffix(".xlsx.bak").read_bytes() == original
