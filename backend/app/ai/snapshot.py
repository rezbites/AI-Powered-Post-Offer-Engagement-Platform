"""Canonical input snapshot and cache key.

Everything the model is shown about a candidate is assembled here, in a
deterministic order, and hashed. That hash does two jobs:

* **Caching.** Dashboards re-render constantly. Without a cache key, every page
  load would re-analyse every visible candidate and bill for it. Identical
  candidate state must produce an identical hash so the stored analysis can be
  reused.
* **Change detection.** Any new interaction, stage completion or date change
  alters the snapshot, changes the hash, and correctly invalidates the cached
  analysis. Freshness therefore falls out of the data rather than needing a TTL
  that is either too eager or too stale.

The snapshot is also the grounding reference: the guardrail checks quoted
evidence against exactly this text, so the model cannot cite something it was
never shown.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

from app.db.models import Candidate, Interaction

# How much conversation to include. Recent messages carry nearly all the signal
# for a joining decision, and an unbounded transcript would make cost grow with
# history for no analytical gain.
MAX_INTERACTIONS = 12
MAX_CONTENT_CHARS = 1200


@dataclass(frozen=True)
class InteractionSnapshot:
    direction: str
    channel: str
    days_ago: int
    content: str


@dataclass(frozen=True)
class CandidateSnapshot:
    """Immutable view of everything the model gets to see."""

    candidate_id: str
    name: str
    role_title: str
    location: str
    status: str
    days_to_joining: int
    days_since_interaction: int | None
    stages_completed: int
    stages_total: int
    stages_overdue: int
    pending_stage: str | None
    interactions: list[InteractionSnapshot]

    def to_dict(self) -> dict:
        """Ordered, JSON-safe representation used for hashing and prompting."""
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "role_title": self.role_title,
            "location": self.location,
            "status": self.status,
            "days_to_joining": self.days_to_joining,
            "days_since_interaction": self.days_since_interaction,
            "stages_completed": self.stages_completed,
            "stages_total": self.stages_total,
            "stages_overdue": self.stages_overdue,
            "pending_stage": self.pending_stage,
            "interactions": [
                {
                    "direction": i.direction,
                    "channel": i.channel,
                    "days_ago": i.days_ago,
                    "content": i.content,
                }
                for i in self.interactions
            ],
        }

    def input_hash(self) -> str:
        """SHA-256 over the canonical JSON form.

        `sort_keys` and a fixed separator make the encoding stable across
        Python versions and dict insertion order - without both, logically
        identical snapshots could hash differently and quietly defeat the cache.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def candidate_text(self) -> str:
        """Only the candidate's own words.

        This is the corpus the grounding guardrail checks quoted evidence
        against. Recruiter messages are excluded deliberately: a model quoting
        the recruiter back at us is not evidence about the candidate.
        """
        return "\n".join(i.content for i in self.interactions if i.direction == "inbound")


def build_snapshot(
    candidate: Candidate,
    interactions: list[Interaction],
    *,
    today: date,
    stages_completed: int,
    stages_total: int,
    stages_overdue: int,
    pending_stage: str | None,
) -> CandidateSnapshot:
    """Assemble the snapshot.

    Timestamps are converted to *relative* day offsets rather than absolute
    dates. Two reasons: the model reasons better about "8 days ago" than about
    a calendar date, and an absolute date would change the hash every day even
    when nothing about the candidate had changed, defeating the cache.
    """
    ordered = sorted(interactions, key=lambda i: i.occurred_at, reverse=True)[:MAX_INTERACTIONS]

    snapshots: list[InteractionSnapshot] = []
    for interaction in reversed(ordered):  # oldest first reads naturally
        occurred = interaction.occurred_at.date()
        snapshots.append(
            InteractionSnapshot(
                direction=interaction.direction,
                channel=interaction.channel,
                days_ago=(today - occurred).days,
                content=interaction.content.strip()[:MAX_CONTENT_CHARS],
            )
        )

    last_interaction = max((i.occurred_at for i in interactions), default=None)
    days_since = (today - last_interaction.date()).days if last_interaction else None

    return CandidateSnapshot(
        candidate_id=candidate.id,
        name=candidate.name,
        role_title=candidate.role_title,
        location=candidate.location,
        status=candidate.status,
        days_to_joining=(candidate.joining_date - today).days,
        days_since_interaction=days_since,
        stages_completed=stages_completed,
        stages_total=stages_total,
        stages_overdue=stages_overdue,
        pending_stage=pending_stage,
        interactions=snapshots,
    )
