# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.

Open a [GitHub security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, or email the maintainers listed in `CONTRIBUTING.md`.

Please include: affected version or commit, reproduction steps, impact, and any
proof-of-concept. We aim to acknowledge within 5 working days.

Do not include real manufacturing records, customer data or production SOPs in a
report. Reproduce with the synthetic dataset instead (`scripts/generate_dataset.py`).

## Supported versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | yes       |
| < 1.0   | no        |

---

## Security model

### Threat: uploaded documents are untrusted input

SOPs and process reports arrive from outside the system and are treated as
hostile by default.

- **Prompt injection.** Every uploaded document is scanned for instruction-like
  text ("ignore previous instructions", "output compliant", "disregard the
  above", role-play framing, fake system prompts). A hit raises a
  `prompt_injection` finding, forces **human review**, and is surfaced in the UI
  and in exports. Implementation: `backend/app/utils/injection.py`.
- **Prompt isolation.** Document text is never concatenated into instructions.
  It is wrapped in explicit `<<<UNTRUSTED_SOP_TEXT_BEGIN>>> … <<<UNTRUSTED_SOP_TEXT_END>>>`
  delimiters, and the system prompt states that content inside those markers is
  data to analyse, never commands to follow.
- **No LLM authority over verifiable facts.** Numeric comparisons, unit
  conversion, step sequencing, version selection and citation verification are
  deterministic code. An LLM cannot upgrade a finding to `confirmed_deviation`,
  cannot invent a citation, and cannot clear an escalation.
- **Citation verification.** Every quote must resolve to stored source text at
  the stated offsets. Unverifiable quotes are marked invalid, capped at 0.4
  confidence and blocked from `confirmed` status. See `docs/architecture.md`.
- **File handling.** Extension and MIME allow-list, size cap
  (`MAX_UPLOAD_MB`), SHA-256 content hashing and duplicate detection. Files are
  stored under `var/storage/` by hash, never by user-supplied filename, so a
  crafted name cannot traverse the filesystem.

### Threat: credential and key exposure

- API keys are **never** sent to the browser. The provider API returns
  `api_key_last4` only (e.g. `4f2a`).
- Keys set through the UI are encrypted at rest before being written to the
  database.
- Keys are redacted from logs, error messages, exported reports and audit
  events. `.env` is git-ignored; `.env.example` contains no real values.
- **No chargeable request can be sent while `LLM_ALLOW_PAID_REQUESTS=false`**
  (the default). The "Test connection" button returns HTTP 402 in that state.
  `LLM_REQUEST_BUDGET_USD` caps spend per analysis.

### Authentication and authorisation

- Passwords are hashed with PBKDF2-HMAC-SHA256 (240,000 iterations, per-user
  random salt). Plaintext passwords are never stored or logged.
- Sessions use signed JWTs (`HS256`) with a configurable expiry.
- Four roles — administrator, auditor, reviewer, viewer — are enforced
  **server-side** on every route. Hiding a button in the UI is never the control.
- **Separation of duties:** the auditor who runs an analysis cannot sign off its
  findings. Only a reviewer or administrator can record a review decision.
- Sign-in is rate limited per client IP (`LOGIN_RATE_LIMIT_PER_MINUTE`,
  default 10/min) as a brute-force guard, independent of the general API limit.

### Audit trail integrity

- `ReviewAction` and `ApiAuditEvent` are **append-only**. There is no API that
  updates or deletes a row in either table.
- A reviewer decision never overwrites the machine finding; both are retained
  and exported together, so a disagreement stays visible.
- Original uploaded files are preserved byte-for-byte alongside their hash.

---

## Hardening checklist before any real deployment

This project ships developer defaults. It is **not** production-hardened out of
the box.

- [ ] Set a strong unique `SECRET_KEY`. The default literally says
      `dev-insecure-change-me-in-production`.
- [ ] Change `BOOTSTRAP_ADMIN_PASSWORD`, then delete or rotate the demo accounts
      (`admin`, `auditor`, `reviewer`) — their passwords are in this repository.
- [ ] Terminate TLS in front of the application. It serves plain HTTP.
- [ ] Replace SQLite with PostgreSQL (`DATABASE_URL`) for concurrent use.
- [ ] Restrict `CORS_ORIGINS` to your real origin; never leave it as `*`.
- [ ] Put `var/` on storage with access controls and backups — it holds uploaded
      documents and the audit trail.
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`.
- [ ] Review `RATE_LIMIT_PER_MINUTE` and `LOGIN_RATE_LIMIT_PER_MINUTE`; the
      in-process limiter is per-instance and resets on restart — use a shared
      limiter (or your ingress) behind a load balancer.
- [ ] Decide explicitly whether `LLM_ALLOW_PAID_REQUESTS` may be enabled, and
      confirm that sending SOP text to a third-party provider is permitted by
      your data-classification policy. Offline mode exists for when it is not.

## Known limitations with security relevance

- The rate limiter is in-memory and per-process, so it is ineffective across
  multiple replicas.
- JWTs are stateless; there is no server-side revocation list. Signing out
  clears the browser token only. Shorten `ACCESS_TOKEN_EXPIRE_MINUTES` if that
  matters to you.
- Uploaded files are not scanned for malware.
- The application has not undergone an independent penetration test.

See `docs/limitations.md` for the full, non-security list.
