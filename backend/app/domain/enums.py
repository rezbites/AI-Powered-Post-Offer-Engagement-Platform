"""Canonical enumerations shared by the ORM, the API and the LLM schema.

Defined once, here, because the same values must line up in three places:
database columns, Pydantic request/response models, and the closed enums the
LLM is constrained to emit. Divergence between those three is exactly how a
model ends up returning a `next_action` the UI cannot render.

All inherit `str` so they serialise to plain strings in JSON and store as
VARCHAR — see `docs/decisions.md` on why native database enums were rejected.
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    """Likelihood the candidate does not join. Ordinal, not a probability."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        """Numeric order for sorting and for +/-1 band accuracy in evals."""
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[self.value]


class CandidateStatus(str, Enum):
    """Operational position in the pipeline — distinct from RiskLevel.

    JOINED and DROPPED_OUT are terminal. They exist so offer-to-join conversion
    is computable; the brief never states this, but the metric it asks for is
    impossible without recording an outcome.
    """

    OFFER_ACCEPTED = "offer_accepted"
    ENGAGED = "engaged"
    AT_RISK = "at_risk"
    JOINED = "joined"
    DROPPED_OUT = "dropped_out"

    @property
    def is_terminal(self) -> bool:
        return self in {CandidateStatus.JOINED, CandidateStatus.DROPPED_OUT}


class RiskSource(str, Enum):
    """Provenance of the current risk level.

    HUMAN always wins over AI and RULE; the UI surfaces this so a recruiter can
    see at a glance whether they are looking at a model output or a colleague's
    judgement.
    """

    RULE = "rule"
    AI = "ai"
    HUMAN = "human"


class StageStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class InteractionChannel(str, Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    CALL = "call"
    NOTE = "note"


class InteractionDirection(str, Enum):
    """INBOUND is candidate-authored and is what risk detection reads.
    OUTBOUND is recruiter-authored; a run of unanswered OUTBOUND messages is
    itself a risk signal."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SignalType(str, Enum):
    """Closed set of semantic signals the LLM may extract.

    Closed on purpose: an open-ended `signals` list would let injected text
    invent categories the application cannot act on, and would make signal
    precision/recall impossible to measure in the eval harness.
    """

    RELOCATION_CONCERN = "relocation_concern"
    COMPETING_OFFER = "competing_offer"
    COMPENSATION_CONCERN = "compensation_concern"
    NOTICE_PERIOD_ISSUE = "notice_period_issue"
    LOW_ENTHUSIASM = "low_enthusiasm"
    POSITIVE_INTENT = "positive_intent"

    @property
    def is_negative(self) -> bool:
        return self is not SignalType.POSITIVE_INTENT


class NextAction(str, Enum):
    """Closed set of recommended actions.

    Every value maps to a concrete affordance in the recruiter UI. Keeping this
    closed is the core guardrail: candidate-supplied text cannot cause the
    system to propose an action it does not know how to perform.
    """

    CALL_CANDIDATE = "CALL_CANDIDATE"
    SEND_RELOCATION_SUPPORT = "SEND_RELOCATION_SUPPORT"
    SEND_REMINDER = "SEND_REMINDER"
    MANAGER_INTRODUCTION = "MANAGER_INTRODUCTION"
    SCHEDULE_CONVERSATION = "SCHEDULE_CONVERSATION"
    ESCALATE = "ESCALATE"
    # A concern the recruiter has already acted on and the candidate is
    # working through. Without this the model had no way to say "handled,
    # check back" and would recommend repeating what was already sent.
    MONITOR = "MONITOR"
    NO_ACTION = "NO_ACTION"

    @property
    def label(self) -> str:
        return {
            "CALL_CANDIDATE": "Call candidate",
            "SEND_RELOCATION_SUPPORT": "Send relocation support",
            "SEND_REMINDER": "Send reminder",
            "MANAGER_INTRODUCTION": "Introduce hiring manager",
            "SCHEDULE_CONVERSATION": "Schedule a conversation",
            "ESCALATE": "Escalate to HR lead",
            "MONITOR": "Monitor - follow up to confirm",
            "NO_ACTION": "No action needed",
        }[self.value]


class AnalysisStatus(str, Enum):
    """How an analysis was produced — the failure-handling audit trail.

    VALID    first attempt parsed and validated cleanly
    REPAIRED second attempt succeeded after feeding the error back
    FAILED   both attempts failed; a deterministic fallback was stored instead
    """

    VALID = "valid"
    REPAIRED = "repaired"
    FAILED = "failed"


class ProviderName(str, Enum):
    """Recorded on every analysis so a Demo Mode row can never later be
    mistaken for genuine model output."""

    GEMINI = "gemini"
    CLAUDE = "claude"
    MOCK = "mock"


class MessageStatus(str, Enum):
    """Generated messages are drafts until a human approves them.

    This is the primary prompt-injection defence: no AI output produces an
    outward-facing side effect without a recruiter in the loop.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    SENT_SIMULATED = "sent_simulated"


class FollowUpStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class UserRole(str, Enum):
    RECRUITER = "recruiter"
    ADMIN = "admin"


class AutomationRunStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class AuditAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RISK_OVERRIDE = "risk_override"
    RISK_REVERT = "risk_revert"
    STAGE_COMPLETE = "stage_complete"
    STAGE_RESET = "stage_reset"
    MESSAGE_APPROVE = "message_approve"
    MESSAGE_SEND = "message_send"
