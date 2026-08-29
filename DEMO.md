# Demo walkthrough

A script for recording a video, and a guide to understanding the system if you
are reading rather than watching.

**Target length: 5–6 minutes.** Long enough to show judgement, short enough
that someone watches to the end.

---

## Before you record

```bash
docker compose up -d
docker compose exec -T api python -m app.db.seed
```

Wait for all three containers, then open http://localhost:3000.

**Checklist:**

- [ ] Header badge reads `LIVE — claude-haiku-4-5-20251001` (not DEMO MODE)
- [ ] Attention queue has candidates in it
- [ ] Have a second tab on http://localhost:8000/docs
- [ ] Close other tabs; a clean browser looks deliberate
- [ ] Zoom to ~110% so text is readable in the recording

**Pick your candidate before you start recording.** Find the relocation one:

```bash
docker compose exec -T db psql -U postgres -d engagement -tAc \
  "select name from candidates where notes like '%relocation_concern%' limit 3"
```

Open them once beforehand and click **Re-analyse** with **Claude**, so the
analysis is cached and the demo does not stall for five seconds on camera.

---

## The script

### 0:00 — What this is (30s)

> "This is a post-offer engagement platform. It tracks candidates between
> accepting an offer and actually turning up on day one — which is where
> companies quietly lose people."

Land the framing immediately:

> "The design question I kept returning to was: what does a recruiter need at
> nine in the morning? Not a database. A list of who needs them today, and
> why."

---

### 0:30 — The attention queue (60s)

Point at the ranked list.

> "So the dashboard leads with that. It's a ranked queue, not a table dump.
> Each row carries the risk band, the reasons behind it, and a recommended
> action — so a recruiter can decide whether to act without opening anything."

Read one row aloud:

> "Joining in two days, no response for eight days, four steps overdue."

Then the point that matters:

> "This ranking is deterministic — a pure function, no LLM call in the sort
> path. A queue that reshuffles between refreshes is one recruiters stop
> trusting. The model's contribution happens upstream."

Scroll to the table below.

> "Underneath is the full list with the filters the brief asked for — joining
> month, recruiter, role, risk, status. Note the last-contact column goes amber
> past five days. That's the same threshold the automation rule uses."

---

### 1:30 — Where the AI actually earns its place (90s)

Open the relocation candidate.

> "Here's a candidate who raised a concern."

Point at the **Detected in the candidate's own words** panel:

> "The model found a relocation concern — and it has to quote the candidate
> verbatim to claim it. If that quote isn't in their actual messages, the
> signal gets dropped and counted. That's the guardrail against confident
> fiction: a signal you can't check against the transcript is one a recruiter
> shouldn't believe."

Now the two-axis point — **this is the part to spend time on**:

> "Two separate numbers here, and they answer different questions. The band is
> how likely they are to drop out. The evidence meter is how well-supported
> that judgement is. You can have HIGH risk at 45% confidence — one worrying
> sentence and nothing else."

Expand **How was 70% arrived at?**

> "And it's traceable. Four messages on record, two signals quoted verbatim,
> the rules engine and the model agreeing. That's where the number comes from."

Optional, if you want to show engineering honesty:

> "This actually caught a bug. The original formula summed past its own ceiling,
> so every well-evidenced candidate reported 95% — the clamp was doing the work,
> not the evidence. Recalibrated so it can't saturate."

---

### 3:00 — Human in the loop (45s)

Click **Override risk**.

> "The recruiter always wins. Pick a band, and a reason is mandatory — an
> unexplained override is indistinguishable from a mis-click three weeks later."

Point at the certainty buttons:

> "And they state their own certainty. Someone who's spoken to the candidate
> knows something different from someone acting on a hunch. Flattening both to
> 'certain' throws away the more useful half."

Save it.

> "Source now reads Recruiter override, with who and why. And no later analysis
> overwrites it — that's the whole point of tracking provenance."

---

### 3:45 — Provider toggle (45s)

Scroll to the risk panel header.

> "The AI sits behind a provider port. Same candidate, three backends."

Switch to **Mock**, hit **Re-analyse**.

> "That's the deterministic mock — instant, free, and it genuinely reads the
> messages rather than returning canned text. It's why this whole thing runs
> with no API key at all."

Point at the header badge.

> "And it's labelled everywhere. If a mock returned flawless output with no
> indication of what it was, you'd be right to wonder whether the AI is
> hardcoded."

Switch back to **Claude**, re-analyse.

> "Real model call — slower, and it catches things the keyword matcher can't:
> negation, paraphrase, tone."

---

### 4:30 — The automation rule (45s)

> "The brief asked for an automated rule: candidate joining within seven days
> with no contact for five — flag them, draft a message, create a follow-up."

Open the API docs tab, `POST /automation/run`, execute. Or from a terminal:

```bash
curl -X POST http://localhost:8000/api/v1/automation/run \
  -H 'Content-Type: application/json' -d '{}'
```

> "All three happen. And critically —"

Run it a second time.

> "— running it again creates nothing. Zero. The idempotency key is a database
> constraint, not application logic a race could slip past. Without that, an
> hourly job buries the queue in duplicates by lunchtime."

---

### 5:15 — Analytics (30s)

Open **Analytics**.

> "Every metric the brief listed. The funnel is the interesting one — you can
> see where people stall, Documentation to Manager Introduction."

Point at a recruiter with no resolved candidates:

> "And this says 'no data yet', not zero percent. Those are different facts, and
> in a tool where these numbers shape how people are judged, they shouldn't
> render the same."

---

### 5:45 — Close (20s)

> "Under the hood: 198 tests, and an eval harness that scores the AI against 22
> labelled scenarios — schema validity, signal precision and recall, grounding
> violations, cost per analysis."

If you want one closing line, make it this:

> "The golden set deliberately includes cases the system fails. An eval that
> only contains passing cases measures nothing."

---

## If they ask you these

**"Why not just let the LLM decide the risk level?"**
Because you can't audit it, it drifts between model versions, and a recruiter
can't argue with it. Rules own the countable facts; the model only reads
language. The band comes from both. The model's own proposal is stored — it
disagreed with the engine on 28 of 96 seeded analyses, and that gap is
queryable.

**"Is that confidence number calibrated?"**
No, and the UI says so. It's an ordinal — 0.85 means better-supported than
0.60, not correct 85% of the time. Real calibration needs historical
joined/dropped outcomes to fit against, and this system has none. Claiming
otherwise would be the easiest thing here to pull apart.

**"What happens when the LLM is down?"**
Analysis falls back to a rules-only assessment, marked `failed`, carrying no
signals — because without the model there's no semantic extraction, and
inventing signals would be exactly the dishonesty the guardrails prevent. The
dashboard never 500s.

**"Why is your scheduler inside the web server?"**
Because it's one fewer service on a one-day budget, and I'd rather name the
flaw than hide it: with more than one replica every replica fires the same job.
A Postgres advisory lock means one wins, and every action is idempotent. That's
a mitigation, not a distributed scheduler. The production shape is an external
trigger dispatching to workers, and moving there is a scheduling change, not a
rewrite.

**"How do you stop prompt injection?"**
Closed enums are the load-bearing part — injected text can at worst pick a
different *valid* action, which a recruiter then reviews. Untrusted content is
delimiter-wrapped and labelled data-not-instructions. But the real defence is
that no AI output causes a side effect without human approval: messages are
drafts until someone approves them, and injected text can't approve itself.

**"What would you do differently at a million candidates?"**
Four things carry most of it: AI calls move behind a queue so request latency
stops depending on provider latency; the scheduler moves out of the API
process; analytics read nightly rollups instead of scanning; and dashboards
read replicas. README §6 has the detail.

**"What's the weakest part?"**
Reads are unauthenticated — RBAC only guards the audit trail. It's a demo
affordance so there's no login wall, it's documented in two places, and it's
the first thing I'd close.

---

## Understanding it yourself

Read in this order:

| # | File | What it tells you |
|---|---|---|
| 1 | `README.md` | Architecture, the AI flow, risk model and its limits |
| 2 | `backend/app/domain/risk.py` | The hybrid model, with the reasoning in the docstring |
| 3 | `backend/app/domain/confidence.py` | Why confidence is derived, not self-reported |
| 4 | `backend/app/ai/pipeline.py` | validate → repair → fallback, and why each stage exists |
| 5 | `backend/app/ai/guardrails.py` | Grounding — the check that drops invented quotes |
| 6 | `docs/decisions.md` | 17 decisions, what was rejected, when to revisit |

The module docstrings carry the reasoning, not just the *what*. If you only
read two, make them `risk.py` and `confidence.py` — that's where the judgement
lives.

### Try it yourself

```bash
make help      # every shortcut
make eval      # score the AI against the golden set, free
make test      # 198 tests
make psql      # database shell
```

Then: add a candidate, log an inbound message mentioning a competing offer,
switch the provider to Claude, and hit Re-analyse. Watch the signal appear with
its quote, the band move, and the confidence derivation change.
