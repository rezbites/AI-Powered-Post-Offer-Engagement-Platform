# Database access

## What this actually is

**PostgreSQL 16.15**, running in a Docker container on your machine. Not AWS RDS
— there is no cloud database in this project. `docker-compose.yml` defines a
`db` service using the `postgres:16-alpine` image, with data persisted in a
Docker volume named `pgdata`.

"Postgres" and "SQL" are not alternatives: Postgres *is* a SQL database. The
assignment requires "a SQL database with a sensible schema", and Postgres
satisfies that.

The connection string the API uses is:

```
postgresql+asyncpg://postgres:postgres@db:5432/engagement
```

`db` is the container hostname on the compose network. From your laptop the
same database is reachable at `localhost:5432`, because compose publishes the
port.

## Connection details

| Setting  | Value        |
|----------|--------------|
| Host     | `localhost`  |
| Port     | `5432`       |
| Database | `engagement` |
| User     | `postgres`   |
| Password | `postgres`   |

These are development credentials, defined in `.env.example` and safe to
commit. A real deployment supplies them through environment variables and the
Postgres container is not exposed publicly.

## Viewing it in VS Code

Install one extension, then connect with the details above.

**Recommended: "Database Client" by cweijan** — the simplest for browsing
tables and running queries.

1. Open the Extensions panel (`Ctrl+Shift+X`)
2. Search for `Database Client`, install it
3. A database icon appears in the left sidebar — click it
4. Click **+ Create Connection** → **PostgreSQL**
5. Fill in the table above, click **Connect**
6. Expand `engagement` → `public` → tables; double-click any table to browse
   its rows, or right-click → **New Query** to run SQL

**Alternative: SQLTools.** Install both `SQLTools` and
`SQLTools PostgreSQL/Cockroach Driver`, then `Ctrl+Shift+P` →
`SQLTools: Add New Connection`.

If the connection is refused, the container is not running. Start it with:

```bash
docker compose up -d db
```

## Reading the schema without any extension

`docs/schema.sql` is a full schema dump — every table, column, index and
constraint. Open it in VS Code like any file. Regenerate it after a migration:

```bash
docker compose exec -T db pg_dump -U postgres -d engagement \
  --schema-only --no-owner --no-privileges > docs/schema.sql
```

The authoritative definition, though, is the Python source:

- `backend/app/db/models.py` — the ORM models, with commentary on why each
  table is shaped the way it is
- `backend/migrations/versions/` — the migration history

## Querying from the terminal

```bash
make psql                              # interactive prompt
docker compose exec db psql -U postgres -d engagement
```

Useful meta-commands once inside `psql`:

| Command                 | Shows                                  |
|-------------------------|----------------------------------------|
| `\dt`                   | all tables                             |
| `\d candidates`         | one table's columns, indexes, FKs      |
| `\d+ candidates`        | the same, plus storage and comments    |
| `\di`                   | all indexes                            |
| `\q`                    | quit                                   |

For one-off queries without entering the prompt, `-c` runs and exits:

```bash
docker compose exec -T db psql -U postgres -d engagement -c "select count(*) from candidates;"
```

## Queries worth running

**Risk distribution and where each band came from.** `risk_source` is the
human-in-the-loop audit: `rule` is deterministic, `ai` came from the pipeline,
`human` means a recruiter overrode it.

```sql
select risk_level, risk_source, count(*)
from candidates group by 1,2 order by 1,2;
```

**Signals extracted across the population**, unnesting the JSON column:

```sql
select s->>'type' as signal, count(*)
from ai_analyses, jsonb_array_elements(signals::jsonb) s
group by 1 order by 2 desc;
```

**Where the model and the risk engine disagreed.** The engine is authoritative;
the model's proposal is stored purely so this gap stays measurable.

```sql
select model_risk_level, risk_level, count(*)
from ai_analyses
where model_risk_level is distinct from risk_level
group by 1,2;
```

**The idempotency guarantee.** This must always return 1 — it is what stops an
hourly job flooding the attention queue with duplicates:

```sql
select max(c) from (
  select count(*) c from follow_up_actions
  group by candidate_id, rule_key, dedupe_date
) x;
```

**The brief's worked example**, with the verbatim quote the pipeline extracted:

```sql
select c.name, a.risk_level, a.risk_confidence,
       s->>'type' as signal, s->>'evidence' as quote
from candidates c
join ai_analyses a on a.candidate_id = c.id,
     jsonb_array_elements(a.signals::jsonb) s
where s->>'type' = 'relocation_concern'
limit 5;
```

**LLM cost and latency ledger.** `ai_analyses` doubles as observability, so
these questions need no separate monitoring stack:

```sql
select provider, status, count(*),
       round(avg(latency_ms)) as avg_ms,
       sum(tokens_in) as tokens_in, sum(tokens_out) as tokens_out
from ai_analyses group by 1,2;
```

## Resetting

```bash
make seed     # wipe demo data and reseed 60 candidates
make reset    # destroy the volume entirely, migrate, reseed
```

`make reset` deletes the `pgdata` volume — everything in the database is lost.
That is fine here because all data is seeded, but do not run it against
anything you care about.
