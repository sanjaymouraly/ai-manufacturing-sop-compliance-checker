# AI Manufacturing SOP Compliance Checker

Checks manufacturing SOPs against evidence and flags compliance gaps, conflicts, and missing controls.

## Features

- Evidence-first SOP audit pipeline with quote-level verification.
- Finding classifications: confirmed deviation, possible deviation, insufficient evidence, SOP conflict, and no detected deviation.
- Role-based access with separation of duties across administrator, auditor, reviewer, and viewer roles.
- Append-only audit trail with versioned reviews and reviewer history.
- PDF, JSON, and CSV exports with citations, verification status, and unresolved conflicts.
- Analytics for findings, severity, missed steps, recurring conflicts, and review turnaround.

## Stack

Python, Node.js, FastAPI, Uvicorn, Pydantic, SQLAlchemy, pypdf, ReportLab, HTTPX, Pillow

## Clone

```bash
git clone https://github.com/sanjaymouraly/ai-manufacturing-sop-compliance-checker.git
cd ai-manufacturing-sop-compliance-checker
```

## Setup

```bash
# The project ships a Docker Compose setup.
docker compose up --build
```

See `docs/` or the compose file for environment variables and ports.

## Run

```bash
docker compose up --build
```

## Tests

```bash
pytest -q
```

## Notes

- Sample data, when included, is synthetic.

## License

MIT © 2025 [Sanjay Mouraly](https://github.com/sanjaymouraly)
