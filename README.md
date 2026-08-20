# Prompt2Insight

**A bilingual, conversation-aware AI assistant for safe business intelligence.**

Prompt2Insight converts English and Arabic questions into validated, read-only SQL, executes approved queries against PostgreSQL or MySQL analytics databases, and returns grounded answers, tables, and interactive charts.

It combines a [FastAPI](https://fastapi.tiangolo.com/) backend, a [Next.js](https://nextjs.org/) chatbot interface, PostgreSQL-backed conversation memory, and [LiteLLM](https://docs.litellm.ai/) model routing.

![Prompt2Insight chatbot showing a sales trend analysis](assets/chatbot.png)

## Features

- Natural-language analytics in English, Arabic, Egyptian Arabic, and code-switched input
- RTL-aware Arabic interface and localized responses
- PostgreSQL and MySQL analytics connectors with schema introspection
- Persistent conversations that can be reopened and continued
- Bounded conversational context with summaries and structured BI state
- Bilingual semantic catalogs for business definitions, metrics, dimensions, and joins
- Structured SQL planning with primary and fallback LLM models
- SQL AST validation, approved-schema enforcement, and read-only execution
- `EXPLAIN` cost checks, timeouts, join limits, and bounded result sets
- Grounded natural-language answers with deterministic fallbacks
- Responsive line, area, table, KPI, bar, donut, and scatter visualizations
- Execution provenance, warnings, model metadata, and retry information
- Mock mode for local UI and API development without an LLM provider

## Example

**Question**

> Show me the latest sales trend.

**Answer**

> Total sales peaked at 117,938.16 in November 2018 and reached their lowest point of 4,519.89 in February 2015.

The interface also provides a responsive visualization, result table, generated SQL, and technical execution details.

## Quick start

### Prerequisites

- Docker and Docker Compose for the complete local stack
- Python 3.12+ and [uv](https://docs.astral.sh/uv/) for backend-only development
- Node.js 22+ and npm for frontend-only development
- A Groq API key for live LLM planning

### 1. Configure the environment

Create a `.env` file in the repository root.

For mock mode:

```dotenv
MOCK_MODE=true
LITELLM_MASTER_KEY=local-development-key
GROQ_API_KEY=

SQL_PLANNER_PRIMARY_MODEL=groq/qwen/qwen3.6-27b
SQL_PLANNER_FALLBACK_MODEL=groq/openai/gpt-oss-20b
LITELLM_ANSWER_PRIMARY_MODEL=groq/qwen/qwen3.6-27b
LITELLM_ANSWER_FALLBACK_MODEL=groq/openai/gpt-oss-20b
LITELLM_TIMEOUT_SECONDS=60
```

Mock mode exercises the local interface and API without contacting an external LLM provider.

For live planning, change `MOCK_MODE` to `false` and provide `GROQ_API_KEY`. Model identifiers are passed through LiteLLM and can be replaced with models supported by another configured provider.

### 2. Start the application

```bash
docker compose up --build
```

### 3. Open the services

| Service | URL |
|---|---|
| Web application | [http://localhost:3000](http://localhost:3000) |
| API documentation | [http://localhost:8000/docs](http://localhost:8000/docs) |
| LiteLLM proxy | [http://localhost:4000](http://localhost:4000) |

Stop the stack with:

```bash
docker compose down
```

Application data is retained in the `app_db_data` Docker volume.

## Using Prompt2Insight

1. Open **Settings & connections**.
2. Create a PostgreSQL or MySQL analytics connection using dedicated read-only credentials.
3. Test and save the connection.
4. Introspect the database schema.
5. Review and publish the semantic catalog.
6. Select the connection in the chat workspace.
7. Ask a question in English or Arabic.
8. Continue the analysis with follow-up questions in the same conversation.

An example semantic catalog is available at [`catalogs/analytics_catalog.example.yaml`](catalogs/analytics_catalog.example.yaml).

## Architecture

```mermaid
flowchart LR
    Browser["Next.js chatbot"] --> API["FastAPI API"]
    API --> AppDB["Application PostgreSQL"]
    API --> LLM["LiteLLM proxy"]
    API --> Analytics["PostgreSQL or MySQL analytics DB"]
    LLM --> Provider["Configured LLM provider"]
```

The application database stores connection profiles, schema snapshots, catalog revisions, conversations, messages, analytical requests, and execution provenance. Analytics databases use separate read-only credentials.

### Question-to-answer flow

1. **Build context:** Load the selected connection's semantic catalog, current schema snapshot, bounded conversation summary, structured BI state, and recent messages.
2. **Plan:** Request a structured query plan from the primary model, validate its schema, and use the configured fallback when necessary.
3. **Validate:** Parse the SQL AST and enforce the approved dialect, schema, tables, columns, functions, joins, parameters, and row limits.
4. **Estimate:** Run the database-specific `EXPLAIN` guard and reject excessive cost or estimated rows.
5. **Execute:** Run the query in a read-only transaction with statement and lock timeouts.
6. **Repair:** Permit one repair attempt only for approved recoverable query errors, then repeat every safety gate.
7. **Answer:** Generate a grounded response and validate numeric claims and chart fields against the executed result.
8. **Persist:** Save messages, analytical state, normalized SQL, warnings, and execution metadata before rendering the response.

The model receives approved business context and bounded result data. It does not receive database credentials or a direct database connection.

## Safety model

Prompt2Insight treats the LLM as a planner rather than a database client. The backend applies the following controls before executing generated SQL:

- Exactly one `SELECT` or `SELECT`-CTE statement
- SQL AST validation
- Current-schema fingerprint verification
- Approved physical tables, columns, functions, and joins
- Bound parameters
- Maximum join and output-row limits
- Database-specific `EXPLAIN` checks
- Estimated-row and query-cost limits
- Read-only transactions
- Statement and lock timeouts
- Sensitive-column and privacy-policy enforcement

Parse failures, policy rejections, stale schemas, authentication failures, unavailable databases, timeouts, and excessive costs do not enter the SQL-repair path. Recoverable execution, parameter, or approved column-resolution errors receive at most one repair attempt, and the repaired SQL must pass every validation and execution gate again.

Generated answers are checked against returned data. Invalid chart fields or unsupported chart shapes are corrected deterministically or omitted.

These application controls complement database permissions; they do not replace them. Analytics connections should always use least-privilege, read-only database accounts.

## Conversation memory

Prompt2Insight stores conversations and messages in the application PostgreSQL database. Each conversation is bound to its selected analytics connection and retains:

- Recent user and assistant messages
- A compact summary of older messages
- The last successful analytical question and normalized SQL
- Selected metrics, dimensions, filters, and result columns
- Chart state and bounded result metadata

The backend reconstructs a bounded context for every LLM request rather than sending unlimited conversation history or complete result datasets.

## Local development

### Backend

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000` and reads environment configuration from the backend directory or repository root.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000/api/v1`. Set `NEXT_PUBLIC_API_BASE_URL` to use a different backend location.

### Make targets

```bash
make up      # Build and start the Compose stack
make down    # Stop the Compose stack
make logs    # Follow service logs
make test    # Run backend tests
make lint    # Run Ruff and mypy
make format  # Format backend code with Ruff
```

## Testing

### Backend

```bash
cd backend
uv run pytest
```

### Frontend

```bash
cd frontend
npm test
npx tsc --noEmit
npm run build
```

### Connector contract tests

Connector tests are opt-in because they require isolated PostgreSQL and MySQL analytics databases.

```bash
docker compose --profile integration up -d postgres-analytics mysql-analytics

cd backend
P2I_RUN_INTEGRATION=1 \
P2I_POSTGRES_ANALYTICS_URL=postgresql+asyncpg://analytics:analytics@127.0.0.1:5433/analytics \
P2I_MYSQL_ANALYTICS_URL=mysql+asyncmy://analytics:analytics@127.0.0.1:3307/analytics \
uv run pytest tests/integration/test_database_connectors.py
```

## Project layout

```text
backend/       FastAPI application, connectors, migrations, and tests
frontend/      Next.js chatbot and connection-management interface
catalogs/      Bilingual semantic catalog examples
infra/         LiteLLM configuration and integration database contracts
data/          Sample analytics data
docs/images/   README and documentation images
```

## Contributing

Issues and pull requests are welcome. Before submitting a change, run the relevant tests, type checks, lint checks, and production build.

Do not commit database passwords, provider API keys, generated `.env` files, or customer data.

## License

This project is distributed under the terms in [LICENSE](LICENSE).
