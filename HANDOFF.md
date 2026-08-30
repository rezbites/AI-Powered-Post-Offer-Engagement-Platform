# Handoff — Post-Offer Engagement Platform

**Deadline: 8:00 PM IST, Sunday 30 August 2026.**

Stages 1–9 are complete and **verified against a running stack**, now including
**Live Mode against real Gemini**. The README (a graded deliverable) is written.
Stages 10 and 12 remain, plus two diagrams, `docs/decisions.md` and screenshots.

---

## 1. Current state — ALL STAGES COMPLETE

| Stage | Status |
|---|---|
| 1 Foundation | ✅ compose, config, structured logging, error envelope, health probes |
| 2 Data | ✅ 12 tables, 3 migrations, 60 seeded candidates |
| 3 Core API | ✅ candidates CRUD + filters, interactions, stages, audit |
| 4 Domain | ✅ risk / confidence / attention / rules as pure functions |
| 5 AI pipeline | ✅ port, mock, Gemini, Claude, validate→repair→fallback |
| 6 Automation | ✅ idempotent follow-ups, scheduler, advisory lock, run log |
| 7 Analytics | ✅ every brief-mandated metric |
| 8 Frontend | ✅ queue, dashboard, detail, analytics, add candidate |
| 9 Auth | ✅ JWT, RBAC, rate limiting |
| 10 Eval + tests | ✅ 22-scenario golden set, 198 tests |
| 11 Docs | ✅ README (6 sections, 6 diagrams), decisions.md, database.md |
| 12 Critical review | ✅ swept; findings in §6 |

**198 tests** (166 unit + 32 integration) · `docker compose run --rm api pytest -q`

### Running it

```bash
docker compose up -d
docker compose exec -T api python -m app.db.seed
```

Frontend http://localhost:3000 · API docs http://localhost:8000/docs

Active provider is **Claude Haiku**. Gemini's adapter still works but its key
is unset (free tier is 20 req/day and was exhausted). Demo Mode needs no key.

### ⚠️ Key hygiene

`.env` holds a real Anthropic key and is gitignored — verify with
`git check-ignore -v .env` before any push. It was pasted into a chat, so treat
it as exposed and **rotate it** when demoing is done.

---

### What remains (nothing blocking)

- **Screenshots or a demo video** — the brief asks; nothing captured.
- **Reads are unauthenticated** — RBAC guards only `/audit`. Deliberate demo
  affordance, documented in README §5 and decisions.md §15. Decide before this
  touches real data.
- Frontend has no tests.
- No CI pipeline.

---

## 2. Design invariants — do not break these

These are load-bearing decisions. Several were arrived at by fixing a bug;
reverting them silently reintroduces it.

1. **The LLM never picks the risk band.** It extracts typed signals with
   verbatim quotes. `domain/risk.py::assess` computes the band from those
   signals *plus* timing/silence/journey. `ai_analyses.model_risk_level`
   stores what the model proposed purely so disagreement stays measurable
   (currently 28 of 96).

2. **Confidence is derived, never self-reported.** `domain/confidence.py`
   computes it from evidence volume, inbound presence, recency, quote support,
   and rule/LLM agreement. The model's own number lives in `model_confidence`
   as telemetry. It is an *uncalibrated ordinal*, labelled "heuristic" in the
   UI. Never present it as a probability.

3. **Human overrides are never overwritten.** `risk_source = human` is checked
   before any AI or rule write. 1.0 confidence is reserved for human overrides.

4. **Closed enums are the injection guardrail.** `risk_level`,
   `signals[].type`, `next_action`. Free text is confined to fields that do
   not drive control flow.

5. **Grounding: quotes must appear in the candidate's own messages.**
   `ai/guardrails.py`. Recruiter messages don't count as candidate evidence.
   Ungrounded signals are dropped and counted in `dropped_signals`.

6. **Nothing reaches a candidate without human approval.** Messages are
   drafts until approved. This is the real injection defence.

7. **Follow-up suppression is per-rule, not boolean.**
   `CandidateContext.open_follow_up_rules` is a `frozenset[str]`. A
   paperwork reminder must not silence a high-risk escalation — that bug made
   `high_risk_unattended` dead code.

8. **Terminal candidates (joined / dropped_out) are excluded** from risk,
   attention queue and all rules. `assess()` returns `None` for them.

9. **Demo Mode is labelled everywhere** — `provider` on every row,
   `/health/ready`, `/ai/status`, a persistent UI badge. Mock output must
   never be mistakable for model output.

10. **`None` ≠ `0`.** Conversion rate is `None` when nothing has resolved
    (not 0%). Latency uses `is not None`, not truthiness. Both were real bugs.

---

## 3. Stage 9 — Auth ✅ VERIFIED

All checks passed against the running stack:

| Check | Result |
|---|---|
| Admin login | OK — returns token, `role=admin` |
| Recruiter login | OK |
| Wrong password | `Invalid email or password.` |
| Unknown account | `Invalid email or password.` — **identical**, no enumeration |
| `/auth/me` with token | `admin`, authenticated |
| `GET /audit` anonymous | **401** |
| `GET /audit` as recruiter | **403** |
| `GET /audit` as admin | **200** |
| 22 AI requests in a minute | 20 × 200, **2 × 429** with `retry_after: 59` |

Seeded logins: the six emails in `backend/app/db/seed.py::RECRUITERS`, password
`demo1234`. Only `aditya.menon@example.com` is an admin.

**Open decision, deliberately left explicit:** reads are unauthenticated so the
demo works without a login page. RBAC is enforced only on `/audit`. Either add
a login page and apply `AuthedActorDep` to mutating routes, or keep it and make
sure the README's statement of this stays accurate (it currently says so in
§5 "What I would improve").

## 4. Stage 10 — Eval harness ✅ / integration tests ❌

### 4a. Eval harness — DONE

`backend/evals/golden_set.json` (22 labelled scenarios) and
`backend/evals/run_eval.py`. Run with `make eval` (mock, free) or
`make eval-live` (compares both providers — **see the quota warning**).

**Head-to-head, 22 scenarios (both runs valid, 0 failures):**

```
  metric                          mock        claude
  schema_validity_pct           100.00        100.00
  band_exact_pct                 72.73         72.73
  signal_precision                0.88          0.75
  signal_recall                   0.94          0.94
  signal_f1                       0.91          0.83
  grounding_drops                 0.00          0.00
  latency_p50_ms                  1.00       3900.00
```

Claude Haiku matching the mock's 0.94 recall is what confirms the earlier
Gemini figure (0.44) was a quota artefact rather than a model or prompt
problem. Claude's lower precision is real though: it over-reports
`low_enthusiasm` and `positive_intent` on thin evidence.

**Mock baseline detail:**

```
schema valid first pass  100.0%      band exact          72.7%
repaired / failed        0 / 0       band within one    100.0%
signal precision          0.88       grounding drops        0
signal recall             0.94       injection leaks        0
latency p50/p95        1 / 1 ms      est. cost/1k        $0.10
```

### ⚠️ Gemini free tier is 20 requests PER DAY

Not per minute — `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
`quotaValue: 20`. A full 22-scenario run exhausts it and then some.

**Two live eval runs on 2026-08-29 were invalidated by this.** Calls that hit
429 exhausted their retries and fell back to the deterministic path, which
emits zero signals by design — indistinguishable from genuinely poor recall
unless you check the `failed` count. The reported recall (0.44, then 0.38) is
an artefact, not a measurement.

The harness now prints a loud warning whenever `failed > 0`, and takes
`--limit N`. **Use `--limit 8` on the free tier**, or the run is worthless.

`prompts/analysis_v2.md` exists but is **not active** (`PROMPT_VERSION = "v1"`).
It was written to test whether v1 under-reports signals; the comparison was
contaminated, so promoting it would be acting on invalid evidence. Re-run on
fresh quota before deciding.

### 4a-bis. Original spec, for reference

Create `backend/evals/golden_set.json` — ~20–25 labelled scenarios. **Source
them from `backend/app/db/seed.py::ARCHETYPES`**, which already encodes ten
realistic situations with an `expected_risk` label each. Include the brief's
relocation example verbatim.

Each entry:
```json
{
  "id": "relocation_01",
  "description": "...",
  "snapshot": { /* CandidateSnapshot.to_dict() shape */ },
  "expected_risk": "MEDIUM",
  "expected_signals": ["relocation_concern", "positive_intent"]
}
```

Create `backend/evals/run_eval.py` reporting:

| Metric | Definition |
|---|---|
| Schema validity rate | % parsing + validating on first attempt |
| Exact band accuracy | % matching `expected_risk` |
| ±1 band accuracy | use `RiskLevel.rank` |
| Signal precision / recall | against `expected_signals` |
| Grounding violations | signals dropped by the guardrail |
| p50 / p95 latency | from `AnalysisOutcome.latency_ms` |
| Cost per analysis | tokens × published Gemini rate |

Runs against the mock by default (deterministic, CI-safe); `--live` flag for
real Gemini. Add a `make eval` target.

### 4b. Integration tests

`backend/tests/test_api_integration.py` using `httpx.AsyncClient` +
`ASGITransport`. Mark `@pytest.mark.integration`. Cover:
- candidate list + each filter reconciles with analytics counts
- override → `risk_source=human` → audit row written → revert restores
- automation run twice → zero duplicates (the key property)
- 404 / 422 return the error envelope with a `request_id`

Use a separate test database or transactional rollback per test. `pytest.ini`
already declares the `integration` and `llm` markers.

---

## 5. Stage 11 — Documentation (README ✅ done)

`README.md` is written and covers all six mandated sections, Demo Mode, setup,
security, observability, testing, layout and assumptions. It contains **two**
Mermaid diagrams (system architecture, ER) plus the AI pipeline and automation
flow as flowcharts — four in total.

**Still outstanding:**

- `docs/decisions.md` — the trade-off table exists inside README §5; extract
  and expand it with "options considered / chosen / why / when to revisit".
- Two more diagrams: **engagement journey** (6 stages with SLA gates) and
  **production / 1M architecture** (queued AI, rollups, partitioned scan,
  read replicas). README §6 has the prose to draw from.
- **Screenshots or a demo video** — the brief explicitly asks, nothing captured.

Original requirement, for reference — the brief demands six sections:

1. **Architecture and database schema**
2. **AI flow and how structured output is validated**
3. **How joining-risk classification works and its limitations**
4. **How the automated engagement workflow works**
5. **Key trade-offs and what you would improve for production**
6. **What you would change at 1 million candidates**

Plus: setup instructions, and a **Demo Mode** section (works with no API key).

Most of the substance already exists as module docstrings — mine them rather
than rewriting:
- `app/domain/risk.py` — hybrid rationale, why not pure-LLM, limitations
- `app/domain/confidence.py` — why not self-reported, what the number is not
- `app/ai/pipeline.py` — the full flow and why each stage exists
- `app/ai/guardrails.py` — grounding, injection defence
- `app/modules/automation/service.py` — idempotency mechanism
- `app/modules/automation/scheduler.py` — the named multi-replica flaw
- `app/modules/analytics/repository.py` — the 1M-candidate section
- `app/db/models.py` — schema rationale
- `docs/database.md` — already written, links from README

### Six diagrams (Mermaid, inline in README)

1. **System architecture** — recruiter → Next.js → FastAPI modules → Postgres + Gemini
2. **Database ER** — `docs/schema.sql` already dumped; derive from it
3. **AI pipeline** — snapshot → hash → cache → generate → validate → repair → fallback → guardrails → persist → accept/override
4. **Engagement journey** — the 6 stages with SLA gates
5. **Automation flow** — scan → predicate → dedupe bucket → follow-up → attention queue
6. **Production / 1M architecture** — queued AI, rollup tables, partitioned scan, read replicas

Also write `docs/decisions.md` with the trade-off table (options considered /
chosen / why / when to revisit). The plan file at
`C:\Users\shash\.claude\plans\scaffold-the-project-thoroughly-tidy-umbrella.md`
has a populated version of this table — copy it.

### Honest limitations that MUST appear in the README

Do not omit these; stating them is worth more than hiding them:

- Risk thresholds are **hand-tuned, not learned** — no historical joined/dropped
  outcomes exist to calibrate against.
- Confidence is an **uncalibrated ordinal heuristic**, not a probability.
- Signal extraction is **English-only**; politeness and sarcasm skew it.
- **Silence is ambiguous** — busy ≠ disengaged.
- Recruiter conversion rates are **statistically noisy** at these sample sizes.
- The scheduler is **in-process**; the advisory lock is a mitigation, not a
  distributed scheduler.
- Rate limiting is **per-process**; effective limit is `limit × replicas`.
- In Demo Mode the mock uses **keyword matching**, which cannot handle negation
  ("no relocation issues at all") or paraphrase.

---

## 5b. Live Mode — verified result

One real Gemini call on the relocation candidate:

```
provider: gemini · model: gemini-2.5-flash · status: valid · 4743 ms
tokens: 1019 in / 210 out
RISK: MEDIUM · model proposed: MEDIUM · agreed: True
SIGNALS:
  positive_intent      -> "Really looking forward to getting started."
  relocation_concern   -> "I am still figuring out relocation and accommodation."
NEXT: Call candidate
```

Both quotes are exact spans of the candidate's messages, so the grounding
guardrail passed with zero drops. Note the latency contrast worth mentioning in
a demo: ~4700 ms live versus ~1 ms mock, which is precisely why the
`input_hash` cache exists.

---

## 5c. Full loop verified on live Gemini

New candidate → log conversation → analyse:

```
created                risk LOW · confidence 0.13 · source rule
logged 2 interactions  (1 outbound, 1 inbound)
analysed               gemini · valid · 6350 ms · 927/203 tokens
  RISK: HIGH  conf 0.40   model said MEDIUM · agreed False
  notice_period_issue -> "my current employer is not releasing me on time…"
  relocation_concern  -> "Also still sorting out a place to stay in Pune."
  NEXT: Call candidate
```

Two things worth demoing from this. Gemini pulled **two distinct signals from
one sentence**, both with exact quotes. And confidence fell to 0.40 *because*
the model and the engine disagreed on the band — that is the derived-confidence
mechanism doing precisely what it was built for, and it is only visible because
both numbers are stored.

---

## 5d. Provider toggle — the side-by-side

Same candidate, same inbound message (*"I have another offer in hand and the
pay there is a bit higher"*), run through both providers:

```
mock       1 ms   HIGH conf 0.40  model said MEDIUM  agreed False
                  [competing_offer, low_enthusiasm]
gemini  5380 ms   HIGH conf 0.40  model said HIGH    agreed True
                  [competing_offer, compensation_concern, low_enthusiasm]
```

Gemini extracted **compensation_concern** from "the pay there is a bit higher";
the keyword matcher had no rule for it. This is the clearest demonstration in
the project of *why* the provider port exists — and the mock is still good
enough that the product is fully usable without a key.

Note the toggle deliberately bypasses the analysis cache: asking for Gemini and
receiving a cached mock result would defeat the point.

---

## 6. Stage 12 — Critical review

Known weak points to address or document:

Swept on completion. Verified clean: no secrets in tracked source, `.env`
untracked, no TODO/FIXME left behind, no bare excepts that swallow errors
(both re-raise), `raw_response` never returned by an API model and excluded
from audit snapshots, no truthiness checks left on numeric fields.

Remaining known weaknesses, all documented rather than hidden:

| Issue | Where | Suggested action |
|---|---|---|
| Reads are unauthenticated | all routers | decide and document |
| `raw_response` stores candidate text | `ai_analyses` | add a retention note |
| Offset pagination degrades deep | `core/schemas.py` | documented; note keyset as the fix |
| uuid4 PKs hurt index locality | `db/base.py` | documented; UUIDv7 is the fix |
| Batch analysis is sequential | `ai/service.py` | documented; queue is the fix |
| No frontend tests | `frontend/` | at minimum note it |
| No CI pipeline | — | a simple GitHub Action running pytest would help |

---

## 7. Gotchas already hit (do not rediscover these)

1. **`alembic/` shadowed the installed package** with `PYTHONPATH=/app`.
   Directory is named `migrations/`. Do not rename it back.
2. **`pip --prefix=/install` leaves site-packages off `sys.path`.** The
   Dockerfile copies into `/usr/local`. Do not "tidy" this.
3. **Lazy relationship access in async context → `MissingGreenlet`.** Always
   `selectinload` or pass data through explicitly. **Bit three times** - most
   recently by reaching for `candidate.analyses[0]` in a router. If you need
   a relationship, load it in the repository and pass it down the call chain.
4. **ContextVar reset in `finally` runs before the access log.** Logging must
   happen inside the `try`.
5. **`func.case` is invalid in SQLAlchemy 2.0** — `case` is top-level.
6. **`FILTER (WHERE ...)` breaks SQLite** — use `SUM(CASE WHEN ...)`.
7. **Restarting the API takes ~6s.** Poll `/health` before curling, or you get
   a confusing empty-response error.
8. **Date arithmetic differs between Postgres and SQLite** — do it in Python
   where portability matters.

---

## 8. Submission checklist

- [ ] Stage 9 verified (§3)
- [ ] README with all six mandated sections + Demo Mode + setup
- [ ] Six diagrams
- [ ] `docs/decisions.md` trade-off table
- [ ] Eval harness with a metrics table
- [ ] Integration tests
- [ ] `docker compose up` works from a clean clone (`make reset`)
- [ ] Screenshots or a short demo video (**the brief asks for this**)
- [ ] Repo pushed — `CLAUDE.md` and the `.docx` are gitignored and purged
      from history; keep them out
- [ ] Deployed URL if available (optional)

---

## 9. If you only have an hour

1. **README** with the six sections — mine the module docstrings (§5).
2. **Three diagrams**: system architecture, AI pipeline, automation flow.
3. **Verify Stage 9** (§3) or explicitly disable auth and say so.
4. **Screenshots** of the attention queue, a candidate detail page showing the
   Why panel with a verbatim quote, and the analytics funnel.

The code is complete and working. Documentation is what converts that into
marks — Backend 25 + AI 25 are already earned by working code, but *Engineering
maturity* and the README's reasoning sections are pure documentation.
