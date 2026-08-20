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
  -> answer generation
  -> lightweight answer grounding (one regeneration, then deterministic fallback)
  -> deterministic result-aware chart recommendation and normalization
```

Query-level database errors receive one planner repair attempt. Repaired SQL always returns
through the same AST, physical-schema, row-limit, EXPLAIN, cost, and read-only execution path;
timeouts, availability/authentication failures, and safety-policy rejections are never repaired.

The semantic catalog supplies business-definition guidance, preferred metric and dimension
expressions, descriptions, and English/Arabic terminology and aliases. It is planning context,
not a hard metric authorization engine, metric/dimension ACL, join authorization layer, or
privacy aggregation policy engine. Derived analytical expressions are allowed when they use
physical schema objects supplied to the planner.

The security seam is the database and SQL layer: dedicated read-only credentials and
transactions, SELECT/SELECT-CTE-only single statements, introspected approved schemas and tables,
physical column-existence checks, dangerous SQL/function restrictions, bounded output,
statement and lock timeouts, and EXPLAIN cost control. Once safe SQL executes, answer or chart
problems cannot erase the result table, validated SQL, or execution provenance. Answer grounding
rejects clear fabricated result numbers without trying to prove every contextual number in
natural language. The LLM supplies only semantic chart intent; the application validates it
against the result schema and cardinality, then normalizes, replaces, or omits it independently.
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

## Conversation persistence and memory

The chat UI uses `/chat/new` to start a thread and `/chat/{conversationId}` to reopen one.
`POST /api/v1/conversations` binds each conversation to one ready connection profile and stores its
language. `GET /api/v1/conversations` lists active threads, `GET /{id}` returns its ordered
messages and saved analytics payloads, `PATCH /{id}` renames, changes language, archives, or
restores it, and `DELETE /{id}` removes it. `POST /{id}/messages` persists a user message and its
assistant result; client message IDs make retries idempotent. The existing
`POST /{id}/requests` endpoint remains available for analytics requests.

```text
connection_profiles 1 ──< conversations 1 ──< messages
                         │                    (conversation_id, sequence_number unique)
                         └──< analytical_requests ──< query_executions
```

`conversations` has a UUID primary key, nullable connection-profile foreign key, language, title,
summary, JSONB structured BI state, archive timestamp, and an `updated_at` index. `messages` has a
UUID primary key, cascading conversation foreign key, ordered sequence number, constrained role
(`user`, `assistant`, or `system`), content, JSONB metadata, and an index on
`(conversation_id, sequence_number)`. The unique sequence constraint keeps message ordering stable
across reloads and restarts.

For every new question the backend builds planner context from the bound connection's catalog and
schema, stored summary, structured BI state (last question/SQL, metrics, dimensions, filters, and
bounded result sample), and recent persisted messages. Sensitive values are redacted; old history
is compacted into the summary once the configured threshold is crossed, retaining the configured
recent-message window. `CONVERSATION_CONTEXT_TOKEN_BUDGET` and
`CONVERSATION_OUTPUT_TOKEN_RESERVE` bound the prompt before planner execution.

For a local persistence check, start the stack, create and submit a conversation, restart only the
`backend` and `frontend` services, then reopen `/chat/{conversationId}`. The app database volume is
not removed by a normal restart. Check migrations with
`docker compose exec -T backend sh -lc 'cd /app && alembic current && alembic upgrade head'`;
run backend tests with `cd backend && python -m pytest`, and frontend checks with
`cd frontend && npm test && npx tsc --noEmit && npm run build`.
