# Contributing

Thanks for your interest. This project analyses manufacturing quality records,
so correctness and traceability matter more than feature velocity. Please read
the principles below before opening a pull request.

## Non-negotiable principles

1. **Never certify.** No code, label, copy string or export may state or imply
   that a document *is compliant*, *passes*, or is *approved for release*. The
   vocabulary is fixed: `confirmed_deviation`, `possible_deviation`,
   `insufficient_evidence`, `sop_conflict`, `no_detected_deviation`. There is a
   unit test that fails if marketing-style wording creeps in.
2. **Deterministic code owns verifiable facts.** Numeric comparison, unit
   conversion, tolerance bands, step ordering, date arithmetic and version
   selection are implemented in Python and must stay that way. An LLM may add
   narrative observations; it may never decide a measurable fact.
3. **No evidence, no finding.** Every finding carries quotes that resolve to
   stored source text at recorded character offsets. If a quote cannot be
   re-found, the citation is invalid and the finding cannot be `confirmed`.
   Never relax `STRICT_CITATION_VERIFICATION` to make a test pass.
4. **Uploaded documents are untrusted.** Treat all document text as hostile
   input. See `SECURITY.md`.
5. **Escalate rather than guess.** When the authoritative SOP revision cannot be
   determined, the correct behaviour is to block and require human review - not
   to pick the newest revision.
6. **Never fake results.** Do not commit mocked screenshots, hand-written
   "sample" benchmark numbers, or tests that assert on stubs pretending to be
   real runs. If something cannot be verified, document it as unverified.

## Getting set up

```bash
git clone https://github.com/<you>/manufacturing-sop-ai-auditor.git
cd manufacturing-sop-ai-auditor
./scripts/setup.sh          # Windows: .\scripts\setup.ps1
./scripts/start.sh          # Windows: .\scripts\start.ps1
```

`setup.sh` is re-runnable and non-destructive. Use `--reset` to wipe the
database, generated dataset and uploads and start clean.

## Running the checks

Everything CI runs, you can run locally:

```bash
# Backend: lint, types, tests
cd backend
../.venv/bin/python -m ruff check app eval tests ../scripts
../.venv/bin/python -m ruff format --check app eval tests
../.venv/bin/python -m mypy app
../.venv/bin/python -m pytest -q

# Evaluation harness (deterministic, offline, no cost)
../.venv/bin/python -m eval.harness --out ../eval_results/latest.json

# Frontend: types, unit tests, build
cd ../frontend
npx tsc -b
npx vitest run
npx vite build

# End-to-end (needs both servers running; see below)
npx playwright test
```

### Running the E2E suite

Playwright drives a real browser against a real server. Start the API with a
raised sign-in limit, because the suite signs in dozens of times per minute:

```bash
LOGIN_RATE_LIMIT_PER_MINUTE=200 ./scripts/start.sh
# in another terminal
cd frontend && npx playwright test
```

Without that variable the brute-force guard returns HTTP 429 and tests fail
with the app sitting on the login screen. Do not lower the production default
(10/min) to work around it.

Screenshots in `docs/images/` are generated, never hand-made:

```bash
cd frontend && npx playwright test e2e/screenshots.spec.ts
```

## Pull request checklist

- [ ] `ruff check` and `ruff format --check` clean
- [ ] `mypy app` clean
- [ ] `pytest` green, with a test covering the behaviour you changed
- [ ] `tsc -b`, `vitest run` and `vite build` clean
- [ ] `npx playwright test` green if you touched the UI
- [ ] Evaluation harness still reports **0 invalid citations** and correct
      escalation on all labelled critical conflicts
- [ ] `CHANGELOG.md` updated under "Unreleased"
- [ ] No secrets, no real manufacturing data, no `.env`

## Adding to the labelled dataset

The evaluation set lives in `scripts/generate_dataset.py` and is regenerated
deterministically (fixed seed). When adding a scenario, provide:

- the expected findings, by category
- the evidence references that prove them
- the applicable SOP revision
- whether escalation is expected

Then regenerate and re-run the harness:

```bash
python scripts/generate_dataset.py && python scripts/seed.py --reset
cd backend && python -m eval.harness
```

A scenario whose label you cannot justify from the document text is a bad
scenario. Fix the label or drop it - do not tune the engine to match it.

## Code style

- **Python**: 3.11+, full type annotations, `from __future__ import annotations`,
  SQLAlchemy 2.0 typed ORM style (`Mapped[...]` / `mapped_column`). Line length
  110. Formatting and linting are enforced by ruff.
- **TypeScript**: strict mode, no `any` in new code. Derive API types from the
  real response shapes - do not write them from memory, they drift.
- **Comments** explain *why*, not *what*. A comment restating the code is noise;
  a comment recording a decision ("newest is deliberately not authoritative
  here") is valuable.
- **Accessibility is not optional**: every interactive element reachable by
  keyboard, every table captioned, colour never the only signal, visible focus
  rings. The UI is used by non-technical reviewers.

## Commit messages

Conventional Commits, e.g. `fix(audit): keep unit mismatch out of confirmed status`.
Reference the issue number where one exists.
