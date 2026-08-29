"""Pure, I/O-free view of a candidate's situation.

Everything that decides *what a recruiter should do next* - risk scoring,
confidence, attention ranking, automation predicates - reads this structure
and nothing else. No ORM objects, no database session, no clock lookups beyond
an explicitly passed `today`.

That constraint is what makes the interesting logic unit-testable without a
database, and it is why the risk engine can be exercised across dozens of
scenarios in milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.domain.enums import CandidateStatus, RiskLevel, SignalType


@dataclass(frozen=True)
class SignalView:
    """An LLM-extracted concern plus the quote evidencing it."""

    type: SignalType
    evidence: str


@dataclass(frozen=True)
class CandidateContext:
    """Immutable snapshot used by every decision function."""

    candidate_id: str
    name: str
    status: CandidateStatus
    joining_date: date
    offer_date: date
    last_interaction_at: datetime | None
    # Number of consecutive outbound messages with no inbound reply after them.
    unanswered_outbound: int
    total_interactions: int
    inbound_interactions: int
    stages_total: int
    stages_completed: int
    stages_overdue: int
    signals: list[SignalView] = field(default_factory=list)
    # Which rules already have an unresolved follow-up for this candidate.
    # Deliberately not a bare boolean: a low-priority paperwork reminder must
    # not suppress a high-risk escalation, so predicates need to ask about
    # the specific rules they care about.
    open_follow_up_rules: frozenset[str] = frozenset()

    # --- Derived time quantities -----------------------------------------
    def days_to_joining(self, today: date) -> int:
        """Negative once the joining date has passed."""
        return (self.joining_date - today).days

    def days_since_interaction(self, today: date) -> int | None:
        """None when the candidate has never been contacted at all.

        Callers must handle None explicitly rather than defaulting it to 0 - a
        candidate with no contact is a *worse* case than one contacted today,
        and collapsing the two hides exactly the candidates most at risk.
        """
        if self.last_interaction_at is None:
            return None
        last = self.last_interaction_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (today - last.date()).days

    @property
    def has_open_follow_up(self) -> bool:
        """Whether any follow-up is open. Used for attention-queue ranking,
        where any in-flight work is a reason to rank a candidate lower."""
        return bool(self.open_follow_up_rules)

    def has_open_follow_up_from(self, rule_keys: frozenset[str]) -> bool:
        """Whether a follow-up from one of these specific rules is open."""
        return bool(self.open_follow_up_rules & rule_keys)

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def negative_signals(self) -> list[SignalView]:
        return [s for s in self.signals if s.type.is_negative]

    @property
    def has_positive_signal(self) -> bool:
        return any(s.type is SignalType.POSITIVE_INTENT for s in self.signals)


@dataclass(frozen=True)
class RiskAssessment:
    """Output of the risk engine.

    `factors` is human-readable and drives the UI's "Why?" panel directly; it
    is not debug output. A risk band a recruiter cannot interrogate is a band
    they will not trust or act on.
    """

    level: RiskLevel
    confidence: float
    score: float
    factors: list[str]
    rationale: str
