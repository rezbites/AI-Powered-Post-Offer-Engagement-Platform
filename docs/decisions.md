# Engineering decisions

Every significant choice, what was rejected, and the condition under which it
should be revisited. A decision without a stated reversal trigger is a decision
nobody will ever revisit.

---

## 1. Hybrid risk model, not pure-LLM

**Options considered**

| Option | Verdict |
|---|---|
| Pure LLM — ask the model for a band | Rejected |
| Pure rules — thresholds on dates and counts | Rejected |
| **Hybrid — rules own countable facts, LLM owns semantics** | **Chosen** |

**Why.** A model asked to output "HIGH" cannot be audited, drifts between
versions, and gives a recruiter nothing to disagree with. Pure rules cannot
read *"I am still figuring out relocation and accommodation"* and understand it
as a concern. Splitting by what each is good at keeps the band reproducible and
explainable while still catching semantic signal.

**Advantages.** Deterministic and testable; every band decomposes into weighted
factors the UI can show; model failure degrades to rules rather than to nothing.

**Disadvantages.** Weights are hand-tuned, so the model encodes my priors.
There is a second system to maintain. Rules and LLM can disagree — though that
disagreement is captured rather than hidden.

**Revisit when** real joined/dropped outcomes exist. Then the weights should be
fitted rather than asserted, and this becomes a calibrated classifier.

---

## 2. Confidence derived, not self-reported

**Rejected:** adding `confidence` to the LLM schema and using what comes back.

**Why.** Self-reported LLM confidence tracks fluency more than evidence and is
unfalsifiable — there is nothing to check it against. Confidence is instead
computed from observable properties: evidence volume, whether the candidate
ever replied, recency, quote support, and agreement between the rule and signal
layers.

The model is *still asked* for its confidence, and it is stored as
`model_confidence`. Keeping both makes the gap measurable, which is a real
calibration signal rather than a discarded field.

**Disadvantage.** The derived number is itself uncalibrated — an ordinal, not a
probability. The UI says "heuristic" for exactly this reason.

**Revisit when** outcome labels allow genuine calibration.

---

## 3. No RAG

**Rejected:** embeddings + vector store over interaction history.

**Why.** Per-candidate context is a handful of messages, roughly 1–3k tokens.
It fits in the prompt whole. Retrieval would add infrastructure, latency and a
*new failure mode* — bad recall — to solve a problem that does not exist.

**Revisit when** messages must be grounded in a corpus: relocation policy,
compensation FAQs, a library of high-converting templates. At that point
retrieval earns its place; today it would be resume-driven design.

---

## 4. Provider port with multiple adapters

**Chosen:** an `AIProvider` interface with Claude, Gemini and a deterministic
mock behind it.

This was validated rather than assumed. Adding the Claude adapter *after* the
pipeline was complete touched no pipeline code, no validation, no guardrails
and no UI logic — because everything downstream depends on the port. The
abstraction earned its keep in the only way an abstraction can.

The mock is not a stub: it reads the same snapshot and quotes real sentences.
That is what makes Demo Mode a working product rather than a placeholder, and
what makes the failure-path tests free and reproducible.

**Disadvantage.** Providers differ in how they force structure — Gemini has
`response_schema`, Claude uses forced tool use — so each adapter carries real
logic rather than being a thin wrapper.

---

## 5. The engine is authoritative over the model's band

**Rejected:** storing the model's `risk_level` as the candidate's risk.

**Why.** This was originally implemented the wrong way and caught in review:
the risk engine's own documentation said the LLM "never picks the band", and
the service layer was doing exactly that. `ai_analyses.risk_level` now holds
the blended band; `model_risk_level` holds the proposal.

The disagreement rate is a first-class metric — 28 of 96 on seeded data. A gap
that widens after a prompt or model change is a signal worth acting on.

---

## 6. In-process scheduler, with a named flaw

**Rejected:** Celery/ARQ + Redis.

**Why.** One fewer service to run, explain and debug, on a one-day budget.

**The flaw, stated plainly:** APScheduler runs inside the API process, so every
replica fires the same job. Mitigated two ways — a Postgres advisory lock means
one replica wins each tick, and every action is idempotent so a double-run
creates nothing. That is a mitigation, not a distributed scheduler.

**Revisit when** there is more than one replica, or jobs outgrow a tick. The
production shape is an external trigger dispatching to workers off the request
path. `run_all_rules` is a plain function, so that is a scheduling change, not
a rewrite.

---

## 7. Idempotency via a deterministic dedupe bucket

**Rejected:** "has a follow-up been created in the last N days?" read-then-write.

**Why.** A read-then-write races: two concurrent runs both read "no" and both
insert. The bucket is a pure function of `(today, window)`, stored on the row
and covered by `uq_follow_up_idempotency`. The guarantee lives in the database.

Each insert gets a savepoint, so a collision rolls back one row rather than the
whole sweep.

---

## 8. Per-rule follow-up suppression, not a boolean

**Rejected:** `has_open_follow_up: bool` on the context.

**Why.** This was a real bug. `stage_overdue` fires for most candidates, so a
routine "upload your documents" reminder set the flag and silenced the
high-risk escalation — making `high_risk_unattended` unreachable in practice.
The lowest-priority rule was outranking the highest.

`open_follow_up_rules: frozenset[str]` lets each predicate ask about the rules
it actually cares about. Two regression tests pin both directions.

---

## 9. App-layer enums, not native Postgres enums

**Why.** Adding a value to a native PG enum needs a migration and a table lock;
here it is a code change validated by Pydantic at the boundary. It also keeps
the schema dialect-neutral, which is what lets the integration suite run on
SQLite with no container.

**Disadvantage.** The database will accept an invalid string written by
something that bypasses the application.

**Revisit when** the schema stabilises and multiple writers exist.

---

## 10. Offset pagination

**Rejected:** keyset/cursor pagination.

**Why.** The dashboard needs jump-to-page and a total count for its filter
summary. Cursors give neither.

**Disadvantage.** Deep offsets make the database walk skipped rows.

**Revisit at** roughly 100k candidates, or when anyone actually pages deep.
The fix is keyset ordering on `(joining_date, id)`.

---

## 11. String UUIDv4 primary keys

**Rejected:** auto-increment integers.

**Why.** `/candidates/1` invites enumeration; UUIDs are also safe to generate
without coordination.

**Disadvantage.** 36-byte keys, and random v4 gives poor B-tree insert
locality at high write volume.

**Revisit when** write volume makes index fragmentation measurable. UUIDv7 or
ULID keeps the non-enumerable property while sorting by creation time.

---

## 12. Client-side data fetching in Next.js

**Rejected:** React Server Components fetching server-side.

**Why.** Server fetching needs one URL for the compose network
(`http://api:8000`) and another for the browser (`http://localhost:8000`).
Getting that wrong produces confusing "fetch failed" errors mid-demo. One
origin is more reliable.

**Disadvantage.** No server-side render, so first paint is slower and there is
a loading state.

**Revisit when** SEO or first-paint latency matters — neither applies to an
internal tool behind a login.

---

## 13. Storing AI output rather than recomputing

**Why.** Dashboards read it on every render, recruiters override it, analytics
aggregate it. All three need a durable, attributable row. Recomputing per
request would also bill per page load.

Freshness comes from a SHA-256 hash of the canonical input snapshot rather than
a TTL: any change to candidate facts or interactions produces a new hash and
invalidates the cache automatically. A TTL is always either too eager or too
stale.

**Disadvantage.** `raw_response` stores candidate text for debugging and needs
a retention policy it does not yet have.

---

## 14. Rate limiting in-process

**Why.** The purpose is protecting the LLM budget from one enthusiastic user or
a runaway script — not defending against a distributed attacker. Redis for that
alone would be a poor trade.

**Disadvantage.** Counters are per-process, so the effective limit is
`limit × replicas`, and they reset on restart.

**Revisit when** there is more than one replica, or abuse becomes real. Then it
belongs in Redis or at the edge.

---

## 15. Reads are unauthenticated

**Current state, stated rather than hidden.** RBAC is enforced only on
`/audit`, which carries PII and every judgement a recruiter has made. Other
routes accept anonymous callers so the demo works without a login wall.

**This is the weakest point in the system.** It is a demo affordance, not a
defensible production posture. The fix is small — `AuthedActorDep` already
exists and applying it is one line per route — but it would require a login
page, which was cut for time.

**Revisit:** before this touches real candidate data. Immediately.

---

## 16. Analytics computed live

**Rejected:** pre-aggregated rollup tables.

**Why.** At 60 candidates the aggregate queries return in milliseconds.
Building rollups now would add refresh scheduling and staleness handling for a
dataset that fits in one page of results.

**Revisit at** roughly 100k candidates. Then: nightly rollups per day, per
recruiter and per stage, with only the current-day slice computed live.

---

## 17. SQLite for integration tests

**Why.** No container needed, and the suite runs in seconds. Only possible
because the schema was kept dialect-neutral for other reasons — a happy
consequence of decision 9, not a driver of it.

**Disadvantage, stated honestly.** These tests would not catch a
Postgres-specific regression. The advisory-lock path is untested here, since
SQLite has no advisory locks.

**Revisit when** Postgres-specific features enter the query layer. Then
Testcontainers.
