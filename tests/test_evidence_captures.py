"""The review captures' own invariants, asserted rather than written down.

Rounds 46, 47 and 48 each raised a finding against a hand-written number in
`.planning/AUDIT-2026-09-05-CLOSEOUT.md` about the files in `evidence/`:

- r46 counted with a glob (`round3*`, `round4*`) that cannot match rounds 1-29,
  so "four of seventeen" was wrong in both figures and the remediation it
  certified had left five captures unannotated.
- r47 certified the fix with an unanchored `grep -c 'model requested'`, which
  counts a review body *quoting* the header as an instance of the header. It
  cannot reach zero, so it certified nothing.
- r48 found the corrected number falsified by the commit that wrote it: the
  sentence said 45 captures while adding the 46th.

Three narrowings of one error class is the signal to stop narrowing. The
number was never the point - these four properties were, and a property is
something a test holds. This is the same move `test_support_matrix.py` records
for SUPPORT-MATRIX.md: prose cannot be trusted to stay true on its own, so the
authoritative statement is the one with a test behind it.

No assertion pins an exact total. The only count is a floor, which cannot rot
as captures are added - a number that says "at least" survives the commit that
writes it, which is precisely what "45 captures" did not.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CAPTURES = sorted((_ROOT / "evidence").glob("opus5-wrapper-*.txt"))

# The model's own self-label, as a line of its own. Anchored: a review body
# that QUOTES the marker (round 46's does, discussing this very rule) is not
# itself marked, and that distinction is the whole content of r47's finding.
#
# BOTH labels, since round 50: the first version matched only `ox-alpha` and
# so was blind to the 27 captures opening `🔵 Opus 5` - 24 of them explained
# by nothing at all. That is the stronger false-provenance claim of the two,
# because it names the model `review.sh` actually requests. A guard narrower
# than the thing it counts is r46's defect, and it had been rewritten into
# the file whose whole purpose was to end that class.
# Matched by FORM, not by enumerating labels. Round 50 caught this regex
# listing `⚪ ox-alpha` alone and so missing 27 captures opening `🔵 Opus 5`;
# round 51 caught the repair still being a list of two, in the file whose
# stated purpose is to end "a guard narrower than the thing it counts". The
# model-indicator rule can emit other colours, and a capture opening with one
# would be unexplained and invisible here - the identical shape, one label on.
#
# `\ufe0f` is the optional variation selector some emoji carry.
_MARKER = re.compile(r"^[⚪🔵🟢🟣🔴🟠]\ufe0f?\s+\S", re.MULTILINE)

# The labels actually seen in `evidence/` today. An allowlist for the reader,
# never the predicate - and asserted below, so a `_MARKER` that silently stops
# matching them (a changed variation selector, say) reddens instead of
# quietly counting nothing.
_KNOWN_LABELS = ("⚪ ox-alpha", "🔵 Opus 5")

# The wrapper header that records what was REQUESTED, which is the provenance.
# Anchored to the comment column for the same reason as the marker.
_HEADER = re.compile(r"^# model requested:", re.MULTILINE)

# A round number, not the literal "4": `"NOTE (round 4"` matched rounds 4 and
# 40-49 and would have gone blind at round 50 - while round 49 had just made
# this the SOLE predicate for whether a note exists, so a correctly annotated
# capture would have false-REDded and a misplaced note false-GREENed.
_NOTE = re.compile(r"^# NOTE \(round \d+\)")
_CORRECTED_REFERENCE = "captures from round 46 onward"

# The wording is asserted INSIDE the note block, not anywhere in the file.
# Three review bodies quote the phrase while instructing the fix, so a
# whole-file search would be satisfied by prose about the note rather than by
# the note - the same confusion r47 raised about `grep -c 'model requested'`.

# Before this round the wrapper did not print the header, so a capture from
# an earlier round cannot carry one - the note said "later captures record
# this inline" until r46 measured that false against its own commit.
_FIRST_ROUND_WITH_HEADER = 46


def _leading_comments(text: str) -> str:
    """The run of `#` lines the wrapper writes at the top of a capture.

    Both acceptance predicates read this rather than the whole file. A review
    BODY that quotes `# model requested:` or `# NOTE (round n)` at column 0 -
    the shape rounds 46, 47 and 48 bodies all took while instructing a fix -
    would otherwise self-certify a capture that has neither. `_HEADER` only
    fed the round-boundary test until round 50 promoted it to an acceptance
    predicate, where a stray match greens instead of reddening.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        kept.append(line)
    return "\n".join(kept)


def _note_block(text: str) -> str | None:
    """The retro-note as one unwrapped string, or None if there is no note.

    The note is a run of `# ` comment lines, and its sentences wrap across
    them, so the lines are rejoined before matching - a phrase split over two
    lines is still the phrase. Returning None rather than "" keeps "no note"
    distinguishable from "note that says nothing", which is the difference
    between the two tests that use this.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        # Anchored to the comment column. A review body quoting the note's
        # opening while instructing a fix - the shape rounds 46, 47 and 48 all
        # took - is prose ABOUT a note, not a note, and matching it here made
        # `_note_block` return "" rather than None: an empty note on a capture
        # that has none, reddening two tests for a file with nothing wrong.
        if not _NOTE.match(line):
            continue
        block = []
        for candidate in lines[index:]:
            if not candidate.startswith("#"):
                break
            block.append(candidate.lstrip("#").strip())
        return " ".join(block)
    return None


def _round_of(path: Path) -> int | None:
    """The round a capture belongs to, or None for the few named otherwise.

    `attempt1-wrong-index` and `final-` predate the numbering and carry no
    round; returning None keeps them in the marker/note checks (which apply to
    every capture) and out of the header check (which is defined by round).
    """
    found = re.search(r"-round(\d+)-", path.name)
    return int(found.group(1)) if found else None


def test_there_are_captures_to_check() -> None:
    """Guard the guard: an empty glob would make every test below vacuous.

    A directory that silently stops matching is the shape of failure this
    file exists to catch - r46's whole defect was a pattern narrower than the
    thing it counted.
    """
    assert len(_CAPTURES) >= 45, [p.name for p in _CAPTURES]
    marked = [p for p in _CAPTURES if _MARKER.search(p.read_text(encoding="utf-8"))]
    assert marked

    # Every known label is still reached by the form regex. Without this, a
    # `_MARKER` that matched some third thing and none of the real labels
    # would satisfy the line above and leave every capture unchecked.
    #
    # Matched against the LABEL ITSELF, not against a file that contains it:
    # the first version asked whether a marked capture also contained the
    # label somewhere, and a `_MARKER` rewritten to match the wrapper's
    # `# Exact claude ...` banner passed it - every capture carries both the
    # banner and, further down, a label. Its own control caught that.
    for label in _KNOWN_LABELS:
        assert _MARKER.match(label), label


def test_every_capture_carrying_the_marker_explains_it() -> None:
    """A bare model label in a permanent record reads as a provenance claim.

    It is not one: `review.sh` invokes `claude -p --model claude-opus-5` and
    refuses to run on any substitution, so the invocation is the provenance
    and the marker is the model's own self-label under this machine's
    model-indicator rule. Every capture that carries it says so.
    """
    unexplained = []
    for path in _CAPTURES:
        text = path.read_text(encoding="utf-8")
        if not _MARKER.search(text):
            continue
        # Either explanation will do, and they are the same statement made two
        # ways: the header records what was REQUESTED (so the label beside it
        # is visibly a self-label), and the retro-note says so in words for
        # captures written before the header existed.
        head = _leading_comments(text)
        if _HEADER.search(head) or _note_block(head) is not None:
            continue
        unexplained.append(path.name)
    assert not unexplained, unexplained


def test_no_capture_carries_the_note_without_the_marker() -> None:
    """The other direction: a note explaining a marker that is not there.

    Checked because r46's remediation was applied by hand to a list of files
    I had produced with a broken glob, and a hand-applied edit can land on the
    wrong file as easily as it can miss one.
    """
    misplaced = []
    for path in _CAPTURES:
        text = path.read_text(encoding="utf-8")
        if _note_block(_leading_comments(text)) is not None and not _MARKER.search(
            text
        ):
            misplaced.append(path.name)
    assert not misplaced, misplaced


def test_every_note_makes_the_claim_that_is_true() -> None:
    """The notes first said "Later captures record this inline", which the
    commit introducing them made false: the wrapper change shipped alongside,
    so it could not act on a capture already written. The corrected wording
    names the round it becomes true from, and the next test measures that.
    """
    wrong = []
    for path in _CAPTURES:
        block = _note_block(_leading_comments(path.read_text(encoding="utf-8")))
        if block is not None and _CORRECTED_REFERENCE not in block.lower():
            wrong.append(path.name)
    assert not wrong, wrong


def test_the_header_appears_exactly_from_the_round_it_claims() -> None:
    """Both directions, which is what makes it evidence rather than assertion.

    A test that only checked "round >= 46 has the header" would pass on a
    tree where every capture had it, and the note's claim is precisely that
    the earlier ones do not.
    """
    missing = []
    unexpected = []
    for path in _CAPTURES:
        round_number = _round_of(path)
        if round_number is None:
            continue
        # Anchored to the leading comment block for the same reason as the
        # acceptance test above: a round >= 46 capture that LOST its header
        # but whose body quotes one would read as present here, which is the
        # false direction for a boundary this note's claim rests on.
        head = _leading_comments(path.read_text(encoding="utf-8"))
        has_header = bool(_HEADER.search(head))
        if round_number >= _FIRST_ROUND_WITH_HEADER and not has_header:
            missing.append(path.name)
        if round_number < _FIRST_ROUND_WITH_HEADER and has_header:
            unexpected.append(path.name)
    assert not missing, missing
    assert not unexpected, unexpected
