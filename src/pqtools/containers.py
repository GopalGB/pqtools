"""Read (and, gated, write) Power Query M source inside the real files it
lives in: .xlsx, .pbix, .pbit (OPC zip archives) and .pbip/TMDL projects.

Container shape, verified against a real Power BI Desktop sample
(``.samples/real-powerbi-fuzzy-matching.pbix``, Microsoft's own published
sample - see ``tests/test_containers.py::test_read_sections_real_pbix_sample``,
skipped when the sample is not present):

- .xlsx / .pbix / .pbit are OPC zip archives. The Power Query payload is a
  zip member whose name contains "DataMashup" (a direct part named exactly
  ``DataMashup`` in .pbix/.pbit), or a ``customXml/item*.xml`` member
  wrapping a ``<DataMashup>`` element whose text is base64 of the blob (the
  .xlsx case).
- The blob is NOT a bare version+length+zip pair. It is a 4-byte
  little-endian version, followed by four length-prefixed segments in a
  fixed order: ``packageParts`` (a ZIP containing ``Config/Package.xml``,
  ``[Content_Types].xml`` and ``Formulas/Section1.m``), ``permissions``
  (BOM + XML), ``metadata`` (4 bytes then BOM + XML), and
  ``permissionBindings`` (opaque binary). The segment lengths must consume
  the blob exactly - a short read or trailing bytes means the format
  changed and this module refuses to guess.
- Only ``Formulas/Section1.m`` inside ``packageParts`` is ever interpreted.
  ``permissions``, ``metadata`` and ``permissionBindings`` are opaque and
  are never parsed - only ever carried through byte-for-byte.
"""

from __future__ import annotations

import base64
import binascii
import difflib
import io
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from . import core
from .core import FileSnapshot, MQueryError

_CONTAINER_SUFFIXES = (".xlsx", ".pbix", ".pbit")
_SEGMENT_NAMES = ("packageParts", "permissions", "metadata", "permissionBindings")
_XML_DATAMASHUP = re.compile(
    rb"<(?:[\w.\-]+:)?DataMashup\b[^>]*>(.*?)</(?:[\w.\-]+:)?DataMashup>",
    re.DOTALL,
)
_TMDL_SOURCE = re.compile(
    r"source\s*=\s*\r?\n[ \t]*```\r?\n(.*?)\r?\n[ \t]*```",
    re.DOTALL,
)
_OPENERS = {"LeftParenthesis", "LeftBracket", "LeftBrace"}
_CLOSERS = {"RightParenthesis", "RightBracket", "RightBrace"}


class ContainerError(MQueryError):
    code = "M_CONTAINER_ERROR"


@dataclass(frozen=True)
class QuerySection:
    path: str
    source: str
    container: str
    kind: str


@dataclass(frozen=True)
class MashupBlob:
    """The four opaque-except-packageParts segments of a DataMashup blob."""

    version: int
    package_parts: bytes
    permissions: bytes
    metadata: bytes
    permission_bindings: bytes


@dataclass(frozen=True)
class _Located:
    outer_member: str
    shape: str  # "direct" (a literal DataMashup part) or "xml" (base64 in XML)
    blob: MashupBlob
    m_text: str
    xml_bytes: bytes | None  # the xml member's raw bytes, "xml" shape only
    match_start: int  # base64 text span within xml_bytes, "xml" shape only
    match_end: int


def _parse_blob(blob: bytes, container_name: str) -> MashupBlob:
    if len(blob) < 4:
        raise ContainerError(f"{container_name}: malformed DataMashup blob header")
    (version,) = struct.unpack_from("<I", blob, 0)
    offset = 4
    segments: list[bytes] = []
    for _ in _SEGMENT_NAMES:
        if offset + 4 > len(blob):
            raise ContainerError(f"{container_name}: malformed DataMashup blob header")
        (length,) = struct.unpack_from("<I", blob, offset)
        offset += 4
        segment = blob[offset : offset + length]
        if len(segment) != length:
            raise ContainerError(
                f"{container_name}: DataMashup blob declared length exceeds payload"
            )
        segments.append(segment)
        offset += length
    if offset != len(blob):
        raise ContainerError(
            f"{container_name}: DataMashup blob has unexpected trailing bytes"
        )
    package_parts, permissions, metadata, permission_bindings = segments
    return MashupBlob(
        version, package_parts, permissions, metadata, permission_bindings
    )


def _pack_blob(blob: MashupBlob) -> bytes:
    parts = [struct.pack("<I", blob.version)]
    for segment in (
        blob.package_parts,
        blob.permissions,
        blob.metadata,
        blob.permission_bindings,
    ):
        parts.append(struct.pack("<I", len(segment)))
        parts.append(segment)
    return b"".join(parts)


def _section1_text(package_parts: bytes, container_name: str) -> str:
    try:
        inner = zipfile.ZipFile(io.BytesIO(package_parts))
    except zipfile.BadZipFile as error:
        raise ContainerError(
            f"{container_name}: DataMashup inner zip is unreadable"
        ) from error
    if "Formulas/Section1.m" not in inner.namelist():
        raise ContainerError(
            f"{container_name}: DataMashup inner zip has no Formulas/Section1.m"
        )
    raw = inner.read("Formulas/Section1.m")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ContainerError(
            f"{container_name}: Formulas/Section1.m is not valid UTF-8"
        ) from error


def _locate(data: bytes, container_name: str) -> _Located:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ContainerError(f"{container_name}: not a zip archive") from error
    names = archive.namelist()

    direct_error: ContainerError | None = None
    for name in names:
        if "DataMashup" not in PurePosixPath(name).name:
            continue
        try:
            blob = _parse_blob(archive.read(name), container_name)
            m_text = _section1_text(blob.package_parts, container_name)
        except ContainerError as error:
            direct_error = error
            continue
        return _Located(name, "direct", blob, m_text, None, 0, 0)

    xml_error: ContainerError | None = None
    for name in names:
        if not name.lower().endswith(".xml"):
            continue
        xml_bytes = archive.read(name)
        match = _XML_DATAMASHUP.search(xml_bytes)
        if not match:
            continue
        try:
            raw_blob = base64.b64decode(match.group(1), validate=False)
        except binascii.Error:
            xml_error = ContainerError(
                f"{container_name}: DataMashup element is not valid base64"
            )
            continue
        try:
            blob = _parse_blob(raw_blob, container_name)
            m_text = _section1_text(blob.package_parts, container_name)
        except ContainerError as error:
            xml_error = error
            continue
        return _Located(
            name, "xml", blob, m_text, xml_bytes, match.start(1), match.end(1)
        )

    if direct_error is not None:
        raise direct_error
    if xml_error is not None:
        raise xml_error
    raise ContainerError(f"{container_name}: no DataMashup part found")


def _copy_zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    copy.compress_type = info.compress_type
    copy.external_attr = info.external_attr
    copy.create_system = info.create_system
    copy.comment = info.comment
    copy.extra = info.extra
    return copy


def _replace_zip_member(
    archive: bytes, member: str, new_data: bytes, container_name: str
) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            infos = source.infolist()
            contents = {info.filename: source.read(info) for info in infos}
    except zipfile.BadZipFile as error:
        raise ContainerError(f"{container_name}: not a zip archive") from error
    if member not in contents:
        raise ContainerError(f"{container_name}: zip member not found: {member}")
    contents[member] = new_data
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as destination:
        for info in infos:
            destination.writestr(_copy_zip_info(info), contents[info.filename])
    return buffer.getvalue()


def read_sections(path: Path) -> list[QuerySection]:
    """Find every Power Query M section inside `path`.

    .xlsx/.pbix/.pbit are read as a single-member list (one DataMashup part
    per container). .pbip (or a bare directory laid out the same way) is
    walked for ``*.pq`` files and TMDL ``source = ``` blocks; a file that is
    ambiguous (more than one fenced M block) is skipped rather than guessed
    at - it contributes nothing to the result, it never raises.
    """
    suffix = path.suffix.lower()
    if suffix in _CONTAINER_SUFFIXES:
        snapshot = core._snapshot(path)
        located = _locate(snapshot.data, path.name)
        return [
            QuerySection(
                path="Formulas/Section1.m",
                source=located.m_text,
                container=str(path),
                kind=suffix[1:],
            )
        ]
    if suffix == ".pbip" or path.is_dir():
        return _read_pbip(path)
    raise ContainerError(f"{path.name}: unsupported container suffix {suffix!r}")


def _read_pbip(path: Path) -> list[QuerySection]:
    root = path if path.is_dir() else path.parent
    sections: list[QuerySection] = []
    for file in sorted(root.rglob("*")):
        suffix = file.suffix.lower()
        if suffix not in (".pq", ".tmdl"):
            continue
        try:
            snapshot = core._snapshot(file)
            text = snapshot.data.decode("utf-8", "strict")
        except (OSError, MQueryError, UnicodeDecodeError):
            continue
        try:
            relative = str(file.relative_to(root))
        except ValueError:
            relative = str(file)
        if suffix == ".pq":
            sections.append(QuerySection(relative, text, str(file), "pbip"))
            continue
        matches = _TMDL_SOURCE.findall(text)
        if len(matches) != 1:
            continue  # none, or ambiguous - never invent a section
        sections.append(QuerySection(relative, matches[0], str(file), "pbip"))
    return sections


def split_shared(section_source: str, container: str = "<string>") -> dict[str, str]:
    """Split a section document into its ``shared Name = ...;`` members.

    Uses the pinned parser (`core.parse`), not a regex, so nested braces,
    parens, strings and comments never confuse the split: walks the token
    stream for ``shared`` followed by an identifier, then slices by token
    offsets out to the matching top-level semicolon.
    """
    try:
        parsed = core.parse(section_source)
    except MQueryError as error:
        raise ContainerError(f"{container}: {error.message}") from error
    tokens: list[dict[str, Any]] = parsed["tokens"]
    members: dict[str, str] = {}
    depth = 0
    for index, token in enumerate(tokens):
        kind = token["kind"]
        if kind in _OPENERS:
            depth += 1
        elif kind in _CLOSERS:
            depth -= 1
        elif (
            depth == 0
            and kind == "KeywordShared"
            and index + 1 < len(tokens)
            and tokens[index + 1]["kind"] == "Identifier"
        ):
            start = int(token["start"])
            end = _member_end(tokens, index)
            members[str(tokens[index + 1]["text"])] = section_source[start:end]
    return members


def _member_end(tokens: list[dict[str, Any]], shared_index: int) -> int:
    depth = 0
    for token in tokens[shared_index:]:
        kind = token["kind"]
        if kind in _OPENERS:
            depth += 1
        elif kind in _CLOSERS:
            depth -= 1
        elif kind == "Semicolon" and depth == 0:
            return int(token["end"])
    return int(tokens[-1]["end"])


def _rebuild(
    original: bytes, located: _Located, new_source: str, container_name: str
) -> bytes:
    try:
        new_m_bytes = new_source.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ContainerError(
            f"{container_name}: replacement source must be valid UTF-8"
        ) from error
    if len(new_m_bytes) > core.MAX_BYTES:
        raise ContainerError(f"{container_name}: replacement source exceeds 10 MiB")
    new_package_parts = _replace_zip_member(
        located.blob.package_parts, "Formulas/Section1.m", new_m_bytes, container_name
    )
    new_blob = MashupBlob(
        located.blob.version,
        new_package_parts,
        located.blob.permissions,
        located.blob.metadata,
        located.blob.permission_bindings,
    )
    new_blob_bytes = _pack_blob(new_blob)
    if located.shape == "direct":
        new_outer_bytes = new_blob_bytes
    else:
        assert located.xml_bytes is not None
        new_outer_bytes = (
            located.xml_bytes[: located.match_start]
            + base64.b64encode(new_blob_bytes)
            + located.xml_bytes[located.match_end :]
        )
    return _replace_zip_member(
        original, located.outer_member, new_outer_bytes, container_name
    )


def _verify_rebuild(
    original: bytes, rebuilt: bytes, new_source: str, container_name: str
) -> None:
    """Re-read `rebuilt` from scratch and assert it changed nothing except
    the M source. Raises ContainerError on any mismatch; never trusts the
    rebuild that produced `rebuilt` to have gotten it right."""
    try:
        before_outer = zipfile.ZipFile(io.BytesIO(original))
        after_outer = zipfile.ZipFile(io.BytesIO(rebuilt))
    except zipfile.BadZipFile as error:
        raise ContainerError(
            f"{container_name}: rebuilt container is not a readable zip archive"
        ) from error
    before_names = before_outer.namelist()
    if before_names != after_outer.namelist():
        raise ContainerError(
            f"{container_name}: rebuilt container's member list changed"
        )
    located_before = _locate(original, container_name)
    for name in before_names:
        if name == located_before.outer_member:
            continue
        if before_outer.read(name) != after_outer.read(name):
            raise ContainerError(
                f"{container_name}: rebuilt container changed "
                f"an unrelated member: {name}"
            )
    located_after = _locate(rebuilt, container_name)
    if located_after.blob.version != located_before.blob.version:
        raise ContainerError(
            f"{container_name}: rebuilt blob changed its version field"
        )
    if located_after.blob.permissions != located_before.blob.permissions:
        raise ContainerError(
            f"{container_name}: rebuilt blob changed its permissions segment"
        )
    if located_after.blob.metadata != located_before.blob.metadata:
        raise ContainerError(
            f"{container_name}: rebuilt blob changed its metadata segment"
        )
    if (
        located_after.blob.permission_bindings
        != located_before.blob.permission_bindings
    ):
        raise ContainerError(
            f"{container_name}: rebuilt blob changed its permissionBindings segment"
        )
    before_inner = zipfile.ZipFile(io.BytesIO(located_before.blob.package_parts))
    after_inner = zipfile.ZipFile(io.BytesIO(located_after.blob.package_parts))
    before_inner_names = before_inner.namelist()
    if before_inner_names != after_inner.namelist():
        raise ContainerError(
            f"{container_name}: rebuilt package parts member list changed"
        )
    for name in before_inner_names:
        if name == "Formulas/Section1.m":
            continue
        if before_inner.read(name) != after_inner.read(name):
            raise ContainerError(
                f"{container_name}: rebuilt package parts changed "
                f"an unrelated member: {name}"
            )
    if located_after.m_text != new_source:
        raise ContainerError(
            f"{container_name}: rebuilt container's M source does not read back exactly"
        )
    try:
        core.parse(located_after.m_text)
    except MQueryError as error:
        raise ContainerError(
            f"{container_name}: rebuilt container's M source does not parse"
        ) from error


def write_sections(path: Path, new_source: str, *, write: bool = False) -> str:
    """Replace the M source inside a container's single DataMashup part.

    SAFETY: builds the new container bytes with only the Section1.m member
    changed - every other byte of every other zip member, and every other
    DataMashup blob segment (permissions/metadata/permissionBindings), is
    carried through unmodified - then re-reads its own output from scratch
    and verifies the member list, every unrelated member's bytes, the three
    opaque blob segments, that the new M reads back exactly, and that it
    still parses. Any failed assertion raises ContainerError and writes
    nothing. With ``write=False`` (the default) this only returns a unified
    diff of the M and touches no file. With ``write=True`` it goes through
    `core._atomic_write` - the same lock, concurrent-change re-check, and
    fsync'd atomic replace that `core.update_file` uses - so there is no
    second, less-tested write path for containers.

    UNVALIDATED FOR PRODUCTION USE: the round-trip logic here is exercised
    against synthesized fixtures and one real Power BI Desktop sample in
    this repo's test suite (see ``tests/test_containers.py``), but it has
    never been run against a workbook saved by real Excel, nor against the
    wide range of real-world .xlsx/.pbit files this format can take. It is
    deliberately kept out of the CLI (``pq format/rename/replace-source``
    refuse on a container) until that validation exists.
    """
    suffix = path.suffix.lower()
    if suffix not in _CONTAINER_SUFFIXES:
        raise ContainerError(
            f"{path.name}: write is only supported for .xlsx, .pbix, .pbit containers"
        )

    def build(snapshot: FileSnapshot) -> tuple[str, bytes | None]:
        located = _locate(snapshot.data, path.name)
        diff = "".join(
            difflib.unified_diff(
                located.m_text.splitlines(True),
                new_source.splitlines(True),
                fromfile=f"{path}!{located.outer_member}!Formulas/Section1.m",
                tofile=f"{path}!{located.outer_member}!Formulas/Section1.m",
            )
        )
        if new_source == located.m_text:
            return diff, None
        try:
            core.parse(new_source)
        except MQueryError as error:
            raise ContainerError(
                f"{path.name}: replacement source does not parse: {error.message}"
            ) from error
        rebuilt = _rebuild(snapshot.data, located, new_source, path.name)
        _verify_rebuild(snapshot.data, rebuilt, new_source, path.name)
        return diff, rebuilt

    if not write:
        snapshot = core._snapshot(path)
        diff, _unused = build(snapshot)
        return diff
    return core._atomic_write(path, build)
