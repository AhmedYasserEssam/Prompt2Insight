import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.resolve_language import resolve_response_language
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    QueryPlan,
)
from app.domain.databases.connector import SQLDatabaseConnector
from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.ai.litellm_gateway import GenerationResult, ModelGroup
from app.infrastructure.catalogs.models import AnalyticsCatalog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanningContext:
    dialect: SQLDialect
    catalog: AnalyticsCatalog
    schema_snapshot: SchemaSnapshot
    catalog_revision_id: UUID
    schema_snapshot_id: UUID
    connection_profile_id: UUID | None = None
    credential_reference: str | None = None


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


class ConnectorResolver(Protocol):
    async def connect(self, context: PlanningContext) -> SQLDatabaseConnector: ...


class AnalyticsRequestService:
    def __init__(
        self,
        *,
        mock_mode: bool,
        repository: AnalyticsRequestStore,
        planning_context_store: PlanningContextStore | None = None,
        planner: QueryPlanner | None = None,
        planner_model_group: ModelGroup[QueryPlan] | None = None,
        query_executor: QueryPlanExecutor | None = None,
        connector_resolver: ConnectorResolver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._mock_mode = mock_mode
        self._repository = repository
        self._planning_context_store = planning_context_store
        self._planner = planner
        self._planner_model_group = planner_model_group
        self._query_executor = query_executor
        self._connector_resolver = connector_resolver
        self._settings = settings

    async def run(self, *, conversation_id: UUID, request: AnalyticsRequest) -> AnalyticsResponse:
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
        planner_metadata = None
        try:
            result = await self._planner.plan(
                question=request.question,
                dialect=context.dialect,
                catalog=context.catalog,
                schema_snapshot=context.schema_snapshot,
                model_group=self._planner_model_group,
            )
            planner_metadata = result.metadata
            logger.debug(
                "Planner produced plan request_id=%s status=%s metric_ids=%s dimension_ids=%s "
                "database_dialect=%s",
                request.request_id,
                result.output.status,
                result.output.metric_ids,
                result.output.dimension_ids,
                result.output.database_dialect.value,
            )
            if result.output.status != "ready":
                response = self._planner_response(request=request, result=result)
            elif (
                self._query_executor is None
                or self._connector_resolver is None
                or self._settings is None
            ):
                raise RuntimeError("Production execution dependencies are not configured.")
            else:
                connector = await self._connector_resolver.connect(context)
                try:
                    execution = await self._query_executor.execute(
                        plan=result.output,
                        catalog=context.catalog,
                        schema_snapshot=context.schema_snapshot,
                        connector=connector,
                        settings=self._settings,
                    )
                finally:
                    await connector.close()
                response = AnalyticsResponse(
                    status=(
                        AnalyticsStatus.EMPTY_RESULT
                        if execution.result.row_count == 0
                        else AnalyticsStatus.SUCCESS
                    ),
                    request_id=request.request_id,
                    language=result.output.response_language,
                    table=self._query_executor.result_table(execution.result),
                    sql=execution.validated.normalized_sql,
                    query_plan=result.output,
                    warnings=["Result rows were truncated to the configured limit."]
                    if execution.result.truncated
                    else [],
                    model_metadata=result.metadata,
                )
        except Prompt2InsightError as exc:
            logger.warning(
                "Analytics request failed request_id=%s error_code=%s detail=%s",
                request.request_id,
                exc.code.value,
                exc.message,
            )
            response = AnalyticsResponse(
                status=AnalyticsStatus.FAILED,
                request_id=request.request_id,
                language=language,
                error_code=exc.code,
                retryable=exc.retryable,
                model_metadata=planner_metadata,
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
