"""Evaluation harness for the SOP compliance engine.

Runs every labelled scenario in ``data/scenarios`` through the real audit pipeline
and scores the output against the ground-truth labels.

Metrics produced
----------------
* per-category precision / recall / F1 on (scenario, category) claims
* step-localisation accuracy for labels that name a step
* version-selection correctness (which revision the engine deemed authoritative)
* authority-blocking correctness
* escalation accuracy (human review required vs labelled expectation)
* citation validity (every quote must resolve to stored source text)
* false positives on positive-control scenarios and forbidden categories
* latency distribution, token usage and cost

Scoring philosophy
------------------
A *claim* is a (scenario, category) pair that the engine actively asserts. Purely
informational output (``compliant_observation`` / ``no_detected_deviation``) is not a
claim and is never scored as a false positive, but it is counted separately so that
suppression of real findings cannot hide behind it.

Usage
-----
    python -m eval.harness                       # deterministic engine, fresh temp DB
    python -m eval.harness --mode llm_assisted   # requires a configured provider
    python -m eval.harness --out eval/results/latest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SCENARIO_DIR = ROOT / "data" / "scenarios"

# Classifications that constitute an actively asserted claim.
CLAIM_CLASSIFICATIONS = {
    "confirmed_deviation",
    "possible_deviation",
    "insufficient_evidence",
    "sop_conflict",
}
INFORMATIONAL_CATEGORIES = {"compliant_observation"}


@dataclass
class CategoryScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positives": self.tp,
            "false_positives": self.fp,
            "false_negatives": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    kind: str
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    expected_categories: list[str] = field(default_factory=list)
    predicted_categories: list[str] = field(default_factory=list)
    expected_revision: str | None = None
    actual_revision: str | None = None
    expected_blocked: bool = False
    actual_blocked: bool = False
    expected_escalation: bool = False
    actual_escalation: bool = False
    outcome: str = ""
    duration_ms: int = 0
    citations_total: int = 0
    citations_invalid: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0.0

    def fail(self, message: str) -> None:
        self.passed = False
        self.failures.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "kind": self.kind,
            "passed": self.passed,
            "failures": self.failures,
            "expected_categories": sorted(self.expected_categories),
            "predicted_categories": sorted(self.predicted_categories),
            "version_selection": {
                "expected": self.expected_revision,
                "actual": self.actual_revision,
                "expected_blocked": self.expected_blocked,
                "actual_blocked": self.actual_blocked,
            },
            "escalation": {"expected": self.expected_escalation, "actual": self.actual_escalation},
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "citations": {"total": self.citations_total, "invalid": self.citations_invalid},
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "findings": self.findings,
        }


def load_scenarios() -> list[dict[str, Any]]:
    index_path = SCENARIO_DIR / "index.json"
    if not index_path.exists():
        raise SystemExit("No dataset found. Run 'python scripts/generate_dataset.py' first.")
    out = []
    for entry in sorted(SCENARIO_DIR.glob("S*.json")):
        out.append(json.loads(entry.read_text(encoding="utf-8")))
    return sorted(out, key=lambda s: s["id"])


async def run_evaluation(
    *, mode: str = "deterministic", limit: int | None = None, verbose: bool = True
) -> dict[str, Any]:
    from app.db import session_scope
    from app.models.entities import AuditFinding, Citation, ProcessReport, Requirement
    from app.services.audit.pipeline import run_audit

    scenarios = load_scenarios()
    if limit:
        scenarios = scenarios[:limit]

    results: list[ScenarioResult] = []
    scores: dict[str, CategoryScore] = {}
    step_hits = 0
    step_total = 0
    started = time.time()

    for scenario in scenarios:
        result = ScenarioResult(
            scenario_id=scenario["id"],
            title=scenario["title"],
            kind=scenario["kind"],
            expected_revision=scenario.get("expected_revision"),
            expected_blocked=bool(scenario.get("expected_authority_blocked")),
            expected_escalation=bool(scenario.get("expected_escalation")),
        )
        report_file = scenario["report_file"]

        with session_scope() as db:
            report = (
                db.query(ProcessReport)
                .filter(ProcessReport.file_name == report_file)
                .order_by(ProcessReport.id.desc())
                .first()
            )
            if report is None:
                result.fail(f"report {report_file} not found in the seeded database")
                results.append(result)
                continue

            t0 = time.perf_counter()
            audit = await run_audit(db, report, mode=mode)
            elapsed = int((time.perf_counter() - t0) * 1000)
            db.flush()

            result.duration_ms = audit.duration_ms or elapsed
            result.outcome = str(audit.outcome)
            result.actual_escalation = bool(audit.requires_human_review)
            result.actual_blocked = str(audit.outcome) == "blocked_unresolved_authority"
            resolution = audit.version_resolution or {}
            result.actual_revision = resolution.get("resolved_revision_code")
            result.tokens = int((audit.token_usage or {}).get("total_tokens", 0) or 0)
            result.cost_usd = float(audit.cost_usd or 0.0)

            findings = db.query(AuditFinding).filter(AuditFinding.audit_id == audit.id).all()
            requirement_steps: dict[int, str] = {}
            req_ids = [f.requirement_id for f in findings if f.requirement_id]
            if req_ids:
                for req in db.query(Requirement).filter(Requirement.id.in_(req_ids)).all():
                    requirement_steps[req.id] = (req.step_number or "").strip()

            citations = (
                db.query(Citation).filter(Citation.finding_id.in_([f.id for f in findings] or [-1])).all()
            )
            result.citations_total = len(citations)
            result.citations_invalid = sum(1 for c in citations if not c.verified)

            predicted: dict[str, set[str]] = {}
            predicted_classifications: dict[str, set[str]] = {}
            for finding in findings:
                step = requirement_steps.get(finding.requirement_id or -1, "")
                record = {
                    "code": finding.finding_code,
                    "category": str(finding.category),
                    "classification": str(finding.classification),
                    "severity": str(finding.severity),
                    "step": step or None,
                    "requirement_key": finding.requirement_key,
                    "title": finding.title,
                    "verification": str(finding.verification_status),
                    # Attribution is required to compare deterministic and
                    # LLM-assisted behaviour; without it the two runs are not
                    # separable in the machine-readable report.
                    "detector": str(finding.detector),
                }
                result.findings.append(record)
                if str(finding.classification) in CLAIM_CLASSIFICATIONS:
                    predicted.setdefault(str(finding.category), set()).add(step)
                    predicted_classifications.setdefault(str(finding.category), set()).add(
                        str(finding.classification)
                    )

            result.predicted_categories = sorted(predicted)

        # ---------------- scoring ----------------
        expected_entries = scenario.get("expected_findings") or []
        expected_by_category: dict[str, set[str]] = {}
        for entry in expected_entries:
            expected_by_category.setdefault(entry["category"], set()).add(entry.get("step") or "")
        result.expected_categories = sorted(expected_by_category)

        for category in set(expected_by_category) | set(predicted):
            score = scores.setdefault(category, CategoryScore())
            in_expected = category in expected_by_category
            in_predicted = category in predicted
            if in_expected and in_predicted:
                score.tp += 1
            elif in_expected:
                score.fn += 1
                result.fail(f"missed expected finding category '{category}'")
            else:
                if category in INFORMATIONAL_CATEGORIES:
                    continue
                score.fp += 1
                result.fail(f"unexpected finding category '{category}'")

        for entry in expected_entries:
            wanted = entry.get("classification")
            if not wanted:
                continue
            got = predicted_classifications.get(entry["category"], set())
            if wanted not in got:
                result.fail(
                    f"expected '{entry['category']}' to be classified '{wanted}', got "
                    + (", ".join(sorted(got)) or "no claim")
                )

        for category, steps in expected_by_category.items():
            for step in steps:
                if not step:
                    continue
                step_total += 1
                if step in predicted.get(category, set()):
                    step_hits += 1
                else:
                    result.fail(f"expected '{category}' at step {step}, engine pointed elsewhere")

        for forbidden in scenario.get("forbidden_categories") or []:
            if forbidden in predicted:
                result.fail(f"forbidden category '{forbidden}' was reported")

        if scenario.get("expect_zero_confirmed_deviations"):
            confirmed = [f for f in result.findings if f["classification"] == "confirmed_deviation"]
            if confirmed:
                result.fail(
                    "expected zero confirmed deviations, got "
                    + ", ".join(f"{f['category']}@{f['step']}" for f in confirmed)
                )

        if result.expected_blocked != result.actual_blocked:
            result.fail(
                f"authority blocking mismatch (expected {result.expected_blocked}, "
                f"got {result.actual_blocked})"
            )
        elif not result.expected_blocked and result.expected_revision != result.actual_revision:
            result.fail(
                f"version selection mismatch (expected {result.expected_revision}, "
                f"got {result.actual_revision})"
            )

        if result.expected_escalation != result.actual_escalation:
            result.fail(
                f"escalation mismatch (expected {result.expected_escalation}, got {result.actual_escalation})"
            )

        if result.citations_invalid:
            result.fail(f"{result.citations_invalid} invalid citation(s)")

        results.append(result)
        if verbose:
            mark = "PASS" if result.passed else "FAIL"
            print(f"  [{mark}] {result.scenario_id} {result.title[:58]:<58} {result.duration_ms:>5} ms")
            for failure in result.failures:
                print(f"         - {failure}")

    # ---------------- aggregate ----------------
    durations = [r.duration_ms for r in results] or [0]
    sorted_durations = sorted(durations)

    def pct(p: float) -> int:
        if not sorted_durations:
            return 0
        index = min(len(sorted_durations) - 1, round((p / 100) * (len(sorted_durations) - 1)))
        return sorted_durations[index]

    version_cases = [r for r in results if not r.expected_blocked]
    version_correct = sum(1 for r in version_cases if r.expected_revision == r.actual_revision)
    blocking_correct = sum(1 for r in results if r.expected_blocked == r.actual_blocked)
    escalation_correct = sum(1 for r in results if r.expected_escalation == r.actual_escalation)
    positives = [r for r in results if r.kind == "positive"]
    positive_clean = sum(1 for r in positives if r.passed)
    total_citations = sum(r.citations_total for r in results)
    invalid_citations = sum(r.citations_invalid for r in results)

    micro = CategoryScore(
        tp=sum(s.tp for s in scores.values()),
        fp=sum(s.fp for s in scores.values()),
        fn=sum(s.fn for s in scores.values()),
    )
    macro_f1 = statistics.fmean([s.f1 for s in scores.values()]) if scores else 0.0

    by_kind: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_kind.setdefault(r.kind, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(r.passed)

    return {
        "schema_version": "1.0.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "scenario_count": len(results),
        "scenarios_passed": sum(1 for r in results if r.passed),
        "pass_rate": round(sum(1 for r in results if r.passed) / len(results), 4) if results else 0.0,
        "by_kind": by_kind,
        "classification_metrics": {
            "micro": micro.to_dict(),
            "macro_f1": round(macro_f1, 4),
            "per_category": {k: v.to_dict() for k, v in sorted(scores.items())},
        },
        "step_localisation": {
            "labelled_steps": step_total,
            "correct": step_hits,
            "accuracy": round(step_hits / step_total, 4) if step_total else 1.0,
        },
        "version_selection": {
            "resolvable_cases": len(version_cases),
            "correct": version_correct,
            "accuracy": round(version_correct / len(version_cases), 4) if version_cases else 1.0,
            "blocking_cases": len(results),
            "blocking_correct": blocking_correct,
            "blocking_accuracy": round(blocking_correct / len(results), 4) if results else 1.0,
        },
        "escalation": {
            "cases": len(results),
            "correct": escalation_correct,
            "accuracy": round(escalation_correct / len(results), 4) if results else 1.0,
        },
        "citations": {
            "total": total_citations,
            "invalid": invalid_citations,
            "validity_rate": round((total_citations - invalid_citations) / total_citations, 4)
            if total_citations
            else 1.0,
        },
        "false_positive_control": {
            "positive_scenarios": len(positives),
            "clean": positive_clean,
            "clean_rate": round(positive_clean / len(positives), 4) if positives else 1.0,
        },
        "detector_attribution": _detector_attribution(results),
        "latency_ms": {
            "min": min(durations),
            "p50": pct(50),
            "p95": pct(95),
            "max": max(durations),
            "mean": round(statistics.fmean(durations), 1),
            "total_wall_seconds": round(time.time() - started, 2),
        },
        "cost": {
            "total_tokens": sum(r.tokens for r in results),
            "total_usd": round(sum(r.cost_usd for r in results), 6),
        },
        "scenarios": [r.to_dict() for r in results],
    }


def _detector_attribution(results: list[Any]) -> dict[str, Any]:
    """Split findings by the engine that produced them.

    The LLM is advisory: it may never originate a confirmed deviation, and the
    deterministic findings must be unaffected by whether the LLM ran. Recording
    the split makes both properties checkable from the machine-readable report
    rather than taken on trust.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for result in results:
        for finding in result.findings:
            detector = finding.get("detector") or "unknown"
            bucket = buckets.setdefault(
                detector, {"findings": 0, "by_classification": {}, "confirmed_deviations": 0}
            )
            bucket["findings"] += 1
            classification = finding.get("classification", "unknown")
            bucket["by_classification"][classification] = (
                bucket["by_classification"].get(classification, 0) + 1
            )
            if classification == "confirmed_deviation":
                bucket["confirmed_deviations"] += 1
    llm_confirmed = buckets.get("llm", {}).get("confirmed_deviations", 0)
    return {
        "by_detector": buckets,
        # Hard invariant: a language model must never be able to assert a
        # confirmed deviation, regardless of what it returns.
        "llm_confirmed_deviations": llm_confirmed,
        "llm_confirmed_deviation_invariant_held": llm_confirmed == 0,
    }


def print_summary(report: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(f"EVALUATION SUMMARY  (mode={report['mode']})")
    print("=" * 78)
    print(
        f"Scenarios passed       : {report['scenarios_passed']}/{report['scenario_count']} "
        f"({report['pass_rate'] * 100:.1f}%)"
    )
    for kind, bucket in sorted(report["by_kind"].items()):
        print(f"  {kind:<12}         : {bucket['passed']}/{bucket['total']}")
    micro = report["classification_metrics"]["micro"]
    print(f"Micro precision/recall : {micro['precision']:.3f} / {micro['recall']:.3f} (F1 {micro['f1']:.3f})")
    print(f"Macro F1               : {report['classification_metrics']['macro_f1']:.3f}")
    print(
        f"Step localisation      : {report['step_localisation']['correct']}/"
        f"{report['step_localisation']['labelled_steps']} "
        f"({report['step_localisation']['accuracy'] * 100:.1f}%)"
    )
    vs = report["version_selection"]
    print(
        f"Version selection      : {vs['correct']}/{vs['resolvable_cases']} "
        f"({vs['accuracy'] * 100:.1f}%)   blocking {vs['blocking_correct']}/{vs['blocking_cases']}"
    )
    esc = report["escalation"]
    print(f"Escalation accuracy    : {esc['correct']}/{esc['cases']} ({esc['accuracy'] * 100:.1f}%)")
    cit = report["citations"]
    print(
        f"Citations              : {cit['total']} total, {cit['invalid']} invalid "
        f"({cit['validity_rate'] * 100:.2f}% valid)"
    )
    fpc = report["false_positive_control"]
    print(f"Positive controls clean: {fpc['clean']}/{fpc['positive_scenarios']}")
    lat = report["latency_ms"]
    print(
        f"Latency (ms)           : p50 {lat['p50']}  p95 {lat['p95']}  max {lat['max']}  mean {lat['mean']}"
    )
    print(
        f"Tokens / cost          : {report['cost']['total_tokens']} tokens, "
        f"${report['cost']['total_usd']:.4f}"
    )
    print("\nPer-category:")
    print(f"  {'category':<26} {'TP':>4} {'FP':>4} {'FN':>4} {'prec':>7} {'rec':>7} {'F1':>7}")
    for category, score in report["classification_metrics"]["per_category"].items():
        print(
            f"  {category:<26} {score['true_positives']:>4} {score['false_positives']:>4} "
            f"{score['false_negatives']:>4} {score['precision']:>7.3f} "
            f"{score['recall']:>7.3f} {score['f1']:>7.3f}"
        )
    failed = [s for s in report["scenarios"] if not s["passed"]]
    if failed:
        print(f"\nFailing scenarios ({len(failed)}):")
        for scenario in failed:
            print(f"  {scenario['scenario_id']} {scenario['title']}")
            for failure in scenario["failures"]:
                print(f"      - {failure}")
    print("=" * 78)
    print("These figures describe engine behaviour on a synthetic benchmark. They are not")
    print("evidence of regulatory compliance and do not certify any manufacturing process.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SOP compliance evaluation benchmark.")
    parser.add_argument(
        "--mode",
        default="deterministic",
        choices=["deterministic", "llm_assisted"],
        help="analysis mode to benchmark",
    )
    parser.add_argument("--out", default=None, help="write the machine-readable JSON report here")
    parser.add_argument("--limit", type=int, default=None, help="only run the first N scenarios")
    parser.add_argument(
        "--reuse-db", action="store_true", help="use the configured database instead of a fresh temporary one"
    )
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    parser.add_argument(
        "--fail-under-f1",
        type=float,
        default=None,
        metavar="F1",
        help="exit non-zero if the overall finding F1 falls below this value (for CI gating)",
    )
    parser.add_argument(
        "--allow-invalid-citations",
        action="store_true",
        help="do not fail when a citation cannot be verified (never use this in CI)",
    )
    args = parser.parse_args()

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if not args.reuse_db:
        # ``ignore_cleanup_errors`` keeps the benchmark from failing on Windows
        # when an antivirus/indexer still holds a handle to a SQLite file after
        # the run. The engine is disposed explicitly below as well.
        temp_dir = tempfile.TemporaryDirectory(prefix="sop-eval-", ignore_cleanup_errors=True)
        os.environ["VAR_DIR"] = temp_dir.name

    sys.path.insert(0, str(ROOT / "scripts"))
    from seed import seed

    print(f"Preparing evaluation database ({'temporary' if temp_dir else 'configured'}) ...")
    summary = seed(reset=not args.reuse_db, quiet=True)
    print(
        f"  {summary['total_sop_revisions']} SOP revisions, {summary['total_reports']} reports, "
        f"{summary['conflicts']} conflicts\n"
    )

    print(f"Running {args.limit or 'all'} scenarios in '{args.mode}' mode ...")
    report = asyncio.run(run_evaluation(mode=args.mode, limit=args.limit, verbose=not args.quiet))
    print_summary(report)

    out_path = Path(args.out) if args.out else BACKEND / "eval" / "results" / f"{args.mode}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nMachine-readable report: {out_path}")

    if temp_dir is not None:
        # SQLite keeps the database file open through the connection pool. On
        # Windows an open handle blocks deletion, which previously turned a
        # fully passing benchmark into exit code 1.
        from app.db import engine

        engine.dispose()
        temp_dir.cleanup()

    # --- CI gates ----------------------------------------------------------
    # These are hard quality floors, not advisory numbers. An unverifiable
    # citation means the engine produced evidence that does not exist in the
    # source, which is the one failure mode this project must never ship.
    failures: list[str] = []

    invalid = report.get("citations", {}).get("invalid", 0)
    if invalid and not args.allow_invalid_citations:
        failures.append(f"{invalid} invalid citation(s) - evidence must resolve to stored source text")

    escalation = report.get("escalation", {})
    missed = escalation.get("cases", 0) - escalation.get("correct", 0)
    if missed > 0:
        failures.append(f"{missed} scenario(s) were escalated incorrectly or not escalated at all")

    version = report.get("version_selection", {})
    version_missed = version.get("blocking_cases", 0) - version.get("blocking_correct", 0)
    if version_missed > 0:
        failures.append(
            f"{version_missed} scenario(s) resolved an SOP revision that should have been blocked"
        )

    # A language model is advisory only. If one ever originates a confirmed
    # deviation the separation between deterministic adjudication and AI
    # assistance has broken, and no threshold should let that pass.
    attribution = report.get("detector_attribution", {})
    llm_confirmed = attribution.get("llm_confirmed_deviations", 0)
    if llm_confirmed:
        failures.append(
            f"{llm_confirmed} confirmed deviation(s) were produced by the LLM detector; "
            "only deterministic checks may confirm a deviation"
        )

    if args.fail_under_f1 is not None:
        f1 = float(report.get("classification_metrics", {}).get("micro", {}).get("f1", 0.0))
        if f1 < args.fail_under_f1:
            failures.append(f"micro F1 {f1:.3f} is below the required {args.fail_under_f1:.3f}")

    if report["scenarios_passed"] != report["scenario_count"]:
        failures.append(
            f"{report['scenario_count'] - report['scenarios_passed']} of "
            f"{report['scenario_count']} scenarios did not match their labels"
        )

    if failures:
        print("\nBENCHMARK FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("\nBenchmark gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
