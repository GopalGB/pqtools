"""The builtin registry must not lose an implementation to a later one.

0.9.0 development shipped a second `Table.Range` that silently replaced the
first. Both were in the same file, and the merge check only compared names
*across* modules, so nothing reported it. The only visible symptom was one
unrelated-looking test failing on a missing bounds check.

A dict cannot preserve that history: by the time the module finishes
importing, the first registration is already gone. So the check reads the
source, and these tests check the check - a duplicate detector that never
fires is indistinguishable from one that works.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from pqtools.builtins import _MODULES, _registered_names
from pqtools.evaluate import BUILTINS


def test_no_module_registers_a_name_twice() -> None:
    """The live invariant. This is the assertion that would have caught it."""
    offenders = {}
    for module in _MODULES:
        names = _registered_names(module)
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            offenders[module.__name__] = repeated
    assert offenders == {}


def test_no_two_modules_register_the_same_name() -> None:
    seen: dict[str, str] = {}
    for module in _MODULES:
        for name in module.BUILTINS:
            assert name not in seen, (
                f"{name} is registered by both {seen[name]} and {module.__name__}"
            )
            seen[name] = module.__name__


def test_every_registered_name_survives_into_the_public_registry() -> None:
    # A name counted in the module but missing from BUILTINS would mean a
    # registration was dropped somewhere in the merge.
    for module in _MODULES:
        for name in module.BUILTINS:
            assert name in BUILTINS


# --------------------------------------------------------------------------
# The detector itself
# --------------------------------------------------------------------------


def _module_from(tmp_path: Path, name: str, source: str) -> object:
    (tmp_path / f"{name}.py").write_text(source)
    sys.path.insert(0, str(tmp_path))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


def test_detector_reports_a_duplicate_registration(tmp_path: Path) -> None:
    """Positive control: the check has to fail on the bug it exists for."""
    module = _module_from(
        tmp_path,
        "dup_registry_fixture",
        'BUILTINS = {"Table.Range": 1}\nBUILTINS.update({"Table.Range": 2})\n',
    )
    names = _registered_names(module)
    assert names.count("Table.Range") == 2


def test_detector_ignores_dict_literals_that_are_not_registrations(
    tmp_path: Path,
) -> None:
    """Negative control, and the reason this is parsed rather than grepped.

    `_sources.py` builds navigation rows like `{"Name": ..., "Data": ...}`.
    A pattern match over the source counts those as registrations and reports
    five duplicates in a clean file. A check that cries wolf gets switched off.
    """
    module = _module_from(
        tmp_path,
        "row_registry_fixture",
        'def row():\n    return {"Name": 1, "Data": 2}\n\n\n'
        'def other():\n    return {"Name": 3, "Data": 4}\n\n\n'
        'BUILTINS = {"Folder.Files": row}\n',
    )
    assert _registered_names(module) == ["Folder.Files"]


def test_detector_falls_back_to_the_dict_when_source_is_unavailable() -> None:
    """A package installed as a zip or a .pyc has no readable source.

    Failing shut there would make the package unimportable in exactly the
    environments hardest to debug, so the fallback returns the dict's keys -
    weaker, but never wrong about what is registered *now*.
    """

    class NoSource:
        __name__ = "no_source"
        BUILTINS = {"A.B": None, "C.D": None}

    assert sorted(_registered_names(NoSource())) == ["A.B", "C.D"]


@pytest.mark.parametrize("name", ["Table.Range", "Table.Buffer", "Table.HasColumns"])
def test_the_three_names_that_were_actually_lost_still_work(name: str) -> None:
    assert name in BUILTINS
