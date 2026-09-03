import base64
import io
import os
import struct
import zipfile
from pathlib import Path

import pytest

from pqtools import containers
from pqtools.containers import ContainerError, QuerySection, read_sections, split_shared
from pqtools.core import MAX_BYTES, SafeWriteError

REAL_SAMPLE = (
    Path(__file__).parent.parent / ".samples" / "real-powerbi-fuzzy-matching.pbix"
)

M_SOURCE = "section Section1;\nshared Q1 = 1;\n"


def _inner_zip(m_text: str | None, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Config/Package.xml", "<Package/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
        if m_text is not None:
            archive.writestr(
                "Formulas/Section1.m",
                m_text.encode("utf-8") if isinstance(m_text, str) else m_text,
            )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _blob(
    m_text: str | None = M_SOURCE,
    *,
    version: int = 0,
    permissions: bytes = b"\xef\xbb\xbf<permissions/>",
    metadata: bytes = b"\x00\x00\x00\x00\xef\xbb\xbf<metadata/>",
    permission_bindings: bytes = b"\x01\x02\x03\x04binding",
    package_parts: bytes | None = None,
) -> bytes:
    parts = package_parts if package_parts is not None else _inner_zip(m_text)
    out = [struct.pack("<I", version)]
    for segment in (parts, permissions, metadata, permission_bindings):
        out.append(struct.pack("<I", len(segment)))
        out.append(segment)
    return b"".join(out)


def _pbix(blob: bytes, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Version", "1.0")
        archive.writestr("DataMashup", blob)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _xlsx(blob: bytes, extra: dict[str, bytes] | None = None) -> bytes:
    encoded = base64.b64encode(blob)
    xml = b'<?xml version="1.0"?><DataMashup xmlns="x">' + encoded + b"</DataMashup>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("customXml/item1.xml", xml)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# read_sections - both container shapes
# --------------------------------------------------------------------------


def test_read_sections_finds_pbix_direct_shape(tmp_path: Path):
    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob()))
    sections = read_sections(path)
    assert sections == [
        QuerySection("Formulas/Section1.m", M_SOURCE, str(path), "pbix")
    ]


def test_read_sections_finds_xlsx_xml_shape(tmp_path: Path):
    path = tmp_path / "report.xlsx"
    path.write_bytes(_xlsx(_blob()))
    sections = read_sections(path)
    assert sections == [
        QuerySection("Formulas/Section1.m", M_SOURCE, str(path), "xlsx")
    ]


def test_read_sections_pbit_uses_pbit_kind(tmp_path: Path):
    path = tmp_path / "template.pbit"
    path.write_bytes(_pbix(_blob()))
    sections = read_sections(path)
    assert sections[0].kind == "pbit"


# --------------------------------------------------------------------------
# read_sections - every failure path, each with a distinguishable message
# --------------------------------------------------------------------------


def test_read_sections_refuses_a_file_that_is_not_a_zip(tmp_path: Path):
    path = tmp_path / "report.pbix"
    path.write_bytes(b"not a zip archive at all")
    with pytest.raises(ContainerError, match="not a zip archive"):
        read_sections(path)


def test_read_sections_refuses_a_plain_zip_with_no_datamashup(tmp_path: Path):
    path = tmp_path / "report.pbix"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "no mashup here")
    path.write_bytes(buffer.getvalue())
    with pytest.raises(ContainerError, match="no DataMashup part"):
        read_sections(path)


def test_read_sections_refuses_a_blob_whose_length_exceeds_the_payload(tmp_path: Path):
    path = tmp_path / "report.pbix"
    good = _blob()
    # Corrupt the first segment's declared length so it claims more bytes
    # than the blob actually has.
    inflated = struct.pack("<I", 0) + struct.pack("<I", 999_999) + good[8:]
    path.write_bytes(_pbix(inflated))
    with pytest.raises(ContainerError, match="declared length exceeds payload"):
        read_sections(path)


def test_read_sections_refuses_a_blob_with_trailing_bytes(tmp_path: Path):
    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob() + b"trailing garbage"))
    with pytest.raises(ContainerError, match="trailing bytes"):
        read_sections(path)


def test_read_sections_refuses_when_inner_zip_is_unreadable(tmp_path: Path):
    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob(package_parts=b"not a zip")))
    with pytest.raises(ContainerError, match="inner zip is unreadable"):
        read_sections(path)


def test_read_sections_refuses_when_section1_m_is_absent(tmp_path: Path):
    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob(m_text=None)))
    with pytest.raises(ContainerError, match="no Formulas/Section1.m"):
        read_sections(path)


def test_read_sections_refuses_non_utf8_section1_m(tmp_path: Path):
    path = tmp_path / "report.pbix"
    parts = _inner_zip(None, extra={"Formulas/Section1.m": b"\xff\xfe\x00bad"})
    path.write_bytes(_pbix(_blob(package_parts=parts)))
    with pytest.raises(ContainerError, match="not valid UTF-8"):
        read_sections(path)


def test_read_sections_unsupported_suffix(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(ContainerError, match="unsupported container suffix"):
        read_sections(path)


# --------------------------------------------------------------------------
# safety model reuse: MAX_BYTES and symlinks (core._snapshot for free)
# --------------------------------------------------------------------------


def test_read_sections_refuses_a_container_over_max_bytes(tmp_path: Path):
    # core._snapshot enforces the cap; its own error type surfaces
    # unchanged, since read_sections reuses it "for free" rather than
    # re-implementing the size guard.
    path = tmp_path / "big.pbix"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("filler", b"x" * (MAX_BYTES + 1))
    path.write_bytes(buffer.getvalue())
    with pytest.raises(SafeWriteError, match="10 MiB"):
        read_sections(path)


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink semantics; Windows needs Developer Mode"
)
def test_read_sections_refuses_a_symlink(tmp_path: Path):
    # Same reasoning: this is core._snapshot's guard, reused unchanged.
    real = tmp_path / "report.pbix"
    real.write_bytes(_pbix(_blob()))
    link = tmp_path / "link.pbix"
    link.symlink_to(real)
    with pytest.raises(SafeWriteError, match="non-symlink"):
        read_sections(link)


# --------------------------------------------------------------------------
# split_shared
# --------------------------------------------------------------------------


def test_split_shared_slices_exact_member_source():
    source = "section Section1; shared A = let x = 1 in x; shared B = 2;"
    members = split_shared(source)
    assert members == {
        "A": "shared A = let x = 1 in x;",
        "B": "shared B = 2;",
    }


def test_split_shared_ignores_private_members_and_handles_quoted_names():
    source = 'section Section1;\nPrivate = 1;\nshared #"A B" = 2;\n'
    members = split_shared(source)
    assert members == {'#"A B"': 'shared #"A B" = 2;'}


def test_split_shared_raises_container_error_on_unparseable_source():
    with pytest.raises(ContainerError, match="report.pbix"):
        split_shared("let =", container="report.pbix")


# --------------------------------------------------------------------------
# write_sections - round trip, dry run, corruption detection
# --------------------------------------------------------------------------


def test_write_sections_dry_run_touches_nothing(tmp_path: Path):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    diff = containers.write_sections(path, "section Section1;\nshared Q1 = 2;\n")
    assert "-shared Q1 = 1;" in diff
    assert "+shared Q1 = 2;" in diff
    assert path.read_bytes() == original


def test_write_sections_round_trip_replaces_only_the_m(tmp_path: Path):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob(), extra={"Report/Layout": b"untouched layout bytes"})
    path.write_bytes(original)
    new_source = "section Section1;\nshared Q1 = 99;\n"

    diff = containers.write_sections(path, new_source, write=True)
    assert diff

    sections = read_sections(path)
    assert sections[0].source == new_source

    archive = zipfile.ZipFile(path)
    assert archive.read("Report/Layout") == b"untouched layout bytes"


def test_write_sections_xlsx_shape_round_trip(tmp_path: Path):
    path = tmp_path / "report.xlsx"
    path.write_bytes(_xlsx(_blob(), extra={"xl/sheet1.xml": b"<sheet/>"}))
    new_source = "section Section1;\nshared Q1 = 7;\n"
    containers.write_sections(path, new_source, write=True)
    sections = read_sections(path)
    assert sections[0].source == new_source
    archive = zipfile.ZipFile(path)
    assert archive.read("xl/sheet1.xml") == b"<sheet/>"


def test_write_sections_noop_reproduces_the_file_byte_for_byte(tmp_path: Path):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    sections = read_sections(path)
    diff = containers.write_sections(path, sections[0].source, write=True)
    assert diff == ""
    assert path.read_bytes() == original


def test_write_sections_refuses_replacement_that_does_not_parse(tmp_path: Path):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    with pytest.raises(ContainerError, match="does not parse"):
        containers.write_sections(path, "let =", write=True)
    assert path.read_bytes() == original


def test_write_sections_refuses_unsupported_container(tmp_path: Path):
    path = tmp_path / "Model.pbip"
    path.write_text("{}")
    with pytest.raises(ContainerError, match="write is only supported"):
        containers.write_sections(path, M_SOURCE, write=True)


def test_write_sections_corruption_is_caught_and_leaves_the_file_untouched(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob(), extra={"Report/Layout": b"stays put"})
    path.write_bytes(original)

    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        buffer = io.BytesIO(rebuilt)
        with zipfile.ZipFile(buffer, "a") as archive:
            archive.writestr("Injected", b"corruption")
        return buffer.getvalue()

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match="member list changed"):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_write_sections_catches_a_corrupted_segment(tmp_path: Path, monkeypatch):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)

    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        located_after = containers._locate(rebuilt, name)
        tampered = containers.MashupBlob(
            located_after.blob.version,
            located_after.blob.package_parts,
            b"tampered permissions",
            located_after.blob.metadata,
            located_after.blob.permission_bindings,
        )
        tampered_blob = containers._pack_blob(tampered)
        return containers._replace_zip_member(
            rebuilt, "DataMashup", tampered_blob, name
        )

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match="permissions segment"):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def _tamper_blob(rebuilt: bytes, name: str, **overrides) -> bytes:
    located = containers._locate(rebuilt, name)
    fields = {
        "version": located.blob.version,
        "package_parts": located.blob.package_parts,
        "permissions": located.blob.permissions,
        "metadata": located.blob.metadata,
        "permission_bindings": located.blob.permission_bindings,
    }
    fields.update(overrides)
    tampered_blob = containers._pack_blob(containers.MashupBlob(**fields))
    return containers._replace_zip_member(rebuilt, "DataMashup", tampered_blob, name)


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"metadata": b"tampered metadata"}, "metadata segment"),
        ({"permission_bindings": b"tampered bindings"}, "permissionBindings segment"),
        ({"version": 7}, "version field"),
    ],
)
def test_write_sections_catches_every_opaque_segment_corruption(
    tmp_path: Path, monkeypatch, override, match
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        return _tamper_blob(rebuilt, name, **override)

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match=match):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_write_sections_catches_an_unrelated_outer_member_content_change(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob(), extra={"Report/Layout": b"original layout"})
    path.write_bytes(original)
    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        return containers._replace_zip_member(
            rebuilt, "Report/Layout", b"corrupted layout", name
        )

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match="changed an unrelated member"):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_write_sections_catches_an_unrelated_inner_member_content_change(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        located_after = containers._locate(rebuilt, name)
        tampered_inner = containers._replace_zip_member(
            located_after.blob.package_parts,
            "Config/Package.xml",
            b"<Corrupted/>",
            name,
        )
        return _tamper_blob(rebuilt, name, package_parts=tampered_inner)

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match="package parts changed an unrelated"):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_write_sections_catches_m_that_does_not_read_back_exactly(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        # Silently write different M than what was asked for.
        return real_rebuild(data, located, new_source + "\n// oops\n", name)

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(ContainerError, match="does not read back exactly"):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_write_sections_catches_m_that_no_longer_parses_after_rebuild(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "report.pbix"
    original = _pbix(_blob())
    path.write_bytes(original)
    real_rebuild = containers._rebuild

    def corrupting_rebuild(data, located, new_source, name):
        rebuilt = real_rebuild(data, located, new_source, name)
        located_after = containers._locate(rebuilt, name)
        tampered_inner = containers._replace_zip_member(
            located_after.blob.package_parts,
            "Formulas/Section1.m",
            b"let =",
            name,
        )
        blob = _tamper_blob(rebuilt, name, package_parts=tampered_inner)
        return blob

    monkeypatch.setattr(containers, "_rebuild", corrupting_rebuild)
    with pytest.raises(
        ContainerError, match="does not read back exactly|does not parse"
    ):
        containers.write_sections(
            path, "section Section1;\nshared Q1 = 2;\n", write=True
        )
    assert path.read_bytes() == original


def test_read_sections_xml_shape_with_invalid_base64_is_a_container_error(
    tmp_path: Path,
):
    path = tmp_path / "report.xlsx"
    xml = b'<?xml version="1.0"?><DataMashup xmlns="x">not-base64!!!</DataMashup>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("customXml/item1.xml", xml)
    path.write_bytes(buffer.getvalue())
    with pytest.raises(ContainerError, match="not valid base64|no DataMashup part"):
        read_sections(path)


def test_replace_zip_member_refuses_a_non_zip_archive():
    with pytest.raises(ContainerError, match="not a zip archive"):
        containers._replace_zip_member(b"garbage", "x", b"y", "name")


def test_replace_zip_member_refuses_a_missing_member(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("only.txt", "hi")
    with pytest.raises(ContainerError, match="zip member not found"):
        containers._replace_zip_member(buffer.getvalue(), "missing", b"data", "name")


# --------------------------------------------------------------------------
# .pbip / TMDL walking
# --------------------------------------------------------------------------


def test_read_sections_pbip_finds_pq_files_and_fenced_tmdl_source(tmp_path: Path):
    project = tmp_path / "Proj.pbip"
    project.write_text("{}")
    model_dir = tmp_path / "Proj.SemanticModel" / "definition" / "tables"
    model_dir.mkdir(parents=True)
    tmdl = model_dir / "Sales.tmdl"
    tmdl.write_text(
        "table Sales\n"
        "\tpartition Sales = m\n"
        "\t\tmode: import\n"
        "\t\tsource =\n"
        "\t\t\t```\n"
        "\t\t\tlet\n"
        "\t\t\t    Source = 1\n"
        "\t\t\tin\n"
        "\t\t\t    Source\n"
        "\t\t\t```\n"
    )
    loose = tmp_path / "Proj.SemanticModel" / "extra.pq"
    loose.write_text("let A = 1 in A")

    sections = read_sections(project)
    by_path = {section.path: section for section in sections}
    assert "let A = 1 in A" in by_path[str(loose.relative_to(tmp_path))].source
    tmdl_section = by_path[str(tmdl.relative_to(tmp_path))]
    assert "Source = 1" in tmdl_section.source
    assert all(section.kind == "pbip" for section in sections)


def test_read_sections_pbip_skips_ambiguous_tmdl_file(tmp_path: Path):
    model_dir = tmp_path / "Proj.SemanticModel" / "definition" / "tables"
    model_dir.mkdir(parents=True)
    tmdl = model_dir / "TwoPartitions.tmdl"
    tmdl.write_text(
        "table T\n"
        "\tpartition A = m\n"
        "\t\tsource =\n\t\t\t```\n\t\t\tlet X = 1 in X\n\t\t\t```\n"
        "\tpartition B = m\n"
        "\t\tsource =\n\t\t\t```\n\t\t\tlet Y = 2 in Y\n\t\t\t```\n"
    )
    sections = read_sections(tmp_path)
    assert sections == []


def test_read_sections_pbip_directory_with_nothing_found_returns_empty(tmp_path: Path):
    (tmp_path / "readme.txt").write_text("nothing here")
    assert read_sections(tmp_path) == []


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_check_reports_container_prefixed_diagnostics(tmp_path: Path, capsys):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(
        _pbix(_blob(m_text='let A = Web.Contents(Url), Password = "x" in A'))
    )
    assert main(["check", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"{path}!Formulas/Section1.m:" in out
    assert "M002" in out and "M003" in out


def test_cli_check_exit_code_2_on_error_diagnostic(tmp_path: Path):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob(m_text="let =")))
    assert main(["check", str(path)]) == 2


def test_cli_format_on_container_refuses_with_container_error(tmp_path: Path, capsys):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob()))
    assert main(["format", str(path)]) == 2
    err = capsys.readouterr().err
    assert "M_CONTAINER_ERROR" in err
    assert "not enabled" in err
    assert path.read_bytes() == _pbix(_blob())


def test_cli_rename_and_replace_source_on_container_refuse(tmp_path: Path):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob()))
    assert main(["rename", str(path), "--old", "Q1", "--new", "Q2"]) == 2
    assert main(["replace-source", str(path), "--source", "let A = 1 in A"]) == 2


def test_cli_dependencies_on_container(tmp_path: Path, capsys):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob(m_text="let A = Number.From(1) in A")))
    assert main(["dependencies", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Number.From" in out


def test_cli_parse_on_container(tmp_path: Path, capsys):
    from pqtools.cli import main

    path = tmp_path / "report.pbix"
    path.write_bytes(_pbix(_blob()))
    assert main(["parse", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Section" in out


# --------------------------------------------------------------------------
# Real Power BI Desktop sample (git-ignored; skipped when absent)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_SAMPLE.exists(), reason="real sample not present")
def test_read_sections_real_pbix_sample(tmp_path: Path):
    sections = read_sections(REAL_SAMPLE)
    assert len(sections) == 1
    assert sections[0].source.startswith("section Section1;")
    members = split_shared(sections[0].source)
    assert set(members) == {"People", "Sales"}


@pytest.mark.skipif(not REAL_SAMPLE.exists(), reason="real sample not present")
def test_write_sections_real_pbix_sample_noop_round_trip_is_byte_identical(
    tmp_path: Path,
):
    copy = tmp_path / "sample.pbix"
    copy.write_bytes(REAL_SAMPLE.read_bytes())
    original_bytes = copy.read_bytes()

    sections = read_sections(copy)
    diff = containers.write_sections(copy, sections[0].source, write=True)
    assert diff == ""
    assert copy.read_bytes() == original_bytes


@pytest.mark.skipif(not REAL_SAMPLE.exists(), reason="real sample not present")
def test_write_sections_real_pbix_sample_preserves_opaque_segments_on_change(
    tmp_path: Path,
):
    copy = tmp_path / "sample.pbix"
    copy.write_bytes(REAL_SAMPLE.read_bytes())

    before = containers._locate(REAL_SAMPLE.read_bytes(), "sample.pbix")
    new_source = before.m_text + "\n// touched by pqtools\n"
    containers.write_sections(copy, new_source, write=True)

    after = containers._locate(copy.read_bytes(), "sample.pbix")
    assert after.m_text == new_source
    assert after.blob.permissions == before.blob.permissions
    assert after.blob.metadata == before.blob.metadata
    assert after.blob.permission_bindings == before.blob.permission_bindings
    assert after.blob.version == before.blob.version

    original_zip = zipfile.ZipFile(REAL_SAMPLE)
    new_zip = zipfile.ZipFile(copy)
    assert original_zip.namelist() == new_zip.namelist()
    for name in original_zip.namelist():
        if name == "DataMashup":
            continue
        assert original_zip.read(name) == new_zip.read(name), name
