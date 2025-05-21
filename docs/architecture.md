# Architecture

## Design position

The central claim of this system is narrow and deliberate:

> **A finding is only as good as the evidence that can be re-found in the
> source document.**

Everything below follows from that. The engine is deterministic first; an LLM
is an optional commentator that is never allowed to decide a verifiable fact,
invent a quote, or clear an escalation.

A second principle shapes the version logic:

> **Newest is not authoritative. If authority cannot be justified, block.**

Most "AI compliance" tooling silently picks the latest document. In a real
plant the latest revision is frequently a draft, superseded in one site but not
another, or effective from a future date. Guessing there is worse than
refusing, so the pipeline refuses and escalates.

---

## Stack

| Layer | Choice | Why |
| --- | --- | --- |
| API | FastAPI + Pydantic v2 | typed request/response contracts, generated OpenAPI |
| ORM | SQLAlchemy 2.0 (typed `Mapped[...]`) | explicit schema, mypy-checkable |
| Database | SQLite by default, PostgreSQL supported | zero-setup clone-and-run; `DATABASE_URL` swaps it |
| Migrations | Alembic, with drift checking in CI | schema and models cannot diverge silently |
| Frontend | React 18 + TypeScript (strict) + Vite + Tailwind | fast builds, no runtime CSS framework weight |
| Auth | PyJWT + stdlib PBKDF2 | no native build dependencies |
| PDF out | ReportLab | pure Python |
| PDF in | pypdf, Pillow | pure Python |
| Retrieval | custom lexical matcher | no model download, deterministic, inspectable |
| Tests | pytest, Vitest, Playwright | unit, integration, E2E on a real browser |

**No native-compilation dependencies anywhere.** The whole system installs from
wheels on Linux, macOS and Windows without a toolchain, which is why
`setup.ps1` can work without WSL.

---

## Request and analysis flow

```
          ┌──────────────┐
Browser ──▶  React SPA   │  same-origin /api/*, JWT in localStorage
          └──────┬───────┘
                 │
          ┌──────▼───────────────────────────────────────────┐
          │ FastAPI                                          │
          │  rate limit → auth → RBAC → route → service      │
          └──────┬───────────────────────────────────────────┘
                 │
          ┌──────▼───────┐     ┌───────────────┐
          │ Audit engine │────▶│ LLM adapter   │ (optional, offline by default)
          └──────┬───────┘     └───────────────┘
                 │
          ┌──────▼───────────────────────────┐
          │ SQLite / PostgreSQL + var/storage │
          └───────────────────────────────────┘
```

### The eleven pipeline stages

Each stage is timed and appended to the audit's `pipeline_log`, which the UI
renders so a reviewer can see exactly what the engine did and how long it took.

| # | Stage | Responsibility | Can an LLM influence it? |
| --- | --- | --- | --- |
| 1 | `document_lookup` | find the report and candidate SOP revisions | no |
| 2 | `version_resolution` | decide which revision governed the run, or block | no |
| 3 | `conflict_detection` | find contradictions within and across SOPs | no |
| 4 | `requirement_extraction` | parse steps, parameters, limits, prohibitions | no |
| 5 | `evidence_retrieval` | pair report events with requirements | no |
| 6 | `compliance_comparison` | numeric, unit, sequence and record checks | no |
| 7 | `contradiction_analysis` | operator statements that contradict each other | no |
| 8 | `llm_analysis` | narrative observations only | yes (constrained) |
| 9 | `citation_verification` | every quote must resolve to stored text | no |
| 10 | `risk_classification` | severity, classification, escalation decision | no |
| 11 | `report_generation` | persist findings, citations, audit events | no |

Stage 8 is skipped entirely in `deterministic` mode — the default — and the
system is fully functional without it.

---

## Version resolution: the authority rule

```
candidates = revisions of the declared SOP code
  ├─ applicability filter (line, product, site)
  ├─ in-force filter (effective_date ≤ execution_date ≤ expiry_date)
  └─ status filter (approved)

exactly one approved + in force      → RESOLVED  (basis: approved_and_in_force)
several approved + in force
    ├─ explicit, consistent supersession chain → RESOLVED (basis: explicit_documented_supersession)
    └─ otherwise                               → BLOCKED  + SOP conflict + escalation
zero approved + in force
    ├─ only expired revisions        → BLOCKED
    ├─ execution before all effective dates → BLOCKED
    └─ none at all                   → BLOCKED
unknown approver / unknown execution date     → BLOCKED
```

A blocked audit produces outcome `blocked_unresolved_authority`, no deviation
findings, and a mandatory human-review flag. It never falls back to "use the
newest". The resolution object records every candidate with the reasons it was
accepted or rejected, and the UI exposes that list behind
*"Show all N revisions considered and why"*.

---

## Evidence retrieval and comparison

Report events are paired to requirements with a weighted score:

```
score = 0.55·lexical + 0.25·step_anchor + 0.12·parameter_anchor + 0.08·verb_anchor
```

Greedy 1:1 assignment above a 0.52 threshold. Unmatched report entries produce
at most 6 informational findings so a noisy document cannot flood the queue.

Numeric comparison is unit-aware through a self-contained unit registry.
Separators are normalised, never deleted (`m/s` → `m per s`), because deleting
them silently changes meaning. Abbreviation synonyms are deliberately
restricted — an early version expanded `no` to `number` and erased a negation.

Acceptance bands come from the requirement itself:

- `between X and Y` → band is the stated range
- `== X ± tol` → band is `2·tol`
- open-ended (`≥ X`, `≤ X`) → graded by percentage of the limit

Severity is the fraction of the band exceeded: ≥1.0 critical, ≥0.33 major,
otherwise minor. A prohibited action is always critical.

Negation is treated as a numeric limit, not a prohibition: *shall not exceed*,
*shall be less than* and *shall not fall below* are all bounds.

---

## Citation verification

This is the gate that makes the rest trustworthy.

```
for each citation:
    1. the cited document must be in this audit's evidence base
       (resolved revision, the report, or a revision named in a conflict)
    2. char_start/char_end must lie inside the stored text
    3. the quote must match the stored text:
         exact → containment → whitespace-normalised → offset recovery
    otherwise → INVALID_CITATION
```

An invalid citation caps the finding's confidence at 0.4 and downgrades
`confirmed_deviation` to `insufficient_evidence`. The UI shows
"✕ could not be verified" on the quote and a count in the collapsed card header,
so a reviewer sees it without opening anything.

The evaluation harness fails the build if a single invalid citation is produced
on the curated dataset.

---

## Classification vocabulary

Five classifications, none of which is a pass mark:

| Classification | Meaning |
| --- | --- |
| `confirmed_deviation` | a verifiable rule was broken and every quote verified |
| `possible_deviation` | indications exist but the evidence is not conclusive |
| `insufficient_evidence` | the record does not contain enough information to judge |
| `sop_conflict` | the SOP library contradicts itself; the question cannot be answered |
| `no_detected_deviation` | nothing found **by these checks** — explicitly not a certification |

Audit outcomes resolve by precedence:

```
blocked_unresolved_authority
  → human_review_required          (any SOP conflict or prompt injection)
    → deviations_found
      → human_review_required      (critical/major with insufficient evidence)
        → no_deviation_detected
```

---

## LLM integration

The adapter layer supports OpenAI, Anthropic, OpenRouter, any OpenAI-compatible
endpoint, Ollama, and an offline deterministic stub. Contract:

- Runs **after** the deterministic analysis, with its results in context.
- May emit only `possible_deviation`, `insufficient_evidence` or `sop_conflict`.
  It cannot produce `confirmed_deviation`.
- Every observation must carry a verbatim quote that `find_span()` can locate in
  stored text. Unlocatable observations are discarded, not shown.
- Every explanation is suffixed `[AI-assisted observation — requires verification]`.
- Document text is wrapped in `<<<UNTRUSTED_SOP_TEXT_BEGIN>>> … <<<UNTRUSTED_SOP_TEXT_END>>>`
  and the system prompt states that this region is data, never instructions.
- `guard_paid_request()` raises `LLMBudgetError` before any billable call when
  `LLM_ALLOW_PAID_REQUESTS` is false; `/providers/test` returns HTTP 402.
- Responses are cached by prompt hash, and token counts and costs are recorded
  per audit.

---

## Data model

15 tables.

```
User
SopDocument ─┬─ SopRevision ─┬─ DocumentSection
             │               └─ Requirement
             └─ (sop_code groups revisions)

ProcessReport ── ReportEvent

Audit ─┬─ AuditFinding ── Citation
       └─ (resolved_revision_id → SopRevision)

SopConflict   (revision_a, revision_b, fingerprint, severity, status)
ReviewAction  (append-only)
ApiAuditEvent (append-only)
ProviderConfig
ExportArtifact
```

Invariants:

- `ReviewAction` and `ApiAuditEvent` have **no update or delete path** in the API.
- A reviewer decision is stored alongside the machine finding, never over it.
- Citations carry character offsets; for PDFs the stored text is page-prefixed
  (`[page N]`) so an offset always maps back to a page.
- `SopConflict.fingerprint` is unique, so re-running detection does not
  duplicate conflicts.
- Uploaded files are stored by SHA-256 hash, not by user-supplied name.

---

## Frontend

- Same-origin `/api/*` calls; the Vite dev server proxies to the backend, and in
  production the backend serves the built SPA from the same port.
- Token in `localStorage['sop-auditor-token']`; a 401 clears it and dispatches
  `sop-auth-expired`, which the app shell listens for.
- `useAsync(fn, deps)` for loading/error state; `PageShell` guarantees that the
  page heading renders in **every** state, so a failed fetch never leaves a
  blank, untitled page.
- `can(role)` gates UI affordances. It is a convenience, not a control — the
  server enforces RBAC independently.
- Accessibility: one `<nav aria-label="Main navigation">`, a skip link on every
  page, `sr-only` captions on every table, visible focus rings, and colour
  always paired with a text label.

---

## Deployment shapes

**Development** — two processes, Vite proxying `/api` to uvicorn:

```
scripts/start.sh            # or .\scripts\start.ps1
```

**Single port** — the API serves the built SPA; one process, one port:

```
scripts/start.sh --prod     # or .\scripts\start.ps1 -Prod
```

**Container** — the same single-port shape:

```
docker compose up --build
```

Unknown non-`/api` paths fall through to `index.html` so client-side routes
survive a hard refresh, while unknown `/api` paths return JSON 404s rather than
the SPA shell. Static file serving is constrained to the build directory;
traversal attempts resolve to the SPA, never to a file outside it.
