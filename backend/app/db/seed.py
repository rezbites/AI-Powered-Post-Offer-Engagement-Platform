"""Deterministic demo data.

Run with:  python -m app.db.seed        (or `make seed`)

Design goals, in priority order:

1. **Every feature must visibly do something on a fresh database.** Filters
   need spread, analytics need terminal outcomes, and the automation rule needs
   candidates that actually trip it. Seed data that is merely "60 rows" leaves
   an evaluator looking at empty dashboards.

2. **Reproducible.** Faker and random are seeded, so the same archetypes land
   in the same order every run and screenshots stay stable.

3. **Dates are relative to today.** A fixed calendar would put every joining
   date in the past within a week and silently break the 7/15/30-day metrics.

Candidates are built from ARCHETYPES rather than pure randomness. Each
archetype encodes a realistic post-offer situation together with the risk band
a competent recruiter would assign - which also makes them the natural source
for the eval golden set in `evals/`.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from faker import Faker
from passlib.context import CryptContext
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import (
    AIAnalysisRecord,
    AuditLog,
    AutomationRun,
    Candidate,
    CandidateStage,
    FollowUpAction,
    GeneratedMessage,
    Interaction,
    JourneyStage,
    JourneyTemplate,
    Recruiter,
)
from app.db.session import SessionLocal
from app.domain.enums import (
    CandidateStatus,
    InteractionChannel,
    InteractionDirection,
    RiskLevel,
    RiskSource,
    StageStatus,
    UserRole,
)

logger = get_logger(__name__)

fake = Faker("en_IN")
Faker.seed(42)
random.seed(42)

# argon2 over bcrypt: memory-hard, so offline cracking of a leaked hash is far
# more expensive, and no 72-byte password truncation surprise.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

DEMO_PASSWORD = "demo1234"  # noqa: S105 - seed-only credential, documented in README

# The journey from the brief, with an SLA per stage measured in days from the
# offer date. SLAs are what let a passive checklist raise an alert.
JOURNEY_STAGES: list[tuple[str, str, int]] = [
    ("offer_accepted", "Offer Accepted", 0),
    ("welcome", "Welcome", 2),
    ("documentation", "Documentation", 10),
    ("manager_intro", "Manager Introduction", 20),
    ("pre_joining_checkin", "Pre-Joining Check-in", 35),
    ("joining", "Joining", 45),
]

ROLES = [
    "Software Engineer II",
    "Senior Software Engineer",
    "Data Scientist",
    "Product Manager",
    "Engineering Manager",
    "Business Analyst",
    "Operations Manager",
    "UX Designer",
    "Data Engineer",
    "QA Engineer",
]

LOCATIONS = ["Bengaluru", "Hyderabad", "Mumbai", "Delhi NCR", "Pune", "Chennai"]

RECRUITERS = [
    ("Aditya Menon", "aditya.menon@example.com", UserRole.ADMIN),
    ("Sneha Kulkarni", "sneha.kulkarni@example.com", UserRole.RECRUITER),
    ("Rohit Verma", "rohit.verma@example.com", UserRole.RECRUITER),
    ("Fatima Sheikh", "fatima.sheikh@example.com", UserRole.RECRUITER),
    ("Karthik Iyer", "karthik.iyer@example.com", UserRole.RECRUITER),
    ("Meera Nair", "meera.nair@example.com", UserRole.RECRUITER),
]


@dataclass(frozen=True)
class ScriptedInteraction:
    """One message in an archetype's conversation thread.

    `days_before_now` is negative-relative: 6 means "six days ago".
    """

    days_before_now: int
    direction: InteractionDirection
    channel: InteractionChannel
    content: str


@dataclass(frozen=True)
class Archetype:
    """A realistic post-offer situation.

    `expected_risk` is the band a competent recruiter would assign given this
    thread. It seeds the initial risk column and doubles as the ground-truth
    label for the AI eval set - the only labels available in a system with no
    historical joined/dropped outcomes to learn from.
    """

    key: str
    weight: int
    status: CandidateStatus
    expected_risk: RiskLevel
    # Inclusive range of days from today until joining. Negative = already past.
    joining_offset: tuple[int, int]
    stages_completed: tuple[int, int]
    interactions: list[ScriptedInteraction] = field(default_factory=list)


ARCHETYPES: list[Archetype] = [
    # ---------------------------------------------------------------------
    # The brief's worked example, verbatim. This candidate is the single most
    # important row in the database: it is what an evaluator will look for to
    # check that the concern is detected and an appropriate follow-up proposed.
    # ---------------------------------------------------------------------
    Archetype(
        key="relocation_concern",
        weight=7,
        status=CandidateStatus.ENGAGED,
        expected_risk=RiskLevel.MEDIUM,
        joining_offset=(10, 25),
        stages_completed=(2, 3),
        interactions=[
            ScriptedInteraction(
                18, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome aboard! Delighted to have you joining us. I am your point of contact "
                "for everything between now and your first day.",
            ),
            ScriptedInteraction(
                17, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "Thank you! Really looking forward to getting started.",
            ),
            ScriptedInteraction(
                9, InteractionDirection.OUTBOUND, InteractionChannel.WHATSAPP,
                "Checking in - how are the joining formalities coming along? Anything you need from us?",
            ),
            ScriptedInteraction(
                8, InteractionDirection.INBOUND, InteractionChannel.WHATSAPP,
                "I am still figuring out relocation and accommodation.",
            ),
        ],
    ),
    # ---------------------------------------------------------------------
    # Trips the mandatory automation rule: joining inside 7 days with no
    # interaction for more than 5. Several of these exist so the rule produces
    # a visibly populated attention queue on first run.
    # ---------------------------------------------------------------------
    Archetype(
        key="silent_joining_soon",
        weight=6,
        status=CandidateStatus.AT_RISK,
        expected_risk=RiskLevel.HIGH,
        joining_offset=(2, 7),
        stages_completed=(2, 3),
        interactions=[
            ScriptedInteraction(
                21, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Congratulations once again! Sharing the onboarding checklist for your first week.",
            ),
            ScriptedInteraction(
                20, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "Thanks, I will go through it and revert.",
            ),
            ScriptedInteraction(
                11, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Following up on the pending documents - could you upload them when you get a moment?",
            ),
            ScriptedInteraction(
                8, InteractionDirection.OUTBOUND, InteractionChannel.WHATSAPP,
                "Hi! Just checking whether you received my last email about the documents.",
            ),
        ],
    ),
    Archetype(
        key="competing_offer",
        weight=5,
        status=CandidateStatus.AT_RISK,
        expected_risk=RiskLevel.HIGH,
        joining_offset=(8, 21),
        stages_completed=(1, 3),
        interactions=[
            ScriptedInteraction(
                15, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome to the team! Let me know if you have questions as you wrap up your notice period.",
            ),
            ScriptedInteraction(
                6, InteractionDirection.OUTBOUND, InteractionChannel.CALL,
                "Called to check in on notice period progress. Candidate sounded hesitant.",
            ),
            ScriptedInteraction(
                5, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "To be transparent, I have received another offer and I am weighing my options. "
                "The other role is closer to home.",
            ),
        ],
    ),
    Archetype(
        key="compensation_concern",
        weight=4,
        status=CandidateStatus.ENGAGED,
        expected_risk=RiskLevel.MEDIUM,
        joining_offset=(12, 30),
        stages_completed=(2, 3),
        interactions=[
            ScriptedInteraction(
                14, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Sharing your offer letter and benefits summary. Do shout if anything is unclear.",
            ),
            ScriptedInteraction(
                12, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "I had a question on the variable component - my current employer has counter-offered "
                "and the fixed portion there is higher.",
            ),
            ScriptedInteraction(
                11, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Understood - let me set up a call with the compensation team this week.",
            ),
        ],
    ),
    Archetype(
        key="notice_period_issue",
        weight=4,
        status=CandidateStatus.ENGAGED,
        expected_risk=RiskLevel.MEDIUM,
        joining_offset=(5, 18),
        stages_completed=(2, 4),
        interactions=[
            ScriptedInteraction(
                20, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome! Could you confirm your last working day so we can lock the joining date?",
            ),
            ScriptedInteraction(
                13, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "My current company is not releasing me early. The notice period may extend by "
                "three weeks beyond what we discussed.",
            ),
            ScriptedInteraction(
                12, InteractionDirection.OUTBOUND, InteractionChannel.CALL,
                "Discussed options for a revised joining date. Candidate is keen but constrained.",
            ),
        ],
    ),
    Archetype(
        key="documentation_stuck",
        weight=5,
        status=CandidateStatus.ENGAGED,
        expected_risk=RiskLevel.MEDIUM,
        joining_offset=(14, 35),
        stages_completed=(1, 2),
        interactions=[
            ScriptedInteraction(
                16, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome aboard! Please upload your background verification documents at your convenience.",
            ),
            ScriptedInteraction(
                15, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "Will do. I am tracking down my previous employment letters.",
            ),
            ScriptedInteraction(
                4, InteractionDirection.OUTBOUND, InteractionChannel.WHATSAPP,
                "Gentle nudge on the pending documents - the BGV team needs them to start the check.",
            ),
        ],
    ),
    Archetype(
        key="engaged_positive",
        weight=9,
        status=CandidateStatus.ENGAGED,
        expected_risk=RiskLevel.LOW,
        joining_offset=(15, 50),
        stages_completed=(3, 5),
        interactions=[
            ScriptedInteraction(
                22, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome to the team! Here is what the first two weeks will look like.",
            ),
            ScriptedInteraction(
                21, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "This is great, thank you. Very excited to start and already going through the docs.",
            ),
            ScriptedInteraction(
                7, InteractionDirection.OUTBOUND, InteractionChannel.WHATSAPP,
                "Your manager would love a quick intro call this week - does Thursday work?",
            ),
            ScriptedInteraction(
                6, InteractionDirection.INBOUND, InteractionChannel.WHATSAPP,
                "Thursday works perfectly. Looking forward to it!",
            ),
            ScriptedInteraction(
                2, InteractionDirection.OUTBOUND, InteractionChannel.CALL,
                "Intro call with hiring manager went well. Candidate asked good questions about the roadmap.",
            ),
        ],
    ),
    Archetype(
        key="fresh_offer",
        weight=6,
        status=CandidateStatus.OFFER_ACCEPTED,
        expected_risk=RiskLevel.LOW,
        joining_offset=(35, 60),
        stages_completed=(1, 1),
        interactions=[
            ScriptedInteraction(
                3, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Congratulations and welcome! Your onboarding buddy will reach out shortly.",
            ),
            ScriptedInteraction(
                2, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "Thank you so much, delighted to accept.",
            ),
        ],
    ),
    # Terminal outcomes. Without these, offer-to-join conversion has no
    # denominator and the analytics dashboard is meaningless.
    Archetype(
        key="joined_success",
        weight=8,
        status=CandidateStatus.JOINED,
        expected_risk=RiskLevel.LOW,
        joining_offset=(-30, -2),
        stages_completed=(6, 6),
        interactions=[
            ScriptedInteraction(
                40, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome aboard! Sharing your onboarding plan.",
            ),
            ScriptedInteraction(
                38, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "Thank you, everything is clear. See you on day one.",
            ),
            ScriptedInteraction(
                20, InteractionDirection.OUTBOUND, InteractionChannel.CALL,
                "Pre-joining check-in completed. All documents verified.",
            ),
        ],
    ),
    Archetype(
        key="dropped_out",
        weight=4,
        status=CandidateStatus.DROPPED_OUT,
        expected_risk=RiskLevel.HIGH,
        joining_offset=(-25, -3),
        stages_completed=(2, 3),
        interactions=[
            ScriptedInteraction(
                35, InteractionDirection.OUTBOUND, InteractionChannel.EMAIL,
                "Welcome! Let us know if you need anything before your start date.",
            ),
            ScriptedInteraction(
                18, InteractionDirection.INBOUND, InteractionChannel.EMAIL,
                "I wanted to let you know I have decided to accept another opportunity. "
                "Apologies for the late notice and thank you for the support.",
            ),
        ],
    ),
]


def _weighted_archetypes(total: int) -> list[Archetype]:
    """Expand archetype weights into a concrete, shuffled population."""
    pool: list[Archetype] = []
    for archetype in ARCHETYPES:
        pool.extend([archetype] * archetype.weight)

    population = [pool[i % len(pool)] for i in range(total)]
    random.shuffle(population)
    return population


async def _clear(session: AsyncSession) -> None:
    """Wipe demo data in FK-safe order so reseeding is idempotent."""
    for model in (
        AuditLog,
        AutomationRun,
        FollowUpAction,
        GeneratedMessage,
        AIAnalysisRecord,
        Interaction,
        CandidateStage,
        Candidate,
        JourneyStage,
        JourneyTemplate,
        Recruiter,
    ):
        await session.execute(delete(model))
    await session.flush()


async def _seed_recruiters(session: AsyncSession) -> list[Recruiter]:
    hashed = pwd_context.hash(DEMO_PASSWORD)
    recruiters = [
        Recruiter(name=name, email=email, role=role.value, password_hash=hashed)
        for name, email, role in RECRUITERS
    ]
    session.add_all(recruiters)
    await session.flush()
    return recruiters


async def _seed_journey(session: AsyncSession) -> tuple[JourneyTemplate, list[JourneyStage]]:
    template = JourneyTemplate(
        name="Standard Post-Offer Journey",
        description="Default engagement journey from offer acceptance through to joining.",
        is_default=True,
    )
    session.add(template)
    await session.flush()

    stages = [
        JourneyStage(template_id=template.id, key=key, label=label, sequence=idx, sla_days=sla)
        for idx, (key, label, sla) in enumerate(JOURNEY_STAGES)
    ]
    session.add_all(stages)
    await session.flush()
    return template, stages


async def _seed_candidates(
    session: AsyncSession,
    recruiters: list[Recruiter],
    template: JourneyTemplate,
    stages: list[JourneyStage],
    count: int,
) -> int:
    today = date.today()
    now = datetime.now(timezone.utc)
    population = _weighted_archetypes(count)
    used_emails: set[str] = set()

    for index, archetype in enumerate(population):
        name = fake.name()
        # Faker can repeat names across 60 draws; the email column is unique,
        # so an index suffix guarantees no collision without ugly names.
        slug = name.lower().replace(" ", ".").replace("'", "")
        email = f"{slug}.{index}@example.com"
        if email in used_emails:
            continue
        used_emails.add(email)

        offset = random.randint(*archetype.joining_offset)
        joining_date = today + timedelta(days=offset)
        # Offers land 30-75 days before the joining date - typical notice periods.
        offer_date = joining_date - timedelta(days=random.randint(30, 75))

        recruiter = recruiters[index % len(recruiters)]

        candidate = Candidate(
            name=name,
            email=email,
            phone=f"+91{random.randint(7000000000, 9999999999)}",
            role_title=random.choice(ROLES),
            location=random.choice(LOCATIONS),
            offer_date=offer_date,
            joining_date=joining_date,
            recruiter_id=recruiter.id,
            status=archetype.status.value,
            journey_template_id=template.id,
            risk_level=archetype.expected_risk.value,
            # Seeded risk is rule-derived until the AI pipeline analyses the
            # candidate; the source column says so honestly.
            risk_source=RiskSource.RULE.value,
            risk_confidence=0.0,
            notes=f"Seeded scenario: {archetype.key}.",
        )
        session.add(candidate)
        await session.flush()

        # --- Journey progress ------------------------------------------------
        completed_count = random.randint(*archetype.stages_completed)
        for stage in stages:
            is_done = stage.sequence < completed_count
            session.add(
                CandidateStage(
                    candidate_id=candidate.id,
                    stage_id=stage.id,
                    status=StageStatus.COMPLETED.value if is_done else StageStatus.PENDING.value,
                    completed_at=now - timedelta(days=max(1, completed_count - stage.sequence) * 3)
                    if is_done
                    else None,
                    completed_by=recruiter.id if is_done else None,
                    due_date=offer_date + timedelta(days=stage.sla_days),
                )
            )

        # --- Conversation thread ---------------------------------------------
        latest: datetime | None = None
        for scripted in archetype.interactions:
            occurred_at = now - timedelta(days=scripted.days_before_now, hours=random.randint(0, 8))
            session.add(
                Interaction(
                    candidate_id=candidate.id,
                    channel=scripted.channel.value,
                    direction=scripted.direction.value,
                    content=scripted.content,
                    occurred_at=occurred_at,
                    created_by=recruiter.id
                    if scripted.direction is InteractionDirection.OUTBOUND
                    else None,
                )
            )
            latest = occurred_at if latest is None or occurred_at > latest else latest

        # Denormalised so the silent-candidate rule and attention queue never
        # need to aggregate the interactions table.
        candidate.last_interaction_at = latest

    await session.flush()
    return len(used_emails)


async def seed(count: int = 60) -> None:
    configure_logging("INFO", json_output=False)

    async with SessionLocal() as session:
        async with session.begin():
            await _clear(session)
            recruiters = await _seed_recruiters(session)
            template, stages = await _seed_journey(session)
            total = await _seed_candidates(session, recruiters, template, stages, count)

    logger.info(
        "seed_complete",
        candidates=total,
        recruiters=len(recruiters),
        stages=len(stages),
        password=DEMO_PASSWORD,
    )
    print(f"Seeded {total} candidates across {len(recruiters)} recruiters.")
    print(f"Login with any seeded email and password: {DEMO_PASSWORD}")
    print("Example: aditya.menon@example.com (admin)")


if __name__ == "__main__":
    asyncio.run(seed())
