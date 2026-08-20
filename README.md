# Prompt2Insight

Prompt2Insight is a bilingual (English and Arabic) business-intelligence assistant. It turns plain-language questions into safe, read-only SQL for PostgreSQL or MySQL, executes approved queries, and presents grounded answers with optional charts.

It combines a FastAPI backend, a Next.js chat interface, PostgreSQL-backed conversation history, and LiteLLM model routing.

> **Recommended:** Run the complete local environment with Docker Compose:
>
> ```bash
> docker compose up --build
> ```
>
> This starts the frontend, backend, LiteLLM proxy, and application database together. See [Quick start](#quick-start) for the required `.env` configuration.

## What it does

- Connects to PostgreSQL and MySQL analytics databases and introspects their schemas.
- Uses a bilingual semantic catalog to give the planner business terminology and metric definitions.
- Supports English, Arabic, Egyptian Arabic, and code-switched questions with RTL-aware UI rendering.
- Saves conversations, messages, analytical requests, and execution provenance.
- Produces result-aware chart recommendations and validates them before rendering.
- Provides a mock mode for local UI and API development without an LLM provider.

## Architecture

### Service topology

```text
Browser
  │
  │ HTTP  •  English/Arabic UI  •  RTL/LTR presentation
  ▼
Next.js frontend (port 3000)
  │
  │ /api/v1
  ▼
FastAPI backend (port 8000)
  ├──────────────────────────────► PostgreSQL application database (port 5432)
  │                                 connection profiles • schema snapshots • catalog revisions
  │                                 conversations • messages • requests • execution provenance
  │
  ├──────────────────────────────► LiteLLM proxy (port 4000)
  │                                 planner and answer model aliases • provider routing
  │                                 primary/fallback configuration
  │                                    │
  │                                    ▼
  │                                 Groq or another configured LLM provider
  │
  └──────────────────────────────► Analytics database
                                    PostgreSQL or MySQL • separate read-only credentials
                                    schema introspection • EXPLAIN • read-only query execution
```

### Question-to-answer flow

```text
1. Conversation and context

   User question
       │
       ├── redact credential-like values before persistence and model context
       ├── resolve response language (English, Arabic, or auto)
       └── load selected connection's:
             semantic catalog + physical schema snapshot + conversation summary
             bounded BI state + recent message window
       │
       ▼
   Bounded planner context
   (context budget reserves output tokens; only a small result sample is retained)

2. Structured SQL planning and provider fallback

   Planner request — strict QueryPlan JSON schema — temperature 0
       │
       ▼
   Primary planner alias
       ├── valid structured response ───────────────────────────────► query plan
       └── unavailable or invalid response
             │
             ├── one same-model retry
             │     (adds correction instructions when the JSON/schema output is invalid)
             │
             └── still failing ──► fallback planner alias
                                      ├── one correction attempt
                                      ├── valid response ──────────► query plan
                                      └── failure ─────────────────► retryable LLM failure
       │
       ├── needs clarification ────────────────────────────────────► clarification response
       └── unsupported ────────────────────────────────────────────► unsupported response

3. SQL safety and execution gates

   Ready query plan
       │
       ├── verify plan dialect and current schema fingerprint
       ├── SQL AST policy validation
       │     • exactly one SELECT or SELECT-CTE statement
       │     • approved physical tables and columns only
       │     • safe functions and join-count limit
       │     • bound parameters and maximum output-row limit
       ├── database-specific EXPLAIN
       ├── estimated-row and query-cost limits
       └── read-only transaction
             • statement timeout • lock timeout • bounded result set
       │
       ├── success ────────────────────────────────────────────────► result table
       └── recoverable query error (execution, parameter, or column)
             │
             └── one SQL-repair request
                   (question + failed SQL + sanitized error + approved schema/catalog)
                   │
                   └── run every safety and execution gate again
                         ├── success ──────────────────────────────► result table
                         └── failure ──────────────────────────────► failed response

   SQL parse and policy rejections, authentication/availability failures, timeouts, stale schemas,
   and excessive costs never enter the repair path. Unknown or ambiguous physical-column errors
   are the exception: they receive the single repair attempt shown above.

4. Answer and chart production

   Result table
       │
       ├── no rows ────────────────────────────────────────────────► deterministic empty-result answer
       └── rows returned
             │
             ▼
           Answer request — strict AnswerOutput JSON schema
             │
             ├── primary answer alias
             │     └── same primary/fallback model routing and one JSON correction attempt
             └── generation failure ───────────────────────────────► deterministic result summary
             │
             ▼
           Answer grounding validation
             • non-empty answer
             • numeric claims must exist in result rows or request/execution context
             • chart columns must exist in the executed result and y-values must be numeric
             │
             ├── passes ──────────────────────────────────────────► validated answer
             └── fails ──► one answer-regeneration request
                              ├── passes ─────────────────────────► validated answer + warning
                              └── fails ──────────────────────────► deterministic result summary
             │
             ▼
           Deterministic chart normalization
             • KPI for single-row metrics
             • line chart for temporal series
             • bar/donut/scatter only when column type and cardinality allow it
             • incompatible model suggestion is replaced or omitted

5. Persistence and display

   Validated response
       │
       ├── persist request, normalized SQL, result metadata, warnings, and model metadata
       ├── persist user and assistant messages with idempotent client message IDs
       ├── update bounded conversation BI state; compact old history best-effort
       └── render answer, table, chart, and SQL in the Next.js workspace
```

The LLM receives business context and result data only; it never receives database credentials or a direct database connection. Fallback use, its reason, retry count, model alias, and latency are captured as execution metadata.

## Prerequisites

- Docker and Docker Compose for the full local stack
- Python 3.12+ and [uv](https://docs.astral.sh/uv/) for backend-only development
- Node.js 22+ and npm for frontend-only development
- A Groq API key when running with live LLM planning

## Quick start

1. Create a `.env` file in the repository root. The following configuration enables mock mode:

   ```dotenv
   MOCK_MODE=true
   LITELLM_MASTER_KEY=local-development-key
   GROQ_API_KEY=
   SQL_PLANNER_PRIMARY_MODEL=groq/qwen/qwen3.6-27b
   SQL_PLANNER_FALLBACK_MODEL=groq/qwen/qwen3.6-27b
   LITELLM_ANSWER_PRIMARY_MODEL=groq/qwen/qwen3.6-27b
   LITELLM_ANSWER_FALLBACK_MODEL=groq/qwen/qwen3.6-27b
   LITELLM_TIMEOUT_SECONDS=60
   ```

2. Start the stack:

   ```bash
   docker compose up --build
   ```

3. Open the application at [http://localhost:3000](http://localhost:3000). The API documentation is available at [http://localhost:8000/docs](http://localhost:8000/docs), and LiteLLM is exposed at [http://localhost:4000](http://localhost:4000).

To use a live planner, set `MOCK_MODE=false` and provide `GROQ_API_KEY`. Model identifiers in `.env` are passed to LiteLLM; choose values supported by your provider.

Stop the local services with `docker compose down`. Application database data is retained in the `app_db_data` Docker volume.

## Local development

### Backend

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The API runs on `http://localhost:8000`. By default it reads `.env` from the backend directory or repository root.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000/api/v1`. Override this for another backend location with `NEXT_PUBLIC_API_BASE_URL`.

### Make targets

```bash
make up      # build and start the Compose stack
make down    # stop the Compose stack
make logs    # follow service logs
make test    # run backend tests
make lint    # run Ruff and mypy for the backend
make format  # format backend code with Ruff
```

## Connect an analytics database

Use the connection workspace in the web app to test a connection, save it, and introspect its schema. Then review and publish a semantic catalog for that connection before asking questions. An example catalog is available at [`catalogs/analytics_catalog.example.yaml`](catalogs/analytics_catalog.example.yaml).

Use a dedicated analytics account with read-only access. The application supports PostgreSQL and MySQL connectors; its internal application state is stored in PostgreSQL.

## Testing

Run the normal backend suite:

```bash
cd backend
uv run pytest
```

Run frontend tests, type checking, and a production build:

```bash
cd frontend
npm test
npx tsc --noEmit
npm run build
```

The connector contract tests are opt-in because they require isolated PostgreSQL and MySQL analytics databases:

```bash
docker compose --profile integration up -d postgres-analytics mysql-analytics
cd backend
P2I_RUN_INTEGRATION=1 \
P2I_POSTGRES_ANALYTICS_URL=postgresql+asyncpg://analytics:analytics@127.0.0.1:5433/analytics \
P2I_MYSQL_ANALYTICS_URL=mysql+asyncmy://analytics:analytics@127.0.0.1:3307/analytics \
uv run pytest tests/integration/test_database_connectors.py
```

## Security model

Prompt2Insight treats the model as a planner, not a database client. The model never receives database credentials or executes SQL directly. Before execution, the backend enforces a single `SELECT` or CTE statement, validates the query's AST and physical schema references, rejects unsafe SQL and functions, applies output limits, inspects `EXPLAIN` cost, and uses read-only execution with statement and lock timeouts.

Query errors may receive one planner repair attempt. Safety-policy, authentication, availability, and timeout failures are not retried as altered SQL. Natural-language answers are grounded against query results, and chart specifications are normalized against the returned schema and cardinality.

These controls complement—not replace—database permissions. Always use least-privilege, read-only credentials for analytics connections.

## Project layout

```text
backend/       FastAPI application, database connectors, migrations, and tests
frontend/      Next.js chat and connection-management interface
catalogs/      Bilingual semantic catalog examples
infra/         LiteLLM configuration and integration database contracts
data/          Sample data
```

## License

This project is distributed under the terms in [LICENSE](LICENSE).
