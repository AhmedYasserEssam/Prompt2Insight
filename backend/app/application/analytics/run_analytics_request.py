from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.analytics.resolve_language import resolve_response_language
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    QueryPlan,
)
from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.ai.litellm_gateway import GenerationResult, ModelGroup
from app.infrastructure.catalogs.models import AnalyticsCatalog


@dataclass(frozen=True)
class PlanningContext:
    dialect: SQLDialect
    catalog: AnalyticsCatalog
    schema_snapshot: SchemaSnapshot
    catalog_revision_id: UUID
    schema_snapshot_id: UUID


class AnalyticsRequestStore(Protocol):
    async def get(self, request_id: UUID) -> AnalyticsResponse | None: ...

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
        planning_context: PlanningContext | None = None,
    ) -> AnalyticsResponse: ...


class PlanningContextStore(Protocol):
    async def get_planning_context(self, conversation_id: UUID) -> PlanningContext: ...


class QueryPlanner(Protocol):
    async def plan(
        self,
        *,
        question: str,
        dialect: SQLDialect,
        catalog: AnalyticsCatalog,
        model_group: ModelGroup[QueryPlan],
        schema_snapshot: SchemaSnapshot | None = None,
    ) -> GenerationResult[QueryPlan]: ...


class AnalyticsRequestService:
    def __init__(
        self,
        *,
        mock_mode: bool,
        repository: AnalyticsRequestStore,
        planning_context_store: PlanningContextStore | None = None,
        planner: QueryPlanner | None = None,
        planner_model_group: ModelGroup[QueryPlan] | None = None,
    ) -> None:
        self._mock_mode = mock_mode
        self._repository = repository
        self._planning_context_store = planning_context_store
        self._planner = planner
        self._planner_model_group = planner_model_group

    async def run(
        self, *, conversation_id: UUID, request: AnalyticsRequest
    ) -> AnalyticsResponse:
        existing = await self._repository.get(request.request_id)
        if existing is not None:
            return existing

        language = resolve_response_language(request.question, request.response_language)
        if self._mock_mode:
            answer = (
                "اربط ملف اتصال بقاعدة بيانات ثم فعّل مخطط الاستعلام."
                if language == "ar"
                else "Register a database connection profile and enable the query planner."
            )
            return await self._repository.save(
                conversation_id=conversation_id,
                request=request,
                response=AnalyticsResponse(
                    status=AnalyticsStatus.FAILED,
                    request_id=request.request_id,
                    language=language,
                    answer=answer,
                    error_code=ErrorCode.NOT_CONFIGURED,
                ),
            )

        if (
            self._planning_context_store is None
            or self._planner is None
            or self._planner_model_group is None
        ):
            raise RuntimeError("Production planner dependencies are not configured.")

        context = await self._planning_context_store.get_planning_context(conversation_id)
        try:
            result = await self._planner.plan(
                question=request.question,
                dialect=context.dialect,
                catalog=context.catalog,
                schema_snapshot=context.schema_snapshot,
                model_group=self._planner_model_group,
            )
            response = self._planner_response(request=request, result=result)
        except Prompt2InsightError as exc:
            response = AnalyticsResponse(
                status=AnalyticsStatus.FAILED,
                request_id=request.request_id,
                language=language,
                error_code=exc.code,
                retryable=exc.retryable,
            )
        return await self._repository.save(
            conversation_id=conversation_id,
            request=request,
            response=response,
            planning_context=context,
        )

    @staticmethod
    def _planner_response(
        *, request: AnalyticsRequest, result: GenerationResult[QueryPlan]
    ) -> AnalyticsResponse:
        plan = result.output
        status = {
            "ready": AnalyticsStatus.PLANNED,
            "needs_clarification": AnalyticsStatus.NEEDS_CLARIFICATION,
            "unsupported": AnalyticsStatus.UNSUPPORTED,
        }[plan.status]
        return AnalyticsResponse(
            status=status,
            request_id=request.request_id,
            language=plan.response_language,
            answer=plan.clarification_question if plan.status == "needs_clarification" else None,
            warnings=(
                ["Query plan generated; SQL execution is pending Step 5."]
                if plan.status == "ready"
                else []
            ),
            query_plan=plan,
            model_metadata=result.metadata,
        )

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return await self._repository.get(request_id)
