# pqtools - announcement copy (drafts)

All copy here is limited to what the package actually does today. It edits Power Query **M source**;
it does not execute queries. Executing M needs Microsoft's Mashup Engine (proprietary, Windows-only,
not redistributable) or a host - which is why the ORACLE feasibility report returned NO-GO on a
standalone runtime. Nothing below claims otherwise, and nothing below promises a roadmap item.

**Links used everywhere:** PyPI https://pypi.org/project/pqtools/ · GitHub https://github.com/GopalGB/pqtools

---

## 1. One-line description (PyPI, GitHub "About", everywhere)

> Unofficial linter, formatter, refactoring CLI and local runner for Power Query M source (.pq) - including the queries inside .pbix/.xlsx. Like ruff and black, for M.

GitHub topics: `power-query` `power-bi` `m-language` `linter` `formatter` `python` `cli` `pq`

**STATUS 2026-09-03: DONE.** All four of this file's outbound items are live or applied:
PyPI `pqtools` 0.4.0, GitHub public, both site cards committed (portfolio: submodule commit
`c61afc0` on branch `case-study/agent-platform`, not yet on `master`; NorthBore: root commit
`171eca82f`, `On Going Project/NorthBore/portfolio/work/index.html` - not `index.html`, see the
correction in §4). Only remaining: yank `mquery-toolkit` 0.1.0 (handed to G - PyPI required a
password re-confirmation the browser session can't supply) and pasting the LinkedIn draft
below (automation lane still dead).

## 2. LinkedIn post - DRAFT ONLY (updated for 0.4.0 - containers + eval shipped)

The automation lane has been dead since 2026-07-10. Paste this by hand, or revive the lane once
with `node marketing/tools/linkedin-save-cookies.js` and it can be queued normally.

---

Power Query M has no ruff. No black. No pandas.

If you write M for Power BI, you know the workflow: open the editor, click into a query, read a wall
of unformatted text, hope you spot the problem. There is no `--check` for CI, and no way to run the
transform chain without opening the app.

So I built one.

`pip install pqtools`

```
pq check query.pq                          # lint: unreachable bindings, unresolved refs, dynamic URLs, credential literals
pq format query.pq                         # prints a diff; --write to apply
pq rename query.pq --old Source --new Raw  # refuses a rename that would silently rebind a name
pq check report.pbix                       # same checks, run on every query inside a real .pbix/.xlsx
pq eval query.pq --bind Source=sales.csv   # runs the transform chain against data you supply
```

That last one is the one I actually wanted. You bind your own value to the step that would normally
call a connector - `Csv.Document`, `Web.Contents`, whatever - and that step is never evaluated. Only
the `Table.*` chain downstream of it runs, against your data, locally. It's not pandas, and it never
touches a live connector or credential - but for the "does this transform logic actually do what I
think" question, you no longer need Power BI open to answer it.

Three things I still care about in it:

Parsing is not mine. Delegates to Microsoft's own `@microsoft/powerquery-parser` and
`powerquery-formatter`, pinned and bundled.

Edits are dry-run by default. `--write` is an atomic replace that preserves encoding, newline
convention, final newline, file mode and BOM, refuses symlinks, and re-checks the file before it
swaps it.

The container writer (editing the M inside a real .pbix without touching anything else in the file)
is built and verified byte-for-byte against a real Power BI sample - but I don't own a copy of Excel,
so it isn't exposed in the CLI until it's verified against a real .xlsx too. Shipping a write path I
can't personally verify isn't worth the risk.

220 tests. CI green on 9 OS/Python cells. MIT. Unofficial, not affiliated with Microsoft.

https://github.com/GopalGB/pqtools

---

## 3. gopalbagaswar.com

**Source found:** `career-change/portfolio/site/` (Next.js) -> Cloudflare Pages project `gopal-portfolio`
(domains gopalbagaswar.com, www). Do NOT edit `out/` or `.next/` - those are build output.
The project list is `components/sections/work.tsx`, a typed `Feature[]`.

The site's rule is "each project in its REAL medium ... No mockups", and the `card` kind exists exactly
for projects with no product UI. It carries no href by design, so the repo URL rides in `meta`.
The voice pairs an earnest phrase with a self-deprecating one via `<Sar serious=... honest=... />` -
matched below.

**APPLIED 2026-09-03** (submodule commit `c61afc0`, updated for 0.4.0 - containers + eval, 220 tests):

```tsx
  {
    kind: "card",
    index: "Open source · on PyPI",
    name: "pqtools",
    blurb: (
      <>
        <Sar serious="A linter, formatter and local runner for Power Query M" honest="ruff and black, for the language nobody tools" />{" "}
        — lint and format a query from CI, or run its transform chain against{" "}
        <Sar serious="data you supply" honest="never a live connector" />{" "}
        — without opening Power BI.
      </>
    ),
    tech: "Python · Microsoft's own pinned M parser behind a Node bridge · local M evaluator · atomic writes · GitHub Actions",
    proof: "ON PYPI · MIT",
    hero: { value: "220", label: "tests across Python and the bridge" },
    chips: ["parse", "format", "check", "rename", "containers", "eval", "CI"],
    meta: ["9 OS/Python cells green", "reads .pbix/.xlsx queries", "github.com/GopalGB/pqtools"],
    badge: "UNOFFICIAL · NOT AFFILIATED WITH MICROSOFT",
  },
```

**Committed on `case-study/agent-platform`, not `master`** - that branch was already checked out and
already had one card (Gemma 4 Vision Fine-Tuning) master doesn't have yet. Merging the two branches
for what actually deploys is G's call, not made here. Deploy itself is also G's click either way:
`cd career-change/portfolio/site && npm run build`, then the project's normal Cloudflare Pages deploy.

## 4. northbore.com

**CORRECTION 2026-09-03:** this section's original plan was wrong about the mechanism. `build_projects.py`
still reads `data/projects.json`, but its `AUTO:WORKGRID` injection into `index.html` was disabled in
the 2026-08-16 theme revamp (the homepage now shows only 3 curated proof cards; `render_workgrid()` is
dead code, kept "for a future reuse" per its own comment). The real "all shipped work" page is
**`work/index.html`** - hand-authored HTML, no markers, a `.work-card.oss` class already used for
5 merged-PR entries (mem0, Gradio, AnythingLLM, LiveKit Agents, Optuna). Neither file was tracked in
git before this - first commit for both.

**APPLIED 2026-09-03** (root commit `171eca82f`):
- `data/projects.json`: appended a `frame: "blueprint"` entry (`id: "pqtools"`, matches the shape
  already used by the `quant` entry - no `shot`, since there's no product screenshot for a CLI tool).
  Kept for `--probe` tracking even though nothing currently injects it into a page.
- `work/index.html`: new "Published open source" section (own package, distinct from "Merged into
  open source" = PRs into other people's repos), one `.work-card.oss` article, no image, PyPI + GitHub
  links - same pattern as the existing 5 OSS cards, just describing a shipped package instead of a PR.

NorthBore sells services, not this library - listed as shipped proof, per the standing rule that
sibling products stay out of NorthBore's offers. Deploy is G's click, same as gopalbagaswar.com.

## 5. Ordering constraint - SATISFIED 2026-09-03

Both entries link to https://pypi.org/project/pqtools/. Order followed: release 0.4.0 under the new
name (tag `v0.4.0`, commit `831969d`) -> verified live from a clean venv (`pip install pqtools`,
`pq check`/`pq eval` both ran correctly) -> then the two site edits (this file, done) and the LinkedIn
post (drafted, not yet posted - paste it by hand) -> then yank `mquery-toolkit` 0.1.0 (**blocked, not
done**: PyPI's yank action demanded a password re-confirmation the automated browser session cannot
supply; task space `4` was handed off to G with the release-management page already open at
https://pypi.org/manage/project/mquery-toolkit/releases/ - confirm the password, then Yank on the
0.1.0 row with reason "renamed to pqtools").

## 6. What is deliberately NOT claimed anywhere

- "Replaces Power Query" / "no need to open Power Query" - it edits source; it cannot run a query.
- "All Power Query operations" - no connector emulation, no query folding, no credential handling,
  no PBIX repacking, no DAX.
- Any roadmap item as if it existed. Reading queries out of `.pbix`/`.xlsx` and execution through the
  Fabric and PQTest adapters are plausible next steps, not shipped features, and the Fabric adapter is
  mocked in tests with no credentialed live proof.
