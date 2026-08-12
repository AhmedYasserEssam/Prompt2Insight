from pathlib import Path
from uuid import UUID, uuid4

from app.application.analytics.run_analytics_request import AnalyticsRequestService, PlanningContext
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ModelExecutionMetadata,
    QueryPlan,
)
from app.domain.databases.models import DatabaseCapabilities, SchemaSnapshot, SQLDialect
from app.infrastructure.ai.litellm_gateway import GenerationResult, ModelGroup
from app.infrastructure.catalogs.loader import load_catalog


class InMemoryRequestRepository:
    def __init__(self) -> None:
        self.responses: dict[UUID, AnalyticsResponse] = {}
        self.saved_conversation_id: UUID | None = None

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return self.responses.get(request_id)

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
    ) -> AnalyticsResponse:
        self.saved_conversation_id = conversation_id
        self.responses[request.request_id] = response
        return response


async def test_request_is_persisted_and_recovered_by_global_id() -> None:
    repository = InMemoryRequestRepository()
    service = AnalyticsRequestService(mock_mode=True, repository=repository)
    conversation_id = uuid4()
    request = AnalyticsRequest(request_id=uuid4(), question="Revenue by month")

    created = await service.run(conversation_id=conversation_id, request=request)
    recovered = await service.get(request.request_id)

    assert created.request_id == request.request_id
    assert recovered == created
    assert repository.saved_conversation_id == conversation_id


class ProductionRepository(InMemoryRequestRepository):
    def __init__(self, context: PlanningContext) -> None:
        super().__init__()
        self.context = context
        self.saved_context: PlanningContext | None = None

    async def get_planning_context(self, conversation_id: UUID) -> PlanningContext:
        return self.context

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
        planning_context: PlanningContext | None = None,
    ) -> AnalyticsResponse:
        self.saved_context = planning_context
        return await super().save(
            conversation_id=conversation_id, request=request, response=response
        )


class PlannerStub:
    def __init__(self, result: GenerationResult[QueryPlan]) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def plan(self, **kwargs: object) -> GenerationResult[QueryPlan]:
        self.calls.append(kwargs)
        return self.result


async def test_production_request_reaches_planner_and_persists_planning_context() -> None:
    catalog, _ = load_catalog(
        Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"
    )
    context = PlanningContext(
        dialect=SQLDialect.MYSQL,
        catalog=catalog,
        schema_snapshot=SchemaSnapshot(
            dialect=SQLDialect.MYSQL,
            database_name="analytics",
            server_version="8",
            tables=[],
            capabilities=DatabaseCapabilities(dialect=SQLDialect.MYSQL, server_version="8"),
        ),
        catalog_revision_id=uuid4(),
        schema_snapshot_id=uuid4(),
    )
    plan = QueryPlan(
        status="ready",
        response_language="ar",
        database_dialect=SQLDialect.MYSQL,
        interpretation="الإيرادات حسب الشهر",
    )
    planner = PlannerStub(
        GenerationResult(
            output=plan,
            metadata=ModelExecutionMetadata(
                provider="litellm", model="groq/qwen/qwen3.6-27b", generation_stage="planner"
            ),
        )
    )
    repository = ProductionRepository(context)
    service = AnalyticsRequestService(
        mock_mode=False,
        repository=repository,
        planning_context_store=repository,
        planner=planner,
        planner_model_group=ModelGroup("planner", "primary", "fallback", QueryPlan),
    )
    request = AnalyticsRequest(request_id=uuid4(), question="وريني revenue لكل شهر في 2025")

    response = await service.run(conversation_id=uuid4(), request=request)

    assert response.status is AnalyticsStatus.PLANNED
    assert response.query_plan == plan
    assert response.model_metadata == planner.result.metadata
    assert repository.saved_context == context
    assert planner.calls[0]["dialect"] is SQLDialect.MYSQL
    assert planner.calls[0]["catalog"] == catalog
