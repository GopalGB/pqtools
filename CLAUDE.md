# pqtools

Run, lint and format Power Query M without Power BI. Python package, MIT,
remote `GopalGB/pqtools`. This folder IS the git repo.

    .venv/bin/python -m pytest -q          # ~10 min, 3746 tests
    bash scripts/release_gate.sh           # 8 steps, the thing that says "ship"

## What this package will not do

- **Never claim to "replace Power Query."** Anything that needs Microsoft's
  Mashup Engine raises a typed error. An honest refusal beats an approximation
  a user cannot tell apart from the real answer.
- **Never invent.** Not a function name, not an arity, not an enum number. The
  three gates below exist because each of those has happened at least once:
  - `tests/test_catalog.py` - every registered name is in Microsoft's reference
  - `tests/test_documented_signatures.py` - arity AND nullability, per builtin
  - `tests/test_documented_enum_values.py` - every enum number, per `*-type` page
  All three run offline from checked-in fixtures harvested by
  `scripts/harvest_signatures.py`. **Positive-control a gate before trusting
  it** - reintroduce the defect and watch it go red.
- **Do not weaken a gate to get green.** If a gate blocks a new name, add the
  citation (the page that shows it), never the exception.

## Invariants

- `.samples/` is gitignored and never committed.
- No secret value in code, tests or docs - name the variable and its location.
- The MAX HQ pre-push gate is never bypassed. `GIT_PUSH_BYPASS=1` only when
  there is genuinely no TTY *and* the equivalent scan has been run and is clean.
- **Push, tag and PyPI publish are G's explicit call.** They are outbound and
  irreversible; everything else here is internal and reversible, so just do it.

## What is true right now

`SUPPORT-MATRIX.md` is the authoritative statement of what this package
supports - counts, connectors, per-option behaviour, credential rules, rename
scope. `tests/test_support_matrix.py` checks its numbers and its connector
table against the live registry, so it cannot drift the way the README's
prose did. `ops/STATUS.md` and `.planning/m-inventory.md` are HISTORICAL and
say so at the top; do not quote them as current.

**86% is a count of NAMES**, not semantic compatibility. Registered names,
verified signatures, and semantic behaviour are three different numbers - keep
them apart in anything you write.

## Layout

    src/pqtools/builtins/   the M standard library, split by family
    tests/fixtures/         doc-examples.json (Microsoft's own worked examples),
                            m-signatures.json, m-enum-values.json
    ops/                    release docs + publish-*.sh (HUB-README.md is the
                            old hub README, kept under a different name)
    evidence/               matrix + review logs from the 0.1.x gates

The offline doc cache lives at `/tmp/pqtools-doc-cache/*.html` and is not in
git. Re-fetch with `scripts/harvest_doc_examples.py` /
`scripts/harvest_signatures.py`; both no-op safely when the cache is absent,
because the fixtures - not the cache - are the source of truth.
