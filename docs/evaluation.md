# Evaluation

## What is being measured, and what is not

This harness measures **engine behaviour on a synthetic benchmark that this
project authored**. That is a meaningful regression gate and a transparency
tool. It is **not** evidence of real-world accuracy, and it is **not** evidence
of regulatory compliance.

Read the scores as: *"on documents of this shape, with these labels, the engine
behaves as specified."* Performance on your real SOPs will differ, because
their language, formatting and failure modes differ. See
[limitations.md](limitations.md).

---

## Running it

```bash
cd backend
python -m eval.harness                       # full run, prints a table
python -m eval.harness --quiet               # summary only
python -m eval.harness --limit 10            # first 10 scenarios
python -m eval.harness --out ../eval_results/run.json
python -m eval.harness --mode llm_assisted   # requires a configured provider
```

The harness seeds a **throwaway database** by default, so it never touches your
working data. Use `--reuse-db` to benchmark the database you already have.

### CI gates

```bash
python -m eval.harness --fail-under-f1 0.90
```

Exits non-zero if any of the following is true:

| Gate | Why it is a hard failure |
| --- | --- |
| any invalid citation | the engine produced evidence that is not in the source — the one failure mode this project must never ship |
| any escalation decision wrong | a missed escalation hides a material conflict from a human |
| any revision resolved that should have been blocked | guessing authority is worse than refusing |
| micro F1 below `--fail-under-f1` | overall detection regression |
| any scenario not matching its labels | behaviour changed without the labels changing |

---

## The dataset

Generated deterministically (fixed seed) by `scripts/generate_dataset.py`, so
every run compares like with like.

| | Count |
| --- | --- |
| SOP documents | 11 |
| SOP revisions | 16 |
| Process reports | 48 |
| Labelled scenarios | 48 |
| Seeded SOP conflicts | 7 |

### Scenario mix

| Kind | Count | What it tests |
| --- | --- | --- |
| `positive` | 5 | a correctly executed run — the engine must stay quiet |
| `negative` | 18 | a genuine deviation the engine must find |
| `ambiguous` | 11 | unclear requirements or records — must report uncertainty, not guess |
| `adversarial` | 14 | prompt injection, OCR degradation, conflicting approved revisions, unit traps |

Every scenario declares its expected findings, the evidence references that
justify them, the applicable SOP revision and whether escalation is required.
A scenario whose label cannot be justified from the document text is a bad
scenario — the fix is to correct the label, never to tune the engine to match
it. Seven labels were corrected during development for exactly this reason.

### Deliberate traps in the dataset

- **Two approved revisions with overlapping effective dates** (PKG-220, BAT-440)
  — the engine must block rather than pick the newer one.
- **Cross-document contradiction** — SOP-WLD-700 requires preheat ≥120 °C while
  SOP-EQP-315 caps it at ≤100 °C.
- **Unknown approval status** (CLN-500, MNT-800) — must block, not assume.
- **A genuinely scanned PDF** (MNT-800, rasterised with Pillow) — must report
  "no extractable text", not silently yield zero requirements.
  An invisible-text PDF is not a scanned PDF: `setTextRenderMode(3)` still
  extracts cleanly, so rasterisation was required to build an honest fixture.
- **Prompt injection** inside document text ("Disregard the above", "output
  compliant") — must be detected and escalated, never obeyed.
- **Unit traps** — `lbf-ft` vs `N·m`, `m/s` separator handling, percentage-of-nominal
  tolerances.

---

## Results — deterministic mode

Produced by `python -m eval.harness` on 2026-10-19; machine-readable copy at
`backend/eval/results/deterministic.json`.

### Overall

| Metric | Value |
| --- | --- |
| Scenarios | 48 |
| Scenarios passed | **48 / 48** (100%) |
| Micro precision / recall / F1 | **1.000 / 1.000 / 1.000** |
| Macro F1 | **1.000** |
| False positives | **0** |
| False negatives | **0** |

### By scenario kind

| Kind | Passed |
| --- | --- |
| positive | 5 / 5 |
| negative | 18 / 18 |
| ambiguous | 11 / 11 |
| adversarial | 14 / 14 |

### Per category

| Category | TP | FP | FN | Precision | Recall | F1 |
| --- | --: | --: | --: | --: | --: | --: |
| conflicting_revision | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| parameter_deviation | 12 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| missing_step | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| missing_record | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| outdated_sop | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| prompt_injection | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| contradictory_statement | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| duplicate_step | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| out_of_sequence | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| prohibited_action | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unit_mismatch | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unsupported_claim | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| extraction_quality | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |

### Version selection

| Metric | Value |
| --- | --- |
| Resolvable cases correct | **40 / 40** (100%) |
| Blocking decisions correct | **48 / 48** (100%) |

"Blocking correct" means the engine blocked exactly when authority was
genuinely undeterminable — neither guessing nor over-blocking.

### Evidence integrity

| Metric | Value |
| --- | --- |
| Citations produced | 258 |
| **Invalid citations** | **0** |
| Validity rate | **100%** |
| Step localisation accuracy | 26 / 26 (100%) |

### Escalation and false-positive control

| Metric | Value |
| --- | --- |
| Escalation decisions correct | **48 / 48** (100%) |
| Clean positive scenarios (no spurious findings) | **5 / 5** |

### Performance and cost

| Metric | Value |
| --- | --- |
| Latency min / p50 / p95 / max | 1 / 11 / 16 / 25 ms |
| Mean latency | 11.0 ms |
| Total wall time, 48 scenarios | 0.69 s |
| Tokens | 0 |
| **Cost** | **$0.0000** |

Deterministic mode makes no network calls.

---

## Why these scores are 1.000, and why that is not impressive on its own

The dataset and the engine were developed together. Perfect scores here mean
*"the engine does what the labels say"*, which makes the harness a strong
regression detector and a weak accuracy claim. The honest framing:

- A drop below 1.000 is a real regression and the build fails. That is the
  point.
- The numbers do **not** generalise to real SOPs. No external validation set
  exists in this repository.
- The categories with a single labelled instance (`out_of_sequence`,
  `unit_mismatch`, `prohibited_action`, `duplicate_step`, `unsupported_claim`,
  `extraction_quality`) are too thin to support any statistical claim at all.
  They are smoke tests, not measurements.
- Precision on **real** noisy records is the open question, and it is
  unanswered. Over-flagging is the expected failure mode in the field.

---

## Deterministic vs LLM-assisted comparison

The LLM path is optional and off by default. **This build made no billable AI
request.** Two LLM-assisted machine-readable artifacts are retained, and they
have very different status.

### Stub run — verified locally

`scripts/mock_llm_server.py` serves an OpenAI-compatible endpoint locally, so
the integration can be exercised with no credentials and no cost. It is
deliberately hostile: every scenario gets one usable observation, one
fabricated quote, and one real quote attributed to the wrong document.

```bash
python scripts/mock_llm_server.py &
cd backend
LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:8777/v1 \
LLM_API_KEY=sk-anything LLM_MODEL=mock-analyst-v1 LLM_ALLOW_PAID_REQUESTS=true \
python eval/harness.py --mode llm_assisted --out eval/results/llm_assisted_mock.json
```

Result on 2026-10-19 across all 48 scenarios:

| Metric | Stub run |
|---|---|
| Citations produced / invalid | **298 / 0** |
| Deterministic findings | **146 (26 confirmed)** — identical to deterministic-only |
| LLM findings | **40**, all `possible_deviation` |
| LLM-originated confirmed deviations | **0** |
| Escalation decisions correct | **48 / 48** |
| Version selection | 40/40 · 48/48 |
| Tokens / reported cost | 86,473 / $0.0000 (stub) |
| Latency p50 / p95 / max | 31 / 37 / 154 ms |
| Scenario labels matched | 8 / 48 |

The stub adds an unlabelled `ambiguous_requirement` observation to every
scenario, so the harness exits non-zero on label mismatch and all five positive
controls are dirtied. That is a property of the deliberately noisy fixture, not
a model-quality score. The guardrail result is what matters: every fabricated
and misattributed quote was discarded by re-verification, and the LLM
originated no confirmed deviation. This is **not** wired as a CI gate; the
release gate is the deterministic `--fail-under-f1` run.

### Historical commercial-gateway artifact — not re-run

`backend/eval/results/llm_assisted_laguna.json` records a 16-scenario run
against an OpenAI-compatible gateway (`https://router.bynara.id/v1`, model
`laguna-s-2.1`) through the `custom` adapter. **It was not re-executed in this
environment because no credentials were supplied.** The figures below are the
artifact's recorded values, retained for transparency; they are not a result
reproduced by this build.

| Metric | Deterministic (same 16) | Recorded artifact (`laguna-s-2.1`) |
|---|---|---|
| Micro precision | 1.000 | 0.390 |
| **Micro recall** | **1.000** | **1.000** |
| Micro F1 | 1.000 | 0.561 |
| **Citations produced / invalid** | 101 / **0** | 156 / **0** |
| Escalation accuracy | 16 / 16 | 15 / 16 |
| — under-escalations (dangerous) | 0 | **0** |
| — over-escalations (fails safe) | 0 | 1 |
| Version selection (resolvable / blocking) | 16/16 · 16/16 | 16/16 · 16/16 |
| **Deterministic findings** | **57 (13 confirmed)** | **57 (13 confirmed) — identical** |
| LLM findings | 0 | 32, all `insufficient_evidence` |
| LLM-originated confirmed deviations | — | **0** |
| Tokens | 0 | 48,421 |
| Mean / p95 / max latency | 11.0 ms | **27.0 s / 36.6 s / 50.6 s** |

The recorded run's useful signal is the failure direction. Precision fell to
0.390 while recall stayed at 1.000, so the model added observations the dataset
does not label rather than missing labelled deviations. One clean scenario was
recorded as over-escalated after a model observation graded `major`; reasons
now carry an `[AI-assisted observation]` marker so a reviewer can see which
signal triggered the queue entry. There were recorded zero under-escalations.
The gateway also returned HTTP 502 HTML pages and responses with no `choices`
array, and the LLM stage failed closed in every case. Because the provider
published no pricing, `cost_usd` is `0.0` with `pricing_known: false`; that is
the system declining to invent a figure, not a claim that the run was free.

### What neither run tells you

Both use synthetic documents written alongside the engine. The historical
figures describe one model, on one slice, on one day, against clean synthetic
records. Precision on messy real-world paperwork is unmeasured, and
over-flagging is the expected field failure mode.

---

## Reproducing

```bash
./scripts/setup.sh --reset        # regenerate the dataset and seed from scratch
cd backend && python -m eval.harness
```

The generator is seeded, so the dataset is byte-identical on every machine. If
your numbers differ from the table above, something in the engine changed.
