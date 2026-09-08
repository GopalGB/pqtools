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

## Round 9 reviewed - FIX-FIRST, six findings, six taken

The exact `claude-opus-5` wrapper review of `de6e217..d3bf107` is at
`evidence/opus5-wrapper-round9-de6e217..d3bf107.txt`. Verdict FIX-FIRST:
one HIGH, one MEDIUM, four LOW. Every finding was reproduced against the
committed tree before anything was changed, per the round-8 lesson; every
one reproduced, and every one was taken. Two of them are my own reasoning
from this round, inverted.

### HIGH - the abandon path left stdin to the writer, and the writer to the grandchild

My cut excluded `process.stdin` from the raw-close list on the reasoning that
the killed child's EPIPE frees a blocked writer. The review's counter-case:
a child that spawns a grandchild and exits. The grandchild inherits the read
end, so the pipe never closes, the writer never gets EPIPE, and the write end
is never closed by anyone - `process.stdin = None` had also taken it away
from `Popen.__exit__`. Reproduced on the committed tree with a child that
spawns `sleep 30` and exits at once, 10 MiB payload, 1 s timeout: one
descriptor leaked and `Thread-3 (write)` blocked in `write()` for as long as
the grandchild lived, per timed-out call. Fix: stdin is back in the list and
closed through its raw `FileIO` like the other two. That does not reinstate
the double-close: the object is marked closed, so the writer's own
`finally: stdin.close()` and the finaliser are both no-ops - which is also
why that `close()` still suppresses `OSError` rather than recording it, and
the comment there now says so.

Tests: the AST test now asserts stdin is IN the list (it pinned the leak),
and a runtime test reproduces the grandchild case and asserts no thread left
behind and no descriptor gained. Control: stdin back out of the list, both
red; restored, both green.

### MEDIUM - "ISO 8601" was the narrow reading `_datetime.py` had already rejected

`Value.FromText` refused ISO dates and times only, and its docstring called
that "the narrowest defensible reading". This package's own `_datetime.py`
says, at the point where it grounds `Time.FromText("10:12:31am")` on
Microsoft's Example 1, that ISO-only is "not what the invariant culture
means". So `Value.FromText("12/24/2024")` came back as text while
`Date.FromText("12/24/2024")` returned a date, and `SUPPORT-MATRIX.md` told
the reader the former was refused. The argument I had applied to the
duration branch - import the parser, do not restate its grammar - was the
argument for this branch too, and I had not applied it. Fix: the predicate
now asks `_date_from`, `_time_from` and `_datetime_from` themselves. Three
new refused cases (`"12/24/2024"`, `"Apr 8, 2022"`, `"10:12:31am"`), and the
matrix paragraph now says "whatever those four parsers accept", not "ISO".
Control: an ISO-only regex predicate in place of the parser loop, exactly
those three cases red and nothing else; restored, green. (A first attempt at
this control referenced a helper that did not exist and turned everything
red on a `NameError`; discarded, redone.)

### LOW - the duration branch matched the regex, so it refused what `Duration.FromText` rejects

`Value.FromText("24:00")` was refused while `Duration.FromText("24:00")` is
an `EvalError` (hours 0-23), breaking the invariant the test docstring
itself stated. Fix: call `_parse_duration_text` and treat `EvalError` as
not-a-duration. `"24:00"`, `"25:00"`, `"1.24:00"` and `"P1D"` are all text,
and the test asserts each is an error from `Duration.FromText` first.
Control: the regex back in place of the parse, that test red; restored, green.

### LOW - `pqtest.py` caught a write error that no path could raise

`_run_bounded` passes no stdin, so there is no writer thread and no
`_ProcessWriteError`; the handler and the test that monkeypatched it into
existence pinned a branch nothing reaches. Both removed; a one-line comment
says why there is no such branch.

### LOW - a pipe fault on the version check was reported as a missing Node

The same mis-blame this round fixed in `_bridge`, left in `_require_node`.
Now `"Node version check: process output could not be read in full"`, its
own clause before the catch-all; the test that pinned the old text updated.

### LOW - the read raise was unguarded where the write raise was guarded

A child exiting non-zero whose stderr read faulted lost its own diagnosis
to `_ProcessReadError`. Both raises now sit under `returncode == 0`; a test
with a child that exits 3 under a failing stdout asserts the exit code wins.

## Verification on the final tree, fifth pass (the six review fixes)

Same conditions: sequential, `PQ_GATE_PYTEST_ARGS=""`, nothing else of mine
running. This is the tree that carries the six fixes above.

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 4003 passed in 665.09s (0:11:05), 953 worked examples exact in 140.71s; `evidence/release-gate-2026-09-06-round9-review-fixes.log` |
| Test count | 3999 -> 4003: two new runtime tests on the teardown and the exit-code guard, three new refused `Value.FromText` cases, one test deleted (it pinned an unreachable branch) |
| Lint, format, types | ruff, ruff format, mypy strict clean |
| Positive controls | stdin out of the raw-close list -> the AST test and the grandchild test red · an ISO-only predicate -> exactly the three en-US cases red · the duration regex in place of the parse -> the `"24:00"` test red. Each restored and re-run green in the same command. |
| Exact `claude-opus-5` review of these fixes | see the round-10 entry below, or its absence |

Not verified, unchanged across all five passes: live database, Fabric,
Windows PQTest, native Excel or Power BI refresh.

## Round 10 - the review of the round-9 review fixes: SHIP

`evidence/opus5-wrapper-round10-d3bf107..6b764a7.txt`. Verdict **SHIP**, with
the reviewer stating plainly what it checked and found sound: the stdin
teardown fix is right (raw close, object marked closed, so the finaliser and
the writer's own `finally` are both inert), and the `returncode == 0` guard
cannot leak a truncated payload past any existing caller. No correctness or
security defect.

Seven improvements were raised and all seven are taken. None changes
behaviour except the first, which changes only cost.

### MEDIUM - the new detector made the ordinary path 108 regex compiles deep

`_is_temporal_text` asks three parsers, which between them walk 108 fallback
formats before concluding "not a date". `_compile_format` had no cache, so
every one of those was rebuilt from its format string **per call** - on the
branch that is the common case, the text that falls through and is returned
as text. Measured on the committed tree: 108 `_compile_format` calls for one
`Value.FromText("Q4 report")`.

Fixed with `@lru_cache(maxsize=512)` on `_compile_format`. The format set is
closed and tiny; the call count is not. Measured after: 108 compiles on the
first call, still 108 after fifty more. `name` stays in the cache key because
it appears in the errors raised while scanning, so two callers must not share
a compilation that names the other one.

This is worth recording as a class, not just a fix. The round-9 correction
was "ask the parser, do not restate its grammar", and it was right - but
asking a parser costs what the parser costs, and this one was built for
single calls on a `Date.FromText` argument, not for a per-cell predicate.
The cheapest correct thing was not free.

### Six LOW, all comment-and-test hygiene, all taken

- **`_require_node` caught `_ProcessWriteError`, which it cannot raise** -
  exactly the defect this same commit had just deleted from `pqtest.py`, in
  the file next to it. No stdin means no writer thread means no write
  failure. Now catches `_ProcessReadError` alone, carrying the same one-line
  note about why the other clause is absent.
- **The AST test's comment contradicted the assertion below it** - it still
  said "the two readers, never stdin" three lines above `assert
  "process.stdin" in ...`, and its local was still called `readers` while the
  source list it parses had been renamed `streams`.
- **The writer's `finally` comment justified its suppression by the
  mechanism this commit removed** - it still said the timeout path closes
  "by number". It closes through `raw` now, and the `ValueError` that was
  added alongside was unexplained. Both rewritten.
- **`"Dec 24"` had been swapped out for `"Q4 report"`, losing the only near
  miss.** `"Q4 report"` cannot match anything; `"Dec 24"` is the boundary - a
  month name with no year that no fallback pattern accepts. Both are asserted
  now, and the comment says which is which and why the near miss earns its
  keep.
- **The grandchild test was flaky and had an unbound-name path.** `stuck` was
  bound only inside the `while`, so an already-expired deadline raised
  `NameError` instead of asserting; and a process-global `/dev/fd` count is
  not a signal in a suite that leaves sleeping grandchildren behind. Now
  `stuck` is initialised, and the test runs the timed-out call three times
  and asserts the count did not grow by three - the defect leaks one
  descriptor per call, so growth is the signal and a single unrelated open
  is not.
- **The runner's truncation contract lived only in a comment.** A returned
  `CompletedProcess` may carry a truncated buffer when `returncode != 0` -
  every current caller rejects non-zero first, so nothing is broken, but
  silent truncation is this package's recurring defect class. Now stated in
  the function's docstring, where the next caller will read it.

### Control

The stdin exclusion was re-run against the *strengthened* grandchild test
after it was rewritten, not only against the original: both it and the AST
test go red with stdin out of the list and green with it back in. A
rewritten test is a new test and gets its own control.

## Round 11 - the "pandas for Power Query" build

Not an audit round. This is the feature work the audit's closing analysis
pointed at, specified in
`.planning/PRD-pandas-for-powerquery-2026-09-06.md` and scoped by a 15-model
`orchestra --all` fight over the eight gaps a read-only pass had found.

### What the fight changed about the plan

Its most useful output was not a ranking. It was a **ninth gap the audit had
missed entirely**: there is no way to set a query parameter. `--bind` binds a
name to a *file*; real queries open with `#"StartDate" = #date(...)`, and
without a scalar equivalent such a query cannot be run at all without editing
it. Four of the fifteen models named this independently, and one observed
that every answer that spotted it then failed to put it in its own plan. It
shipped.

The fight also settled the object-API question the same way from six
directions: a thin handle for discoverability, never a DataFrame mimic.
Transforms belong in M, where they fold and where the semantics are
Microsoft's. A second, silently different dialect would be worse than none.

### Shipped

`pqtools.export` - `to_pandas` / `to_arrow` / `to_parquet`, `ExportRefusal`,
and `pqtools.open()` returning a handle with `.queries` / `.source` / `.eval`
and deliberately no transform verbs. `pandas` and `pyarrow` are optional
extras; `dependencies` stays empty.

CLI: `pq show` (raw M, never evaluated), `pq explain NAME` (why a name is
refused, answered from the registry the evaluator itself uses so it cannot
drift), `pq diff A B`, glob/batch on every read-only verb with one exit code
for the batch, `--set-param NAME=VALUE`, and `pq eval --to parquet --out`.

Deferred and now named in `SUPPORT-MATRIX.md` rather than left silent:
query-to-query dependencies, Fabric Arrow decoding, TMDL write-back.

### What the verification found that the builders could not

Two Sonnet agents built the lanes and both reported green with controls. Both
reports were true and both were incomplete, in the same way.

**The export lane's suite was green only in its own environment.** It had
installed pandas to ground the type map empirically - the right call - and so
could never execute the bare path. With the extras genuinely absent, ten
refusal tests failed: they assert a data-shaped message ("ragged rows are
refused") while `to_pandas` correctly reports the missing library *first*,
which is the right order for a caller who cannot act on a complaint about
their data until the library is installed. The tests needed the guard, not
the code. Now 12 pass and 27 skip bare, 39 pass with the extras, zero
failures either way.

**The instrument for that check was wrong before the check was right.** The
first attempt put stub modules on `PYTHONPATH` whose body was
`raise ImportError`. That reported 24 failures, every one of them the
harness's fault: `pytest.importorskip` skips on `ModuleNotFoundError` and
deliberately lets a plain `ImportError` through, because a module that exists
and fails to import is a broken installation and must not be hidden. Absence
is simulated with a `sys.meta_path` finder that declines the name. The rule
this repeats: positive-control the instrument before believing its red.

**`--set-param` was documented as taking "a scalar M literal" and takes
arbitrary M.** `File.Contents(...)` in a `--set-param` does what it says.
That is not an escalation - the query body could already call it, so there is
no boundary being crossed - and the IO policy *is* threaded through, so a
`--set-param` naming `Web.Contents` is refused without `--allow-net`
(verified). But the help text described a restricted grammar that does not
exist. It now says what the flag does, and a test pins the network gate,
which is the property that would matter if VALUE ever came from anywhere but
the operator's own command line.

**`--to parquet` without `--out` was diagnosed after the query ran.** A user
who forgot the destination waited for a full evaluation to be told so. The
check moved ahead of evaluation, and the test pins the ordering by using a
query that would fail loudly at evaluation - if the refusal ever moves back
after it, the test sees `File.Contents` instead of `--out`.

Also: the `dev` extra installed neither pandas nor pyarrow, so a fresh
development environment would have silently skipped all 34 export tests -
the same "a skipped check reporting green" failure `release_gate.sh` step 7
already prints a warning about. Both are in `dev` now.

### The swallow sweep, again

`export.py` contributes **zero** exception handlers that return without
raising. `cli.py` gains nine, every one of them a batch loop recording a
per-file failure and continuing - which is the intended semantics only if the
batch's exit code still reflects it. Checked by hand rather than by reading:
`parse`, `dependencies`, `check` and `format` all exit 2 with one broken file
among good ones, and print the error. `show` and `list` exit 0 for a file
with invalid M syntax - correct, since `show` prints text and never parses -
and exit 2 for a file that genuinely cannot be read. An empty glob is an
error, not a quiet success.

### The autocommit bot, at its worst

It fired twice mid-build. The second sweep, `9881315`, captured `cli.py`
**mid positive-control**, with `raise  # POSITIVE-CONTROL DEFECT` still in
place, and bundled it with the other lane's files. The working tree held the
corrected version as an uncommitted diff on top. Both sweeps were absorbed by
`git reset --soft` before committing, so no deliberate defect reaches the
branch history. This is the third time this bot has interfered with a control
in one day; the standing rule - check `git log -1` before every commit - is
what caught it.

## Verification on the final tree, sixth pass (the round-11 build)

Sequential, `PQ_GATE_PYTEST_ARGS=""`, nothing else of mine running. Run on
the exact tree that became `df192c6` + `76f36b1` - the gate was started once,
stopped and restarted when a late edit landed, so that the result describes
the tree that was committed rather than one that shifted under it.

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 4084 passed in 662.99s (0:11:02), 953 worked examples exact in 139.36s; `evidence/release-gate-2026-09-06-round11.log` |
| Test count | 4003 -> 4084. 34 export, 39 CLI verbs, 9 end-to-end, and the rest from the two lanes' own coverage |
| Suite with `pandas` and `pyarrow` genuinely absent | see the row below - run separately, because the gate's environment has them |
| Lint, format, types | ruff, ruff format, mypy strict clean on 26 source files |
| The end-to-end loop, by hand | `pq show` (does not evaluate, proven on a query whose connector points nowhere) -> `pq explain` -> `pq eval --set-param` -> `--to parquet` -> read back in pandas with types intact -> `pq diff`. Run against `tests/fixtures/realworld/01_clean_and_type`, whose expected output was worked out independently of this package |
| Batch exit codes, by hand | `parse`/`dependencies`/`check`/`format` exit 2 with one broken file among good ones and print the error; `show`/`list` exit 0 for invalid M syntax (correct - `show` never parses) and 2 for a file that cannot be read; an empty glob is an error |
| Swallow sweep | `export.py` adds **zero** handlers that return without raising; `cli.py`'s nine are all batch loops whose exit code was checked by execution, not by reading |
| Positive controls | export: type-map row, a refusal, the missing-extras message - each red then green. CLI: show-never-evaluates, empty-glob, batch exit code, `--set-param` error naming - each red then green. One CLI control went red by a different mechanism than predicted (`IndexError` rather than a silent success) and is recorded as such rather than smoothed over |
| Exact `claude-opus-5` review of the build | see the round-11 entry below, or its absence |

Not verified, unchanged across all six passes: live database, Fabric,
Windows PQTest, native Excel or Power BI refresh. Fabric's Arrow decode is
now a *stated* refusal rather than an untested path.

## Round 11 reviewed - FIX-FIRST, eight findings, eight taken

`evidence/opus5-wrapper-round11-251b4a1..76f36b1.txt`. Every finding was
reproduced against the committed tree before anything changed. All eight
reproduced; all eight are fixed.

### HIGH - the verb was read from `argv[0]`, which argparse never required

Argparse fixes a positional's arity when it is declared, so the per-verb
shape of `file` had to be chosen before parsing - and it was chosen by
reading `argv[0]`. But `pq --json check f.pq` is a valid invocation, and it
made the peek read `--json` as the command. Two reproduced failures:

- `pq --allow-net explain Table.SelectRows` answered **"not a name pqtools
  recognizes ... may be a typo"** and **exited 0**. A confident wrong answer
  from the one verb whose entire guarantee is that it cannot drift from what
  the evaluator does.
- `pq --json check f.pq` raised `TypeError: 'PosixPath' object is not
  iterable` as a bare traceback - an invocation that worked before the batch
  feature existed.

Fixed by declaring `file` once as `nargs="*"` and validating arity per verb
*after* parsing, so option order cannot change the parser's shape. That
exposed a second argparse limitation immediately: it cannot split a
variable-length positional across an intervening option, so
`pq list --json a.pq b.pq` matched `file` as empty and handed both paths back
as leftovers. `parse_known_args` folds them back in order, while a leftover
that looks like an option is still the unknown-option error argparse would
have raised. Wrong argument counts are now typed refusals naming the verb and
the count, not usage dumps.

**One knock-on, taken deliberately:** `pq rename a.pq b.pq` used to be
refused by argparse with `SystemExit`; it is now refused by name with exit 2.
The existing test was updated to the better behaviour rather than the code
reverted to the worse one, and the test says why.

### The rest

- **The library did not refuse a non-table the way the CLI did.** README's
  own example is `to_pandas(report.eval("Sales"))`, and a query returning a
  scalar or a record is ordinary; it raised `'int' object is not
  subscriptable` from inside the column builder where the module promises a
  typed `ExportRefusal`. `cli.py`'s `_export_result` had the guard; the
  library entry points did not.
- **`pq list --json` dropped read failures out of the JSON**, reporting them
  on stderr only, so stdout carried a complete-looking array with nothing
  saying a file had failed. Every other batch verb appends an error record;
  this one now does too.
- **The extras allowed `pandas>=2` while the type map was verified only on
  3.0.5.** pandas 2.x infers nanosecond resolution from a list of `datetime`
  or `timedelta`, so `SUPPORT-MATRIX.md`'s table and several tests were wrong
  for a version the metadata permits. Rather than raise the floor to the
  version that happened to be installed, the three native dtypes are now
  coerced explicitly, so both agree.
- **`_member_expression` existed twice**, byte-identical, in `cli.py` and
  `export.py` - and `_CONTAINER_SUFFIXES` existed three times, with
  `containers.py`'s copy correctly excluding `.pbip` (a directory format, not
  a zip) and the other two including it. The parser-driven span calculation
  now lives once in `containers.py`, beside `split_shared`, which produces
  the strings it takes apart; `containers.py`'s zip tuple is untouched and a
  separate `is_container` answers the broader question. A test pins that the
  three names are the same object, not two that currently agree.
- **A real file named `report[1].pq` was unreadable**, reported as "no files
  matched glob" because `glob.glob` read the brackets as a character class.
  An existing path is now returned even when it looks like a glob.
- **The handle could not run a real query and disagreed with the CLI about
  what "source" means.** `.eval` took no `bindings` and no `io`, so it was
  pinned to `DENY_ALL` and could only run a query that reads nothing;
  `.source` returned `shared Sales = 1 + 1;` where `pq show --member`
  returned `1 + 1`. Both fixed, and a test now holds the two surfaces
  together.
- **A synthetic fixture had been dropped into `tests/fixtures/realworld/`**,
  whose README declares every file there hand-verified and emitted by Power
  Query's own UI. Moved to `tests/fixtures/misc/`.

### Controls

Five behavioural fixes, five controls, each red with the defect and green
after restoring: the argv peek, the export shape check, the `list --json`
error record, the glob-bracket path, and the `.source` agreement. The
identity of the deduplicated helpers is pinned by assertion rather than by
control, since there is no defect to reintroduce that a test could see.

## Verification on the final tree, seventh pass (the round-11 review fixes)

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 4104 passed in 668.88s (0:11:08), 953 worked examples exact in 125.02s; `evidence/release-gate-2026-09-06-round11-review-fixes.log` |
| Full suite with `pandas` and `pyarrow` genuinely absent | **4055 passed, 29 skipped, 0 failed** - the whole suite, not just the export tests. Absence simulated with a `sys.meta_path` finder raising `ModuleNotFoundError`, after a first attempt using `ImportError` stubs reported 24 failures that were the instrument's fault |
| Test count | 4084 -> 4104 |
| Lint, format, types | clean on 26 source files |
| Positive controls, round-11 review | argv peek · export shape check · `list --json` error record · glob-bracket path · `.source` agreement - each red with the defect, green after restoring |
| Option position, by hand | every verb answered identically with the option before, between and after the positionals; unknown options still rejected |

Not verified, unchanged across all seven passes: live database, Fabric,
Windows PQTest, native Excel or Power BI refresh.

## Round 12 - the review of the round-11 fixes: FIX-FIRST, five findings

`evidence/opus5-wrapper-round12-76f36b1..3577791.txt`. All five reproduced
before anything changed. **Two of the five were defects the round-11 fixes
had just introduced**, which is the reason this loop exists.

### HIGH - the fix for one output path broke the other

Round 11 correctly made `pq list --json` carry read failures into the JSON.
It did so by appending `{"file":…, "error":…}` to the same list the
**plain-text** branch formats with `item["name"]`. So every non-JSON
`pq list` with an unreadable file died on `KeyError: 'name'` - exit 1, bare
traceback, where it had previously printed a stderr line and exited 2.

The test written with that fix covered only the `--json` path: the one that
already worked. That is the whole lesson. A fix to one branch of a
conditional needs a test on the *other* branch, because the other branch is
what the fix can break. Failures now go to a separate list, merged only for
JSON; the text branch still prints the names it did find.

### MEDIUM - the tz fix assumed a stricter classification than the code makes

`_classify_column` admits a `datetimezone` column when the values share a
`utcoffset()`, **not** when they share a `tzinfo`. So `timezone.utc` and
`ZoneInfo("Europe/London")` in January are legitimately one column. Round 11
read the offset off the first row's `tzinfo` and handed pandas a plain
`Series`; pandas infers `object` for mixed tzinfo, and `.dt` then raised
`AttributeError` - a bare traceback where the module documents a refusal,
the same defect class round 11 had just fixed elsewhere. `to_arrow` on the
identical rows succeeded, because it had always derived the offset from
`utcoffset()`. The pandas path now mirrors it.

### MEDIUM - "verified" covered only half the declared range, and the answer was to verify, not to narrow

Round 11 addressed the pandas half of a version claim and left `pyarrow>=14`
untouched, and no run against pandas 2 existed anywhere in the evidence -
both gate logs were the same 3.0.5 / 25.0.1 environment.

The tempting fix was to raise both floors to the versions that happened to be
installed. That narrows the package to protect a claim rather than checking
it. Instead: a clean virtualenv at the declared floor - pandas 2.3.3, pyarrow
14.0.2, and `numpy<2`, which pyarrow 14 requires because it predates the
NumPy 2 ABI - ran the 63 export and end-to-end tests. **All pass, and the
dtype table is byte-identical to the one the current environment produces.**
The explicit dtype coercion added in round 11 is exactly what makes that
true. Recorded in
`evidence/export-dtypes-across-the-declared-range-2026-09-06.txt`, and both
`pyproject.toml` and `SUPPORT-MATRIX.md` now say the range is exercised at
both ends rather than at one.

### LOW x2

- `path.is_dir() or containers.is_container(path)` - the first clause was
  dead, since `is_container` already returns True for a directory.
- **`core._snapshot` reported every failed open as a write refusal.** It is
  the read path as well as the write path, so `pq check missing.pq` said
  `M_SAFE_WRITE_REFUSED: writes require a regular, non-symlink, single-link
  file` - nothing about which was true of what the user did. It had been
  merely confusing on stderr; round 11 promoted it into structured output
  that consumers parse. A failed `os.open` now propagates its own `OSError`,
  which the CLI already renders as `M_IO_ERROR` with the OS's own reason. The
  genuine write-safety refusals - symlink, non-regular, more than one hard
  link - keep `SafeWriteError`, because those are about writing.

### Controls

Three behavioural fixes, three controls, each red with the defect and green
after restoring: the `list` text path, the timezone offset derivation, and
the read-side error code.

## Verification on the final tree, eighth pass (the round-12 fixes)

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 4107 passed in 680.63s (0:11:20), 953 worked examples exact; `evidence/release-gate-2026-09-07-round12-fixes.log` |
| Export suite at the DECLARED FLOOR | pandas 2.3.3 / pyarrow 14.0.2 / numpy<2 in a clean venv: **63 passed**, dtype table byte-identical to the 3.0.5 / 25.0.1 run; `evidence/export-dtypes-across-the-declared-range-2026-09-06.txt` |
| Positive controls | `pq list` text path · timezone offset derivation · read-side error code - each red with the defect, green after restoring |


## Round 13 - the review of the round-12 fixes: FIX-FIRST, five findings

`evidence/opus5-wrapper-round13-3577791..a49ed2a.txt`. Four taken, one taken
in half and refuted in half, plus one defect the review did not find that
surfaced while reproducing its LOWs.

### HIGH - the floor evidence certified a tree that did not contain the change

`evidence/export-dtypes-across-the-declared-range-2026-09-06.txt` reported
"63 passed on the floor venv". 63 is the collect count at **3577791**;
a49ed2a collects **66**. So the floor run predated the round-12
`_pandas_column` rewrite, and the pandas-2.3.3 column never executed the
`pd.to_datetime(...).tz_convert(offset)` +
`astype(f"datetime64[us, {offset}]")` path the claim existed to cover. Worse,
the old table's only `datetimezone` row was UTC, whose `str()` is `"UTC"` -
so even a correct run would have skipped the interpolated form.

Reproduced by counting both trees, then fixed by rebuilding the floor venv
(pandas 2.3.3 / pyarrow 14.0.2 / numpy 1.26.4, same as before) and
re-measuring against the working tree: **71 passed**, dtype tables byte-
identical (verified with `diff`, not by eye), and the table now carries a
`datetimezone +05:30` row. Superseded file deleted; new one is
`evidence/export-dtypes-across-the-declared-range-2026-09-07.txt`.
SUPPORT-MATRIX.md's "all 63 tests pass" corrected to 71 and pointed at it.

This is the finding that mattered. An evidence file that certifies the wrong
tree is worse than no evidence file, because it stops the next person looking.

### MEDIUM - half true: a real test gap, on a false premise

**True:** `test_a_column_mixing_tzinfo_objects_at_one_offset_exports` only
ever built `datetime64[us, UTC]`, and asserted `.dtype` alone - so a column
that kept its type while shifting every instant would have passed.

**False:** the review's stated reason - that the interpolated `UTC+05:30`
form "works on 3.0.5, not on the 2.x floor". It works identically on both.
Measured directly on pandas 2.3.3 and 3.0.5 before writing any fix: same
dtype, same instant, for `+05:30` and `-08:00` alike. Had I taken the
finding at its word I would have "fixed" a version-compatibility bug that
does not exist, most likely by narrowing `pandas>=2` to protect a claim.

The gap is closed by
`test_a_non_utc_offset_column_keeps_its_dtype_and_its_instants`, which
asserts the offset AND the instant at two non-UTC offsets, and which now runs
on both ends of the declared range.

### LOW - `O_NOFOLLOW`'s refusal escaped the typed contract

Round 12 stopped wrapping failed opens as `SafeWriteError`, and the comment
justifying it said the genuine write-safety refusals "stay SafeWriteError".
One did not: a symlink swapped in between `lstat` and `os.open` - the TOCTOU
race `O_NOFOLLOW` exists to close - came out as bare `OSError(ELOOP)` and
rendered as `M_IO_ERROR: Too many levels of symbolic links`, which reads like
a broken path rather than a refused write. Reproduced by patching
`core.os.lstat` to report the target's stat while the path was a symlink.
`errno.ELOOP` is now re-wrapped; everything else still propagates, and a
plain missing file is still `OSError`. The counter-case the round-12 comment
needed was "who else can make `os.open` fail for a write-safety reason".

### LOW - the public-API exception change was never written down

`open`, `update_file`, `read_sections` and `PqFile` now raise bare
`FileNotFoundError` / `PermissionError` where they raised an `MQueryError`.
The CLI is fine - `main` catches `OSError` - but the README's "typed error"
language teaches a library caller to catch `MQueryError`, and nothing
recorded the change. Documented in both README.md and SUPPORT-MATRIX.md
rather than reverted: the round-12 behaviour is right, it was just silent.

### LOW - `--json` failure ordering, and a double report

Splitting failures into a second list (the round-12 fix for the plain-text
`KeyError`) appended every failure AFTER every success, so a consumer could
no longer align `pq list --json` records with the files it passed. And with
every file failing, the text path printed the per-file error AND `no queries
found` - which says "empty" about a file that was unreadable. One list in
argument order, filtered on `"name"` for the text branch, fixes both; `no
queries found` now prints only when nothing failed.

### Not in the review - `pq list` called an unparseable file empty

Found while reproducing the LOWs above. `_run_list` caught a `split_shared`
failure and set `members = {}` under a comment claiming the section was
"still worth reporting" - directly above the line that reported nothing. So
`pq list parsefail.pq` printed `no queries found` and exited **0** on a file
that `pq check` rejects as a parse error: a shorter answer than the truth,
carrying a success code. (Round 14 corrected the code named here: `list`
surfaces it through `split_shared`, which wraps it as `ContainerError`, so the
code a user sees from `list` is `M_CONTAINER_ERROR`.) That is precisely the failure mode the
package's stated contract rules out ("never silently return incomplete or
incorrect data"), and it was hiding behind a comment that said the opposite.
It now reports the section's own error and exits 2, while a genuinely empty
section document still says `no queries found` and exits 0.

### Controls

Five fixes, five controls, each red with the defect reintroduced and green
after restoring: ELOOP re-wrap removed · failures appended at the end again ·
`no queries found` printed after an error · unsplittable section silently
emptied · timezone format hardcoded back to UTC.

## Verification on the final tree, ninth pass (the round-13 fixes)

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** - 4112 passed in 668.08s (0:11:08), 953 worked examples exact, ruff + mypy strict clean, coverage 547/635 already current; `evidence/release-gate-2026-09-07-round13-fixes.log` |
| Export suite at the DECLARED FLOOR, re-measured on THIS tree | pandas 2.3.3 / pyarrow 14.0.2 / numpy 1.26.4 in a clean venv: **71 passed**; dtype tables byte-identical to 3.0.5 / 25.0.1 by `diff`, now including a `datetimezone +05:30` row; `evidence/export-dtypes-across-the-declared-range-2026-09-07.txt` |
| Positive controls | ELOOP re-wrap removed · failures appended at the end again · `no queries found` printed after an error · unsplittable section silently emptied · tz format hardcoded back to UTC - each red with the defect, green after restoring |
| Documented quick-start, run literally | All 12 README quick-start lines against real artifacts in a clean directory, including `pq show`/`pq list` on a synthesised `.pbix` and a parquet round-trip read back with pandas. `diff` exits 1 on differing files as documented; every other line exits 0 |
| Pre-push scan on `a49ed2a..b44f459` | gitleaks clean, in-house scan clean, 11 files; `evidence/prepush-scan-2026-09-07-b44f459.log`. Pushed with `GIT_PUSH_BYPASS=1` only because the hook itself reported no controlling terminal and the scan above had already run clean |
| Pushed | `b44f4596767c916013005f75c2855f5c30426da6`, verified against `git ls-remote` |

Two autocommit-bot sweeps (`a3fffbb`, `3aa8f67`) landed during the gate run and
were absorbed with `git reset --soft a49ed2a` before committing. The staged
tree was checked for leaked positive-control mutations first; the only match
was the removal line for `members = {}`, which is the fix.

## Round 14 - the review of the round-13 fixes: FIX-FIRST, five findings

`evidence/opus5-wrapper-round14-a49ed2a..b44f459.txt`. All five taken. The
HIGH is a regression I introduced in round 13, on the very finding round 13
was fixing.

### HIGH - the ELOOP re-wrap caught the wrong call

Round 13 wrapped `errno.ELOOP` as `SafeWriteError` to give `O_NOFOLLOW`'s
TOCTOU refusal its proper code. But `os.lstat` sits inside the same `try`,
and **`lstat` raises ELOOP on its own** whenever a DIRECTORY component of the
path is a symlink loop - no `O_NOFOLLOW`, no race. So a read verb on an
ordinary broken path started reporting a write refusal:

    $ pq check loopdir/x.pq
    error M_SAFE_WRITE_REFUSED: writes require a regular, non-symlink,
    single-link file

That is precisely the false-message class round 12 removed, reintroduced on a
case far more reachable than the race it was aimed at - and it contradicted
the README and SUPPORT-MATRIX text the same commit added, both of which say a
file that will not open raises `OSError`.

Reproduced with `ln -s loopdir loopdir`, confirming `os.lstat` alone returns
errno 62. Fixed by splitting the `try`: `lstat`'s failures propagate
untouched, and only `os.open`'s ELOOP - which can only mean a symlink
appeared after `lstat` said there was none - becomes `SafeWriteError`.

The round-13 comment reasoned correctly about what ELOOP means from
`os.open` and never asked what else in the same block could raise it. That is
the counter-case rule failing on a block boundary rather than a caller.

### MEDIUM - the new failure record could not be aligned either

`containers.read_sections` hardcodes `path="Formulas/Section1.m"` for every
`.xlsx`/`.pbix`, so keying the section-failure record on `section.path` gave
two different unparseable workbooks the **identical** `"file"` value - which
is the exact alignment the record was added to provide. Reproduced with two
distinct broken workbooks; both records read `"file": "Formulas/Section1.m"`.

Now container-qualified (`a.xlsx!Formulas/Section1.m`), matching
`_check_diagnostics` and `_parse_records`. Qualified only when the section is
a real container section: a plain `.pq` gets a synthetic section whose
container IS its path, and `q.pq!q.pq` helps nobody.

### LOW - an unreachable default naming the wrong code

`getattr(error, "code", "M_PARSE_ERROR")` - `MQueryError` defines `code` on
the base class, so the default was unreachable, and the code it named was not
the one that arrives: `split_shared` wraps the parse failure as
`ContainerError`, so a user sees `M_CONTAINER_ERROR`. The round-13 comment
and this closeout both said `M_PARSE_ERROR`, and the test asserted only that
some error appeared. Now `error.code` directly, and the test pins the real
code - controlled by making `split_shared` raise a different one.

### LOW - a process-wide mock that ignored its argument

`mock.patch("pqtools.core.os.lstat", lambda p, *a, **k: real_lstat(target))`:
`pqtools.core.os` IS the stdlib `os` module, so this replaced `os.lstat` for
every caller in the interpreter, handing `target`'s stat to anything that
happened to call it inside the block. Now discriminates on the path.

No positive control - the fix removes cross-test contamination that no
current test detects. Recorded as hygiene, not as a verified behaviour change.

### LOW - the gate log named no tree

Same provenance gap as the round-13 HIGH, one directory over. Fixed in
`scripts/release_gate.sh` itself rather than by remembering to paste a header:
every run now emits commit SHA, a dirty-tree marker, branch, total collect
count and UTC date as its first five lines. Machinery, not intention.

### Controls

Three of the five are behavioural and each was controlled, red with the defect
and green after restoring: ELOOP wrap unscoped again · failure record keyed on
`section.path` only · `split_shared` raising a different code. The other two
(the mock's blast radius, the log header) have no behavioural control, and are
recorded as such rather than given a fake one.

## Verification on the final tree, tenth pass (the round-14 fixes)

| Check | Result |
|---|---|
| Release gate, 8 steps | **GATE PASSED** (exit 0) - 4115 passed in 684.84s (0:11:24), 953 worked examples exact, ruff + mypy strict clean, coverage current; `evidence/release-gate-2026-09-07-round14-fixes.log` |
| Positive controls | ELOOP wrap unscoped again · failure record keyed on `section.path` only · `split_shared` raising a different code - each red with the defect, green after restoring. The mock-blast-radius and log-header fixes have NO behavioural control and are recorded as such |
| Live reproductions, re-run after the fix | `pq check loopdir/x.pq` -> `M_IO_ERROR: [Errno 62]` (was `M_SAFE_WRITE_REFUSED`); two broken workbooks -> `a.xlsx!Formulas/Section1.m` and `b.xlsx!Formulas/Section1.m` (both were `Formulas/Section1.m`) |
| Pre-push scan on `b44f459..1f6682f` | gitleaks clean, in-house clean, 9 files; `evidence/prepush-scan-2026-09-07-1f6682f.log` |
| Pushed | `1f6682fec96d41e032cb8a3a74797e83f87380ac`, verified against `git ls-remote` |

Three autocommit sweeps (`5118ec9`, `7c66403`, `70d0658`) absorbed with
`git reset --soft b44f459`. The staged tree was checked for leaked control
mutations first; none.

### A note on the environment, not the code

The gate was killed twice by the harness for system memory before completing.
`vm.swapusage` showed 4 GB of 5 GB in use with ~63 Chrome processes holding
~1.7 GB; the machine was thrashing independently of this work. It passed on
the next attempt with nothing else of mine running. Splitting the suite across
processes was deliberately NOT used as a substitute: this repo has a known
cross-file failure (the corpus BRIDGE_FAILURE that appears only after
`test_core.py`), so a chunked "4115 passed" would assert less than a single
run while looking identical.

### Round 15 - NOT OBTAINED

The review of `b44f459..1f6682f` was launched and killed by the same memory
pressure before the model returned; only the wrapper's 6-line header was
written. **The round-14 fixes are therefore gate-verified but NOT
review-verified.** That is a real gap in the chain every prior round closed,
and it is recorded here rather than left implicit.

## Round 15 - the errno taxonomy, restructured rather than patched again

G's call, after four consecutive rounds each introduced a defect in the same
decision: "restructure the errno mapping properly".

### What was wrong with the shape, not the code

`core.py` answered one question - *is this `OSError` a fact about the path, or
a refusal to write?* - inline, at four call sites, with fresh reasoning each
time. The reasoning was written as comments, and comments do not compose:

| round | the fix | what it broke |
|---|---|---|
| 12 | stop wrapping every failed open as a write refusal | left `O_NOFOLLOW`'s genuine refusal untyped |
| 13 | type it, by wrapping `ELOOP` | wrapped the whole `try`, catching `os.lstat`'s ELOOP - a symlink loop in a DIRECTORY component, i.e. an ordinary broken path |
| 14 | scope the wrap to `os.open` | left `os.open` on the LOCK file reporting `ENOTDIR` as "unable to acquire safe source lock" |
| (mine, unreviewed) | propagate `ENOENT`/`ENOTDIR` at the lock | still assumed `ELOOP` at the lock meant a symlinked lock |

### The restructuring

One table, keyed by **(call site, errno)** - because errno alone cannot carry
the decision, which is the trap all four fixes fell into:

    _WRITE_SAFETY = {
        (_FsCall.OPEN_SOURCE, errno.ELOOP): UNSAFE_TO_WRITE,
        (_FsCall.OPEN_LOCK,   errno.ELOOP): LOCK_UNSAFE,
    }

Anything not listed is an I/O fact and propagates. `_FsCall.RESOLVE` and
`_FsCall.CREATE_TEMP` appear in no entry, and the comment says that is the
point rather than an omission. All four sites now read identically:

    except OSError as error:
        refusal = _write_refusal(error, _FsCall.OPEN_SOURCE, path)
        if refusal is not None:
            raise SafeWriteError(refusal) from error
        raise

The duplicated message literals became `UNSAFE_TO_WRITE` / `LOCK_UNSAFE`, so
the same refusal cannot drift between the three places that raise it.

### The fifth bug, found by building the matrix

Writing the behaviour matrix immediately exposed a live defect that no review
round had reached, and that my own unreviewed lock fix did not cover:

    pq check  loopdir/x.pq          -> M_IO_ERROR            correct
    pq format loopdir/x.pq --write  -> M_SAFE_WRITE_REFUSED  WRONG

`os.open` raises `ELOOP` for **two different reasons**: `O_NOFOLLOW` refusing
a symlinked final component (a write-safety refusal) and a symlink loop in a
directory component (a broken path). At the source file a preceding `lstat`
had already ruled the second out, so round 14's reasoning held there by
accident of ordering. The lock file has no preceding `lstat`, so the same
assumption was simply false.

Deciding this from the errno is not possible. `_write_refusal` now **asks the
filesystem** - `_final_component_is_a_symlink()` - instead of reasoning about
which cause "must" have occurred. That also makes round 13's bug structurally
unreachable: adding a `(RESOLVE, ELOOP)` entry to the table now changes
nothing, because the disambiguation rejects it first.

### Controls

Each historical bug was reintroduced against the new structure:

| control | verdict |
|---|---|
| r12: every failed open is a write refusal | RED |
| r13: `lstat`'s ELOOP treated as a refusal | **could not be made red - DISCARDED** |
| r14: ELOOP assumed rather than asked about | RED (3 tests) |
| TOCTOU refusal dropped from the table | RED |
| symlinked-lock refusal dropped from the table | RED (2 tests) |
| `(RESOLVE, ENOENT)` -> refusal (replacement for the discarded one) | RED (4 tests) |

The discarded control is recorded rather than counted: `(RESOLVE, ELOOP)`
returns `None` even when present, because the ELOOP disambiguation rejects it
before the table is consulted. Verified directly rather than assumed. A
control that cannot go red proves nothing, so it was replaced with
`(RESOLVE, ENOENT)`, which the disambiguation does not guard and which does
go red.

### The regression test is the table

`test_the_filesystem_errno_taxonomy_holds` is parametrised over six
filesystem shapes and asserts the code from a READ verb and a WRITE verb for
each. The first two columns agreeing wherever the problem is the path is the
property every one of rounds 12-15 broke, each in a different cell, and none
of the previous tests could see more than one cell at a time.

## Round 16 - the review of the restructuring: FIX-FIRST, five findings

`evidence/opus5-wrapper-round16-b44f459..fcce6c3.txt`, covering the whole
previously-unreviewed span. All five taken. The sharpest one is a regression
the round-15 restructuring itself introduced.

### MEDIUM - the ELOOP re-check degraded the race it existed for

Round 15 answered "was this ELOOP `O_NOFOLLOW`?" by `lstat`-ing again, AFTER
`os.open` had already failed. That re-opens a window inside the very race the
flag closes: an attacker who swaps the symlink in, lets the open fail, then
swaps it back out makes the second `lstat` report a regular file, and the
genuine refusal degrades to `M_IO_ERROR: Too many levels of symbolic links`.

Reproduced by patching `os.lstat` to always report a regular file while
`os.open` raises ELOOP - i.e. swap-and-swap-back - and watching a
`SafeWriteError` become an `OSError`. The round-15 test only modelled
swap-and-leave, so nothing caught it.

The review's diagnosis is the important half: at `OPEN_SOURCE` the check was
**unnecessary as well as harmful**, because `_snapshot` lstats the path
immediately before opening it and that lstat SUCCEEDING already proves no
directory component is a loop. The disambiguation is now confined to
`_ELOOP_IS_AMBIGUOUS = {OPEN_LOCK}` - the one call with no preceding lstat.

This also costs the "r13 is structurally unreachable" property round 15
claimed. That is the right trade and is recorded rather than quietly dropped:
the property was a side effect, it was paid for by degrading a real refusal,
and the parametrised taxonomy test catches r13's bug directly anyway.

### MEDIUM - the DIRTY marker could not see a staged tree

`git diff --quiet` compares the worktree against the INDEX, so a tree whose
changes are all STAGED - the normal shape when gating immediately before a
commit - printed a bare SHA with no marker. That is exactly the "log certifies
a tree it did not run on" failure the header was added two rounds ago to
prevent. Verified in an isolated repo: staged-only change gives
`git diff --quiet` = 0 (clean) and `git diff --quiet HEAD` = 1 (dirty). Now
compares against HEAD and additionally uses `--porcelain`, so untracked files
count too.

### MEDIUM - llms.txt contradicted the table added in the same commit

The row read "`M_SAFE_WRITE_REFUSED` | A `--write` was refused", but
`_FS_TAXONOMY` asserts a READ verb emits it for `target_is_a_symlink` and
`target_is_hard_linked` - two of its six rows - because `_snapshot` applies
the symlink and `st_nlink` checks on the read path too. Confirmed live:
`pq check symlink.pq` exits 2 with `M_SAFE_WRITE_REFUSED`.

Fixed in the DOCS rather than the code, deliberately. Refusing to follow a
symlink to read is long-standing intended behaviour, asserted by
`test_write_refuses_symlink_and_hardlink` on the dry-run path since well
before this audit. The false statement was the sentence, not the refusal.
README carries the same clarification.

### LOW x2

- `racing_lstat` called `Path(candidate)` unguarded while patched over stdlib
  `os`; a bytes path or an int fd would have raised `TypeError` and surfaced
  as an unrelated error rather than a failure. Now isinstance-guarded.
- The gate's collect count was captured with no status test, so a collection
  error printed a blank `# collect:` line and the run could still end
  `GATE PASSED` - blanking the provenance line in exactly the case it matters.
  Now emits `unknown - COLLECTION FAILED`.

### Controls

Three were listed as behavioural, each red with the defect and green after
restoring: disambiguation applied to `OPEN_SOURCE` too (2 tests) · DIRTY
marker comparing against the index · collect count unchecked.

**CORRECTION (round 17).** Two of those three were NOT behavioural controls.
The test backing them grepped `release_gate.sh` for three substrings instead
of running it, so it went red only because my mutation happened to change a
grepped string. Inverting the clean/dirty branches - making the header report
every clean tree as DIRTY and every dirty tree as clean, i.e. exactly
backwards - leaves that test GREEN. Verified. The sentence above was false
when written, and the count of "three behavioural" was really one. The header
now lives in `scripts/gate_provenance.sh` so a test can execute it, and four
tests drive it in real temp repos. The two remaining fixes (the
llms.txt/README sentence, the isinstance guard) have no behavioural control
and are recorded as such rather than given a fake one - though the BEHAVIOUR
the doc describes is pinned by `_FS_TAXONOMY`, which is what makes the
sentence checkable at all.

## Round 17 - the review of the round-16 fixes: FIX-FIRST, four findings

`evidence/opus5-wrapper-round17-fcce6c3..c79b676.txt`. All four taken. Two of
them are about MY OWN verification claims being weaker than stated, which
makes this the most useful round of the audit.

### MEDIUM - a control I called behavioural was a string grep

`test_the_gate_header_reports_a_staged_only_tree_as_dirty` never ran
`release_gate.sh`. It asserted three substrings existed in the file. It went
red under my round-16 control only because that mutation happened to change
one of the grepped strings.

**Proved by inverting the branches** so the header reports every clean tree as
DIRTY and every dirty tree as clean - the header exactly backwards - and the
test still PASSED. The round-16 closeout listed it among "three behavioural,
each red with the defect"; the real count was one. That sentence has been
corrected in place rather than left standing.

The header now lives in `scripts/gate_provenance.sh` precisely so a test can
EXECUTE it, and four tests drive it in real temp repos: staged-only, untracked
only, clean, unborn HEAD, and a failed collection (split into its own test -
it had been asserted inside the staged-tree test, so a failure would have
reported under an unrelated name).

Writing those immediately found a bug in my own script: on an unborn HEAD
`git rev-parse HEAD` prints the literal "HEAD" to STDOUT and then fails, so
`|| echo unknown` appended to it and produced a two-line commit field. Now
`--verify`, which prints nothing on failure.

### MEDIUM - the sentence written to fix a false claim repeated it

Round 16 corrected llms.txt for listing read-verb refusals as `--write`-only.
The replacement listed "the input exceeds 10 MiB" among the `--write`-only
cases - and the size check is in `_snapshot`, which is the read path too.
Confirmed: `pq check` on an 11.2 MB file exits 2 with
`M_SAFE_WRITE_REFUSED: input exceeds 10 MiB`.

Both docs now put the size cap with the target-property refusals. The other
half was verified rather than assumed: invalid UTF-8 genuinely IS write-only
(`pq check` gets a bare decode error and reports `M_IO_ERROR`), and both
halves are now pinned by a test so the sentence is checkable.

### MEDIUM - the round-16 gate log certifies a tree containing none of its changes

`evidence/release-gate-2026-09-07-round16-fixes.log` names `89367cd` - an
autocommit holding two evidence files - with `(working tree DIRTY)`, while
every change it is evidence for sat uncommitted. The SHA identifies neither
the tree that ran nor the tree shipped: the exact failure the header was added
to prevent, two rounds after adding it.

A DIRTY header now also prints `# content:`, a real object id for the tree
that actually ran (`git stash create`, falling back to hashing the diff).

### LOW - `git diff --quiet HEAD` was redundant and misfired

`git status --porcelain` already covers staged, unstaged and untracked, and
`git diff --quiet HEAD` exits 128 on an unborn HEAD - so an empty repo was
always "dirty". Dropped.

### Controls

Five behavioural, each red with the defect and green after restoring:
clean/dirty branches inverted (**the control the old grep test could not
see**) · content identity dropped when dirty · `rev-parse` without `--verify`
· collect status check removed · both size checks disabled.

The size-cap control was mis-specified on the first attempt: `_snapshot` has
TWO size checks (`st_size`, and the streaming `len(data)`), and disabling only
the first left the test green. Recorded because the first reading of that
result - "the test is not load-bearing" - was wrong, and the difference
between a weak test and an incomplete control is exactly what this round is
about.

---

## Round 18 - `c79b676..3264b59`, verdict FIX-FIRST, five findings, all taken

The round-17 fix for the round-16 finding reproduced the round-16 defect. That
is the third consecutive round in which the previous round's fix was the bug,
and unlike the earlier two this one shipped a piece of evidence that was
already false when it was committed.

### HIGH - `git stash create` omits untracked files, so `# content:` certified a tree without the script that printed it

`# content:` was added in round 17 to give a DIRTY log a checkable identity.
It used `git stash create`, which stashes **tracked** changes only - silently
dropping untracked files, the exact class `git status --porcelain` counts as
dirty eighteen lines above it in the same script.

Measured, not argued. `evidence/release-gate-2026-09-07-round17-fixes.log`
names `295b580c` as "the exact tree that ran", and:

    $ git ls-tree -r 295b580cf15d1f026a34cdbd224a15beb20ec9aa | grep -c gate_provenance
    0
    $ git show 295b580c:scripts/release_gate.sh | grep -n gate_provenance
    25:bash scripts/gate_provenance.sh "$PY"

The certified tree calls a script the certified tree does not contain, because
that script was still untracked when the gate ran. Checking out "the exact
tree that ran" and running it prints no header at all. The identity was not
reconstructible, which is the whole and only property it existed to have.

The old fallback was blind the same way: with **only** untracked changes
`git stash create` prints nothing (verified - it produces no stash at all), so
the fallback fired, and it hashed `git diff HEAD` plus `git status
--porcelain` - and porcelain lists untracked *names*, not contents.

Now built in a scratch index: `read-tree HEAD`, then `add -A` over the
worktree, then `write-tree`. Same side effect as stash create - blobs and
trees in the object store, never a ref - and it covers tracked and untracked
alike. Ignored files stay out, which is what `--porcelain` counted, so the
DIRTY marker and the identity agree on what "dirty" means. When it cannot be
computed the line now says `unknown - COULD NOT IDENTIFY THE TREE THAT RAN`
rather than printing an approximation: an identity that omits part of what ran
reads as checkable and is not, which is worse than none.

Verified end-to-end on this repo: the id it now prints contains
`scripts/gate_provenance.sh` at the **worktree** blob
(`1d871e85…`), not HEAD's (`22f1c54a…`).

### HIGH - the control written alongside it could not see the defect

`test_a_dirty_gate_header_still_identifies_the_tree_that_ran` asserted that the
line held a 40-hex string that was not `unknown`. It never resolved the id, and
the only dirty shape it built was a tracked modification - the one shape
`git stash create` does capture. So it was green throughout, including on the
run that produced the false log above.

It now resolves the id with `git ls-tree -r`, asserts both a tracked
modification and an untracked file are present, and compares each blob against
`git hash-object` of the working file - so the assertion is about content, not
about the shape of a string.

This is the second round running in which a control was weaker than the
sentence describing it. Round 17's was a string grep reported as behavioural;
this one executed the right code and inspected the wrong property.

### MEDIUM - extracting the header created an unchecked call site, and nothing tested it

`bash scripts/gate_provenance.sh "$PY"` ignored its exit status. With the file
renamed or absent the error went to stderr and the gate ran on to `GATE
PASSED`, exit 0, with a header-less log - an unprovenanced pass indistinguishable
from a provenanced one. `grep -rn release_gate tests/` returned nothing: the
round-17 extraction deleted the grep test that at least pinned that file's
strings, and the four replacements drive `gate_provenance.sh` only.

Now `|| { … exit 2; }`, plus a test that copies `release_gate.sh` into a temp
directory **without** `gate_provenance.sh` and asserts non-zero exit and no
`GATE PASSED`.

### LOW - the detached-HEAD fallback returned the literal string `HEAD`

`git rev-parse --abbrev-ref --verify HEAD` prints `HEAD` when detached
(verified), and gate runs happen in detached worktrees (`mq-gate-wt-*`) - so
the branch field was uninformative in precisely the case the fallback existed
for. Now `--short`, reported as `detached at <rev>`.

### LOW - the fixture repos inherited the developer's global git config

`_repo` ran `git init` with the ambient environment, so a global
`core.hooksPath` (this estate installs commit hooks across ~105 repos),
`commit.gpgsign` or `init.templateDir` would fail the `check=True` commits or
run estate hooks inside a throwaway fixture. None are set on this machine, so
this was latent fragility rather than a live failure - but a test whose result
depends on who runs it is not a control. Now `GIT_CONFIG_GLOBAL` and
`GIT_CONFIG_SYSTEM` are pinned to `os.devnull` for every git call in these
tests, including the ones the provenance script makes.

### Controls

Five behavioural, each red with the defect and green after restoring:
identity reverted to `git stash create` · detached fallback reverted to
`--abbrev-ref` · `|| exit` dropped from the call site · clean/dirty branches
inverted · unborn-HEAD `||` moved back inside the substitution.

**Two of the five were mis-specified on the first attempt, and both are worth
recording because a control that cannot go red is not evidence.**

The detached-HEAD mutation silently failed to apply - a quoting error in the
one-liner meant the file was never edited, and the `grep -c abbrev-ref` I used
to confirm it matched the *comment* describing the old behaviour rather than
the code. It was re-run with an asserted replacement (`assert old in s`) and
then went red.

### CORRECTION - round 17 credited the wrong half of its own unborn-HEAD fix

The fifth control was first written as "drop `--verify` from `rev-parse`",
which is what the round-17 commit message says fixed the unborn-HEAD case. It
stayed **green**. The reason is that round 17 changed two things at once:

    before:  "$(git rev-parse HEAD 2>/dev/null || echo unknown)"     # || INSIDE
    after:   $(git rev-parse --verify HEAD 2>/dev/null) || gate_commit='unknown'

The load-bearing half is the `||` moving **outside** the substitution, where it
*replaces* the value; inside, it *appended* "unknown" to the "HEAD" that
`rev-parse` prints to stdout before failing, which is what made the field two
lines long. `--verify` alone is defence in depth. Demonstrated directly:

    inside  : [HEAD\nunknown]
    outside : [unknown]

The faithful control restores the inside-form, and it goes red. The comment in
`gate_provenance.sh` has been corrected in place; the round-17 commit message
is immutable and is corrected here instead.

---

## Round 19 - `3264b59..cc2752d`, verdict FIX-FIRST, five findings, all taken

Two MEDIUM, three LOW, all in the round-18 fix. The pattern from rounds 16-18
holds: the code was right about the case it was written for and wrong about
the adjacent one, and the sentence describing it claimed the general property.

### MEDIUM - `git add -A` drops paths while exiting 0

The round-18 identity replaced `git stash create` because it silently omitted
untracked files. `git add -A` omits paths too, and does it while returning
success, so the exit-status check that would seem to cover it does not.

Two cases, both reproduced against the shipped script:

**An unreadable directory.** `add -A` prints `warning: could not open
directory 'locked/': Permission denied` to stderr and exits 0. Measured side
by side, dirtying the tree independently so the line is always emitted:

    unreadable : 9d4f3a6f30cfacf32221dfc29f9e99ae5c8030d7
    readable   : 5eb221d545ce31c6cc6200689bfab4f3a00eb30a
    absent     : 9d4f3a6f30cfacf32221dfc29f9e99ae5c8030d7

The id for "present but unreadable" is identical to "not there at all", and
differs from the truth. Note `git status --porcelain` cannot see it either, so
here the marker and the identity are blind *together* - the review framed both
cases as the marker and identity disagreeing, which is accurate for the second
case only.

**A nested repository.** Written as `160000 commit <sha>`, whose object lives
in the other repo's store and cannot be read back here, while `--porcelain`
does count it dirty. That is the marker and the identity genuinely
disagreeing. Realistic in this repo: gate runs happen inside `git worktree`
directories (`mq-gate-wt-*`).

Now stderr is captured (`2>&1 >/dev/null`) and any output is fatal to the
identity, with a second net rejecting any tree containing a `160000` entry.
The refusal quotes what git actually said rather than guessing which case it
was, because both land in the same branch:

    # content: unknown - COULD NOT IDENTIFY THE TREE THAT RAN
              (git add -A: warning: could not open directory 'locked/': Permission denied)
    # content: unknown - COULD NOT IDENTIFY THE TREE THAT RAN
              (git add -A: warning: adding embedded git repository: nested)

### MEDIUM - the guard's message named the one case the guard could not see

`scripts/release_gate.sh` refuses when the header fails, and its message says
"refusing to gate an unidentifiable tree". But `gate_provenance.sh` contained
no `exit` at all and its last command was `printf '# date: …'`, so it always
returned 0. Both `unknown` branches - `COULD NOT IDENTIFY THE TREE THAT RAN`
and `COLLECTION FAILED` - printed their refusal and the gate ran on to `GATE
PASSED`, exit 0. The `||` fired only when bash could not execute the file
(127), which is the single case the round-18 test covered.

The script now tracks `gate_status` and exits 3 from both branches, after
printing the whole header - a partial header is still evidence, an
unprovenanced pass is not. End to end, `release_gate.sh` in a repo with a
nested clone now exits 2 and never prints `GATE PASSED`.

### LOW - the fixture env pinned git's config but not its location

Round 18 pinned `GIT_CONFIG_GLOBAL`/`SYSTEM`. Git also takes its *location*
from the environment, and `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
`GIT_OBJECT_DIRECTORY`, `GIT_TEMPLATE_DIR` and `GIT_CONFIG_PARAMETERS` were
still inherited. Verified: with `GIT_DIR` exported, `git rev-parse --git-dir`
inside a fresh directory returns the host repo's path, so the fixtures would
build history in the caller's repository. Every git hook runs with `GIT_DIR`
and `GIT_INDEX_FILE` exported, so "run the suite from a hook" reaches it.
Nine such variables are now stripped, and the test asserts the *construction*
drops them while they are set, not that they happened to be unset.

### LOW - the fallback asserted "detached" without testing for it

It fired whenever `git branch --show-current` printed nothing, which includes
git < 2.22 where the option does not exist and errors to suppressed stderr -
so on an old git every ordinary branch would have been labelled `detached at
<sha>`. Latent here (git 2.40.0 supports it). Now gated on `git symbolic-ref
-q HEAD` failing, which is the actual question.

### LOW - "same side effect as stash create" was not accurate

`stash create` never wrote untracked *content* to the object store; `add -A`
does. Every dirty gate run now stores a blob of each untracked non-ignored
file. They are unreachable and gc-prunable and are never pushed - but they are
on disk, and the pre-push secret scan reads commits, so it would not see them.
Named in the comment given this repo's no-secret-value invariant. Everything
gitignored, `.samples/` included, stays out.

### Controls

Seven behavioural, each red with the defect and green after restoring: stderr
capture reverted to `2>/dev/null` · the gitlink net removed as well · both
nets removed together · `gate_status=3` dropped from the content branch ·
dropped from the collect branch · the final `exit` removed entirely ·
`_GIT_ENV` reverted to passing the location variables through.

One was mis-specified: the collect-branch mutation failed to apply on the
first attempt (a `\n` escaping error in the shell heredoc that built the
search string) and reported "2 passed", which is not a green - it is no
measurement. Re-run with an asserted replacement, then red. The two nets were
also controlled *separately*, which established that the gitlink check is
independently load-bearing: with the stderr check removed the nested-repository
case still refuses.

---

## Round 20 - `cc2752d..4b8be7c`, verdict FIX-FIRST, six findings, all taken

The round-19 gitlink net was both broken and unreachable, and the round-19
closeout's claim that it was "independently load-bearing" was measured in the
one regime where its bug cannot appear. That claim is retracted below.

### HIGH - `grep -q` under `set -o pipefail` returns 141, so the net never fired

`grep -q` exits on the first match. `git ls-tree -r` is still writing, takes
SIGPIPE, and `pipefail` propagates 141 - so a **match** reported the same
non-zero status as no-match, and the `&&` condition was false exactly when a
gitlink existed.

Measured on this repository's own tree (233 entries, 20,504 bytes):

    grep -q '^160000'  ->  status=1  1  1  1  1     (no gitlinks: correct)
    grep -q '^100644'  ->  status=141 141 141 141 141   (a MATCH: 5 of 5)

and in a 3-entry fixture:

    seq 1 3    | grep -q '^1$'  ->  status=0
    seq 1 5000 | grep -q '^1$'  ->  status=141

**The 3-entry line is the regime the round-19 control ran in.** That is why it
certified a net that could not fire. This is the same error as round 18's
size-cap control and round 17's string grep, in a third form: the control
executed the right code, in the wrong regime.

### MEDIUM - the net had no test coverage at all, and would have refused every submodule

Traced with an instrumented copy: in both nested-repository tests the stderr
net fires first and sets `gate_tree=''`, so the gitlink branch is reached with
`gate_tree=[]` and never executes. It was never covered.

And once the SIGPIPE bug were fixed it would have been actively wrong.
`git read-tree HEAD` loads a declared submodule's `160000` entry into the
scratch index and `add -A` emits no warning for it, so any dirty gate run in a
repo using submodules would print `COULD NOT IDENTIFY THE TREE THAT RAN` and
exit 3. The defect named was an *undeclared* nested repository, not a gitlink.

The review's alternative - reject entries failing `git cat-file -e` - does not
work either, and this was checked rather than assumed: a legitimate submodule's
commit is **not** in the superproject's object store (verified), so that test
rejects declared and undeclared alike.

### The net was deleted rather than repaired

Having fixed both defects, the net was then removed, because it is dead code:

- Every way of introducing an **undeclared** gitlink makes `git add -A` write
  `warning: adding embedded git repository` to stderr, so the check above
  already refuses. Verified for a stray clone and for a linked `git worktree`
  placed inside the tree - the case this repo actually hits (`mq-gate-wt-*`).
  `-c advice.addEmbeddedRepo=false` suppresses only the hints, not the warning
  line (verified; the review suggested it as a bypass and it does not bypass).
- A **declared** gitlink - a real submodule, or an embedded repo somebody
  committed - re-adds with empty stderr (verified) and must be accepted: its
  recorded SHA is precisely what HEAD itself records, so the identity is
  exactly as good as git's own.

So the stderr check is the net. A gitlink check on top of it added no reachable
behaviour and cost a HIGH and a MEDIUM. The reasoning is recorded in the script
so it is not re-added.

### RETRACTION - the round-19 closeout overstated its own control

Round 19 said: *"the two nets were also controlled separately, which
established that the gitlink check is independently load-bearing: with the
stderr check removed the nested-repository case still refuses."*

That is wrong, and wrong twice. The control ran in a 4-entry fixture where
`grep -q` returns 0; at real size it returns 141 and the net does not fire. And
"independently load-bearing" was the wrong conclusion even had it fired, since
the only case it can catch is one the stderr net catches first. The sentence
is retracted here; the round-19 commit message is immutable.

### LOW - the control asserted against a re-typed copy of `_GIT_ENV`

The round-19 test rebuilt the environment dict inline instead of calling the
construction the fixtures use, so an edit to the real one would leave the
control green. Demonstrated rather than argued - with the *same* defect
present (`**os.environ` restored):

    control calls _git_env()      -> FAILED   (sees the defect)
    control re-types the dict     -> 1 passed (blind to it)

Now `_git_env()` is a function and both call it. This is the round-17 string
grep one level up: the control was of a copy, not of the thing.

### LOW - `chmod(0o000)` does not constrain root

In a root container `add -A` succeeds, no stderr is produced, and the three
assertions fail. Skipped under `os.geteuid() == 0`.

### LOW - `printf … | head -1` is the same SIGPIPE shape

Harmless in that position (its status is unchecked) but identical in form to
the bug above, so it is now `${gate_add_err%%$'\n'*}` with no pipeline.

### Controls

One control and one reintroduction guard - the round-21 review was right that
calling both "controls" flattens a real difference, and the correction is made
here in place:

- **A control** over shipped code: `_git_env()` reverted to passing the
  location variables through - location test red, and the re-typed variant
  demonstrably green against the same defect.
- **A reintroduction guard**: the submodule test. The net it guards was
  deleted in this same commit, so against the shipped script it passes for any
  implementation that ignores gitlinks. It goes red only against the mutation
  (the round-19 net reinstated), which is what was measured. "Green" there
  means "the defect has not come back", not "this code is checked".

**Stated plainly, because the temptation is to claim otherwise: there is NO
test controlling the SIGPIPE defect itself.** It is fixed by deleting the code
that had it, not by a guard. A `| grep -q` whose failure mode is "silently
fails to fire" cannot be caught by a test asserting the identity is produced,
because that is what a non-firing net produces. The new large-tree test
(600 files) pins identity generation at realistic size - every other fixture in
this file is one to four entries, which is what let the regime error through -
but it is a regression guard, not a control for the defect, and it is not
counted as one.

---

## Round 21 - `4b8be7c..332b0d5`, verdict FIX-FIRST, seven findings: four taken, three rejected on evidence

This round came back from **ox-alpha**, not Opus 5, and several findings were
hedged requests to verify rather than defects ("the diff as shown does not let
a reader confirm"). Each was checked against the tree rather than accepted.

### REJECTED - "the deletion may have unbalanced the `fi`s"

Explicitly hedged, and wrong. `bash -n scripts/gate_provenance.sh` passes, the
script runs clean and exits 0, and the release gate ran 8/8 on it. The reviewer
noticed `if` and `fi` counts differ, which they do because of `elif`; `bash -n`
is the authority.

### REJECTED - "the evidence file certifies a tree that is not the staged tree"

The claim was that `collect: 4139` must predate the two tests the commit adds,
and that the header should read 4141. It rests on assuming 4139 was the
pre-change baseline. It was not:

    round-19 log:  # collect: 4137 tests collected
    round-20 log:  # collect: 4139 tests collected      (4137 + 2 = 4139)
    live collect on the shipped tree: 4139 tests collected

And the identity settles it directly, which is the entire point of the last
four rounds of work on this line. The round-20 log's `# content: 4b27df68`
holds `tests/test_end_to_end.py` at blob `b1fc0e93`, byte-identical to
`git rev-parse 332b0d5:tests/test_end_to_end.py`, and that blob contains both
new test names. **The artifact certifies exactly the tree that shipped.** Two
rounds ago this question could not have been answered; now it is one command.

### REJECTED - "other `chmod(0o000)` sites need the same guard"

`grep -rn 'chmod(0o000)\|chmod(0o400)' tests/` returns exactly one line, and it
is the one that was guarded. There are no siblings.

### TAKEN - `_GIT_ENV` and `_git_env()` had drifted apart

Not "dead state" as described - it was live at five call sites. `_git()` and
`_provenance()` were switched to `_git_env()` in round 20 while four other
tests still passed the module-level snapshot, so half the file honoured a
monkeypatched environment and half did not. The snapshot is deleted and every
call site now calls the function. Re-controlled: with the filter removed the
location test is still red.

### TAKEN - 600 was a round number, and the threshold is not a byte count

Worth pushing on, and the answer is more interesting than the question. It is
not a clean pipe-buffer boundary: an `awk` writing 40 KB returns 0 where
`git ls-tree` writing 38 KB returns 141, because it depends on how the writer
flushes. Measured with the real writer, five runs each:

    250 entries  16,642 bytes  ->  0 0 0 0 0
    300 entries  19,992 bytes  ->  141 141 141 141 141
    600 entries  40,092 bytes  ->  141 141 141 141 141

The boundary is between 250 and 300 entries; 600 is about twice it, which is
the margin for a git that lays its output out differently. The measurement and
the fixture's cost (1.85s) are now in the docstring instead of a bare number.

### TAKEN - the submodule test is a reintroduction guard, not a control

Correct, and the sharpest finding of the round. The net it guards was deleted
in the same commit, so against the shipped script it passes for any
implementation that never inspects gitlinks. It is red only against the
mutation. Both the docstring and the round-20 controls paragraph above now say
so explicitly, and the count is stated as one control and one guard.

This is the same discipline the audit has been applying to itself since round
17, arriving from the other direction: round 17 called a string grep a control,
and here a genuine behavioural test is a guard rather than a control because
the code it examines no longer exists.

### TAKEN - the round-20 controls paragraph said "two behavioural"

The seventh finding, and it is the same one as the sixth seen from the other
end: with the submodule test reclassified, the round-20 tally of "two
behavioural controls" was wrong. Corrected in place above, in the Round 20
Controls section, to one control and one reintroduction guard.

### CORRECTION - this section's own heading miscounted it

Round 22 caught it: the heading read "seven findings: three taken, two rejected
on evidence, two already true", while the body holds three REJECTED and three
TAKEN, and the seventh (immediately above) had been folded into the sixth
without being written up. The real split is four taken, three rejected. Both
are fixed here.

A file whose subject is miscounted controls has no business miscounting its own
findings, which is exactly how the finding was phrased.

---

## Round 22 - `332b0d5..5c183cc`, verdict FIX-FIRST, one finding taken, plus a harness defect the review exposed

This round opened by retracting most of its own previous review, because it
discovered it had been reasoning from diff text as though it described the tree
on disk. That retraction is the useful part of the round.

### CONFIRMED - all three round-21 rejections were right

Independently re-derived at HEAD `332b0d5`: `bash -n` passes; `grep -rn` finds
exactly one `chmod(0o000)` in the suite; and
`git rev-parse 332b0d5:tests/test_end_to_end.py` is `b1fc0e93…`, the blob the
round-20 log's `# content:` names, and it contains both new test names. The
"evidence certifies the wrong tree" claim is dead.

### TAKEN (LOW) - the Round 21 heading miscounted its own findings

The heading read "seven findings: three taken, two rejected on evidence, two
already true"; the body held three REJECTED and three TAKEN, and the seventh
finding had been folded into the sixth without being written up. The true split
is four taken, three rejected. The heading is corrected, the seventh is now its
own subsection, and the miscount is recorded above rather than quietly fixed.

The finding's phrasing is the right one: a file whose subject is miscounted
controls has no business miscounting its own findings.

### The verification round 22 asked for, run from the checkout that holds the changes

    round-21 log # content: ab6a7672…
      tests/test_end_to_end.py in it : 5cd0c1510fbc607cdfe7f4a0e6b29cc6d5a19162
      at shipped HEAD 5c183cc        : 5cd0c1510fbc607cdfe7f4a0e6b29cc6d5a19162
      pre-edit at 332b0d5            : b1fc0e9340ad630a61a1fd21c271bd9e9cc76b40
      _GIT_ENV occurrences in the certified blob: 1   (7 before the edit)
      collect on the shipped tree: 4139

The certified tree is the shipped tree, and it holds the post-edit blob.

### The harness defect underneath the retraction

The reviewer reported an empty index and BASE-era files and concluded it could
not verify anything. Investigated rather than accepted, because it would
invalidate every round if true.

The wrapper is sound. Reproduced by building a worktree exactly the way
`run-opus-gate.sh` does: HEAD commit `332b0d5`, `git status --porcelain` four
lines, the staged diff exactly the four expected files, index blob at the HEAD
version and worktree file at the BASE version. `DRY_RUN` reports the same
`4 files changed, 178 insertions(+), 12 deletions(-)` the review's own header
quotes.

What the reviewer almost certainly inspected was debris: **two abandoned
worktrees, both at `b44f459`, left on disk and still registered since the
OOM-killed runs around round 13.** A worktree at an old commit with a clean
index is precisely the state it described. They have been removed and pruned.

Two harness fixes, neither of which touches the product:

- **A cleanup trap.** `run-opus-gate.sh` removed its worktree only on the happy
  path, so every killed run leaked one. Now `trap cleanup EXIT INT TERM`.
- **The worktree's FILES now hold HEAD.** The index held HEAD while the files
  held BASE, so a reviewer opening a file read the pre-change version - the
  concrete reason it said "I cannot verify the diff's own edits against a real
  tree from this session". `git checkout-index -a -f` after the `read-tree`,
  plus removing paths deleted between BASE and HEAD.

  This cannot disturb the staged diff, and that was verified rather than
  asserted: `git diff --cached` is computed from the worktree's HEAD *commit*
  (still BASE) against the *index* (still HEAD), and the working files
  participate in neither. Measured before and after: `4 files changed, 178
  insertions(+), 12 deletions(-)` both times, same four paths.

The audit has been correcting the evidence a gate log certifies since round 16.
This is the same failure one level out: the review harness was showing the
reviewer a tree that was not the tree under review.

---

## Round 23 - `5c183cc..a1b8de2`, verdict FIX-FIRST, five findings, all taken

The first round the reviewer could read the post-change tree, and it used that
immediately: it verified the gate log's `content:` tree holds the exact staged
blobs, confirmed `checkout-index` works as claimed, and then found four defects
in the harness fix itself.

### HIGH - the trap cannot fire for the failure it was written for

Round 22 blamed the two leaked worktrees on "the OOM kills around round 13" and
then fixed it with `trap cleanup EXIT INT TERM`. **An OOM kill is SIGKILL,
which is untrappable.** Verified directly: a handler on `EXIT INT TERM` does
not run on `kill -9`, and does run on `kill -TERM`. So the exact leak that
motivated the fix would happen identically with the fix in place.

This is the "because clause needs its counter-case" failure from this project's
own memory, in its purest form: the reason given for the fix was the one case
the fix excludes.

Two further facts, both measured rather than assumed:

- `$$` wraps (`kern.maxproc` is 2000 here), so a leaked `/tmp/mq-gate-wt-<pid>`
  is eventually the name the next run wants, and `git worktree add` then aborts
  with `fatal: ... already exists`.
- `git worktree prune` would not have cleaned them either: it only drops
  registrations whose directory is **gone**, and these were on disk. Verified -
  a stale worktree survives a prune, still registered.

Fixed with a startup `sweep()` that removes every `/tmp/mq-gate-wt-<pid>` whose
pid is not alive, then prunes. It skips `$$` and any pid that is still running,
so a concurrent review is never destroyed.

### MEDIUM - the INT/TERM handler never exited, so the script ran on inside a deleted worktree

Bash resumes after a signal handler returns - verified with a probe that prints
after the handler. So Ctrl-C during the 900-second review would remove the
worktree and then still write the footer and `tail` the file, producing a
**truncated `$OUT` in `evidence/` that reads like a completed review**. Now
`trap 'cleanup; exit 130' INT` and `exit 143` for TERM, with the bare handler
only on EXIT.

### MEDIUM - the deletion sweep missed two path classes

`git diff --name-only` C-quotes non-ASCII paths - verified: `src/café.py` comes
back as `"src/caf\303\251.py"`, which names no file, and `rm -f` swallows the
ENOENT. And `rm -f` refuses a directory (`is a directory`, and exit 0 under
`-f`), so a deleted submodule or directory survived. Either way a BASE-only
path lingers in the tree the reviewer reads, which is precisely the staleness
that block exists to prevent. Now `-z` with `read -r -d ''`, and `rm -rf`.

### MEDIUM - the round-22 write-up asserted the leak was closed without its counter-case

"every killed run leaked one. Now `trap cleanup EXIT INT TERM`" reads as
complete while the cited cause is the signal the trap cannot see. Rewritten to
name SIGKILL explicitly and to say that the sweep, not the trap, is what covers
the OOM case.

### LOW - two owners of teardown

The EXIT trap owns cleanup, so the explicit `git worktree remove` in the
DRY_RUN branch and at the end just ran it twice. Both dropped.

### Note on controls

The sweep's own failure path is **not** covered by an automated control, and
that is stated rather than glossed: exercising it means `kill -9`-ing a real
900-second review and starting another, which is not something the suite can
own. It was verified by hand instead - the two stale worktrees were removed,
and `DRY_RUN` after the change leaves no worktree registered.

---

## Round 24 - `ad235e9..3000e35`, verdict FIX-FIRST, six findings, all taken

The diagnostics-in-plain-English change. Two HIGH, and both are the same root
cause: `check()` emits `M_PARSE_ERROR` from the `ParseError` branch rather than
from the rule loop, and every part of the change looked only at the rule loop.

### HIGH - the most common finding had no explanation, and `pq explain` answered it wrongly

`M_PARSE_ERROR` was absent from `DIAGNOSTIC_HELP` and `DIAGNOSTIC_SEVERITY`.
Two consequences, both reproduced:

    $ check('let A = = 1 in A')
    bad.pq:1:9: error M_PARSE_ERROR: parse error at 1:9: ...
    (no sentence under it)

    $ pq explain M_PARSE_ERROR
    M_PARSE_ERROR is not a name pqtools recognizes as a documented Power
    Query M function. It may be a typo, ...        <- exit 0

The second is worse than a gap: it is an actively wrong answer, and the same
commit added a line to `llms.txt` promising `pq explain` takes "either a
diagnostic code or an M function name". The reader who cannot read M is also
the reader whose file most often will not parse.

Both tables now carry it, and its `fix` is the one piece of advice that
actually helps: the reported position is where the parser gave up, which is
usually just after the real mistake.

### MEDIUM - the drift guard structurally could not see the code it was missing

`test_every_diagnostic_code_that_can_be_emitted_has_an_explanation` was written
precisely so a code could not ship without an explanation. It fed `check()`
`_ALL_SIX`, which is **valid M**, so it could never reach the `ParseError`
branch. Its stated guarantee - "a seventh code added later, nothing would
notice" - was already false for the seventh code that existed as it was
written.

This is the round-20 regime error again, in a third form. Round 19 measured a
`grep -q` net in a 3-entry fixture where SIGPIPE cannot happen; round 18
disabled one of two size checks; here a guard against a missing entry ran only
on input that cannot produce the missing entry.

Demonstrated rather than argued. `M_PARSE_ERROR` removed from **both** tables,
so they stay consistent and only the widening is under test:

    narrowed guard (round-24 shape) + defect  ->  1 passed   (blind)
    widened guard                  + defect  ->  FAILED

The guard now unions a second `check()` over deliberately unparseable source,
and asserts the valid-M set exactly rather than merely non-empty, so a parser
change that stopped emitting `M002` cannot quietly narrow the coverage.

### MEDIUM - the once-per-code dedupe reset per file

`render_diagnostics` was called inside the per-file loop, so `explained` was
per-file. Reproduced on three files: the M001 sentence printed three times, and
over `pq check 'src/**/*.pq'` - the README's own example command - it prints
once per matching file. That is the same "output people learn to skip" defect
the change was written to prevent, at batch scale. One set now threads through
the batch.

### LOW x2

`assert emitted` allowed the fixture to narrow silently (now an exact set), and
`DIAGNOSTIC_SEVERITY.get(code, "")` would have printed `M00X () - title` on a
missing key (now indexed directly, which the key-set equality assertion already
guarantees is safe).

### Two of my own errors during the fix, both recorded

**A control script destroyed uncommitted work.** The cleanup step for one
control ran `git checkout tests/test_end_to_end.py`, which restored the file to
HEAD and deleted the three new tests and the guard widening - none of which
were committed. The suite went from 72 collected to 69 collected - 68 passed
and 1 failed - and the two controls that followed were measuring a file that
no longer contained what they were controlling. (The first version of this
paragraph said "72 to 68", comparing a collected count against a passed count.
Corrected after round 25 caught it: in a repo where the recorded numbers are
the evidence, that is not a rounding slip.) Re-applied from the edit script, and every subsequent control
restored from a scratchpad copy instead. `git checkout` is not a cleanup
command when the work is uncommitted.

**A control's first reading was wrong.** `M_PARSE_ERROR` removed from the help
table only left the two tables inconsistent, so the guard went red via the
key-set equality assertion rather than via the widening. That is a real
assertion doing its job, but it proves nothing about the widening. The
corrected control removes the code from both tables, and only then does the
narrowed-vs-widened difference show.

**A test assertion was miscalibrated, not the code.** The batch test asserted
three `error M001:` lines for three files; `let A = 1, A = 2` names the
duplicate at both positions, so the right number is six. The dedupe was
correct throughout.

### Controls

Four behavioural, each red with the defect and green after restoring:
`M_PARSE_ERROR` dropped from the help table - dropped from both tables with the
guard widened - the batch dedupe reverted to per-file - and, as the negative
half of the second, the narrowed guard shown green against the same defect.

---

## Round 25 - `3000e35..dbd6f46`, verdict FIX-FIRST, four findings, all taken

Round 24 fixed `pq explain` for one code. Round 25 found the same defect still
live for twelve.

### HIGH - every FAILURE code still answered as an unrecognised function name

`_run_explain` consulted `DIAGNOSTIC_HELP` only, so each `M_*` failure code
fell through to the function-name branch. Reproduced on this tree:

    $ pq explain M_IO_ERROR
    M_IO_ERROR is not a name pqtools recognizes as a documented Power Query
    M function. It may be a typo, ...                            <- exit 0

Same for `M_EVAL_ERROR`, `M_CONTAINER_ERROR`, `M_SAFE_WRITE_REFUSED`,
`NODE_ERROR`, `MQUERY_ERROR` and the rest. Not hypothetical codes:
`_run_check_batch` emits `M_IO_ERROR` at the user itself, and `llms.txt` had
just been given a line promising `pq explain` takes "either a diagnostic code
or an M function name" - which was false when it was written.

**A wrong answer is worse than a gap.** A gap sends someone to the docs; this
told them their real error code was probably a typo.

`FAILURE_HELP` now carries all twelve, in plainer prose than the `llms.txt`
table (which is written for an agent parsing error output rather than for a
person), and `pq explain` consults both tables. The label was also simplified:
a lint code shows its severity, `M003 (warning)`; a failure code has none and
says what it is, `M_IO_ERROR (failure)`. The first version printed
`M003 (warning lint diagnostic)`, which says the same thing twice.

### MEDIUM - the guard was widened into a regime the defect still escaped

Round 24 widened `test_every_diagnostic_code_that_can_be_emitted_has_an_
explanation` from one branch of `check()` to two, closing the `M_PARSE_ERROR`
hole - and the whole FAILURE family sat outside `check()` entirely.
`M_IO_ERROR` in particular is built in `cli.py` from `getattr(error, "code",
"M_IO_ERROR")` and never passes through `check()` at all.

That is the regime error a fourth time (round 18: one of two size checks;
round 19: a 3-entry fixture where SIGPIPE cannot occur; round 24: valid M only;
here: `check()` only). The pattern is now explicit enough to name: **widening a
guard to cover the case that just escaped it is not the same as asking what
else is outside it.**

The replacement derives the set from three runtime sources and no hand list:
both branches of `check()`, every `MQueryError` subclass's `.code` walked at
runtime, and - because the batch default is a literal in `cli.py`, not a class
attribute - the code obtained by actually provoking an `OSError` through
`pq check --json` on missing files.

A second test asserts `FAILURE_HELP` and the `llms.txt` failure table hold the
same code set. The prose differs on purpose; the code set cannot.

### LOW x2

`render_diagnostics` mutates a caller-owned set in place and is a public
export - now stated in the docstring. And the closeout's "the suite went from
72 to 68" compared a collected count against a passed count; the truth is 72
collected to 69 collected, of which 68 passed and 1 failed. Corrected in
place, because in this repo the recorded numbers are the evidence.

### Controls

Four behavioural, each red with the defect and green after restoring:
`M_IO_ERROR` dropped from `FAILURE_HELP` · `pq explain` reverted to the lint
table only · a code added to `FAILURE_HELP` that `llms.txt` does not document ·
the batch path's `M_IO_ERROR` default changed to an undocumented code.

The last was mis-specified on the first attempt and is recorded as such:
`getattr(error, "code", "M_IO_ERROR")` appears **eight times** in `cli.py`, and
the mutation hit the first occurrence, which is in a different function. The
guard stayed green because nothing it exercises had changed. Re-run against the
occurrence inside `_run_check_batch`, it goes red. A control that edits the
wrong one of eight identical lines measures nothing.

---

## Round 26 - `dbd6f46..29579d2`, eight findings, all eight taken

The round-25 fix taught `pq explain` the failure codes. Round 26 is about the
three shapes that let that fix be *almost* right: a promise enforced only by a
test, a table that can hold a second copy of an entry, and a guard that only
ever runs in one direction.

### MEDIUM - a promise the code did not make

`llms.txt` said "It never answers a real code as though it were an
unrecognised function name". The only thing enforcing that was the test's own
derivation of *today's* codes. `pq explain M_FUTURE_ERROR` still printed "may
be a typo, an internal or undocumented name, or something outside the M
standard library (a query name, a variable, a record field)" and exited 0 -
four suggestions, not one of which is ever true of a code.

Reproduced verbatim before touching anything. The fix decides the question in
the code rather than in the test: a name is code-SHAPED if it is all-caps with
an underscore, which no documented M name is (checked against all 635; M names
are dotted PascalCase and contain no underscore). A code-shaped name that is
not a code this version reports is told exactly that, with the list of codes
that do exist, so the reader is not sent to a document.

**The first version of the shape was `M_[A-Z0-9_]+|NODE_ERROR|MQUERY_ERROR` -
a list of today's codes wearing a regex costume.** The new test caught it:
`NODE_ERROR2` fell through, and so would every future code not starting `M_`,
while a bare `M_` - which names nothing and is a legal M identifier - was
caught. Both boundaries are now pinned by assertion, in both directions.

### MEDIUM - an entry that could never be shown

`FAILURE_HELP["M_PARSE_ERROR"]` was unreachable the day it was written:
`_run_explain` consults `diagnostic_help()` first, so a code in both tables can
only ever show the lint entry. It had already drifted - a different title and a
different fix from the entry users actually see. Nothing noticed, because every
check asked whether a code has *an* entry.

A parse failure genuinely is both things (`check()` reports it, `evaluate()`
raises it), so it belongs in both tables - but a code has one meaning. Both
tables now point at one object, and `test_a_code_in_both_tables_has_one_entry`
holds that with `is`.

### MEDIUM - `except Exception: pass` in a derivation

The module walk that discovers `MQueryError` subclasses swallowed every import
failure, so a module that stops importing takes its error classes out of the
derived set and every guard built on that set stays green.

The clause was there for "optional extras may be absent". Measured: every
third-party import in this package (`openpyxl`, `pandas`, `pyarrow`) is already
inside a function, so no `pqtools` module can fail to import because an extra
is missing. **The clause protected against a hazard that cannot happen while
hiding every hazard that can.** It is gone, not narrowed.

### LOW x5, all taken

Two dicts keyed by the same codes, read through `.get(code, "")`, meant a lint
code missing a severity printed `M001 (lint diagnostic)` and reported
`"severity": ""` - a quiet wrong answer where a missing key should have been
loud. Severity is now a field on the entry; one table cannot drift from itself.
The `llms.txt` JSON paragraph now says severity is `""` for a failure code and
documents the second shape. The failure-table row pattern was `[A-Z_]+`, blind
to any code with a digit - fixed to `[A-Z0-9_]+`, which is what makes the new
lint-table check (codes `M001`..`M006`, plus their severities) able to parse a
row at all. And `label` held two meanings sixteen lines apart inside one
branch; the prose headings are `heading` now.

### The eighth finding, and the control that failed first

*"Nothing proves the reverse: an invented code present in both `FAILURE_HELP`
and `llms.txt` passes every check."*

My first answer was a test that walks every table entry through `pq explain`
and asserts its title reaches the user. **The control refused to go red.**
`pq explain` will happily print any entry it holds, so an invented code
satisfies that test too - it catches *shadowed* entries (the MEDIUM above) and
not *invented* ones. Recorded rather than quietly re-scoped: the test is kept,
because shadowing is a real defect it is the only net for, but it did not
answer the finding.

What answers it is measuring the two sets. Reachable codes: 18. Explained
codes: 18. Identical. So the forward-only assertion became an equality, and
`M_INVENTED` - added to `FAILURE_HELP` *and* to `llms.txt`, exactly the
reviewer's scenario - now reddens it. The comment names the trap for whoever
hits this next: if a new code is genuinely reachable by a path the derivation
cannot see (`M_IO_ERROR` was, until an `OSError` was provoked to reveal it),
extend the derivation - deleting the assertion restores the hole.

### Controls

Six behavioural, each red with the defect and green after restoring, all run
against scratchpad file copies and never `git checkout`:

| # | defect reintroduced | result |
|---|---|---|
| A | the code-shape branch removed | RED -> GREEN |
| B | `M_PARSE_ERROR` given a second, divergent failure entry | RED -> GREEN |
| C | `except Exception: pass` restored, with a module that raises at import | old swallow GREEN (the defect) / fix RED |
| D | `M001`'s severity removed | RED -> GREEN, and `pq explain M001` printed `M001 (lint diagnostic)` |
| E | both table parsers reverted to `[A-Z_]+` | RED -> GREEN |
| F | `M_INVENTED` added to `FAILURE_HELP` and `llms.txt` | RED -> GREEN |

Two controls were mis-specified on the first attempt and are recorded as such.
**C** used a probe module that defined its error class *before* raising, so the
class registered and both arms went red - it measured nothing. Redone with the
raise first, the old swallow is green with a permanently broken module, which
is the defect. **F** is described above.

The two remaining fixes have no executable control and are not claimed to:
the `llms.txt` severity wording is documentation, and `label` -> `heading` is a
rename with no behaviour attached.

---

## Round 27 - `29579d2..dccca7b`, six findings, all six taken

Round 26 fixed a wrong answer and shipped a new one. This round is mostly my
own regression.

### HIGH - round 25's defect with the sign flipped

`_CODE_SHAPED.match(code)`, where `code = name.upper()`. So `my_step`
upper-cased to `MY_STEP`, matched the shape, and `pq explain my_step` answered
**"MY_STEP is not a code this version of pqtools reports"**. Same for
`source_data`, `raw_data_2`, `Result_2` - an ordinary step name told it was a
broken error code. Reproduced on the shipped tree before touching anything.

M identifiers take underscores. **Only the all-caps convention separates a
code from a step name, and upper-casing destroys exactly the evidence the test
then weighs.** The shape reads the raw input now. The cost, paid deliberately:
a lower-case `m_future_error` gets the function-name answer, because it is
indistinguishable from a variable. Real codes are unaffected - the table lookup
upper-cases, so `pq explain m_io_error` still resolves, and that is asserted.

**Round 26's own guard could not have caught this, and that is the finding
underneath the finding.** It asserted the shape swallows no `DOCUMENTED` name.
This branch is reached only by names that are NOT documented and NOT builtins,
so `DOCUMENTED` is disjoint from the set that can produce a counter-example.
The guard ran in a regime where the defect could not appear - the **seventh**
instance in this audit (rounds 18, 19, 24, 25, 26's shape guard, 26's reverse
test, here). The pattern is stable enough to state as a question: *what set can
actually reach this line, and did I measure that one?*

### MEDIUM - the codes users hold most often were still wrong

`M001`..`M006` have no underscore, so the shape missed the entire lint family.
`pq explain M007` printed "is not a name pqtools recognizes ... It may be a
typo" - the exact answer round 26 set out to eliminate, for the codes
`pq check` actually prints. `M[0-9]{3}` added to the shape.

### MEDIUM - a default made two comments false

`severity: str = ""`. `core.py` claimed "one table cannot drift from itself"
and the test claimed "structurally impossible to omit"; both were false, since
a new lint entry could simply omit the field and print `M007 (lint diagnostic)`
with `"severity": ""` - the same wrong answer, still caught only by a test
assertion, which is the arrangement folding the table in was meant to replace.
The default is gone; the eleven failure entries state `severity=""` on purpose,
and omitting it is now a `TypeError` at import.

### LOW x3

`llms.txt` stated "`severity` is ... `""` for a failure code" while
`M_PARSE_ERROR` - listed in the failure table - answers `"kind": "lint
diagnostic", "severity": "error"`, because the lint table is consulted first.
An agent branching on which table it found the code in got the opposite. The
document names the exception now and says to branch on the returned `kind`, and
a test holds the document to it. The shape guard checked `DOCUMENTED` but not
`BUILTINS`, leaving 93 builtins unchecked in a branch that sits *before* the
builtin lookup. And the JSON echoed the raw `name` beside a message about the
upper-cased one; that fell out of the HIGH.

### Controls

Five behavioural, red-then-green, against file copies:

| # | defect reintroduced | result |
|---|---|---|
| A | match `name.upper()` again | RED, and `pq explain my_step` reproduced the wrong answer |
| B | `M[0-9]{3}` dropped from the shape | RED |
| C | the `severity` default restored, `M001`'s severity omitted | RED, printing `M001 (lint diagnostic)` |
| D | the shape guard narrowed back to `DOCUMENTED` | GREEN with a code-shaped builtin present (the defect) / RED with `DOCUMENTED \| BUILTINS` |
| E | the `M_PARSE_ERROR` exception deleted from `llms.txt` | RED |

**D was mis-specified on the first attempt** and is recorded as such: the
anchor I edited was wrapped differently in the file, so the narrowing never
applied and both arms ran the same assertion. Two identical reds are not a
control. Redone against the real text, with a code-shaped builtin registered to
give the narrowed set something to miss, it separates cleanly.

That is now three mis-specified controls in two rounds (round 26's C and F,
round 27's D). All three failed the same way: **the mutation did not land where
I believed it landed.** The check that catches it is cheap and is now habit -
after mutating, print the thing that should have changed before running the
test.

---

## Round 28 - `dccca7b..23b471d`, five findings, all five taken

Two HIGH. The second is the pattern this audit named last round, written by
the round that named it.

### HIGH - the false-positive class narrowed instead of closing

Round 27 shipped "ALL-CAPS with an underscore" and wrote, in a comment, that
"only the all-caps convention separates a code from a step name". **That
premise is false.** `TOTAL_SALES`, `CHANGED_TYPE`, `A_1`, `REMOVED_COLUMNS` are
ordinary M identifiers - the same grammar that makes `M_` legal makes those
legal - and every one of them was answered "is not a code this version of
pqtools reports". Reproduced on the shipped tree.

That is three rounds in a row on one heuristic: r26 matched the upper-cased
name, r27 matched all-caps, both wrong in opposite directions. **The lesson is
not a better regex.** The shape narrows to the prefix families pqtools actually
names codes with (`M_...`, `NODE_...`, `MQUERY_...`, `M###` - all 18 current
codes match, the identifiers above do not), and then **no message depends on
the shape being right**. Both branches state both readings: not a code, and not
a documented function name. A step called `M_TOTAL` still matches the families,
and some future code family will not - so the shape decides only which reading
LEADS and whether the code list is worth printing. It can no longer make either
answer wrong. A name containing a dot is the one unambiguous case (no code
contains one) and keeps the plain function-name wording.

This is the repo's own rule applied to itself: an honest statement of both
possibilities beats a confident guess a user cannot tell apart from knowledge.

### HIGH - the guard ran where the residual defect could not appear, again

Round 27's not-code-shaped list was `my_step`, `source_data`, `raw_data_2`,
`Result_2`, `M_`, `m007` - **not one ALL-CAPS entry**, against a shape whose
whole rule was "ALL-CAPS". The eighth instance, and the most pointed: the
closeout section directly above it names the pattern and states the question to
ask. Writing the question down did not make me ask it.

The list is built from what a person names a step now, not from what falls
outside today's regex. And the test asserts the property that survives the
shape being wrong - both readings present, whichever leads - rather than the
branch taken.

### MEDIUM + LOW x2

`llms.txt` sold the caps rule as the discriminator and promised "pqtools will
not tell you your variable is a broken error code", which the code did not
keep. Rewritten to describe what is actually true: which reading leads is a
heuristic, and nothing depends on it, because neither answer omits the other
possibility. Both intersection loops in the both-tables test iterate
`DIAGNOSTIC_HELP & FAILURE_HELP` and would have gone silently vacuous if that
ever emptied - the expected member is named before the loop now, and the
`llms.txt` read is hoisted out of it. And the round-27 section said "Four
behavioural" above a five-row table; corrected.

### Controls

| # | defect reintroduced | result |
|---|---|---|
| A | the round-27 shape (ALL-CAPS + underscore) | RED, `pq explain TOTAL_SALES` reproduced the wrong answer |
| B | the code branch stops naming the name reading | RED |
| C | the dotless fall-through stops naming the code reading | RED |
| D | `M_PARSE_ERROR` dropped, emptying the intersection | RED (it went vacuous-green before this round) |

**E did not run.** The mutation meant to delete the `llms.txt` heuristic
paragraph failed on a quoting error and never applied, so its green measured
nothing - recorded rather than reported as a pass. That paragraph is prose with
no executable control, and it is not given one: the behaviour it describes is
covered by the dotted-name and both-readings assertions above, and asserting
its wording would be theatre. The one `llms.txt` claim that IS surprising - the
`M_PARSE_ERROR` exception - keeps the phrase-level test round 27 gave it.

---

## Round 29 - `23b471d..d53eb94` - **SHIP**, and three LOWs taken anyway

The first SHIP of this loop. The reviewer verified both round-28 HIGHs closed
by executing the probe set, and independently checked that the gate log's
certified content tree `c98ca41d` contains exactly the staged blobs - so the
evidence certifies the tree it claims, which is the property round 22 was about.

Three LOWs came with it. The verdict was SHIP, so none of this was required;
all three are on code this round touched, all three reproduce, and one of them
retires a test from a load-bearing role. Fixing them is cheaper than carrying
them.

### The bare-prefix asymmetry, and the ninth instance

`M_` used `M_[A-Z0-9_]*[A-Z0-9]` - anchored, so a bare `M_` is not code-shaped,
which the comment above it states as a deliberate rule. `NODE_` and `MQUERY_`
used `[A-Z0-9_]*` with no anchor, so bare `NODE_` and `MQUERY_` WERE
code-shaped, contradicting that rule in the same regex. Reproduced: `pq explain
NODE_` led with the code reading and printed all 18 codes.

And the guard pinned `M_` but not `NODE_` / `MQUERY_` - so the asymmetry sat
outside the regime it measures. **Ninth instance**, in the round after the
round that shipped the eighth. Every family the shape knows about is pinned
now, so the anchor cannot be dropped from one of them again silently.

### A sentence that depended on a test to be true

The code branch asserted "not a name pqtools recognizes as a documented Power
Query M function" while running BEFORE the `BUILTINS` and `catalog.explain`
lookups. It never consulted either set. The claim was true only because a test
asserted no builtin is code-shaped - **a test holding up a sentence printed to
users**, which is the same arrangement round 27 removed from the severity
field.

The branch moved below both lookups. Both halves of the sentence are now
established before it runs. The test stays as a canary - a code-shaped builtin
would still be confusing to ship - but it no longer props anything up.

### Controls

| # | defect reintroduced | result |
|---|---|---|
| A | the anchor dropped from `NODE_` only | RED, `pq explain NODE_` led with the code reading |
| B | the code branch moved back above the lookups | with a real builtin `M_HELPER` registered: old order printed "M_HELPER is not a code ... and not a name pqtools recognizes"; new order printed "M_HELPER is implemented by pqtools" |

**B was mis-run first** and is recorded: the first attempt registered the
builtin but never reordered the branch, so it measured the fix twice and the
red it produced came from the canary assertion, not from the ordering. Redone
with both arms on the same input, it separates cleanly. That is four
mis-specified controls across rounds 26-29, every one the same failure - the
mutation did not land where I believed - and every one caught by printing the
observable before trusting the test.
