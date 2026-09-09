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
# A BEST-EFFORT classifier, and deliberately no longer on the acceptance path.
#
# This regex was narrowed four times: `⚪ ox-alpha` alone (r50 caught it missing
# 27 captures), then two literals (r51 caught that as the same defect one label
# on), then a six-emoji class - which cannot be exact either, because
# `CLAUDE.md`'s model-indicator rule ends "any other model: pick the nearest
# color and NAME IT TRUTHFULLY". The label set is open. A fourth narrowing on a
# fresh guess about the input domain is the signal to stop narrowing and remove
# the dependency instead.
#
# So the acceptance test below no longer asks whether a capture carries a
# label. It asks the thing that actually matters - does this capture record
# its provenance - of EVERY capture, which needs no classifier and cannot go
# blind. The 11 captures that carried neither a header nor a note were
# annotated in round 53 to make that unconditional invariant true.
#
# The marker survives for ONE use, and the distinction is the point: an
# inexact classifier is dangerous when it gates ACCEPTANCE, because a miss is
# a silent pass. Used below to find a note attached to a capture that has no
# label, a miss costs one cross-check and can never green anything.
#
# `\ufe0f` is the optional variation selector some emoji carry.
_MARKER = re.compile(r"^[⚪🔵🟢🟣🔴🟠]\ufe0f?\s+\S", re.MULTILINE)

# The labels actually seen in `evidence/` today. A documented allowlist for the
# reader, never the predicate - and asserted against `_MARKER` below, so a
# regex that silently stopped matching the real labels would redden rather
# than quietly classify nothing.
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


def _first_body_line(text: str) -> str:
    """The capture's first line of actual review body.

    The note wordings say a capture "opens with" a self-label, and round 54
    found the predicate behind that phrase was `_MARKER.search(text)` - true
    for a label ANYWHERE, including one quoted inside a review body
    (`opus5-wrapper-round46-*.txt:10` is exactly that). Latent, because every
    such capture today is explained by a header rather than a note, so it
    never reaches the consistency check. Measured before the fix: 0 captures
    where "contains a label" and "opens with one" disagree.
    """
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        return line
    return ""


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
    # Matched against the LABEL ITSELF, not against a file containing one: an
    # earlier version asked whether a marked capture also contained a label
    # somewhere, and a `_MARKER` rewritten to match the wrapper's
    # `# Exact claude ...` banner passed it - every capture carries both the
    # banner and, further down, a label. Its own control caught that.
    for label in _KNOWN_LABELS:
        assert _MARKER.match(label), label
        # And that it is still a label this corpus actually contains. Without
        # this the comment above ("seen in `evidence/` today") could rot to
        # zero live instances without anything reddening - a documented fact
        # with nothing behind it, which is the defect this whole file exists
        # to stop.
        assert any(label in p.read_text(encoding="utf-8") for p in _CAPTURES), label


def test_every_capture_records_its_provenance() -> None:
    """EVERY capture, not only the ones a regex recognises as labelled.

    A bare model label in a permanent record reads as a provenance claim. It
    is not one: `review.sh` invokes `claude -p --model claude-opus-5` and
    refuses to run on any substitution, so the invocation is the provenance
    and any label in the body is the model's own self-label under this
    machine's model-indicator rule.

    Rounds 50, 51 and 52 each caught the classifier deciding WHICH captures
    had to say so being narrower than the set that does. The fourth version
    would have been another guess at an open-ended label set, so this test
    stopped asking the question: a capture that records no provenance is
    unacceptable whether or not it carries a label, and 48 of 48 now do.

    Either explanation counts, and they are the same statement made twice:
    the header records what was REQUESTED, and the retro-note says it in
    words for captures written before the header existed.
    """
    unexplained = []
    for path in _CAPTURES:
        head = _leading_comments(path.read_text(encoding="utf-8"))
        if _HEADER.search(head) or _note_block(head) is not None:
            continue
        unexplained.append(path.name)
    assert not unexplained, unexplained


def test_every_note_describes_the_capture_it_is_attached_to() -> None:
    """A note's claim about the label must match what the capture carries.

    This replaces `..._no_capture_carries_the_note_without_the_marker`, whose
    premise expired in round 53: a note on an unlabelled capture used to mean
    a hand-applied edit had landed on the wrong file, and now means the
    capture predates the header and says so. The purpose survives - a
    misplaced note is still the failure to catch - so the check moved to the
    thing that distinguishes them, which is what the note actually SAYS.

    Unlike the set of model labels, the set of note wordings is closed: there
    are three, all written here. An unrecognised wording fails rather than
    passing quietly, so a new note cannot opt itself out of the check.

    `_MARKER` is used here and nowhere else. A label it fails to recognise
    costs one cross-check on that file; it can never turn an unexplained
    capture green, because the acceptance test above does not consult it.
    """
    claims = {
        "no model self-label": False,
        "opens with a model self-label": True,
        "carries the model marker": True,
    }
    wrong = []
    unrecognised = []
    examined = []
    for path in _CAPTURES:
        text = path.read_text(encoding="utf-8")
        block = _note_block(_leading_comments(text))
        if block is None:
            continue
        examined.append(path.name)
        matched = [expected for phrase, expected in claims.items() if phrase in block]
        if len(matched) != 1:
            unrecognised.append(path.name)
            continue
        if matched[0] is not bool(_MARKER.match(_first_body_line(text))):
            wrong.append(path.name)
    # Non-vacuity, which every sibling guard in this file has and this one
    # did not: `if block is None: continue` means a drift in `_NOTE` or
    # `_note_block` leaves both asserts below passing over zero files. Only
    # the 11 header-less captures are backstopped by the provenance test; the
    # other 33 notes' claims would go unchecked in silence.
    assert len(examined) >= 44, (
        f"expected at least the 44 known notes, examined {len(examined)} - "
        "if the note pattern drifted, this test is looking at nothing"
    )
    assert not unrecognised, unrecognised
    assert not wrong, wrong


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
