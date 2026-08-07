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
- LiteLLM primary/fallback model routing
- Docker Compose development environment
- Unit tests and an implementation checklist

## Security boundary

```text
Question
  -> language resolution
  -> bilingual semantic catalog
  -> structured query plan
  -> SQLGlot validation
  -> database-specific EXPLAIN
  -> cost guard
  -> read-only execution
  -> grounded answer and validated chart
```

The LLM never receives database credentials and never executes SQL directly.

## Start the project

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Frontend: http://localhost:3000
- Backend OpenAPI: http://localhost:8000/docs
- LiteLLM: http://localhost:4000

`MOCK_MODE=true` is the default. The API starts without external LLM keys and returns a controlled bilingual `not_configured` response until the real pipeline is wired.

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
