import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.analytics.answer_validation import validate_answer_output
from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.resolve_language import resolve_response_language
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    AnswerOutput,
    QueryPlan,
    ResultTable,
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


class AnswerGenerator(Protocol):
    async def generate(
        self,
        *,
        model_group: ModelGroup[AnswerOutput],
        system_prompt: str,
        user_prompt: str,
        generation_stage: str | None = None,
        database_dialect: SQLDialect | None = None,
    ) -> GenerationResult[AnswerOutput]: ...


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
        answerer: AnswerGenerator | None = None,
        answer_model_group: ModelGroup[AnswerOutput] | None = None,
        query_executor: QueryPlanExecutor | None = None,
        connector_resolver: ConnectorResolver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._mock_mode = mock_mode
        self._repository = repository
        self._planning_context_store = planning_context_store
        self._planner = planner
        self._planner_model_group = planner_model_group
        self._answerer = answerer
        self._answer_model_group = answer_model_group
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
                table = self._query_executor.result_table(execution.result)
                answer_output = AnswerOutput(answer="")
                answer_metadata = None
                if execution.result.row_count:
                    if self._answerer is None or self._answer_model_group is None:
                        raise RuntimeError("Production answer dependencies are not configured.")
                    answer_result = await self._answerer.generate(
                        model_group=self._answer_model_group,
                        system_prompt=_answer_system_prompt(result.output.response_language),
                        user_prompt=_answer_user_prompt(request.question, table),
                        generation_stage="answer",
                        database_dialect=context.dialect,
                    )
                    validate_answer_output(answer_result.output, table)
                    answer_output = answer_result.output
                    answer_metadata = answer_result.metadata
                response = AnalyticsResponse(
                    status=(
                        AnalyticsStatus.EMPTY_RESULT
                        if execution.result.row_count == 0
                        else AnalyticsStatus.SUCCESS
                    ),
                    request_id=request.request_id,
                    language=result.output.response_language,
                    answer=answer_output.answer or None,
                    insights=answer_output.insights,
                    table=table,
                    chart=answer_output.chart,
                    sql=execution.validated.normalized_sql,
                    query_plan=result.output,
                    warnings=[
                        *answer_output.warnings,
                        *(
                            [_truncation_warning(result.output.response_language)]
                            if execution.result.truncated
                            else []
                        ),
                    ],
                    model_metadata=result.metadata,
                    answer_model_metadata=answer_metadata,
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


def _answer_system_prompt(language: str) -> str:
    return f"""You are the Prompt2Insight answer writer. Respond in {language}.
Use only the executed result table supplied by the user. Do not calculate, estimate, infer,
or mention any number that is not literally present in that table. Keep the answer concise.
You may propose a chart only by referencing exact result-table column names in x_column and
y_columns. Never include data points or values in a chart specification. Localize the answer,
chart title, warnings, and empty-state wording to the requested language. Return only the
required structured JSON result."""


def _answer_user_prompt(question: str, table: ResultTable) -> str:
    return f"User question:\n{question}\n\nExecuted result table (JSON):\n{table.model_dump_json()}"


def _truncation_warning(language: str) -> str:
    return (
        "تم اقتطاع صفوف النتائج عند الحد المسموح به."
        if language == "ar"
        else "Result rows were truncated to the configured limit."
    )
