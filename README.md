# Prompt2Insight Starter

Starter repository for a bilingual English/Arabic business-intelligence assistant that generates safe SQL for PostgreSQL and MySQL.

## Included

- FastAPI backend
- Next.js frontend with RTL/LTR support
- PostgreSQL internal application database
- Shared `SQLDatabaseConnector` interface
- PostgreSQL and MySQL connector implementations
- SQLGlot policy validation
- Bilingual semantic-catalog example
- LiteLLM/Groq Qwen primary planner routing with optional local vLLM support
- Docker Compose development environment
- Unit tests and an implementation checklist

## Security boundary

```text
Question
  -> language resolution
  -> schema plus bilingual business glossary context
  -> structured LLM query plan
  -> basic SQL safety validation
  -> database-specific EXPLAIN
  -> cost guard
  -> read-only execution
  -> grounded answer and validated chart
```

The semantic catalog supplies business-definition guidance, preferred metric and dimension
expressions, descriptions, and English/Arabic terminology and aliases. It is planning context,
not a hard metric authorization engine, metric/dimension ACL, join authorization layer, or
privacy aggregation policy engine. Derived analytical expressions are allowed when they use
physical schema objects supplied to the planner.

The security seam is the database and SQL layer: dedicated read-only credentials and
transactions, SELECT/SELECT-CTE-only single statements, introspected approved schemas and tables,
physical column-existence checks, dangerous SQL/function restrictions, bounded output,
statement and lock timeouts, and EXPLAIN cost control. Answer numeric/date/entity grounding and
chart-column grounding remain separate post-execution checks. The LLM never receives database
credentials and never executes SQL directly.

## Start the project

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Frontend: http://localhost:3000
- Backend OpenAPI: http://localhost:8000/docs
- LiteLLM: http://localhost:4000

`MOCK_MODE=true` is the default. Normal development uses the LiteLLM proxy and Groq's hosted
`groq/qwen/qwen3.6-27b` planner model; set `GROQ_API_KEY`, `LITELLM_BASE_URL`, and
`LITELLM_MASTER_KEY` as shown in `.env.example`. No local vLLM service is needed. vLLM remains
an opt-in provider (`VLLM_ENABLED=true`) and is never part of normal application readiness.
`GET /api/v1/health/llm` checks LiteLLM's configured planner alias. `GET /api/v1/health/vllm`
reports `disabled` unless optional vLLM has been enabled.

Run the optional live provider boundary test only after vLLM is serving the configured Qwen
model:

```bash
cd backend
P2I_RUN_VLLM_INTEGRATION=1 uv run pytest tests/integration/test_vllm_planner.py
```

## Backend development

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run pytest
```

### Database connector contracts

The connector integration tests use isolated analytics databases and restricted
`analytics` credentials. They are opt-in and do not start with the normal stack.

```bash
docker compose --profile integration up -d postgres-analytics mysql-analytics
cd backend
P2I_RUN_INTEGRATION=1 \
P2I_POSTGRES_ANALYTICS_URL=postgresql+asyncpg://analytics:analytics@127.0.0.1:5433/analytics \
P2I_MYSQL_ANALYTICS_URL=mysql+asyncmy://analytics:analytics@127.0.0.1:3307/analytics \
uv run pytest tests/integration/test_database_connectors.py
```

## Frontend development

```bash
cd frontend
npm install
npm run dev
```

Follow `IMPLEMENTATION_CHECKLIST.md` in order.
