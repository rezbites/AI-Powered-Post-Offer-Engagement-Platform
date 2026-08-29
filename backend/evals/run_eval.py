"""AI evaluation harness.

Replaces "the output looks good" with numbers.

    python -m evals.run_eval                 # mock: deterministic, free, CI-safe
    python -m evals.run_eval --provider gemini
    python -m evals.run_eval --compare       # both, side by side

## What the numbers mean, and do not mean

`expected_risk` in the golden set is **a competent recruiter's judgement**, not
a ground-truth outcome. This system has no historical joined/dropped data to
calibrate against, so band accuracy measures *agreement with a reviewer*, not
correctness. Reporting it as accuracy without that caveat would overclaim.

Signal precision and recall are firmer: whether a concern is present in a
transcript is close to objectively checkable.

The golden set deliberately contains cases the mock provider fails - negation,
paraphrase, tone. An eval containing only passing cases measures nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Import cost. Published Gemini Flash pricing per 1M tokens, USD. Adjust when
# it moves; the point is order-of-magnitude cost visibility, not billing.
GEMINI_INPUT_PER_MTOK = 0.075
GEMINI_OUTPUT_PER_MTOK = 0.30

GOLDEN_SET = Path(__file__).parent / "golden_set.json"


@dataclass
class CaseResult:
    id: str
    description: str
    expected_risk: str
    actual_risk: str
    model_risk: str
    expected_signals: set[str]
    actual_signals: set[str]
    status: str
    dropped_signals: int
    latency_ms: int
    tokens_in: int
    tokens_out: int
    injection_leaked: bool = False

    @property
    def band_exact(self) -> bool:
        return self.actual_risk == self.expected_risk

    @property
    def band_within_one(self) -> bool:
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        return abs(rank[self.actual_risk] - rank[self.expected_risk]) <= 1

    @property
    def true_positives(self) -> int:
        return len(self.expected_signals & self.actual_signals)

    @property
    def false_positives(self) -> int:
        return len(self.actual_signals - self.expected_signals)

    @property
    def false_negatives(self) -> int:
        return len(self.expected_signals - self.actual_signals)


@dataclass
class Report:
    provider: str
    cases: list[CaseResult] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        n = len(self.cases)
        if n == 0:
            return {}

        tp = sum(c.true_positives for c in self.cases)
        fp = sum(c.false_positives for c in self.cases)
        fn = sum(c.false_negatives for c in self.cases)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        latencies = sorted(c.latency_ms for c in self.cases)
        tokens_in = sum(c.tokens_in for c in self.cases)
        tokens_out = sum(c.tokens_out for c in self.cases)

        cost = (
            tokens_in / 1_000_000 * GEMINI_INPUT_PER_MTOK
            + tokens_out / 1_000_000 * GEMINI_OUTPUT_PER_MTOK
        )

        return {
            "cases": n,
            # A "valid" first pass means the model produced schema-conforming
            # output without needing the repair round.
            "schema_validity_pct": 100 * sum(c.status == "valid" for c in self.cases) / n,
            "repaired": sum(c.status == "repaired" for c in self.cases),
            "failed": sum(c.status == "failed" for c in self.cases),
            "band_exact_pct": 100 * sum(c.band_exact for c in self.cases) / n,
            "band_within_one_pct": 100 * sum(c.band_within_one for c in self.cases) / n,
            "signal_precision": precision,
            "signal_recall": recall,
            "signal_f1": f1,
            "false_positives": fp,
            "false_negatives": fn,
            # Signals whose quote was not found in the candidate's own words.
            # A rising number here is the earliest warning of hallucination.
            "grounding_drops": sum(c.dropped_signals for c in self.cases),
            "injection_leaks": sum(c.injection_leaked for c in self.cases),
            "latency_p50_ms": statistics.median(latencies),
            "latency_p95_ms": latencies[int(len(latencies) * 0.95) - 1] if len(latencies) > 1 else latencies[0],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "est_cost_usd": cost,
            "est_cost_per_1k_analyses_usd": cost / n * 1000 if n else 0.0,
        }


async def run_case(provider, scenario: dict, today: date) -> CaseResult:
    """Run one scenario through the real pipeline - not a shortcut path."""
    from app.ai import pipeline
    from app.ai.snapshot import CandidateSnapshot, InteractionSnapshot
    from app.domain.context import CandidateContext
    from app.domain.enums import CandidateStatus

    raw = scenario["snapshot"]
    snapshot = CandidateSnapshot(
        candidate_id=raw["candidate_id"],
        name=raw["name"],
        role_title=raw["role_title"],
        location=raw["location"],
        status=raw["status"],
        days_to_joining=raw["days_to_joining"],
        days_since_interaction=raw["days_since_interaction"],
        stages_completed=raw["stages_completed"],
        stages_total=raw["stages_total"],
        stages_overdue=raw["stages_overdue"],
        pending_stage=raw["pending_stage"],
        interactions=[InteractionSnapshot(**i) for i in raw["interactions"]],
    )

    inbound = [i for i in raw["interactions"] if i["direction"] == "inbound"]
    last_inbound_days = raw["days_since_interaction"]

    ctx = CandidateContext(
        candidate_id=raw["candidate_id"],
        name=raw["name"],
        status=CandidateStatus(raw["status"]),
        joining_date=today + timedelta(days=raw["days_to_joining"]),
        offer_date=today - timedelta(days=45),
        last_interaction_at=None
        if last_inbound_days is None
        else __import__("datetime").datetime.combine(
            today - timedelta(days=last_inbound_days),
            __import__("datetime").time.min,
            tzinfo=__import__("datetime").timezone.utc,
        ),
        unanswered_outbound=sum(
            1 for i in raw["interactions"] if i["direction"] == "outbound"
        )
        if not inbound
        else 0,
        total_interactions=len(raw["interactions"]),
        inbound_interactions=len(inbound),
        stages_total=raw["stages_total"],
        stages_completed=raw["stages_completed"],
        stages_overdue=raw["stages_overdue"],
    )

    outcome = await pipeline.analyse(provider, snapshot, ctx, today=today)

    # Blend exactly as production does - the engine decides the band, not the
    # model. Evaluating the model's raw band would measure something the
    # product never shows.
    from dataclasses import replace

    from app.domain import risk
    from app.domain.context import SignalView

    enriched = replace(
        ctx,
        signals=[SignalView(type=s.type, evidence=s.evidence) for s in outcome.analysis.signals],
    )
    assessment = risk.assess(enriched, today=today)
    blended = assessment.level.value if assessment else "LOW"

    # Injection check: the summary must not echo prompt scaffolding back.
    summary_lower = outcome.analysis.summary.lower()
    leaked = any(
        marker in summary_lower
        for marker in ("you are an assistant", "system prompt", "<candidate_data>")
    )

    return CaseResult(
        id=scenario["id"],
        description=scenario["description"],
        expected_risk=scenario["expected_risk"],
        actual_risk=blended,
        model_risk=outcome.analysis.risk_level.value,
        expected_signals=set(scenario["expected_signals"]),
        actual_signals={s.type.value for s in outcome.analysis.signals},
        status=outcome.status.value,
        dropped_signals=len(outcome.dropped_signals),
        latency_ms=outcome.latency_ms,
        tokens_in=outcome.tokens_in or 0,
        tokens_out=outcome.tokens_out or 0,
        injection_leaked=leaked,
    )


async def evaluate(provider_name: str, *, verbose: bool, limit: int | None = None) -> Report:
    from app.ai.factory import get_provider_by_name

    data = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    scenarios = data["scenarios"][:limit] if limit else data["scenarios"]

    provider, honoured = get_provider_by_name(provider_name)
    if not honoured:
        print(
            f"  ! {provider_name} unavailable (no API key). Served by mock instead.",
            file=sys.stderr,
        )

    report = Report(provider=provider.name.value)
    today = date.today()

    for scenario in scenarios:
        result = await run_case(provider, scenario, today)
        report.cases.append(result)
        if verbose:
            mark = "ok  " if result.band_exact else "MISS"
            print(
                f"  {mark} {result.id:28s} expected {result.expected_risk:6s} "
                f"got {result.actual_risk:6s}  signals {sorted(result.actual_signals)}"
            )

    return report


def print_report(report: Report) -> None:
    s = report.summary()
    print(f"\n{'=' * 68}")
    print(f"  EVAL — provider: {report.provider}   ({s['cases']} scenarios)")
    print("=" * 68)
    print("\n  Structured output")
    print(f"    schema valid first pass   {s['schema_validity_pct']:.1f}%")
    print(f"    repaired                  {s['repaired']}")
    print(f"    failed (fell back)        {s['failed']}")
    print("\n  Risk band (agreement with reviewer labels, not ground truth)")
    print(f"    exact                     {s['band_exact_pct']:.1f}%")
    print(f"    within one band           {s['band_within_one_pct']:.1f}%")
    print("\n  Signal extraction")
    print(f"    precision                 {s['signal_precision']:.2f}")
    print(f"    recall                    {s['signal_recall']:.2f}")
    print(f"    f1                        {s['signal_f1']:.2f}")
    print(f"    false positives           {s['false_positives']}")
    print(f"    false negatives           {s['false_negatives']}")
    print("\n  Guardrails")
    print(f"    grounding drops           {s['grounding_drops']}")
    print(f"    injection leaks           {s['injection_leaks']}")
    print("\n  Performance and cost")
    print(f"    latency p50 / p95         {s['latency_p50_ms']:.0f} / {s['latency_p95_ms']:.0f} ms")
    print(f"    tokens in / out           {s['tokens_in']} / {s['tokens_out']}")
    print(f"    est. cost this run        ${s['est_cost_usd']:.5f}")
    print(f"    est. cost / 1k analyses   ${s['est_cost_per_1k_analyses_usd']:.2f}")
    print()


def print_misses(report: Report) -> None:
    misses = [c for c in report.cases if not c.band_exact or c.false_negatives or c.false_positives]
    if not misses:
        print("  No misses.\n")
        return
    print(f"  Misses ({len(misses)}):")
    for c in misses:
        bits = []
        if not c.band_exact:
            bits.append(f"band {c.expected_risk}->{c.actual_risk}")
        if c.false_negatives:
            bits.append(f"missed {sorted(c.expected_signals - c.actual_signals)}")
        if c.false_positives:
            bits.append(f"spurious {sorted(c.actual_signals - c.expected_signals)}")
        print(f"    {c.id:28s} {'; '.join(bits)}")
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate AI extraction quality.")
    parser.add_argument("--provider", default="mock", choices=["mock", "gemini"])
    parser.add_argument("--compare", action="store_true", help="Run both providers.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit JSON for CI.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Only run the first N scenarios. Gemini's free tier allows 20 "
            "requests PER DAY, so a full 22-scenario run exhausts it."
        ),
    )
    args = parser.parse_args()

    providers = ["mock", "gemini"] if args.compare else [args.provider]
    reports = []

    for name in providers:
        report = await evaluate(name, verbose=args.verbose and not args.json, limit=args.limit)
        reports.append(report)

    if args.json:
        print(json.dumps({r.provider: r.summary() for r in reports}, indent=2))
        return 0

    for report in reports:
        print_report(report)
        print_misses(report)

    if args.compare and len(reports) == 2:
        a, b = reports[0].summary(), reports[1].summary()
        print("=" * 68)
        print(f"  {'metric':<28}{reports[0].provider:>18}{reports[1].provider:>18}")
        print("-" * 68)
        for key in ("band_exact_pct", "signal_recall", "signal_f1", "latency_p50_ms"):
            print(f"  {key:<28}{a[key]:>18.2f}{b[key]:>18.2f}")
        print()

    # Non-zero exit if anything failed outright or an injection leaked, so this
    # can gate CI without a human reading the table.
    worst = reports[-1].summary()
    return 1 if worst["failed"] or worst["injection_leaks"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
