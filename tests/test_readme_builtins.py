"""The README's builtin list must equal the real registry.

The README used to claim its list was "verbatim from pqtools.evaluate.BUILTINS,
so it cannot drift out of sync with the code". It was hand-maintained prose, so
it could drift and did: the registry grew from 49 builtins to 270 while the list
kept advertising the original 49. A promise about documentation accuracy is only
worth anything if something enforces it, which is what this test is for.
"""

from __future__ import annotations

import re
from pathlib import Path

from pqtools.evaluate import BUILTINS

_README = Path(__file__).resolve().parent.parent / "README.md"


def _documented_builtins() -> set[str]:
    text = _README.read_text(encoding="utf-8")
    marker = "and these "
    start = text.index(marker)
    fence = text.index("```", start) + 3
    end = text.index("```", fence)
    block = text[fence:end]
    # Two shapes appear: qualified names (Table.Sort) and the intrinsic date/time
    # literals, which are bare and hash-prefixed (#date, #duration). Matching only
    # the dotted form silently ignored the five literals - caught by this test's
    # own first run, which is a fair advertisement for writing it.
    return set(re.findall(r"(?<![\w.])(?:\w+\.\w+|#\w+)(?![\w.])", block))


def test_readme_lists_exactly_the_registered_builtins() -> None:
    documented = _documented_builtins()
    registered = set(BUILTINS)
    missing_from_readme = registered - documented
    not_registered = documented - registered
    assert not missing_from_readme, (
        f"README omits {len(missing_from_readme)} builtin(s): "
        f"{sorted(missing_from_readme)[:10]}"
    )
    assert not not_registered, (
        f"README lists {len(not_registered)} name(s) that are not registered: "
        f"{sorted(not_registered)[:10]}"
    )


def test_readme_states_the_real_builtin_count() -> None:
    text = _README.read_text(encoding="utf-8")
    assert f"and these {len(BUILTINS)} builtins" in text, (
        f"README's builtin count is stale; the registry now holds {len(BUILTINS)}"
    )


def test_evaluate_is_importable_the_way_the_readme_documents_it() -> None:
    # The README's FAQ tells readers `from pqtools import evaluate`. That is
    # also the import anyone - or any code assistant - guesses first, so it
    # has to be the one that works. It did not until 0.6.0.
    from pqtools import EvalError, UnsupportedError, evaluate

    assert callable(evaluate)
    assert issubclass(UnsupportedError, EvalError)


def test_readme_python_blocks_only_import_names_the_package_exports() -> None:
    # A README example that cannot even be imported is worse than no example:
    # it gets copied verbatim into someone's editor and fails on line 1.
    import re

    import pqtools

    text = _README.read_text(encoding="utf-8")
    for imported in re.findall(r"^from pqtools import (.+)$", text, re.MULTILINE):
        for name in (part.strip() for part in imported.split(",")):
            assert hasattr(pqtools, name), f"README imports missing name: {name}"


def test_llms_txt_states_the_real_builtin_count() -> None:
    # llms.txt exists so an assistant answering "how do I run Power Query in
    # Python" quotes something true. A stale number there is repeated far more
    # widely than a stale number in the README.
    path = _README.parent / "llms.txt"
    text = path.read_text(encoding="utf-8")
    assert f"{len(BUILTINS)} M builtins" in text, (
        f"llms.txt builtin count is stale; the registry now holds {len(BUILTINS)}"
    )
