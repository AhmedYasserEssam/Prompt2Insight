import json
import logging
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from app.application.analytics.answer_fallback import deterministic_answer
from app.application.analytics.answer_validation import (
    validate_answer_output,
)
from app.application.analytics.chart_recommendation import ChartPolicy, recommend_chart
from app.application.analytics.execute_query_plan import ExecutedPlan, QueryPlanExecutor
from app.application.analytics.resolve_language import resolve_response_language
from app.application.conversations.conversation_context import (
    ConversationMemoryMessage,
    build_planner_context,
)
from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    AnswerOutput,
    ModelExecutionMetadata,
    QueryPlan,
    ResultTable,
    TitleOutput,
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
    language: str = "auto"
    summary: str | None = None
    context_state: dict[str, object] | None = None
    messages: tuple[ConversationMemoryMessage, ...] = ()


@dataclass(frozen=True)
class _ExecutionOutcome:
    execution: ExecutedPlan
    plan_result: GenerationResult[QueryPlan]
    warnings: list[str]


@dataclass(frozen=True)
class _AnswerOutcome:
    output: AnswerOutput
    metadata: ModelExecutionMetadata | None
    warnings: list[str]


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
        conversation_context: str | None = None,
    ) -> GenerationResult[QueryPlan]: ...

    async def repair(
        self,
        *,
        question: str,
        dialect: SQLDialect,
        catalog: AnalyticsCatalog,
        schema_snapshot: SchemaSnapshot,
        failed_plan: QueryPlan,
        database_error: str,
        model_group: ModelGroup[QueryPlan],
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
            plan_result = await self._planner.plan(
                question=request.question,
                dialect=context.dialect,
                catalog=context.catalog,
                schema_snapshot=context.schema_snapshot,
                model_group=self._planner_model_group,
                conversation_context=build_planner_context(
                    catalog=context.catalog,
                    schema_snapshot=context.schema_snapshot,
                    language=context.language,
                    summary=context.summary,
                    state=context.context_state or {},
                    messages=context.messages,
                    current_question=request.question,
                    settings=self._settings or Settings(),
                ),
            )
            planner_metadata = plan_result.metadata
            logger.debug(
                "Planner produced plan request_id=%s status=%s metric_ids=%s dimension_ids=%s "
                "database_dialect=%s",
                request.request_id,
                plan_result.output.status,
                plan_result.output.metric_ids,
                plan_result.output.dimension_ids,
                plan_result.output.database_dialect.value,
            )
            if plan_result.output.status != "ready":
                response = self._planner_response(request=request, result=plan_result)
            elif (
                self._query_executor is None
                or self._connector_resolver is None
                or self._settings is None
            ):
                raise RuntimeError("Production execution dependencies are not configured.")
            else:
                connector = await self._connector_resolver.connect(context)
                try:
                    execution_outcome = await self._execute_with_repair(
                        request=request,
                        plan_result=plan_result,
                        context=context,
                        connector=connector,
                    )
                finally:
                    await connector.close()

                plan_result = execution_outcome.plan_result
                planner_metadata = plan_result.metadata
                execution = execution_outcome.execution
                table = self._query_executor.result_table(execution.result)
                execution_context = _answer_execution_context(
                    plan_result.output, execution.validated.normalized_sql
                )
                if execution.result.row_count == 0:
                    answer_outcome = _AnswerOutcome(
                        output=AnswerOutput(
                            answer=deterministic_answer(table, plan_result.output.response_language)
                        ),
                        metadata=None,
                        warnings=[],
                    )
                else:
                    answer_outcome = await self._answer_with_recovery(
                        request=request,
                        language=plan_result.output.response_language,
                        dialect=context.dialect,
                        table=table,
                        execution_context=execution_context,
                    )

                answer_output = answer_outcome.output
                chart_recommendation = recommend_chart(
                    table,
                    answer_output.chart,
                    ChartPolicy(
                        max_bar_categories=self._settings.max_chart_bar_categories,
                        max_donut_categories=self._settings.max_chart_donut_categories,
                        max_series=self._settings.max_chart_series,
                    ),
                )
                answer_output = answer_output.model_copy(
                    update={"chart": chart_recommendation.chart}
                )
                response = AnalyticsResponse(
                    status=(
                        AnalyticsStatus.EMPTY_RESULT
                        if execution.result.row_count == 0
                        else AnalyticsStatus.SUCCESS
                    ),
                    request_id=request.request_id,
                    language=plan_result.output.response_language,
                    answer=answer_output.answer or None,
                    insights=answer_output.insights,
                    table=table,
                    chart=answer_output.chart,
                    sql=execution.validated.normalized_sql,
                    query_plan=plan_result.output,
                    warnings=[
                        *answer_output.warnings,
                        *execution_outcome.warnings,
                        *answer_outcome.warnings,
                        *(
                            [_chart_warning(plan_result.output.response_language)]
                            if chart_recommendation.suggestion_rejected
                            else []
                        ),
                        *(
                            [_truncation_warning(plan_result.output.response_language)]
                            if execution.result.truncated
                            else []
                        ),
                    ],
                    model_metadata=plan_result.metadata,
                    answer_model_metadata=answer_outcome.metadata,
                )
        except Prompt2InsightError as exc:
            cause = exc.__cause__ or exc
            logger.warning(
                "Analytics request failed request_id=%s error_code=%s dialect=%s "
                "exception_class=%s detail=%s",
                request.request_id,
                exc.code.value,
                context.dialect.value,
                type(cause).__name__,
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

    async def _execute_with_repair(
        self,
        *,
        request: AnalyticsRequest,
        plan_result: GenerationResult[QueryPlan],
        context: PlanningContext,
        connector: SQLDatabaseConnector,
    ) -> _ExecutionOutcome:
        assert self._query_executor is not None
        assert self._settings is not None
        try:
            execution = await self._query_executor.execute(
                plan=plan_result.output,
                schema_snapshot=context.schema_snapshot,
                connector=connector,
                settings=self._settings,
            )
            return _ExecutionOutcome(execution, plan_result, [])
        except Prompt2InsightError as exc:
            if exc.code not in {
                ErrorCode.QUERY_EXECUTION_FAILED,
                ErrorCode.INVALID_QUERY_PARAMETER,
                ErrorCode.UNAUTHORIZED_COLUMN,
            }:
                raise

            assert self._planner is not None
            assert self._planner_model_group is not None
            logger.warning(
                "Recoverable query failure request_id=%s error_code=%s detail=%s",
                request.request_id,
                exc.code.value,
                exc.safe_detail or exc.message,
            )
            repair_result = await self._planner.repair(
                question=request.question,
                dialect=context.dialect,
                catalog=context.catalog,
                schema_snapshot=context.schema_snapshot,
                failed_plan=plan_result.output,
                database_error=exc.safe_detail or exc.message,
                model_group=self._planner_model_group,
            )
            if repair_result.output.status != "ready" or repair_result.output.sql is None:
                raise Prompt2InsightError(
                    ErrorCode.QUERY_EXECUTION_FAILED,
                    "The query could not be repaired after its execution error.",
                ) from exc
            execution = await self._query_executor.execute(
                plan=repair_result.output,
                schema_snapshot=context.schema_snapshot,
                connector=connector,
                settings=self._settings,
            )
            return _ExecutionOutcome(
                execution,
                repair_result,
                [_sql_repair_warning(repair_result.output.response_language)],
            )

    async def _answer_with_recovery(
        self,
        *,
        request: AnalyticsRequest,
        language: str,
        dialect: SQLDialect,
        table: ResultTable,
        execution_context: str,
    ) -> _AnswerOutcome:
        fallback = AnswerOutput(answer=deterministic_answer(table, language))
        if self._answerer is None or self._answer_model_group is None:
            return _AnswerOutcome(fallback, None, [_answer_fallback_warning(language)])

        user_prompt = _answer_user_prompt(request.question, table, execution_context)
        try:
            first = await self._answerer.generate(
                model_group=self._answer_model_group,
                system_prompt=_answer_system_prompt(language),
                user_prompt=user_prompt,
                generation_stage="answer",
                database_dialect=dialect,
            )
        except Prompt2InsightError as exc:
            self._log_answer_recovery(request.request_id, exc, "generation")
            return _AnswerOutcome(fallback, None, [_answer_fallback_warning(language)])

        try:
            validate_answer_output(
                first.output,
                table,
                request_context=request.question,
                execution_context=execution_context,
            )
        except Prompt2InsightError as first_failure:
            self._log_answer_recovery(request.request_id, first_failure, "validation")
            try:
                regenerated = await self._answerer.generate(
                    model_group=self._answer_model_group,
                    system_prompt=_answer_regeneration_system_prompt(language),
                    user_prompt=(
                        f"{user_prompt}\n\nValidation feedback:\n{first_failure.message}\n"
                        "Regenerate the answer once."
                    ),
                    generation_stage="answer_regeneration",
                    database_dialect=dialect,
                )
            except Prompt2InsightError as regeneration_failure:
                self._log_answer_recovery(request.request_id, regeneration_failure, "regeneration")
                return _AnswerOutcome(
                    fallback, first.metadata, [_answer_fallback_warning(language)]
                )
            try:
                validate_answer_output(
                    regenerated.output,
                    table,
                    request_context=request.question,
                    execution_context=execution_context,
                )
            except Prompt2InsightError as second_failure:
                self._log_answer_recovery(request.request_id, second_failure, "second_validation")
                return _AnswerOutcome(
                    fallback, regenerated.metadata, [_answer_fallback_warning(language)]
                )
            return _AnswerOutcome(
                regenerated.output,
                regenerated.metadata,
                [_answer_regenerated_warning(language)],
            )

        return _AnswerOutcome(first.output, first.metadata, [])

    @staticmethod
    def _log_answer_recovery(request_id: UUID, error: Prompt2InsightError, stage: str) -> None:
        logger.warning(
            "Answer recovery request_id=%s stage=%s error_code=%s detail=%s",
            request_id,
            stage,
            error.code.value,
            error.message,
        )

    @staticmethod
    def _planner_response(
        *, request: AnalyticsRequest, result: GenerationResult[QueryPlan]
    ) -> AnalyticsResponse:
        plan = result.output
        status = {
            "needs_clarification": AnalyticsStatus.NEEDS_CLARIFICATION,
            "unsupported": AnalyticsStatus.UNSUPPORTED,
        }[plan.status]
        return AnalyticsResponse(
            status=status,
            request_id=request.request_id,
            language=plan.response_language,
            answer=plan.clarification_question if plan.status == "needs_clarification" else None,
            query_plan=plan,
            model_metadata=result.metadata,
        )

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return await self._repository.get(request_id)

    async def title_for(self, *, question: str, language: str) -> str | None:
        """Best-effort title generation; callers must fall back without failing analysis."""
        if self._answerer is None or self._answer_model_group is None:
            return None
        result = cast(
            GenerationResult[TitleOutput],
            await self._answerer.generate(
                model_group=cast(
                    ModelGroup[AnswerOutput],
                    ModelGroup(
                        "title",
                        self._answer_model_group.primary_model,
                        self._answer_model_group.fallback_model,
                        TitleOutput,
                    ),
                ),
                system_prompt=(
                    f"Create a concise conversation title in {language}. "
                    "Return only the required JSON."
                ),
                user_prompt=f"First successful analytical question:\n{question}",
                generation_stage="title",
            ),
        )
        output = result.output
        return output.title if isinstance(output, TitleOutput) else None


def _answer_system_prompt(language: str) -> str:
    return f"""You are the Prompt2Insight answer writer. Respond in {language}.
Use result values exactly for analytical claims. Do not invent numerical analytical facts. You
may naturally mention the user's requested filters, dates, years, categories, locations, and
top-N context. Use the executed query/filter context and result rows supplied below. Keep the
answer concise. Preserve exact returned categorical and entity text; do not translate or rename
identifiers or product names. A chart suggestion expresses meaning only: choose a semantic type,
exact result column names in x_column, y_columns, and optional series_column, plus optional text
labels. Never invent chart data or visual styling; the application controls all appearance and
formatting. Return only the required structured JSON result."""


def _answer_regeneration_system_prompt(language: str) -> str:
    return (
        f"{_answer_system_prompt(language)}\n"
        "The prior answer failed grounding validation. Apply the supplied validation feedback "
        "and return one corrected structured answer using exact result values."
    )


def _answer_execution_context(plan: QueryPlan, executed_sql: str) -> str:
    return json.dumps(
        {
            "executed_sql": executed_sql,
            "interpretation": plan.interpretation,
            "filters": [item.model_dump(mode="json") for item in plan.filters],
            "parameters": [item.model_dump(mode="json") for item in plan.parameters],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _answer_user_prompt(question: str, table: ResultTable, execution_context: str) -> str:
    return (
        f"User question:\n{question}\n\nExecuted query/filter context (JSON):\n"
        f"{execution_context}\n\nExecuted result columns and rows (JSON):\n"
        f"{table.model_dump_json()}"
    )


def _sql_repair_warning(language: str) -> str:
    return (
        "تم إصلاح الاستعلام وإعادة التحقق منه قبل التنفيذ."
        if language == "ar"
        else "The query was repaired and fully revalidated before execution."
    )


def _answer_regenerated_warning(language: str) -> str:
    return (
        "تمت إعادة إنشاء الإجابة مرة واحدة بعد فشل التحقق."
        if language == "ar"
        else "The answer was regenerated once after validation failed."
    )


def _answer_fallback_warning(language: str) -> str:
    return (
        "تعذر إنشاء إجابة موثوقة؛ تم استخدام ملخص حتمي للنتيجة."
        if language == "ar"
        else "A reliable generated answer was unavailable; a deterministic result summary was used."
    )


def _chart_warning(language: str) -> str:
    return (
        "تم حذف الرسم البياني غير الصالح؛ تظل نتيجة الاستعلام متاحة."
        if language == "ar"
        else "The invalid chart was omitted; the query result remains available."
    )


def _truncation_warning(language: str) -> str:
    return (
        "تم اقتطاع صفوف النتائج عند الحد المسموح به."
        if language == "ar"
        else "Result rows were truncated to the configured limit."
    )
