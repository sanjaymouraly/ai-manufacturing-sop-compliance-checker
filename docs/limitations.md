# Limitations

Read this before using the tool for anything that matters.

## The one that matters most

**This system does not certify compliance.** It does not approve product
release, does not replace a qualified reviewer, and is not a validated system
under GxP, ISO 9001, IATF 16949, FDA 21 CFR Part 11, EU Annex 11 or any
comparable framework.

`no_detected_deviation` means *"these specific automated checks did not find a
problem"*. It does not mean the run was correct, the product is good, or the
record is complete. The phrase is used everywhere in the UI and in every export
precisely so that nobody reads it as a pass mark.

Every output requires review by someone qualified to make the decision. The
tool exists to make that person faster and better-informed, not to replace
their judgement.

---

## What the engine cannot do

### Document understanding

- **English only.** Requirement extraction, negation handling, unit parsing and
  injection detection are all English-language. Other languages will parse
  poorly and fail silently rather than loudly.
- **Text-based documents.** Diagrams, flowcharts, photographs, engineering
  drawings, annotated screenshots and handwriting are not interpreted. A
  requirement that exists only in a diagram will not be extracted.
- **Tables are flattened.** Complex merged-cell layouts, nested tables and
  multi-column parameter matrices lose structure. Parameters inside them may be
  missed or mis-associated.
- **No OCR engine is bundled.** `ENABLE_OCR` exists, but no OCR backend is
  installed by default. Scanned PDFs are reported as having no extractable text
  — which is the honest outcome — rather than being guessed at. The synthetic
  dataset deliberately includes one genuinely scanned PDF (MNT-800) that
  produces zero requirements, so this behaviour is visible rather than hidden.
- **Requirement extraction is pattern-based**, not semantic. Unusual phrasing,
  implicit requirements, requirements split across sentences, and requirements
  that depend on an external referenced standard will be missed.

### Analysis

- **Conditional and optional steps are handled conservatively.** "If the
  temperature exceeds X, then do Y" is recognised, but deeply nested or
  cross-referenced conditionals may be reported as ambiguous rather than
  evaluated.
- **No process-engineering knowledge.** The engine compares what the SOP says
  with what the record says. It does not know that a particular deviation is
  physically harmless, or that a nominally compliant run was dangerous.
- **Evidence matching is lexical.** A report that describes the same action in
  entirely different vocabulary may not be paired with its requirement,
  producing a false "missing step".
- **Over-flagging is the expected failure mode on real data.** Precision on
  messy, real-world records is unmeasured. Expect to tune
  `EVIDENCE_MATCH_THRESHOLD`.
- **Cross-document reasoning is limited** to explicit conflicts between stored
  revisions. The engine does not build a knowledge graph of the SOP library.

### Evaluation

- **The benchmark is synthetic and self-authored.** 48 scenarios written
  alongside the engine. The 1.000 F1 means the engine matches its own labels —
  a strong regression gate, not an accuracy claim. There is no external
  validation set in this repository.
- Several categories have a single labelled instance. Those are smoke tests;
  no statistical conclusion can be drawn from them.
- **No successful live structured-output benchmark has been performed in this
  build.** No paid credentials were provided. A local stub run was executed and
  its guardrails held (298 citations, 0 invalid; no LLM-originated confirmed
  deviation). Local Ollama connected and completed a plain inference, but its
  observation stage returned repeated tokens or non-JSON and failed closed. A
  historical 16-scenario commercial-gateway artifact is bundled but was not
  re-run; its figures are not reproduced here.

---

## Operational limitations

- **SQLite by default.** Fine for a single user or a demo; it serialises
  writes. Use `DATABASE_URL` with PostgreSQL for concurrent use.
- **The rate limiter is in-process.** It is per-replica and resets on restart,
  so it is ineffective behind a load balancer. Use a shared limiter or your
  ingress.
- **JWTs are stateless.** There is no server-side revocation. Signing out
  clears the browser token only; an already-issued token stays valid until it
  expires.
- **Single-node file storage.** Uploads live on local disk under `var/storage`.
  There is no object-store backend.
- **No background workers.** Analyses run inside the request. A very large
  document will hold a connection for the duration.
- **No malware scanning** on uploaded files.
- **No independent penetration test** has been performed.

---

## LLM-specific caveats

- **An LLM can be wrong confidently.** That is why it cannot produce a
  `confirmed_deviation`, cannot supply an unverifiable quote, and labels every
  observation `[AI-assisted observation — requires verification]`.
- **Sending documents to a third-party provider is a data-governance
  decision.** In `llm_assisted` mode, SOP and report text leaves your
  infrastructure. Offline deterministic mode exists for when that is not
  acceptable, and it is the default.
- **Prompt-injection defence is best-effort.** The scanner catches known
  patterns and the prompts isolate untrusted content, but no defence is
  complete. The real protection is structural: the LLM has no authority over
  verifiable facts, so a successful injection still cannot make the system
  declare something compliant.
- **A dollar budget cannot bound a provider that publishes no prices.** This
  was found in live testing, not in theory. `estimate_cost` returns `0.0` when
  a model has no known rate, so `spent + 0.0` never exceeds any cap and
  `LLM_REQUEST_BUDGET_USD` silently stops applying. Most gateways and all
  self-hosted endpoints publish nothing. `LLM_TOKEN_BUDGET` therefore exists
  as a ceiling that needs no price list; set it whenever pricing is unknown.
  The on/off switch (`LLM_ALLOW_PAID_REQUESTS`) is unaffected and still
  refuses before any network call.
- **Reported cost is `0.0` with `pricing_known: false` for unpriced
  providers.** That is not a claim that the run was free; it is the system
  declining to invent a figure. Check your provider's own billing.
- **A model assigns its own severity, and severity drives escalation.** In the
  bundled historical `laguna-s-2.1` artifact, one clean scenario was recorded as
  routed to human review because the model graded an unconfirmed observation
  `major`. Escalation errs toward review by design, and across that recorded run
  there were **zero under-escalations** — but it does mean an advisory component
  can change an audit's outcome. Escalation reasons are marked
  `[AI-assisted observation]` so reviewers can tell which signal triggered the
  queue entry.
- **Latency is substantial.** The historical artifact recorded a mean of **27 s
  per audit**, with a maximum of 50.6 s — roughly 2,400× the current
  deterministic p50 of 11 ms.
  Plan for asynchronous processing if you enable AI assistance at volume.
- **Providers fail in non-JSON ways.** In this build `qwen3.5:4b` returned
  non-JSON and `gemma3:1b` repeated tokens; the historical gateway artifact also
  recorded HTTP 502 Cloudflare *HTML* pages and responses with no `choices`
  array. These are handled: the LLM stage fails closed and the audit completes
  on deterministic findings. Expect it, and do not treat an AI-assisted run as
  guaranteed to include AI output.

---

## Known issues

| Issue | Impact | Status |
| --- | --- | --- |
| Scanned PDFs yield no requirements without an OCR engine | MNT-800 extracts 0 requirements | by design; documented and visible in the UI |
| Single-instance rate limiting | ineffective across replicas | documented in `SECURITY.md` |
| Demo credentials are in the repository | anyone can read them | must be rotated before any deployment |
| `latest_revision_code` is ordering only | could be misread as authority | labelled in the UI; authority comes from version resolution |
| Dollar budget is inoperative for unpriced providers | `LLM_REQUEST_BUDGET_USD` never trips | mitigated by `LLM_TOKEN_BUDGET`; documented above |
| An AI observation graded major/critical can force escalation | over-escalation wastes reviewer time | reasons marked `[AI-assisted observation]`; 0 under-escalations in the historical artifact |
| Historical artifact reports LLM-assisted precision 0.390 on 16 scenarios | more findings to triage | deterministic findings unchanged; artifact not re-run in this build; see `docs/evaluation.md` |

---

## Verified in this build

- **The Docker image builds and runs.** `docker build -t sop-auditor:final .`
  produced a 444 MB image on Docker 29.8.2 with Linux containers. A container
  started with a fresh volume and `SEED_ON_START=true` reported `healthy`, ran
  the entrypoint migrations, generated and seeded the synthetic dataset, served
  the SPA, accepted the demo login, and completed a deterministic audit with
  three findings and six valid citations. The image was not published to a
  registry.

## Not verified in this build

Stated plainly, because an unverified claim is worse than a missing one:

- **The PowerShell setup and start scripts were executed on this Windows
  host.** `scripts/setup.ps1 -Reset` regenerated the dataset and seeded the
  database, and `scripts/start.ps1 -ApiPort 8011 -WebPort 5181` brought both
  services up with the Vite proxy pointing at the custom API port. The POSIX
  scripts and CI Windows legs were not run here.
- **No billable hosted provider has been contacted in this build.** The
  `custom` adapter was exercised against the local stub; local Ollama was
  contacted but did not produce schema-valid output. The **OpenAI, Anthropic
  and OpenRouter adapters have not contacted their live endpoints** — they are
  covered by unit tests against mocked transports. The bundled historical
  `laguna-s-2.1` artifact was not re-run.
- **No OCR engine has been exercised.** The code path exists and reports
  confidence; it has not been run against a real OCR backend.
- **PostgreSQL has not been tested.** The ORM is database-agnostic and the
  migration uses batch mode, but every test run in this build used SQLite.
