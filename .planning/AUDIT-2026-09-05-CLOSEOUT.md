# Audit closeout - AUDIT-2026-09-05

Baseline `c7ffc5f` (0.10.0). Four commits, `c7ffc5f..a4f5735`. No push, no tag,
no publish, no deploy, nobody contacted. No other MAX HQ project touched.

Companion to [AUDIT-2026-09-05.md](AUDIT-2026-09-05.md), which is preserved
unchanged as the statement of the problem.

**Read the scope limits at the bottom before quoting anything here.** Everything
verified below is LOCAL. No live database, Fabric tenant, Windows PQTest, native
Excel or Power BI refresh was exercised, and no credentials were used.

## Dispositions

| # | Finding | Disposition | Commit |
|---|---|---|---|
| 1 | HIGH - unsafe workbook backup destination | FIXED | `2e27afd` |
| 2 | HIGH - OData pagination silently truncates | FIXED | `f4fdeac` |
| 3 | HIGH - SQL navigation eagerly reads every table | FIXED | `f4fdeac` |
| 4 | MEDIUM - SQL options silently ignored | FIXED | `f4fdeac` |
| 5 | MEDIUM - credential policy differs from documentation | FIXED (code changed, not docs) | `f4fdeac` |
| 6 | MEDIUM - compatibility gates miss defects | FIXED, and wider than reported | `58f439c` |
| 7 | Requirements and documentation reconciliation | DONE, and wider than reported | `a4f5735` |

Every fix was **reproduced end to end before being changed** and
**positive-controlled after**: the defect was reintroduced and the new tests
were watched going red, then restored. Red counts are given per finding.

### 1. Backup destination - FIXED

`src/pqtools/cli.py`. `_container_backup` wrote `<name>.bak` with a plain
write, so it followed a symlink at that path, overwrote a real backup, and did
not stop the edit when it failed.

Now `os.open` with `O_CREAT|O_EXCL|O_NOFOLLOW`, preserving existing backups by
moving to `.bak.1`...`.bak.99`, fsyncing, carrying the source mode over, and
raising before `containers.write_sections` so a failed backup leaves the
container untouched.

`tests/test_container_backup_safety.py`, 11 tests over both callers
(`format --write`, `add --write`), temporary files only, no real user file.
Positive control: 8 of 11 red.

### 2. OData pagination - FIXED

`src/pqtools/builtins/_sources.py`. `OData.Feed` read page one and returned it
as the whole feed.

Now follows `@odata.nextLink` and the v3 `odata.nextLink`, absolute or relative
(`urljoin`), applying `policy.check_net` to every request **and every redirect**,
bounded by a `seen` cycle check, `ctx.budget.tick()`, and a 200-page ceiling.
Every bound raises a typed `EvalError` rather than returning a prefix. A
malformed page raises instead of surfacing a `JSONDecodeError`.

`tests/test_odata_pagination.py`, 13 tests against a local HTTP server.
Includes a next link to a blocked host, asserting the second request was never
made. Positive control: 11 of 13 red.

### 3. SQL navigation - FIXED

`_sources.py`, `builtins/_shared.py`, `evaluate.py`. `Sql.Database` navigation
ran `SELECT *` against every table in the catalog before the query said which
one it wanted.

Each row's `Data` is now a `_DeferredRows` that reads on selection, on its own
short-lived connection. It **refuses loudly on every consumption path except its
own** (`__iter__`, `__len__`, `__getitem__`, `__contains__`, `__eq__`,
`__bool__`, and `__hash__ = None`), so a path this change missed is a typed
error rather than a silently empty table. `_require_list`, `_require_table` and
`_record_field_access` force it.

Scope was checked rather than assumed: PostgreSQL, MySQL and Oracle refuse
navigation without `[Query=...]` and `Odbc.DataSource` returns rows with no
`Data` field, so none of them had this defect.

`tests/test_sql_navigation.py`, 10 tests on a mock `pyodbc`, asserting
connections opened equals connections closed. Positive control: 6 of 10 red.
A full sequential suite was run after this change specifically because
`_require_list` is a hot path: **3780 passed**.

### 4. SQL options - FIXED

`_sources.py`. `CommandTimeout`, `HierarchicalNavigation` and
`MultiSubnetFailover` were popped off the options record and discarded.
`Odbc.Query`, in the same file, had validated and applied the identical
`CommandTimeout` all along, so the two connector families disagreed about one
documented option.

Microsoft's page documents **twelve** options, not the four the code knew about.
Now: `CommandTimeout` and `ConnectionTimeout` validated as durations and applied
to the catalog connection **and the deferred read** (a timeout that stopped at
the catalog would bound the one query that was never the slow one);
`MultiSubnetFailover` writes both properties its page documents, including the
easily-missed `ApplicationIntent=ReadOnly`, and refuses to combine with an
explicit `ConnectionString`; `HierarchicalNavigation` accepts its documented
default `false` and refuses `true` by name rather than inventing a grouped
table shape the page does not specify. The remaining seven refuse by name, as
they already did.

`tests/test_sql_options.py`, 26 tests. Positive control: 12 of 26 red - and the
14 that stayed green are exactly the cases the defect never touched.

### 5. Credentials - FIXED IN CODE, NOT PAPERED OVER IN DOCS

`_sources.py`. Two defects, verified before either was changed.

**Policy.** The README states twice, as a security property, that credentials
come from the environment and not from the M file. `_credentials` read
`[Username=...]`/`[Password=...]` and **preferred them over** the environment.
Neither is a documented Power Query option - the connector pages list no
credential options at all. Both are now refused by name, before any connection,
naming the variable to set instead. **The code was changed to match the
documentation, not the reverse.**

**Escaping - this one is a security defect, not a correctness one.** Values were
interpolated into the ODBC connection string raw. A password of
`x;Encrypt=no` did not produce a wrong password; it produced an extra
connection-string **keyword**, and that one turns TLS off. Server, database and
user were injectable the same way, and a password legitimately containing `;` or
`}` could not be expressed at all. Values are now brace-quoted with `}` doubled,
at **both** sites that build a connection string - the record form of
`Odbc.Query`/`Odbc.DataSource` had the identical hole and is fixed in the same
pass.

`tests/test_sql_credentials.py`, 15 tests. They parse the connection string the
way a driver does, because a substring assertion cannot tell `PWD={a;b}` from
`PWD=a;b` - which is the entire difference the fix makes. Every credential is a
dummy literal; no real credential was read or inspected. Positive control: 10 of
15 red.

### 6. Compatibility gates - FIXED, AND THE GAP WAS WIDER THAN REPORTED

**Arity gate.** `tests/test_documented_signatures.py` reads the source for a
literal `_arity("Name", args, ...)`. A builtin registered through a helper writes
`_arity(name, args, ...)` with `name` as a variable, matches nothing, and drops
out of the comparison silently.

The audit named two connectors. The real number is **82 of 554 callable builtins
(15% of the registry) had never had their arity compared to their page.**

Among them, `PostgreSQL.Database` and `MySQL.Database` accepted a one-argument
call where both pages require two, then connected with `database=""` - which
PostgreSQL resolves to the user's default database. A different database,
silently, on a call the signature forbids. Fixed to `2..3`.

The gate now asks the builtins the text cannot describe directly: call with k
arguments and see whether `_arity` is what refuses. All 82 are compared, and a
new test fails if any documented builtin is covered by neither route.
Positive control: reintroducing the arity gave exactly 2 failures, by name.

**Worked-example gate.** `test_a_documented_example_produces_its_documented_output`
returned when the usage did not evaluate, and again when the printed Output was
not parseable M. Both silent; **21 examples took those exits**, and the only
backstop was a match floor of 110 against an actual **141** - 31 examples of
slack.

All 21 are now recorded exemptions with reasons (6 culture-specific, 3
Mashup-Engine-only, 4 fuzzy matching, 3 genuinely unimplemented functions, 2
needing a file or credentials, 2 defects in Microsoft's own pages, 1 output that
is prose). A case that recovers **fails and asks to be removed**, so the list can
only shrink. The floor is now the measured 141. Positive control run in both
directions: removing an exemption fails, and fabricating one for a working case
fails.

No test was weakened or removed. Both gates check strictly more than before.

### 7. Documentation reconciliation - DONE, AND WIDER THAN REPORTED

Three documents described three different releases at once, and nothing checked
any of them.

Verified by execution before a word was changed, which found **three stale claims
the audit had not named**: `??` and field projection (`r[[a],[b]]`) are listed as
unsupported and both run; `RoundingMode.*`, `TextEncoding.*` and
`BinaryEncoding.*` are described as "deliberately unregistered - their numeric
values could not be verified" while all thirteen are registered with verified
numbers. `#shared` and `meta` do still refuse; those claims were true and were
kept.

- **`SUPPORT-MATRIX.md` (new)** is the single authoritative statement: the three
  counts that are not the same number, the connector table, per-option behaviour
  for all twelve `Sql.Database` options, the credential rule, the nine refusal
  categories, and the rename scope.
- **The framing was the biggest defect.** "pqtools implements 547 of the 635
  functions (86%)" is a count of NAMES, and `llms.txt` exists to be quoted by
  assistants, so that sentence propagated furthest. It now reads "registers 547
  of the 635 names - 86% of NAMES, which is not a measure of semantic
  compatibility and should not be quoted as one", generated by
  `scripts/sync_builtin_list.py` so it cannot be hand-edited back.
- **The three numbers, separated:** 547 of 635 names registered (86.1% of names);
  547 signatures verified for arity and nullability; 73 enum values verified;
  141 of Microsoft's worked examples reproduce their documented output exactly.
  **Semantic compatibility is not measured and is not claimed.** Mashup Engine
  compatibility is explicitly disclaimed.
- **`ops/STATUS.md`** (0.1.0, former package name, 60 tests) and
  **`.planning/m-inventory.md`** (a path that no longer exists) are preserved
  and labelled HISTORICAL at the top, pointing at the matrix.
- **Rename scope** documented as what it is: a **textual, whole-file** guard, so
  a `[`, `=>`, `#"` or non-ASCII character anywhere - including inside a string
  literal or a comment - refuses the whole rename. **The guard was not loosened.**
  Narrowing it needs binding-aware analysis of the parse tree, which was not
  attempted and would need its own regression proof.

Enforcement, because prose is what drifted: `tests/test_support_matrix.py` checks
every count against the live registry, checks each connector row against the
registry (the exact check the stale README paragraph would have failed for four
releases), and fails if either historical file loses its banner.
`tests/test_catalog.py` now also asserts the "% of NAMES" qualifier, so the
misleading framing cannot return while the number stays right.

## Changed paths

Production code: `src/pqtools/cli.py`, `src/pqtools/builtins/_sources.py`,
`src/pqtools/builtins/_shared.py`, `src/pqtools/evaluate.py`.

Gates and tests: `tests/test_documented_signatures.py`, `tests/test_doc_examples.py`,
`tests/test_catalog.py`, plus new `tests/test_container_backup_safety.py`,
`test_odata_pagination.py`, `test_sql_navigation.py`, `test_sql_options.py`,
`test_sql_credentials.py`, `test_support_matrix.py`.

Documentation: `SUPPORT-MATRIX.md` (new), `README.md`, `llms.txt`, `CLAUDE.md`,
`scripts/sync_builtin_list.py`, `ops/STATUS.md`, `.planning/m-inventory.md`.

## Round 2 - the exact `claude-opus-5` review of the fixes

`bash ~/.codex/skills/claude-review/bin/review.sh c7ffc5f..HEAD`, with
`ANTHROPIC_API_KEY` unset so it bills the logged-in plan. The wrapper refuses
any model substitution. **Verdict: FIX-FIRST. 2 HIGH, 3 MEDIUM, 2 LOW.**

Every finding was reproduced before being changed and positive-controlled
after. All seven are fixed in `e1e90f3`. **Two of the HIGHs were regressions
the audit fixes themselves introduced** - worth stating plainly, because it is
the argument for the review step existing.

| Severity | Finding | Status |
|---|---|---|
| HIGH | OData paging carried the caller's `Authorization` to a host the SERVER chose | FIXED |
| HIGH | the 256 MiB response cap became a per-PAGE cap, so the feed total was unbounded | FIXED |
| MEDIUM | a sub-second timeout truncated to `0`, which pyodbc reads as NO timeout | FIXED |
| MEDIUM | the ODBC record form escaped values but not KEYS, so keys were still injectable | FIXED |
| MEDIUM | a deferred navigation value reached the CLI as a bare `AttributeError` traceback | FIXED |
| LOW | README and llms.txt still promised the backup is exactly `<file>.bak` | FIXED |
| LOW | the new arity probe caught `BaseException` and guessed where it could not reach | FIXED |

### The two that were mine

**The credential hop.** `_odata_feed` carried `page_options` - including the
caller's `Headers` - to every next link, and that URL comes from
`@odata.nextLink`, which the remote server writes. Under plain `--allow-net`
with no host allowlist, `check_net` permits it, so the network policy was not
what stopped this; nothing was. Reproduced with two local origins: the second
origin logged `Bearer`. My own code comment said "the headers (which may carry
auth) and the timeout travel on" - the risk was written down and shipped
anyway, which is the part worth remembering.

Cross-origin hops now carry only `Accept`. Same-origin paging is unchanged, so
authenticated paging inside one service still works, and both cases are
pinned by tests.

**The cap that stopped being a cap.** `_MAX_RESPONSE_BYTES` is enforced inside
`_http_fetch`, per response. Before paging existed, a feed was one response and
genuinely bounded at 256 MiB. Following up to 200 of them turned a hard limit
into that limit times two hundred. The running total is now bounded by the same
constant.

### The one worth naming separately

`pq eval 'Sql.Database("s","d")'` ended in a bare Python `AttributeError`
traceback - the failure shape `CLAUDE.md` says this package exists to prevent -
because `_print`'s JSON fallback called `.as_dict()` on the deferred value.
`--format csv` was worse: `csv.DictWriter` would have written the placeholder
into a data cell, where a table nobody read is indistinguishable from a value
somebody measured. That is the silent-wrong-answer class the whole audit is
about, introduced by the fix for finding 3.

Forcing the value at the CLI was considered and rejected: that is exactly the
read-every-table behaviour finding 3 removed. Both paths now refuse in the
typed way and name the step to write instead.

### Round 2b - the sibling the review did not name

The reviewer found the credential leak on the `@odata.nextLink` hop. Asking
whether the *other* way a request can change hosts had the same defect found
that it did: a plain **HTTP 302** forwarded the caller's headers too, through
`_http_fetch` - which every network connector shares, so this was
`Web.Contents`'s defect as much as `OData.Feed`'s. Pre-existing, not
introduced by the paging work.

Reproduced: a server answering 302 to a second local origin received
`Authorization: Bearer ...` in full. `_PolicyRedirectHandler` already
re-checked the policy on every hop, which answers a different question -
"may this request be made", not "may this server be told the caller's secret".

Cross-origin redirects now drop the standard credential headers **and every
header the query supplied**, because a credential does not have to be called
`Authorization` and `X-API-Key` matches no fixed list. `Accept` is kept.
Same-origin redirects are unchanged and have their own test, because a fix
that broke ordinary authenticated services would otherwise have looked like a
pass. Fixed in `a3faea2`.

## Round 3 - the review of the round-2 fixes

`review.sh c7ffc5f..HEAD` again. **Verdict: FIX-FIRST. 1 HIGH, 3 MEDIUM,
4 LOW.** All fixed in `c13c407` and `475ce01`, each reproduced first and
positive-controlled after.

| Severity | Finding | Status |
|---|---|---|
| HIGH | the `--format csv` guard checked only TOP-LEVEL cells, so a nested unread table printed as data | FIXED |
| MEDIUM | SUPPORT-MATRIX.md's 786 / 165 / 141 were hand-written and unchecked | FIXED |
| MEDIUM | the test pinned the literal `"86.1% of NAMES"` instead of computing it | FIXED |
| MEDIUM | `evaluate()` returned a private type with no public way to read it | FIXED |
| LOW | env credentials silently dropped when `ConnectionString` was given | FIXED |
| LOW | a relative `@odata.nextLink` resolved against the pre-redirect URL | FIXED |
| LOW | `_require_table` forced the deferred value twice | FIXED |
| LOW | `_MAX_BACKUPS = 100` never prunes | NOT TAKEN - see residuals |

The HIGH is the sharpest lesson in the whole exercise. The fix for round 2's
CLI finding added a guard whose own comment said it stopped "a table nobody
read being indistinguishable from a value somebody measured" - and the loop
under that comment only looked at top-level cells, so a deferred value nested
one level down (`Table.Group(Sql.Database(...), ...)` produces exactly that)
went to `csv.DictWriter` and printed as `<deferred ...>` in a data cell. The
comment described the intent; the code did not implement it. The JSON path
was correct only because `json.dumps` recurses for free.

The MEDIUMs are the same shape one level up: `SUPPORT-MATRIX.md` was created
because unenforced prose drifts, and it shipped with three hand-written
numbers nothing checked, plus a test that pinned a percentage as a literal
string. Both are now derived.

## Round 4 - the review of the round-3 fixes

`review.sh c7ffc5f..HEAD` again. **Verdict: FIX-FIRST. 1 HIGH, 2 MEDIUM,
4 LOW.** Six taken, one rejected on evidence.

| Severity | Finding | Status |
|---|---|---|
| HIGH | `release_gate.sh` quoted default made the gate's own default path fail | FIXED |
| MEDIUM | the same-origin paging test asserted nothing about headers | FIXED |
| MEDIUM | the `141` worked-example count was hand-written into four files | FIXED |
| LOW | `_container_backup`'s `read_bytes` outside the typed-error handling | **NOT A DEFECT - see below** |
| LOW | `_origin` compared netloc verbatim, so `:443` read as cross-origin | FIXED |
| LOW | two paths in the credentials test were CWD-relative | FIXED |
| LOW | `_doc_example_floor()` checked the ratchet floor, not the measured count | FIXED |

### The HIGH was mine, and my first check could not have caught it

`${PQ_GATE_PYTEST_ARGS-"-n auto"}` expands as ONE word, so the unset path -
the path everyone who does not set the variable takes - ran
`pytest -q "-n auto"` and died with
`invalid parse_numprocesses value: ' auto'`. I had introduced this while
making the gate's parallelism overridable, and I had "verified the default is
preserved" using `echo`, which joins its arguments with spaces and prints
`-n auto` either way. **The check was structurally incapable of showing the
bug it was run to exclude.** Re-verified properly with `set --` (argc == 2)
and against real pytest (`12 tests collected`).

### The LOW that was not a defect

The review said `pq format <pbip-dir> --write` "raises a bare
`IsADirectoryError` traceback". I applied the suggested wrap, then executed
the path before trusting my own comment - and it was false in both
directions. `main()` catches `OSError` and maps it to a typed
`M_IO_ERROR` with a clean message and exit 2. Run against three trees, with a
directory container holding one `.pq` file:

| Tree | Result |
|---|---|
| baseline `c7ffc5f` | `error M_IO_ERROR: [Errno 21] Is a directory: <path>` |
| `832f7e8` (after my backup rewrite) | `error M_IO_ERROR: [Errno 21] Is a directory: <path>` |

No traceback, no regression, and identical to baseline. My "fix" would have
changed a stable error code from `M_IO_ERROR` to `MQUERY_ERROR` for no
defect, and I had written a code comment asserting the reviewer's claim as
though I had reproduced it. **Reverted.** The underlying limitation is real
but different, and is recorded under residuals: `write_sections` rebuilds a
single zip DataMashup part, so directory containers are read-only - `--write`
on one has never been supported and now says so no less clearly than before.

### The `141` was the same defect as the audit's finding 6, one level up

`SUPPORT-MATRIX.md` was created because unenforced prose drifts. The count of
worked examples that reproduce their documented output was then typed by hand
into four places - the matrix, `scripts/sync_builtin_list.py`, and through
that generator into `README.md` and `llms.txt` - and the only test that
looked at it compared the matrix against the *ratchet floor* in
`test_doc_examples.py`, not against the measurement. Two consequences, both
silent: `README.md` and `llms.txt` had no gate at all, and once real matches
rose above the floor the matrix would understate them with every test green.

Now there is one number, `tests/test_doc_examples.py::DOCUMENTED_MATCHES`,
and it is asserted for **equality** against the live count rather than as a
floor. A floor cannot tell a regression from an improvement; the previous
floor of 110 sat 31 below reality, so a third of the matches could have died
unnoticed. `SUPPORT-MATRIX.md`, `README.md` and `llms.txt` all derive from it.
Positive-controlled by moving the constant to 142: four gates went red
(`test_doc_examples`, `test_support_matrix`, and `test_catalog` for both
documents) and green again on restore.

### Positive controls run in this round

| Fix | Control | Result |
|---|---|---|
| `release_gate.sh` quoting | `set --` argc, then real pytest | argc 2; `12 tests collected` |
| same-origin header assertion | drop the header from the second request | red, then green |
| `DOCUMENTED_MATCHES` | set to 142 | 4 gates red, green on restore |
| `_origin` default port | delete the normalisation | the 3 default-port cases red, the 4 genuine-difference cases stayed green |
| CWD-relative test paths | run pytest from outside the repo root | `FileNotFoundError` before, passes after |

The `_origin` control is the one worth keeping: a normalisation that made
*everything* compare equal would also have turned the 3 red cases green, so
the test carries 4 cases that must stay green - including `https://host:80`,
which is a real port change and not a default.

## Requirement check - each clause of the stated requirement, with its evidence

The requirement, verbatim: *"a reliable Python/CLI toolkit for Power Query M
that parses, formats, lints, safely edits queries, and runs supported
real-world transformations and connectors without Power BI. Unsupported
behavior must produce an explicit typed refusal, never silently return
incomplete or incorrect data. Do not claim full Microsoft Mashup Engine
compatibility."*

| Clause | Where it is met | Evidence |
|---|---|---|
| parses | `pq parse`, `pqtools.core.parse` - Microsoft's own pinned parser through the Node bridge | `npm test` 23/23; no bundle drift; every M in the 786-example corpus parses or is a recorded exemption |
| formats | `pq format`, `format_source` - Microsoft's pinned formatter | installed-wheel quick-start, `pq format --write` round trip |
| lints | `pq check`, exit 2 on error-severity, 0 on warnings | CLI tests; `pq check` in the quick-start |
| safely edits | `pq rename` refuses with `M_RENAME_REFUSED` unless binding-aware analysis proves it safe; every container `--write` backs up first to a fresh sidecar (O_CREAT\|O_EXCL\|O_NOFOLLOW), and a failed backup stops the edit; `_atomic_write` refuses with `M_SAFE_WRITE_REFUSED` on concurrent change | `tests/test_container_backup_safety.py` (11), rename scope in SUPPORT-MATRIX.md; guards untouched, not loosened |
| runs supported transformations | 640 registered builtins, arity and nullability checked per builtin against its reference page, 141 worked examples reproducing exactly | `test_documented_signatures.py` (659), `test_doc_examples.py` (953), `DOCUMENTED_MATCHES` asserted for equality |
| connectors without Power BI | Csv, File, Folder, Web, OData (full paging, bounded), Excel, Sql, PostgreSQL, MySQL, Oracle, Odbc - off by default, credentials environment-only, SQL navigation lazy | `test_odata_pagination.py` (27), `test_sql_navigation.py` (19), `test_sql_options.py` (28), `test_sql_credentials.py` (19) - all offline with mocks and fixtures |
| explicit typed refusal, never silent | `M_EVAL_UNSUPPORTED` names the outside system; `DeferredTable` raises on every consumption path but `read()`; both CLI print paths refuse an unread table; OData caps raise instead of truncating; SQL options are applied or refused by name; the 21 unevaluable doc examples are recorded exemptions, not silent returns | release gate step 6 is a positive control on the refusal machinery; every gate in this closeout was positive-controlled |
| no full-Mashup claim | README, llms.txt and SUPPORT-MATRIX.md say "86% of NAMES ... not a measure of semantic compatibility"; llms.txt says "not a Power Query replacement and does not reimplement the Mashup Engine" | `test_catalog.py` asserts the qualifier in both documents; `test_support_matrix.py` (30) ties the numbers to the registry |

What this table does NOT claim, restated so it cannot be read past: no live
database, no Fabric, no Windows PQTest, no native Excel or Power BI refresh
was exercised. Those are named in "Scope limits" and stay open.

## Discoverability - what an AI agent finds, and what it can act on

The ask was that agents should be able to find and use this. The surfaces an
agent actually reads, and what changed on each:

| Surface | State before | Change |
|---|---|---|
| PyPI metadata (`pyproject.toml`) | 19 keywords, 18 classifiers, `Typing :: Typed`, five project URLs | none needed |
| `llms.txt` | what it does, what it refuses, correct usage, coverage sentence - all already gated | added the **error-code table** (every code the code can raise, what it means, what an agent should do) and the **credential rule**; both now test-enforced by `tests/test_llms_txt.py` - a code added without documenting it, or documented without existing, fails |
| `README.md` | no pointer to llms.txt | one paragraph pointing agents at `llms.txt` and `AGENTS.md` |
| `AGENTS.md` | absent | symlink to `CLAUDE.md`, so Codex, Cursor and any agents.md-reading tool get the same build/test/gate instructions with zero drift |

**Staged for G - outbound, not done:** GitHub repository description and
topics are set through the GitHub API or UI, which is outbound. Suggested,
mirroring the PyPI keywords: `power-query`, `power-query-m`, `m-language`,
`power-bi`, `pbix`, `fabric`, `linter`, `formatter`, `python`, `etl`.
Description: the `pyproject.toml` `description` field, verbatim.

## Round 5 - the review of the final tree

The exact `claude-opus-5` wrapper on `c7ffc5f..ee04185`. **Verdict:
FIX-FIRST. 3 HIGH, 2 MEDIUM, 2 LOW.** Every finding reproduced against the
tree before any change; all seven taken, one with its impact corrected.

The first attempt was BLOCKED with "Prompt is too long": 46,288 deletions in
a 2.9 MB diff. That was the wrapper's bundle stub applied to an UNCHANGED
file - HEAD's index got the 1-line stub while BASE kept the real 2.6 MB
bundle, so the diff showed the bundle being deleted. My round-3 repair of the
wrapper's stale stub path is what exposed it: while the path was stale nothing
was stubbed, and an unchanged bundle produced no diff, which was the right
result by accident. The wrapper now stubs a file only if `git diff --quiet
BASE HEAD -- path` says it changed, writes that decision into the review
header, and has a `DRY_RUN=1` that stops after printing the staged stat.
Dry run: 31 files, 3,907 insertions, 150 deletions.

| Severity | Finding | Status |
|---|---|---|
| HIGH | `logs/combined.log` and `logs/error.log` - runtime logs of an unrelated MCP server - were tracked in the package repo, and `logs/` was not ignored | FIXED - **mine**: swept in by `832f7e8`; untracked, `logs/` ignored; 0 secret-shaped lines; sdist verified not to carry them |
| HIGH | `os.chmod(tmp_path, 0o500)` does not deny creation on Windows; the CI matrix has `windows-latest` | FIXED - `skipif(os.name == "nt")`, the reason stated |
| HIGH | two symlink tests had no platform guard, against the repo's own precedent in `test_containers.py` | FIXED - the same guard, the same reason string |
| MEDIUM | the arity probe ran every helper-registered builtin at collection time under an allow-everything `IOPolicy`, safe only because each connector happened to reject the placeholder before touching a driver | FIXED - `_arity` now precedes the policy check in all five DB connectors (the order `Web.Contents` always had), and the probe runs under deny-all `IOPolicy()` |
| MEDIUM | `scripts/sync_builtin_list.py` imported a pytest module to read `DOCUMENTED_MATCHES` | FIXED - the number lives in `tests/fixtures/doc-example-matches.json`; the test asserts equality against it, the script reads it with `json` |
| LOW | `seen` recorded the requested URL, not the landed one, so a next link naming the landed URL was fetched once more before the cycle check caught it | FIXED - `seen.add(landed)`; regression test with a 302 on page one; impact below |
| LOW | `test_support_matrix.py`'s docstring still described the ratchet floor | FIXED |

### What the deny-all probe now enforces

Moving `_arity` ahead of the policy check did more than make the probe safe.
Under a deny-all policy, a helper-registered connector that checks policy
first answers every count with `M_IO_BLOCKED`, which `_accepts` reads as
"got past the arity check"; `_probe_arity` then returns `None` rather than
guess a range, and `test_no_documented_builtin_escapes_both_arity_checks`
names the builtin. So for every builtin the runtime probe covers, arity
before policy is now enforced, not commented.

The scope of that sentence matters, and my first control got it wrong. I
reverted `Sql.Database` to policy-first and the gate stayed green - because
`Sql.Database` has a LITERAL `_arity("Sql.Database", ...)`, so the static
scan covers it and the runtime probe never executes it. Of the five
connectors reordered, only `_generic_database` (PostgreSQL.Database,
MySQL.Database) is in the probe's population; reverting that one turned the
escape test red. For the four literal-arity connectors the reorder is
consistency with `Web.Contents`, and is not enforced by any test.

### The LOW whose impact was overstated

The review said a next link pointing at "page one's built URL (with
`RelativePath`/`Query` applied)" escapes the cycle check and "only the
200-page ceiling stops it". `OData.Feed` refuses every option except
`Timeout`, so a built URL that differs from the requested one cannot occur
there. The reachable case is a 302 on page one: the landed URL is fetched a
second time, identically, and the cycle is caught on the next iteration - one
redundant page, never two hundred. Fixed anyway, because the cycle check
exists so that the ceiling is never what stops a loop; the regression test
asserts exactly two hits, `/odata` then `/landed`.

### Two of my own checks that could not have failed

Recorded because the pattern is the point of this whole closeout. My patch
to `test_doc_examples.py` inserted a reference to `_FIXTURES` and then
asserted `"_FIXTURES" in source` - which was true because I had just written
it. The module failed at collection with `NameError`. And the round-3
`echo`-based check of the release gate's default arguments (round 4, above)
was the same shape. Both were caught by running the thing, neither by reading
it.

### Positive controls run in this round

| Fix | Control | Result |
|---|---|---|
| arity before policy | revert `Sql.Database` to policy-first | stayed GREEN - wrong target, literal arity is statically scanned |
| arity before policy | revert `_generic_database` to policy-first | `test_no_documented_builtin_escapes_both_arity_checks` red; 659 green on restore |
| `seen.add(landed)` | delete the line | the new redirect-cycle test red (1 failed, 27 passed); green on restore |
| `logs/` untracked and ignored | build an sdist, list it | 0 `logs/` entries among the 107 files in the sdist |

## Round 6 - the review of the round-5 fixes

The full-range review of `c7ffc5f..a2250f5` was killed twice by the harness
for low memory - this session's own `claude` process plus the review's, on
an 8 GB machine with other sessions resident. Rather than launch the same
thing a third time, the range was narrowed to what the four full-range
rounds had not yet seen: `ee04185..eb2c7fd`, the round-5 fixes plus the
closeout text, 12 files, +163/-32. The exact `claude-opus-5` wrapper on that
range. **Verdict: FIX-FIRST. 1 HIGH, 1 MEDIUM, 4 LOW.** All six taken.

| Severity | Finding | Status |
|---|---|---|
| HIGH | `seen.add(landed)` overloaded the cycle set that `elif rows or len(seen) > 1` also read as a page counter, so a 302 on page one to a single-entity response raised "reached as a next page but is not a collection" instead of returning the entity | FIXED - **mine, a regression from the round-5 LOW fix**; a `pages` counter, `seen` left to cycle detection; regression test with a redirect to `{"Id": 99}` |
| MEDIUM | the arity-before-policy reorder was enforced by no test for the four literal-arity connectors - the closeout's own control had shown `Sql.Database` staying green | FIXED - `tests/test_connector_arity_before_policy.py`: a zero-argument call under the default deny-all policy must raise the arity `UnsupportedError`, for all six DB connectors |
| LOW | the byte-cap message counted `len(seen)`, two entries per redirected page | FIXED by the same counter |
| LOW | the probe's new comment said "the comparison below fails"; it is `_probe_arity` returning `None` and the escape test that catches it | FIXED - names the test, and the file that pins the literal-arity four |
| LOW | `logs/` in `.gitignore` was unanchored | FIXED - `/logs/` |
| LOW | the generator read the matches fixture with a bare `json.loads(...)[key]` | FIXED - a wrapped error saying what the file is and who asserts it |

### The HIGH, plainly

The round-5 LOW was "one redundant fetch after a redirect". The one-line fix
for it - record the landed URL in `seen` - broke the single-entity path for
any feed whose first request redirects, because one line below, `seen` was
doing a second job. I read the function far enough to add the line and not
far enough to see the second reader. The regression test asserts the entity
comes back and that exactly two requests were made. Reproduced red before
the fix: `1 failed`, with the reviewer's exact message, "was reached as a next page but is not a collection response".

### Positive controls run in this round

| Fix | Control | Result |
|---|---|---|
| `pages` counter | put `len(seen) > 1` back in the branch | the regression test red (1 failed, 28 passed); 29 green on restore |
| literal-arity connectors pinned | revert `Sql.Database` to policy-first | `Sql.Database` red (1 failed, 5 passed); 6 green on restore |

### A note on the commit history of this round

The estate runs an autocommit sweep in this checkout. It captured the
round-5 files seconds before my own `git commit` (message amended onto it,
tree unchanged), and it captured the round-6 files while a positive control
had the broken `len(seen) > 1` branch temporarily back in place - so for a
few minutes HEAD held the regression next to the test that catches it. That
sweep was amended to the verified tree with the message above. Nothing was
pushed at any point.


## Round 7 - the round-6 fixes reviewed, and the static security scan

**Review of `eb2c7fd..bdbdbbe`** (the round-6 fixes, closeout, evidence),
exact `claude-opus-5` wrapper: **SHIP**. The reviewer independently confirmed the round-6 claims that
mattered: `seen` has no reader but the cycle check, `_arity` precedes
`check_db` at all six sites and the new test names all six, and the
credential stripping on both the 302 and next-link paths is intact. Three
LOWs, all taken:

| Finding | Disposition |
|---|---|
| the byte-cap message's `pages` was unverified - the only cap test used no redirect, so `len(seen)` and `pages` agreed | FIXED - the cap test now redirects on page one and asserts the literal `across 1 page(s)`; red under `len(seen)` |
| `test_doc_examples.py` still read the matches fixture bare, while the closeout said the reader was hardened | FIXED - the same wrapped read, naming the file and who maintains it |
| `/logs/` anchored stops ignoring a `logs/` written from a subdirectory, which is the incident class | TAKEN - round 6 said anchor, round 7 says not; on the merits (the tool writes relative to its own cwd; no `logs` package exists) it is unanchored again, with both arguments in the comment |

**Static security scan.** `semgrep` is not installed here and the first
pass said so. **Corrected 2026-09-06:** it became reachable later the same
day over MCP and was run - see round 9. The three passes below were written
before that and their `semgrep` rows are corrected in place. What is installed is ruff, whose `S` ruleset is the
flake8-bandit port, so it ran offline over `src/`: 45 hits.

| Rule | Hits | Disposition |
|---|---|---|
| S105 hardcoded password | 19 | noise - every one is a variable named `token` in the date-format parser (`"yyyy"`, `"MM"`, ...) |
| S101 assert | 18 | noise - every one narrows a value that the preceding lines guarantee (`is not None`, `isinstance`); none guards a security property, so `python -O` stripping them changes an error type, not an outcome |
| S311 non-cryptographic random | 4 | noise - `Number.Random` / `List.Random` are not cryptographic in M either |
| S603 subprocess | 1 | closed by inspection - argument list, no shell, binary resolved with a guard that refuses a `node` in the CWD, bounded by a deadline |
| S310 urlopen scheme | 1 | closed by inspection - `IOPolicy.check_net` refuses anything but `http`/`https` with a host, and the redirect handler re-checks every hop |
| S608 SQL built from strings | 2 | one false positive (`evaluate.py:997` has no SQL); **one real defect, fixed - below** |

### The real one: catalog names were quoted, not escaped

`Sql.Database` navigation built `SELECT * FROM "schema"."table"` from
`INFORMATION_SCHEMA` names by wrapping them in quotes and nothing else -
**present since before the audit baseline** (`c7ffc5f`, line 519). A name
carrying a `"` closes the identifier early, so `Wanted"."Other` reads a
different object than the query named, silently, and a name carrying `;`
runs a second statement - under the rights of the account pqtools connects
with, which need not be the account that was allowed to name the table.
Same family as finding 5's ODBC connection-string escaping.

Reproduced red with the fake driver recording the executed SQL:
`SELECT * FROM "dbo"."Wanted"."Other"`. Fixed with `_sql_identifier`, which
doubles the quote (SQL standard; what SQL Server reads under
`QUOTED_IDENTIFIER`, which ODBC turns on) and refuses a NUL. Green:
`SELECT * FROM "dbo"."Wanted"".""Other"`. The S608 hit on that line
remains, correctly - the rule flags construction, and construction with
escaped identifiers is what this is.

Targeted modules after the fix: 809 passed (SQL navigation/options/credentials, connector arity, OData paging, documented signatures, catalog, support matrix, llms.txt, the worked-example equality).

## Round 8 - the review of the round-7 fixes

`eb2c7fd..bdbdbbe` had already returned **SHIP**; this is the review of the
security-scan fix on top of it, `bdbdbbe..c433e33`. **Verdict: SHIP**, with
one MEDIUM and three LOWs. The MEDIUM was worth taking on its own merits.

The reviewer confirmed the escaping itself: `'"' + name.replace('"','""') +
'"'` is the correct SQL Server form under `QUOTED_IDENTIFIER` (ODBC turns it
on; with it off the result is a loud syntax error, not an injection), the
implicit string concatenation stays one positional argument, and the new
test is a genuine positive control.

| Severity | Finding | Status |
|---|---|---|
| MEDIUM | the NUL guard ran inside the navigation comprehension, so one unusable catalog name refused `Sql.Database(...)` itself and no table could be selected | FIXED - **mine, from the round-7 fix**; `_navigation_sql` defers the build per row |
| LOW | the NUL branch had no test | FIXED - selecting the NUL-named table refuses with the connector named |
| LOW | only the `"` breakout was covered, not the `;` second statement the docstring claims to close | FIXED - both are now parametrised cases |
| LOW | unanchored `logs/` also hides a nested `evidence/logs/` | NOT TAKEN - nothing wants one today; a negation can be added if that changes |
| LOW | working tree diverges from the index, so pytest on disk runs the unescaped SELECT | NOT A FINDING - it describes the gate wrapper's own worktree, which read-trees HEAD over a BASE checkout; this repo was clean throughout, verified before and after |

### The MEDIUM is the audit's own lesson, applied to my own fix

Finding 3 of the original audit was "SQL navigation eagerly reads every
table": selecting one table paid for all of them, and failed if any of them
failed. `DeferredTable` fixed that. Then my round-7 escaping put a *different*
eager failure back in the same comprehension - not a read this time, a
refusal, but with the identical all-or-nothing shape. The test that already
existed for the read case (`test_a_broken_unrelated_table_does_not_break_the
_wanted_one`) is exactly the property I broke, and it did not catch it
because it fakes an unreadable table, not an unnameable one.

Building the statement inside `_deferred_query`'s fetch closure is what the
row's `Data` being lazy always meant. Control: building it eagerly again
turns the new scoping test red and nothing else.


## Scope limits - what was NOT verified

Local completion is reported separately from live verification on purpose.
**Everything above was verified locally, against mock drivers, local HTTP
servers, temporary files and checked-in fixtures.** None of the following was
exercised, and no result here should be read as evidence about any of them:

- **No live database.** Every SQL test injects a mock `pyodbc` (or asserts the
  refusal before a driver is imported). The connection strings and timeouts
  are asserted as *built* and *passed*, not as accepted by SQL Server,
  PostgreSQL, MySQL or Oracle. `MultiSubnetFailover=Yes` and
  `ApplicationIntent=ReadOnly` are written into the string because the page
  says they should be; no cluster confirmed the failover behaviour.
- **No credentials were used or inspected.** Every credential in the tests is
  a dummy literal written for the test.
- **No Fabric tenant, no Windows PQTest, no native Excel, no Power BI
  refresh.** The container work was verified by rewriting fixtures and by
  read-only `pq list` / `pq eval` against the local samples; Excel itself has
  not opened a rewritten workbook on this machine, which the README already
  states and which remains true.
- **No live OData service.** Paging, cycles, the page ceiling, the byte
  ceiling and the credential-stripping are all verified against local HTTP
  servers on `127.0.0.1`.
- **One platform, one interpreter.** macOS on this machine. CI declares three
  operating systems and Python 3.11-3.13; nothing here speaks to the other
  two OSes or the other interpreter versions.

## Residual limitations, stated rather than closed

1. **The backup has a narrow non-atomic window.** `_container_backup` creates
   the sidecar with `O_CREAT|O_EXCL|O_NOFOLLOW` and fsyncs it, so the *create*
   is atomic, an existing backup is never clobbered, a symlink is never
   followed, and a failure raises before the container is touched. But if the
   process is `SIGKILL`ed mid-write, a short `.bak` can remain. No data is
   lost - the container is still untouched at that point, and the next run
   moves to `.bak.1` - so the consequence is a confusing stray file, not
   destruction. Closing it fully would mean writing to a temp file and
   `os.link`ing it into place, which trades the window for a dependency on
   hardlink support. Not done; recorded here as a deliberate choice.
2. **`CommandTimeout`/`ConnectionTimeout` are not implemented for
   PostgreSQL, MySQL or Oracle.** They refuse by name, which is compliant,
   but the capability differs from `Sql.Database` and `Odbc.*`. The three
   drivers express these differently enough that implementing them without a
   live server to test against would be guessing.
3. **`HierarchicalNavigation = true` is refused, not implemented.**
   Microsoft's page does not specify the shape of the schema-grouped table,
   and inventing one is the defect class this package's gates exist to catch.
4. **`Table.RemoveRowsWithErrors` and `Table.ReplaceErrorValues` are not
   implemented.** They refuse in the typed way and are recorded as genuine
   gaps in `tests/test_doc_examples.py`, not as documentation defects.
5. **Semantic compatibility is still unmeasured.** 141 of Microsoft's worked
   examples reproducing their documented output is the strongest evidence
   available here, and it is not a compatibility percentage. See
   `SUPPORT-MATRIX.md`.
6. **`semgrep` code scan ran clean; its supply-chain scan did not run.**
   semgrep 1.157.0 over all 25 modules: 0 findings, 0 errors. The
   supply-chain scan needs a running Semgrep daemon and is unavailable
   here, so dependency risk rests on `pip-audit` alone, which reports no
   known vulnerabilities. `gitleaks` over the branch range found nothing.
7. **`pq rename` remains narrow by design.** It refuses on most real Power
   Query, because a record literal or field access anywhere in the file stops
   it. The guard was documented, not loosened; narrowing it correctly needs
   binding-aware analysis of the parse tree and its own regression proof.

## Verification on the final tree

Sequential throughout - no `pytest -n auto`, one job at a time. Concurrency
was tried early and the machine's memory watchdog killed the waiters; the
runs below were re-done one at a time.

| Check | Command | Result |
|---|---|---|
| Full suite + coverage | `.venv/bin/python -m pytest -q -p no:randomly --cov=pqtools` | **3958 passed, 93%** (16m31s) |
| Lint | `ruff check src tests scripts` | clean |
| Format | `ruff format --check src tests scripts` | 90 files already formatted |
| Types | `mypy` (strict) | no issues, 25 source files |
| Bridge tests | `npm test` | 23/23 |
| Bundle drift | `npm run bundle` + `git diff --exit-code` | no drift |
| Build | `python -m build` | wheel + sdist |
| Package metadata | `twine check` | both PASSED |
| Wheel contents | `py.typed`, `_bridge.cjs`, `THIRD_PARTY_NOTICES.txt` | all present |
| Installed-wheel workflow | fresh venv, README quick-start end to end | format, check, eval, `--format csv`, `--write`, re-eval, public API - all correct |
| Container read path | `pq list` / `pq eval` on `.samples/` | 3 containers enumerated, 3 typed errors for files with no DataMashup part; md5 unchanged, nothing written |
| Secrets | `gitleaks detect --log-opts=c7ffc5f..HEAD` | no leaks |
| Dependencies | `pip-audit` | no known vulnerabilities |
| Static analysis | `semgrep` | **corrected 2026-09-06: RUN, clean** - 1.157.0, all 25 modules, 0 findings, 0 errors (round 9) |

Coverage rose from 91% to 93% across the work; the suite grew from 3746 to
3958 tests.

## Verification on the final tree, second pass

After rounds 4, 5 and 6 the tree is `86f2da3` (code at `07bf9ed`). Run
sequentially, `PQ_GATE_PYTEST_ARGS=""`, nothing else of mine on the machine.

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 3977 tests in 11m03s, 953 worked examples; `evidence/release-gate-2026-09-06-c7ffc5f..86f2da3.log` |
| Lint / format / types | ruff clean, format clean, mypy strict clean (inside the gate) |
| Documented coverage computed, not remembered | README/llms.txt already current across the sync (inside the gate) |
| No invented names; refusal positive control; real workbook query; 953 worked examples | all PASS (inside the gate) |
| Targeted modules after the round-6 fixes | 813 passed |
| Positive controls this pass | every fix controlled; each control turned exactly its own test red, and the one that did not (round 5, `Sql.Database`) is recorded as a wrong target - see rounds 4, 5, 6 |
| sdist contents | 107 files, 0 under `logs/` |
| Exact `claude-opus-5` review | round 4 on `c7ffc5f..ee04185` (full range) and round 6 on `ee04185..eb2c7fd` (delta) - together they cover `c7ffc5f..eb2c7fd`; the round-6 fixes (`07bf9ed`) are reviewed by no model pass, because the full-range rerun was killed twice for memory |

Not verified, unchanged from the first pass: live database, Fabric, Windows
PQTest, native Excel or Power BI refresh. (`semgrep` was listed here as not
installed; corrected in round 9 - it ran clean.) The
three `skipif(os.name == "nt")` guards added in round 5 were verified by
inspection against the repo's precedent, not by a Windows run.

## Verification on the final tree, third pass

Final tree ``bd95332``. Run sequentially, `PQ_GATE_PYTEST_ARGS=""`, nothing
else of mine on the machine.

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 3981 passed in 654.58s (0:10:54), 953 worked examples exact; `evidence/release-gate-2026-09-06-c7ffc5f..bd95332.log` |
| Targeted modules through rounds 7-8 | 809, then 106, then 23 - ruff, ruff format, mypy strict clean at every step |
| Positive controls, rounds 7-8 | catalog-identifier breakout red then green; byte-cap count red under `len(seen)` (4 reported for 3 read); eager SQL build red on the NUL-scoping test |
| Exact `claude-opus-5` reviews | round 4 `c7ffc5f..ee04185` (full range, FIX-FIRST) · round 6 `ee04185..eb2c7fd` (FIX-FIRST) · round 7 `eb2c7fd..bdbdbbe` (**SHIP**) · round 8 `bdbdbbe..c433e33` (**SHIP**, one MEDIUM taken). Together they cover every commit from baseline to `c433e33`. **``bd95332`` - the round-8 MEDIUM fix itself - carries no model review**: the CLI hit its session limit mid-round and the remaining budget went to the gate |
| Static security | gitleaks clean, pip-audit clean (first pass) · ruff bandit ruleset over `src/`: 45 hits, 44 recorded as noise, 1 real and fixed · `semgrep` **corrected 2026-09-06: RUN, clean** - 1.157.0, 25 modules, 0 findings, 0 errors (round 9); its supply-chain scan still unavailable, needs a daemon |

Not verified, unchanged across all three passes: live database, Fabric,
Windows PQTest, native Excel or Power BI refresh. The three
`skipif(os.name == "nt")` guards were reasoned from the repo's own
precedent, not run on Windows.

## Round 9 - the two verification gaps, closed

Rounds 7 and 8 both returned SHIP, so this round was not a review round. It
closed the two things the three passes above had recorded as open, and one
defect the second of them found.

### `semgrep` - was NOT RUN, now run and clean

The three passes all carried `semgrep` as "not installed on this machine".
That stopped being true: its MCP server became reachable on 2026-09-06.
Run in two batches over every module in `src/pqtools/`:

| Batch | Modules | Result |
|---|---|---|
| security-relevant | `_sources.py`, `cli.py`, `core.py`, `io.py`, `containers.py`, `evaluate.py`, `fabric.py` | 0 findings, 0 errors |
| remainder | 18 modules, all of `builtins/*` plus `catalog.py`, `pqtest.py` | 0 findings, 0 errors |

**25 modules, 0 findings, 0 errors**, semgrep 1.157.0. The rows above are
corrected in place rather than silently, so the record shows both what was
claimed and when it changed.

`semgrep_scan_supply_chain` still does not run - it requires an active
Semgrep daemon, which is not available here. Dependency risk therefore rests
on `pip-audit` alone. That is a narrower check, and it is the one gap this
round could not close.

### A swallow-site sweep against the product's own promise

The requirement says unsupported behaviour must raise a typed error and
**never silently return incomplete or incorrect data**. A `semgrep` pass is
generic; that clause is specific, so it was checked directly. An AST walk
over all of `src/pqtools/` enumerated every exception handler that returns
without raising:

| Shape | Count |
|---|---|
| bare `except:` | 0 |
| broad `except Exception` with no re-raise | 0 |
| handler that swallows and returns a value | 18 |

Seventeen of the eighteen are correct and most carry the reasoning already:
`try x` returning `{HasError: true}` is M's own semantics; the `math.inf` /
`math.nan` returns in `_number.py` are documented overflow results; the
`decode_escapes` fallback in `core.py:349` is a deliberate split between a
*name* (list it raw so one bad query name does not refuse the whole file)
and a *value* (strict, because passing it through would corrupt the result).

### The eighteenth: a reader thread that truncated output silently

`core._run_process_bounded` drains the child's stdout and stderr on two
threads. Each caught `(OSError, ValueError)` and returned, leaving a buffer
holding a *prefix* of the real output with nothing to say so. The function
then returned that prefix as a completed process.

Two of the three callers reject a prefix by luck: `_require_node` runs a
version regex over it, `_bridge` runs `json.loads`. The third does not.
`run_pqtest` -> `_run` -> `_run_bounded` decodes the bytes and **returns
them verbatim as the PQTest result**, with no structural check anywhere on
the path. On that path a short read is exactly the failure the requirement
names.

The fix follows the module's existing `_ProcessOutputLimit` idiom rather
than inventing a mechanism: a `_ProcessReadError` sentinel, the failure
recorded by the reader instead of dropped, raised after the threads join,
and mapped by each caller to its own typed error - `NodeError` for the two
Node paths, `AdapterError("PQTest output could not be read in full")` for
the adapter. It is raised after the `try/finally`, so a timeout still wins:
the abandoned readers there fail on the fds this function itself closed.

`_bridge` also stopped reporting a local read fault as `"Node bridge
returned invalid JSON"`, which blamed the child for the host's failure.

**Positive control.** With the raise site removed, the two tests that gate it
went red (`DID NOT RAISE AdapterError` on the adapter path - that is the
silent truncation, reproduced), and the two that do not depend on it stayed
green, as predicted:

| Test | Control |
|---|---|
| `test_process_read_failure_is_raised_not_returned_as_short_output` | red |
| `test_pqtest_refuses_a_truncated_read_rather_than_returning_it` | red |
| `test_node_bridge_names_a_read_failure_rather_than_blaming_the_json` | green - drives the mapping directly, not the raise site |
| `test_node_version_check_refuses_a_truncated_read` | green - records that the version regex rejects a prefix on its own |

The last two are honest about what they do and do not prove; keeping them
green under the control is the point, not a weakness.

This defect predates the baseline `c7ffc5f`. It was not in the original
seven findings and no review round raised it - it was found by checking the
requirement's own wording against the code rather than by reviewing a diff.

### The sweep's second finding: `Value.FromText` returned a wrong type in silence

Verifying the "seventeen of eighteen are correct" claim above meant reading
all seventeen rather than asserting it. Sixteen held. The seventeenth,
`_number.py`'s `Value.FromText`, did not:

```
Value.FromText("2024-12-24T14:33:20")  ->  str  '2024-12-24T14:33:20'
Value.FromText("2024-12-24")           ->  str  '2024-12-24'
Value.FromText("14:33:20")             ->  str  '14:33:20'
```

Microsoft's page, fetched for this round rather than recalled: *"This
function takes a text value and returns a value of type `number`, `logical`,
`null`, `datetime`, `duration`, or `text`"*, and its Example 4 output is
`#datetime(2024, 12, 24, 14, 33, 20)`. So real M returns a datetime for that
text and this returned the string - a wrong type, no error, and nothing in
the result to distinguish it from text that really is text.

The gap was known. A prior round left `test_value_from_text_datetime_-
detection_is_a_disclosed_gap`, pinning the string return and inviting "a
future session that wires in datetime detection" to update it. Two things
were wrong with that disposition:

- **It was disclosed in a source comment and a test name, not to a user.**
  Neither `SUPPORT-MATRIX.md` - the authoritative document - nor `llms.txt`
  mentioned it. A caller had no way to learn it.
- **Its stated reason had expired.** The reason given was "that parsing
  lives in `_datetime.py`, a file this task does not own" - a scoping
  constraint of that round, not a fact about M.

**Detection was still not implemented, deliberately.** The page publishes no
invariant-culture format for the datetime branch; its only datetime example
passes `"de-DE"`. Writing that format table from memory is the invention
this repo forbids. So the fix is the requirement's own remedy - an explicit
typed refusal - not a guess:

| Input | Before | After |
|---|---|---|
| `"2024-12-24"`, `"14:33:20"`, `"2024-12-24T14:33:20"` | the string, silently | `UnsupportedError`, naming `Date.FromText` / `Time.FromText` / `DateTime.FromText` |
| `"12345.6789"`, `"25.4%"`, `"true"`, `""`, `"hello world"`, `"Dec 24"` | unchanged | unchanged |
| `"€1,190", "fr-FR"` and `"24 Dez 2024 14:33:20", "de-DE"` | already refused | already refused |

Detection keys on ISO 8601 alone (`datetime`/`date`/`time.fromisoformat`),
which is what .NET's invariant culture parses - the narrowest defensible
reading of "recognisably a date" without inventing the format table. Numbers
reach `_parse_numeric_literal` first, so `20241224` stays the number
20241224 and never becomes a date.

**The `duration` branch was going to be left open, and then was not.** The
first cut of this fix refused only ISO dates and times, and recorded duration
as a live gap - M's duration text is not ISO 8601, so detecting it appeared
to need a format invented for the purpose. It did not. `Duration.FromText`
is implemented in this package *from its own documented grammar*
(`_datetime._DURATION_TEXT_RE`, with the two documented alternatives written
out above it). The grammar was already owned, grounded and tested; the gap
was only that this function did not ask it.

So the refusal covers duration too, importing that pattern rather than
restating it - a second copy of a documented grammar is a second thing that
can drift. Text the grammar rejects is still text, and correctly so:
`Value.FromText("P1D")` is `"P1D"` here because `Duration.FromText("P1D")`
is an error in this package too. ISO 8601 duration syntax is not M's.

The lesson is the round's own: a gap recorded as "cannot be done without
inventing" was worth re-testing before being written down as a limitation.

**Positive control.** With the refusal removed, the three parametrised cases
went red and both independent pins - plain text still text, duration still a
gap - stayed green, confirming they test something else.

The old pinned test was replaced, not deleted, and its replacement records
what it used to assert and why that changed.

### `M_ADAPTER_ERROR` was documented as something it is not

`llms.txt` told agents this code means "The Fabric transport returned
something malformed, empty, or over its 10 MiB limit" and to treat it as
remote: *"Report it; nothing local is wrong."*

That was already wrong before this round. `AdapterError` is also the whole
PQTest adapter, including refusals that are entirely local - "PQTest path
must name a user-installed regular `.exe`", "PQTest adapter is supported on
Windows only", a wrong version. An agent following that row would report a
local misconfiguration upstream as a remote fault. The row now names both
adapters and tells the reader how to tell them apart. This is the code being
right and the documentation being wrong, which is the only case where
editing the documentation is the fix.

### The same swallow, in the other direction

The sweep listed `core.py:165` - `except BrokenPipeError: pass` in the thread
that writes the child's stdin - and the first pass over the list dismissed it
as "the write path". It is the reader defect mirrored:

```
_run_process_bounded([python, "-c", "pass"], b"x" * 4MiB, 10)
    before ->  CompletedProcess(returncode=0, stdout=b"")
    after  ->  _ProcessWriteError, cause BrokenPipeError [Errno 32]
```

No mock, no patch: the child exits before the payload can be delivered, the
write hits a closed pipe, and the call used to report a clean run of a child
that had received part of its input.

The raise is guarded by `process.returncode == 0`. A child that exits non-zero
already has its own account of what went wrong, and the broken pipe is
usually a consequence of that exit; raising would replace the child's
diagnosis with ours. Pinned by
`test_a_failing_child_keeps_its_own_diagnosis_over_the_broken_pipe`.

**`stdin.close()` is deliberately NOT recorded, and the attempt to record it
was a regression.** `close()` is where a `BufferedWriter` flushes, so on the
face of it an error there is the tail of the payload never arriving - the
same truncation, suppressed by `contextlib.suppress(OSError)`. Recording it
failed a legitimate parse in the full suite, and that failure is what finally
explained the rest of this round.

### The BRIDGE_FAILURE, diagnosed - and the first diagnosis was wrong

While adding the reader tests, a pre-existing test began failing with
`NodeError: BRIDGE_FAILURE` - first in a three-file subset, then in the full
suite, on `tests/test_corpus.py::test_vendor_fixture_checksums_and_parser_-
coverage`. It only ever failed after `tests/test_core.py`, it passed alone,
and every instrumentation attempt made it vanish: a Python-side wrapper on
`_run_process_bounded`, a node-side debug bundle logging every stdin payload
(216 invocations, all intact, `main()` never threw), even one extra list
allocation. The prefix of the suite up to that file passed on its own; the
same prefix inside a full collection (`pytest tests/ -x`) failed in 2m31s.
That was the reproducer that made the rest possible.

**The first diagnosis, recorded here and superseded.** Once `stdin.close()`
errors were surfaced the failure showed `OSError: [Errno 9] Bad file
descriptor`, and this closeout concluded the writer thread was the hazard:
the timeout path closed stdin by integer, the abandoned writer later closed
the same integer, by then recycled. The fix removed `process.stdin` from the
by-number close, with the reasoning that this was safe for the readers
"because the reader threads never close their streams." **That reasoning was
inverted, and the test kept failing.** Never closing the object is what
guarantees its only close is the one nobody controls.

**The actual mechanism.** A second, read-only pass traced it and then built
an isolated `os.pipe()` model with no subprocess in it to prove each step:

1. On timeout the parent closed the two reader pipes with
   `os.close(stream.fileno())` - by integer. That unblocks a reader a
   grandchild has pinned, which is why it was written that way.
2. But it bypasses the `BufferedReader`. The object's `closed` flag stays
   `False`; only the kernel knows the number is gone.
3. The abandoned reader thread, now unblocked, returns. Its frame was the
   last reference to the `BufferedReader`, so CPython finalises it at once,
   and `BufferedReader.__del__` closes its integer **a second time**.
4. By then the OS has handed that integer to the next pipe opened - the
   stdin of `test_corpus`'s next `parse()`. That child reads a document cut
   off mid-stream, `JSON.parse` throws, `_bridge.cjs` catches it and emits
   the generic `BRIDGE_FAILURE`.

The model's output, verbatim: `br.closed AFTER os.close(fd) bypass: False` ·
`recycled? new read fd == freed fd: True` · `victim fd INVALIDATED by the
leaked object finalizer: OSError(9, 'Bad file descriptor')`. It also showed
why every instrumentation hid it - any change to how long the abandoned
thread takes to die moves the finaliser relative to the next `pipe()`.

**The fix.** Close the readers through their raw `FileIO` instead of by
integer: `getattr(stream, "raw", stream).close()`. The model confirmed the
three properties this needs: it returns in microseconds (it does not take the
buffer lock the stuck `read()` holds, which is what makes calling
`BufferedReader.close()` from another thread hang), it unblocks the reader
exactly as `os.close` did, and it flips `closed` to `True` so the finaliser
is a no-op whatever the integer belongs to later. stdin stays with the
writer thread, which closes its own stream - the earlier change was right for
the wrong reason and is kept.

A second, smaller hole came out of the same pass. The writer caught only
`BrokenPipeError`. EBADF - a descriptor closed under the thread - is an
`OSError` but not a `BrokenPipeError`, so it escaped to
`threading.excepthook`, was printed to nowhere, and the call returned a clean
`CompletedProcess(returncode=0)` for a child that had read nothing. Widened
to `except OSError`.

**Controls, both deterministic:**

| Control | What was reintroduced | Red | Stayed green |
|---|---|---|---|
| A | `os.close(stream.fileno())` on the readers | the AST source test; `test_abandoned_reader_stream_is_marked_closed_so_its_finaliser_is_inert`, which captures the real `BufferedReader` and asserts `closed` after the timeout | the EBADF test - independent, as it should be |
| B | `except BrokenPipeError` only | `test_ebadf_on_write_is_recorded_not_lost_to_the_thread` | the two reader tests |

The source test parses `_run_process_bounded`'s AST for `os.close` and
`.fileno` calls rather than grepping, because the comment explaining the
defect quotes the forbidden call and a text search failed on its own
explanation. The earlier runtime control for the stdin change, which could
not go red, stays discarded.

**Result.** `pytest tests/ -x` on the fixed tree did not stop: 3999 passed,
0 failed, 10m21s. The gate on the same tree is recorded below.

**On the two agents.** This was the first round to fan out. One agent's
node-side instrumentation returned only the negative result above; the other,
read-only, produced the mechanism and the model. The estate autocommit bot
swept the first agent's temporary debug bundle into HEAD as `92516a1` mid-
investigation; it was amended out (`4b6144d`, bundle byte-identical to
`de6e217`) and the memory file for that trap now says so.

## Verification on the final tree, fourth pass (round 9)

Run sequentially, `PQ_GATE_PYTEST_ARGS=""`, nothing else of mine on the
machine, on the tree that carries every round-9 change above.

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 3999 passed in 621.75s (0:10:21), 953 worked examples exact in 211.34s; `evidence/release-gate-2026-09-06-round9.log` |
| Gate 4, for the record | 7 of 8 - the corpus test alone, before the reader-stream fix; `scratchpad` only, superseded |
| Plain full suite, `-x` | 3999 passed, 0 failed - the reproducer that used to stop at 700 |
| Lint, format, types | ruff, ruff format, mypy strict clean at every step |
| Positive controls, round 9 | reader raise red/green · writer raise red/green (real EPIPE, no mock) · `Value.FromText` refusal 3 red/green, later 6 · reader integer-close (control A) red on the AST test and the finaliser test · `BrokenPipeError`-only (control B) red on the EBADF test · one runtime control that could not go red was discarded and is recorded as such |
| `semgrep` | 1.157.0, 25 modules, 0 findings, 0 errors; supply-chain scan unavailable (needs a daemon) |
| Exact `claude-opus-5` review of round 9's code | **not yet run at the time of this table** - see the review entry that follows it, or its absence |

Not verified, unchanged across all four passes: live database, Fabric,
Windows PQTest, native Excel or Power BI refresh. The `skipif(os.name ==
"nt")` guards were reasoned from the repo's precedent, not run on Windows.

**Left undone, on purpose:** `_bridge.cjs` still swallows its own exception
in `main().catch(() => emit({ error: "BRIDGE_FAILURE" }))`. That is what made
this take a whole round to find - the child knew exactly what was wrong with
its input and threw the message away. Carrying `error.message` through would
make the next occurrence name itself.

It is not done here because the shipped file is a 2.5 MB esbuild bundle of
`js/bridge.js`, and regenerating it puts a 2.5 MB artifact in a diff whose
point is four small source changes. The groundwork is done and recorded:
esbuild 0.28.2 rebuilds the committed bundle **byte-identically** from the
current source (verified against `src/pqtools/_bridge.cjs` before this was
written), so whoever picks it up can change `js/bridge.js`, run
`npm run bundle`, and trust the rest of the diff. The root cause is fixed;
this is a diagnosability improvement, and it should ride in its own commit.
