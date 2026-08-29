"""End-to-end API tests.

These exercise the real routers, services and SQL against a real database -
what the unit tests deliberately cannot reach. Two categories matter most:

* **Contract**: does a failure produce the documented error envelope, with a
  request_id, rather than a stack trace or a bare string?
* **Invariants that span layers**: does an override actually write an audit
  row, does running automation twice actually create nothing the second time.
  Both are properties the unit tests can only assert about pure functions.

SQLite is used rather than Postgres so the suite needs no running container and
stays fast. That is only possible because the schema was kept dialect-neutral -
app-layer enums, no `FILTER (WHERE ...)`, no native UUID columns - which was a
deliberate decision recorded in docs/decisions.md. The trade is that these
tests would not catch a Postgres-specific regression; the advisory-lock path in
particular is untested here and is noted as such.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import JourneyStage, JourneyTemplate, Recruiter
from app.modules.auth.security import hash_password

pytestmark = pytest.mark.integration

JOURNEY = [
    ("offer_accepted", "Offer Accepted", 0),
    ("welcome", "Welcome", 2),
    ("documentation", "Documentation", 10),
    ("manager_intro", "Manager Introduction", 20),
    ("pre_joining_checkin", "Pre-Joining Check-in", 35),
    ("joining", "Joining", 45),
]


@pytest_asyncio.fixture
async def client(tmp_path):
    """An app wired to a throwaway database.

    A file-backed SQLite database rather than in-memory: the app opens several
    connections, and each would get its own empty in-memory database.
    """
    from app.core.deps import get_current_actor
    from app.db.session import get_session
    from app.main import create_app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Seed the minimum a candidate needs to exist: a recruiter to own them and
    # a journey template to be assigned.
    async with factory() as session:
        recruiter = Recruiter(
            name="Test Recruiter",
            email="recruiter@example.com",
            role="admin",
            password_hash=hash_password("demo1234"),
        )
        template = JourneyTemplate(name="Standard", is_default=True)
        session.add_all([recruiter, template])
        await session.flush()
        session.add_all(
            [
                JourneyStage(template_id=template.id, key=k, label=l, sequence=i, sla_days=s)
                for i, (k, l, s) in enumerate(JOURNEY)
            ]
        )
        await session.commit()
        recruiter_id = recruiter.id

    app = create_app()

    async def _session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _session_override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        ac.recruiter_id = recruiter_id  # type: ignore[attr-defined]
        yield ac

    await engine.dispose()


async def admin_headers(client) -> dict[str, str]:
    """Log in as the seeded admin.

    The audit endpoint is admin-only, so reaching it requires a real token -
    which is itself worth exercising rather than bypassing with a dependency
    override.
    """
    res = await client.post(
        "/auth/login", json={"email": "recruiter@example.com", "password": "demo1234"}
    )
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def candidate_payload(recruiter_id: str, **overrides):
    today = date.today()
    payload = {
        "name": "Priya Sharma",
        "email": "priya@example.com",
        "role_title": "Software Engineer II",
        "location": "Bengaluru",
        "offer_date": str(today - timedelta(days=10)),
        "joining_date": str(today + timedelta(days=20)),
        "recruiter_id": recruiter_id,
    }
    payload.update(overrides)
    return payload


class TestErrorEnvelope:
    """Every failure must be machine-readable in the same shape."""

    async def test_not_found_uses_the_envelope(self, client):
        res = await client.get("/candidates/does-not-exist")
        assert res.status_code == 404
        body = res.json()
        assert body["error"]["code"] == "not_found"
        # The correlation id is what makes a user-reported failure findable.
        assert body["error"]["request_id"]

    async def test_validation_error_names_the_field(self, client):
        payload = candidate_payload(client.recruiter_id, email="not-an-email")
        res = await client.post("/candidates", json=payload)
        assert res.status_code == 422
        fields = res.json()["error"]["details"]["fields"]
        assert any("email" in f["loc"] for f in fields)

    async def test_request_id_is_returned_as_a_header(self, client):
        res = await client.get("/health")
        assert res.headers.get("X-Request-ID")

    async def test_inbound_request_id_is_honoured(self, client):
        """A trace started upstream must stay continuous across the hop."""
        res = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})
        assert res.headers["X-Request-ID"] == "trace-me-123"


class TestCandidateLifecycle:
    async def test_create_materialises_the_full_journey(self, client):
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        assert res.status_code == 201
        body = res.json()
        # Pending is real data from creation, which is what makes stage
        # drop-off measurable later.
        assert len(body["stages"]) == len(JOURNEY)
        assert body["journey"]["total"] == len(JOURNEY)

    async def test_new_candidate_is_assessed_not_left_at_zero(self, client):
        """A brand-new candidate must not read as '0% confident', which would
        imply an assessed judgement rather than an absent one."""
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        assert res.json()["risk"]["confidence"] > 0

    async def test_joining_before_offer_is_rejected(self, client):
        today = date.today()
        payload = candidate_payload(
            client.recruiter_id,
            offer_date=str(today),
            joining_date=str(today - timedelta(days=1)),
        )
        res = await client.post("/candidates", json=payload)
        assert res.status_code == 422

    async def test_duplicate_email_conflicts(self, client):
        payload = candidate_payload(client.recruiter_id)
        assert (await client.post("/candidates", json=payload)).status_code == 201
        res = await client.post("/candidates", json=payload)
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "conflict"

    async def test_unknown_recruiter_is_rejected(self, client):
        payload = candidate_payload("no-such-recruiter")
        res = await client.post("/candidates", json=payload)
        assert res.status_code == 422


class TestFiltersReconcile:
    """Filter counts must agree with analytics; a dashboard whose tiles
    disagree with its table is worse than one with no tiles."""

    async def _seed(self, client, n=3):
        today = date.today()
        for i in range(n):
            await client.post(
                "/candidates",
                json=candidate_payload(
                    client.recruiter_id,
                    name=f"Candidate {i}",
                    email=f"c{i}@example.com",
                    joining_date=str(today + timedelta(days=3 + i)),
                ),
            )

    async def test_total_matches_analytics(self, client):
        await self._seed(client, 3)
        listed = (await client.get("/candidates")).json()["total"]
        totals = (await client.get("/analytics/overview")).json()["totals"]
        assert listed == totals["total_offered"] == 3

    async def test_joining_window_excludes_past_dates(self, client):
        """Without the `>= today` guard, historical joiners would inflate
        'joining in the next 7 days'."""
        today = date.today()
        await client.post(
            "/candidates",
            json=candidate_payload(
                client.recruiter_id,
                email="past@example.com",
                offer_date=str(today - timedelta(days=60)),
                joining_date=str(today - timedelta(days=5)),
            ),
        )
        windows = (await client.get("/analytics/overview")).json()["joining_windows"]
        assert windows["next_7_days"] == 0
        assert windows["overdue"] == 1

    async def test_search_filter_narrows_results(self, client):
        await self._seed(client, 3)
        res = await client.get("/candidates", params={"search": "Candidate 1"})
        assert res.json()["total"] == 1

    async def test_empty_filter_value_is_ignored(self, client):
        """An unselected dropdown sends "", which must not filter on the empty
        string and return nothing."""
        await self._seed(client, 2)
        res = await client.get("/candidates", params={"role_title": ""})
        assert res.json()["total"] == 2


class TestOverrideAudit:
    async def _candidate(self, client):
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        return res.json()["id"]

    async def test_override_records_source_reason_and_confidence(self, client):
        cid = await self._candidate(client)
        res = await client.post(
            f"/candidates/{cid}/risk/override",
            json={"risk_level": "HIGH", "reason": "Manager raised a concern", "confidence": 0.8},
        )
        risk = res.json()["risk"]
        assert risk["level"] == "HIGH"
        assert risk["source"] == "human"
        assert risk["confidence"] == 0.8
        assert risk["override_reason"] == "Manager raised a concern"

    async def test_override_without_a_reason_is_rejected(self, client):
        cid = await self._candidate(client)
        res = await client.post(
            f"/candidates/{cid}/risk/override",
            json={"risk_level": "HIGH", "reason": ""},
        )
        assert res.status_code == 422

    async def test_override_writes_an_audit_row_with_before_and_after(self, client):
        """The evidence that makes human-in-the-loop verifiable rather than
        merely claimed."""
        cid = await self._candidate(client)
        await client.post(
            f"/candidates/{cid}/risk/override",
            json={"risk_level": "HIGH", "reason": "Spoke to them", "confidence": 1.0},
        )
        entries = (
            await client.get(
                "/audit",
                params={"entity_type": "candidate", "entity_id": cid},
                headers=await admin_headers(client),
            )
        ).json()
        override = next(e for e in entries if e["action"] == "risk_override")
        assert override["before"]["risk_level"] != override["after"]["risk_level"]
        assert override["after"]["risk_source"] == "human"

    async def test_revert_restores_non_human_source(self, client):
        cid = await self._candidate(client)
        await client.post(
            f"/candidates/{cid}/risk/override",
            json={"risk_level": "HIGH", "reason": "Spoke to them"},
        )
        res = await client.post(f"/candidates/{cid}/risk/revert")
        risk = res.json()["risk"]
        assert risk["source"] != "human"
        assert risk["override_reason"] is None


class TestEngagement:
    async def _candidate(self, client):
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        return res.json()["id"]

    async def test_logging_an_interaction_clears_the_silence_marker(self, client):
        cid = await self._candidate(client)
        before = (await client.get(f"/candidates/{cid}")).json()
        assert before["days_since_interaction"] is None

        await client.post(
            f"/candidates/{cid}/interactions",
            json={"channel": "email", "direction": "inbound", "content": "Looking forward to it!"},
        )
        after = (await client.get(f"/candidates/{cid}")).json()
        assert after["days_since_interaction"] == 0

    async def test_empty_interaction_content_is_rejected(self, client):
        cid = await self._candidate(client)
        res = await client.post(
            f"/candidates/{cid}/interactions",
            json={"channel": "email", "direction": "inbound", "content": "   "},
        )
        assert res.status_code == 422

    async def test_completing_a_stage_advances_the_journey(self, client):
        cid = await self._candidate(client)
        await client.patch(f"/candidates/{cid}/stages/welcome", json={"status": "completed"})
        detail = (await client.get(f"/candidates/{cid}")).json()
        assert detail["journey"]["completed"] == 1

    async def test_unknown_stage_key_is_a_404(self, client):
        cid = await self._candidate(client)
        res = await client.patch(f"/candidates/{cid}/stages/nonsense", json={"status": "completed"})
        assert res.status_code == 404


class TestAutomationIdempotency:
    """The property that decides whether an hourly job is useful or floods the
    queue by lunchtime."""

    async def _silent_candidate(self, client):
        today = date.today()
        res = await client.post(
            "/candidates",
            json=candidate_payload(
                client.recruiter_id,
                email="silent@example.com",
                offer_date=str(today - timedelta(days=40)),
                joining_date=str(today + timedelta(days=3)),
            ),
        )
        return res.json()["id"]

    async def test_second_run_creates_nothing(self, client):
        await self._silent_candidate(client)

        first = (await client.post("/automation/run", json={"draft_messages": False})).json()
        created_first = sum(r["actions_created"] for r in first)
        assert created_first > 0

        second = (await client.post("/automation/run", json={"draft_messages": False})).json()
        assert sum(r["actions_created"] for r in second) == 0
        assert sum(r["actions_skipped"] for r in second) == created_first

    async def test_run_history_is_recorded(self, client):
        await self._silent_candidate(client)
        await client.post("/automation/run", json={"draft_messages": False})
        runs = (await client.get("/automation/runs")).json()
        assert runs
        assert all(r["finished_at"] for r in runs)

    async def test_follow_ups_reference_the_rule_that_raised_them(self, client):
        await self._silent_candidate(client)
        await client.post("/automation/run", json={"draft_messages": False})
        follow_ups = (await client.get("/follow-ups")).json()
        assert follow_ups
        assert any(f["rule_key"] == "joining_soon_no_contact" for f in follow_ups)


class TestAuthAndRoles:
    async def test_login_returns_a_token(self, client):
        res = await client.post(
            "/auth/login", json={"email": "recruiter@example.com", "password": "demo1234"}
        )
        assert res.status_code == 200
        assert res.json()["access_token"]

    async def test_wrong_password_and_unknown_account_are_indistinguishable(self, client):
        """Differing responses would hand an attacker a user-enumeration
        oracle."""
        wrong = await client.post(
            "/auth/login", json={"email": "recruiter@example.com", "password": "nope"}
        )
        unknown = await client.post(
            "/auth/login", json={"email": "ghost@example.com", "password": "nope"}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    async def test_audit_rejects_anonymous_and_invalid_tokens(self, client):
        """Audit rows carry PII and every judgement a recruiter has made."""
        assert (await client.get("/audit")).status_code == 401
        res = await client.get("/audit", headers={"Authorization": "Bearer forged"})
        assert res.status_code == 401

    async def test_audit_allows_an_admin(self, client):
        res = await client.get("/audit", headers=await admin_headers(client))
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestAIPipelineEndpoints:
    async def test_analysis_is_served_and_labelled(self, client):
        """Demo Mode output must be identifiable as mock, never presented as a
        live model result."""
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        cid = res.json()["id"]
        await client.post(
            f"/candidates/{cid}/interactions",
            json={
                "channel": "email",
                "direction": "inbound",
                "content": "I am still figuring out relocation and accommodation.",
            },
        )

        analysis = (
            await client.post(f"/candidates/{cid}/ai/analyze", params={"provider": "mock"})
        ).json()

        assert analysis["provider"] == "mock"
        assert analysis["mode"] == "demo"
        assert analysis["analysis_status"] == "valid"
        assert any(s["type"] == "relocation_concern" for s in analysis["signals"])
        # Evidence must be a genuine span of what the candidate wrote.
        quote = next(s["evidence"] for s in analysis["signals"] if s["type"] == "relocation_concern")
        assert "relocation" in quote.lower()

    async def test_message_stays_a_draft_until_approved(self, client):
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        cid = res.json()["id"]

        draft = (
            await client.post(
                f"/candidates/{cid}/ai/message", params={"channel": "email", "provider": "mock"}
            )
        ).json()
        assert draft["status"] == "draft"

        approved = (await client.post(f"/ai/messages/{draft['id']}/approve")).json()
        assert approved["status"] == "sent_simulated"

    async def test_approved_messages_cannot_be_edited(self, client):
        """Rewriting an approved message would make the audit record describe
        something other than what was sent."""
        res = await client.post("/candidates", json=candidate_payload(client.recruiter_id))
        cid = res.json()["id"]
        draft = (
            await client.post(
                f"/candidates/{cid}/ai/message", params={"channel": "email", "provider": "mock"}
            )
        ).json()
        await client.post(f"/ai/messages/{draft['id']}/approve")

        res = await client.patch(f"/ai/messages/{draft['id']}", json={"body": "rewritten"})
        assert res.status_code == 409


class TestHealth:
    async def test_ready_reports_provider_and_mode(self, client):
        body = (await client.get("/health/ready")).json()
        assert body["checks"]["database"] == "ok"
        assert body["provider"] in {"mock", "gemini", "claude"}
        assert body["mode"] in {"demo", "live"}
