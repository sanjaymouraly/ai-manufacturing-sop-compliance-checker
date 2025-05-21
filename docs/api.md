# API Reference

Base URL: `/api` — 50 endpoints. Interactive documentation is generated from
the code and served by the running application:

- Swagger UI — <http://localhost:8000/api/docs>
- ReDoc — <http://localhost:8000/api/redoc>
- OpenAPI JSON — <http://localhost:8000/api/openapi.json>

This page covers conventions, authorisation and the shapes you need most. The
generated spec is always the authoritative reference.

---

## Conventions

**Authentication.** All routes except `/api/health`, `/api/auth/login` and `/`
require `Authorization: Bearer <jwt>`.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"auditor","password":"Auditor#12345"}' | jq -r .access_token)

curl -s http://localhost:8000/api/audits -H "Authorization: Bearer $TOKEN"
```

**Pagination.** List endpoints take `page` (1-based) and `page_size`, and return:

```json
{ "items": [], "total": 0, "page": 1, "page_size": 25, "pages": 1 }
```

**Errors.** `{"detail": "...", "request_id": "..."}`. Validation failures (422)
add an `errors` array of `{field, message}`. Every response carries
`X-Request-ID` and `X-Response-Time-ms`.

| Status | Meaning here |
| --- | --- |
| 400 | malformed input the schema could not reject |
| 401 | missing, expired or invalid token |
| 403 | authenticated but the role is not permitted |
| 409 | duplicate content hash (send `allow_duplicate=true` to override) |
| 415 | unsupported upload type |
| 422 | schema validation failure |
| 429 | rate limited (general or sign-in guard) |
| 402 | a paid LLM request was refused because `LLM_ALLOW_PAID_REQUESTS` is false |

**Rate limits.** General: `RATE_LIMIT_PER_MINUTE` (default 240/min/IP).
Sign-in: `LOGIN_RATE_LIMIT_PER_MINUTE` (default 10/min/IP) — a separate,
tighter brute-force guard. Automated suites must raise the second one rather
than retry into it.

---

## Roles

| | administrator | auditor | reviewer | viewer |
| --- | :-: | :-: | :-: | :-: |
| View documents, audits, findings | ✓ | ✓ | ✓ | ✓ |
| Upload SOPs and reports | ✓ | ✓ | | |
| Run an analysis (`POST /audits`) | ✓ | ✓ | | |
| Record a review decision | ✓ | | ✓ | |
| Resolve an SOP conflict | ✓ | | ✓ | |
| Manage providers and users | ✓ | | | |

**Separation of duties is enforced server-side:** the auditor who created an
audit cannot review its findings, even with the reviewer role. Hiding a button
in the UI is never the control.

---

## Authentication

```http
POST /api/auth/login        {"username": "...", "password": "..."}
                            → {"access_token", "token_type", "expires_in", "user"}
GET  /api/auth/me           → the current user
POST /api/auth/change-password  {"current_password", "new_password"}
```

---

## Documents and revisions

```http
GET    /api/documents?search=&process_area=&status=&page=&page_size=
GET    /api/documents/{document_id}
PATCH  /api/documents/{document_id}
POST   /api/documents/upload                      (multipart)
GET    /api/documents/revisions/{revision_id}
GET    /api/documents/revisions/{revision_id}/file
PATCH  /api/documents/revisions/{revision_id}/status
GET    /api/documents/revisions/{left_id}/diff/{right_id}
POST   /api/documents/scan-conflicts
```

Upload fields — `file` (required; `.pdf .docx .txt .md .csv .json`) plus
optional `sop_code`, `revision_code`, `status_override`, `effective_date`,
`expiry_date`, `approved_by`, `supersedes_revision_code`, `applicability_json`,
`allow_duplicate`.

`DocumentOut` includes derived fields: `revision_count`,
`latest_revision_code` (ordering only — **not** an authority claim),
`approved_revision_code` (null unless exactly one revision is approved), and
`has_open_conflicts` (open or escalated).

---

## Reports

```http
GET  /api/reports?search=&page=&page_size=
GET  /api/reports/{report_id}
POST /api/reports/upload       (multipart: file + declared_sop_code,
                                declared_revision_code, executed_at, allow_duplicate)
POST /api/reports/manual       (JSON, for records typed in by hand)
```

---

## Audits

```http
POST /api/audits               {"report_id": 12, "mode": "deterministic"}  → 201
GET  /api/audits?status=&outcome=&requires_human_review=&page=
GET  /api/audits/{audit_id}
GET  /api/audits/{audit_id}/history
POST /api/audits/{audit_id}/review     {"action": "...", "reason": "..."}
```

`GET /api/audits/{id}` returns the full analysis. The two parts worth knowing:

```jsonc
"version_resolution": {
  "resolved_revision_id": 13,
  "resolved_revision_code": "3",
  "authority_basis": "approved_and_in_force_at_execution_date",
  "blocked": false,
  "requires_human_review": false,
  "declared_revision_code": "3",
  "declared_matches_resolved": true,
  "candidates": [
    { "revision_id": 13, "revision_code": "3", "status": "approved",
      "effective_date": "2025-04-01", "expiry_date": null,
      "applicable": true, "in_force_at_execution": true,
      "authoritative": true, "reasons": ["..."] }
  ],
  "notes": ["Revision 3 is the only approved revision in force on the execution date."]
},

"pipeline_log": [
  { "stage": "document_lookup",    "duration_ms": 2, "at": "..." },
  { "stage": "version_resolution", "duration_ms": 4, "at": "..." }
  // ... one entry per stage, with stage-specific extras
]
```

---

## Findings and review

```http
GET  /api/findings?audit_id=&classification=&severity=&review_status=&category=&page=
GET  /api/findings/{finding_id}
GET  /api/findings/{finding_id}/history
POST /api/findings/{finding_id}/review   {"action": "accepted", "reason": "..."}
GET  /api/findings/queue/summary
```

Each finding carries `finding_code`, `classification`, `category`, `severity`,
`expected`, `observed`, `explanation`, `suggested_action`, `confidence`,
`detector` (`deterministic` | `llm`), `verification_status`,
`uncertainty_notes`, `review_status`, and `citations[]`:

```jsonc
{
  "source_type": "sop",
  "document_label": "SOP-WLD-700 rev 1",
  "quote": "Preheat the joint to at least 120 degC before striking the arc.",
  "char_start": 594, "char_end": 659,
  "page": 1, "section_path": "4.2",
  "verified": true, "verification_note": null
}
```

A review decision never overwrites the machine finding — both are retained and
exported together.

---

## SOP conflicts

```http
GET  /api/conflicts?status=open,escalated&severity=&page=
GET  /api/conflicts/{conflict_id}
GET  /api/conflicts/{conflict_id}/history
POST /api/conflicts/{conflict_id}/resolve   {"choice": "revision_b", "rationale": "..."}
```

`status` accepts a **comma-separated list**. Results are ordered by real
severity rank, not alphabetically. Seeded conflicts are `escalated`, not
`open` — filtering on `open` alone returns an empty list, which is why the UI
defaults to `open,escalated`.

Both sides are returned as `evidence_a` / `evidence_b` with
`{revision_id, document_label, revision_code, status, effective_date,
expiry_date, file_name, note, requirement_key, section_path, page, quote,
char_start, char_end, parameter, step_number}`.

---

## Exports

```http
POST /api/exports/audits/{audit_id}/{fmt}          fmt = pdf | json | csv
GET  /api/exports/audits/{audit_id}/{fmt}/inline
GET  /api/exports?audit_id=&page=
GET  /api/exports/{export_id}/download
```

Every export carries the decision-support disclaimer, the resolved revision and
its SHA-256, per-finding citations with verification status, unresolved
conflicts and reviewer history. Artifacts are versioned at
`var/exports/{audit_code}/{audit_code}_v{n}.{fmt}`.

---

## AI provider configuration (administrator only)

```http
GET    /api/providers
GET    /api/providers/catalogue        → dict keyed by provider_type
PUT    /api/providers                  {"provider_type": "openrouter", ...}
DELETE /api/providers/{config_id}
POST   /api/providers/test
POST   /api/providers/models         {"provider_type": "custom", "base_url": "...", "api_key": "..."}
POST   /api/providers/cache/clear
```

API keys are write-only. Responses contain `api_key_last4` and never the key.
`POST /providers/test` returns **402** unless `LLM_ALLOW_PAID_REQUESTS=true`.

`POST /providers/models` is a POST rather than a GET on purpose: discovery has
to work *before* a configuration is saved, and sending an API key as a query
string would write it into access logs and browser history. Omit `api_key` to
reuse the key already stored for that provider type; omit the body entirely to
list models for the active configuration.

### Spend controls on `PUT /api/providers`

| Field | Meaning |
| --- | --- |
| `allow_paid_requests` | Master switch. While `false`, any request that could be billed is refused **before** a connection is opened. |
| `request_budget_usd` | Cumulative USD ceiling. **Only effective for providers with published pricing** — see below. |
| `token_budget` | Cumulative token ceiling; `0` disables it. Checked before the request is sent. |

`request_budget_usd` is enforced against a pre-flight estimate derived from a
price list. When a model has no known rate that estimate is `0.00`, so
`spent + 0.00` can never exceed the cap and the ceiling silently stops
applying. Most gateways and all self-hosted endpoints publish no prices, so
**set `token_budget` whenever `POST /providers/models` returns entries with
`prompt_price_per_1k: null`.** The UI raises a warning in exactly that case.

Read-only counters `spent_usd` and `spent_tokens` are returned alongside.
`spent_usd` stays at `0.0` for unpriced providers, with
`pricing_known: false` on the usage object — the system declines to invent a
figure it cannot compute.

---

## Analytics, events, settings, users

```http
GET  /api/analytics/dashboard
GET  /api/analytics/trends
GET  /api/analytics/reviewers
GET  /api/events?entity_type=&actor=&page=     # append-only audit log
GET  /api/settings
GET  /api/users                                 # administrator
POST /api/users        {"username","password","role","email","full_name"}
PATCH /api/users/{user_id}
GET  /api/health
```

`GET /api/health` always answers, even when the database is unreachable — it
reports `status: "degraded"` with a `database` error string rather than
returning 500, and never includes a connection string or key.
