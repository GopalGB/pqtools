# mquery-toolkit 0.1.0 - deployment plan (EXECUTED 2026-09-03; superseded by the `pqtools` rename, see STATUS.md)

Goal: `mquery-toolkit` 0.1.0 public on GitHub (`GopalGB/mquery-toolkit`) and PyPI, CI green on the 9-cell matrix (CPython 3.11/3.12/3.13 x ubuntu/macos/windows), installable with `pip install mquery-toolkit`.

Written 2026-09-02 by the Opus 5 session. Everything up to the first `git push` is internal and was done tonight (see `STATUS.md` for the verified numbers). Nothing has been pushed or published yet.

**Authorization:** G, 2026-09-02 22:35 IST: "n publish it also make all things in plan, other low level to execute". Publication is authorized. Steps still marked **[G]** are the ones only G can physically perform: the MAX pre-push security gate reads its `y` from the terminal (LAW, not bypassed with `GIT_PUSH_BYPASS`), and PyPI trusted publishing needs G's PyPI login in a browser (no PyPI account or token exists on this machine). Everything else runs by itself once those happen.

**The one command that starts it** (run it in this Claude session with the `!` prefix so the security gate can ask you for `y`):

```sh
! bash "/Users/gopalmacbook/Desktop/Max HQ/pqtools/ops/publish-1-github.sh"
```

That script re-runs the pre-flight (gh auth, GitHub name free, PyPI name still 404, clean worktree, pre-push gate installed, no existing remote), refuses to continue if any of them moved, then creates the public repo and pushes. The MAX pre-push gate lists every file, runs gitleaks, and asks `Confirm push? [y/N]` - that `y` is the authorization moment. It sets the repo topics afterwards and prints the CI URL.

## Where things are

| Thing | Path |
|---|---|
| Code (isolated git repo, branch `master`) | `pqtools/` (the repo itself; the old `code ->` symlink is gone) |
| Planning record (PRD, PLAN, STATE, matrices) | `powerquery-toolkit/planning` -> `.planning/max-log-recovery-powerquery-toolkit-2026-09-01` |
| ORACLE feasibility report (GO) | `powerquery-toolkit/research-feasibility-report.md` |
| Tonight's evidence (matrix logs, review outputs) | `pqtools/evidence/` |
| Review findings + dispositions | `pqtools/ops/REVIEW-opus5-2026-09-02.md` |

## D0 - local readiness (internal, done tonight)

1. Root cause of the 3-day "credit blocker": `ANTHROPIC_API_KEY` is exported at launchctl level, so `claude -p` billed the empty API key instead of the Max login. Fixed at the source in `~/.codex/skills/claude-review/bin/review.sh` (`env -u ANTHROPIC_API_KEY`). Proven by probe, recorded in memory.
2. Three fix rounds, each verified by the planner and matrix-tested twice (see `REVIEW-opus5-2026-09-02.md` for every finding and its disposition):
   - `1ad34a9` release readiness: Node >= 22, lock-file litter, `pyproject.toml`, README, CI + release workflows.
   - `eb186ed` from wrapper round 1: live semgrep bearer rule, every `M002`/`M003` occurrence, Fabric LRO `Succeeded` -> `/result`, bundle-drift CI guard.
   - `259b2cd` from wrapper round 2: Windows-safe write boundary (`lstat`, lock teardown, `.gitattributes`), bounded subprocess joins, sorted rename spans, `shutil.which`, PQTest timeout, query-safe result URL, SHA-pinned actions + tag guard, pip-audit/semgrep in CI, `py.typed`.
3. Frozen matrix run twice on `259b2cd` + `python -m build` + `twine check` (49 tests, 85%).
4. Both review gates on `8326d3f..259b2cd`, reproducible: `evidence/run-opus-gate.sh` (exact `claude-opus-5`, bundle + lockfile as stub blobs, key unset) and `evidence/run-ocr-gate.sh` (`backend-smart` lane, `--kill-after`).
5. Local commits in the project repo. No push.

Exit criterion for D0: `STATUS.md` shows every row green with the run timestamps. If any row is not green, D1 does not start.

## D1 - create the repository and push **[G]**

Pre-flight (internal, run right before, because names can be taken between now and then):

```sh
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools"
gh auth status
gh repo view GopalGB/mquery-toolkit 2>&1 | head -1          # expect: not found
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/mquery-toolkit/json   # expect: 404
git status --short                                          # expect: empty
git log --oneline -3
```

The click:

```sh
gh repo create GopalGB/mquery-toolkit --public --source=. --remote=origin --push \
  --description "Unofficial offline tooling for Power Query M source (parse, format, check, rename). Not affiliated with Microsoft."
```

The MAX pre-push gate (`security/tools/pre-push-hook.sh`) lists every file and runs gitleaks before upload and asks for a `y`. Do not use `--no-verify`.

After the push: `gh repo edit GopalGB/mquery-toolkit --add-topic power-query --add-topic power-bi --add-topic m-language --add-topic linter --add-topic formatter --add-topic python`.

## D2 - CI proves the support matrix (automatic after D1)

`.github/workflows/ci.yml` runs 9 jobs. This is the first time Windows and Python 3.12/3.13 execute at all; the support matrix is only a claim until this is green.

- Green: continue to D3.
- Red: internal fix loop (goal-loop, max 5) in the code repo, then one more push **[G]**. Typical first-run suspects: Windows path/lock behaviour in `update_file`, `pyarrow` wheel availability on 3.13/windows for the `fabric` extra (drop `fabric` from the CI install on that cell rather than the package if it is a wheel-availability issue, and say so in the commit).

Watch it: `gh run watch` or `gh run list --workflow CI --limit 3`.

## D3 - PyPI trusted publisher, one-time **[G, browser]**

No API token is created or stored anywhere. Publishing uses GitHub OIDC.

1. https://pypi.org -> log in -> Account settings -> Publishing -> "Add a new pending publisher":
   - PyPI project name: `mquery-toolkit`
   - Owner: `GopalGB`
   - Repository: `mquery-toolkit`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. Optional but recommended: same on https://test.pypi.org with environment `testpypi`.
3. In GitHub: Settings -> Environments -> create `pypi` (and `testpypi`). Add G as a required reviewer on `pypi` so a tag push cannot publish without a second human click.

## D4 - TestPyPI rehearsal (recommended, one tag push **[G]**)

Uncomment the `testpypi` job in `.github/workflows/release.yml`, push, then:

```sh
git tag -a v0.1.0rc1 -m "rehearsal"     # pyproject version must be bumped to 0.1.0rc1 for this tag
git push origin v0.1.0rc1
```

Verify from a clean venv:

```sh
python3.12 -m venv /tmp/mq-rc && /tmp/mq-rc/bin/pip install --index-url https://test.pypi.org/simple/ --no-deps mquery-toolkit==0.1.0rc1
/tmp/mq-rc/bin/mquery check "pqtools/tests/fixtures/DataConnectors/HelloWorld.query.pq"
```

Then restore the version to `0.1.0` and re-comment the job. If skipping D4, be aware that a failed `v0.1.0` publish means deleting and recreating the tag.

## D5 - release **[G]**

```sh
cd "/Users/gopalmacbook/Desktop/Max HQ/pqtools"
git tag -a v0.1.0 -m "mquery-toolkit 0.1.0"
git push origin v0.1.0                    # triggers release.yml -> build -> PyPI (environment approval if configured)
gh release create v0.1.0 --generate-notes --title "mquery-toolkit 0.1.0"
```

Post-publish verification (internal, the actual proof of deployment):

```sh
python3.12 -m venv /tmp/mq-live && /tmp/mq-live/bin/pip install mquery-toolkit
/tmp/mq-live/bin/mquery --help
/tmp/mq-live/bin/mquery check "pqtools/tests/fixtures/DataConnectors/HelloWorld.query.pq"
/tmp/mq-live/bin/mquery format "pqtools/tests/fixtures/m-spec-let.pq"   # dry-run diff
pip index versions mquery-toolkit
```

Deployment is done only when those five commands succeed from a venv that has never seen the source tree.

## D6 - after publication (internal unless marked)

- README badges (CI, PyPI version) - internal commit + push **[G]**.
- Update `RELEASE.md` in the repo with the tag date and the verification transcript.
- Memory note in MAX HQ: package live, version, date, verification.
- Announcement: LinkedIn lane is dead (day 38+, see autonomy charter); any post is drafted only, G posts.
- Open a `0.1.1` milestone with the two INFO items from the review (AST-based `M002`/`M003`, documented `dependencies()` semantics).

## Rollback

- PyPI never allows re-uploading a version. Bad release: `yank` 0.1.0 on PyPI (keeps installs pinned to it working, hides it from resolvers) and publish 0.1.1.
- GitHub: delete the release and tag (`gh release delete v0.1.0 --yes && git push --delete origin v0.1.0`).

## G's clicks, in order

| # | Step | Reversible? |
|---|---|---|
| 1 | D1 `gh repo create ... --push` | yes (delete repo) |
| 2 | D2 any re-push after a CI fix | yes |
| 3 | D3 PyPI trusted publisher (browser) | yes |
| 4 | D4 rehearsal tag push (optional) | yes |
| 5 | D5 `git push origin v0.1.0` | **no** (version is burned on PyPI; yank only) |
| 6 | D5 `gh release create` | yes |

## Decisions G can still change (one-line edits)

1. LICENSE holder set to `Gopal Bagaswar` (was "MAX HQ").
2. GitHub coordinates `GopalGB/mquery-toolkit` in `pyproject.toml` `[project.urls]` and this plan.
3. `pyproject.toml` was created from the Claude Code lane where the Codex `max-hq-guard` "linter/formatter configuration" rule is not registered; the guard's stated purpose (stop agents loosening lint to go green) is not what happened here, but it is your guard. `git rm pyproject.toml && git checkout 9bc508c -- setup.py` restores the old layout.
4. Whether to grant blanket `git push` for this one new repo so the D2 fix loop does not need a click per push.

## Related, not part of this deployment

Track A (MAX log recovery, PLAN.md Phases 1-4) was blocked on the same two reviewer gates for the same root cause. The `review.sh` fix unblocks its exact-Opus gate too; the isolated Track A review repo path is in `/tmp/track-a-review.path` if it still exists, otherwise re-create it from `PLAN.md` Phase 4. OCR for Track A hits the same Gemini/Groq quota behaviour; use `--exclude` to keep the batch small.
