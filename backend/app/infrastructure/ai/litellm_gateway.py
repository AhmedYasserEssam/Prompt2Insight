import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ModelExecutionMetadata, QueryPlan
from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog


@dataclass(frozen=True)
class ModelGroup[OutputT: BaseModel]:
    """Primary and fallback aliases that must return one output schema."""

    name: str
    primary_model: str
    fallback_model: str
    output_type: type[OutputT]


@dataclass(frozen=True)
class GenerationResult[OutputT: BaseModel]:
    output: OutputT
    metadata: ModelExecutionMetadata


@dataclass(frozen=True)
class AnalyticsModelGroups:
    planner: ModelGroup[QueryPlan]
    answer: ModelGroup[AnswerOutput]


def build_analytics_model_groups(settings: Settings) -> AnalyticsModelGroups:
    return AnalyticsModelGroups(
        planner=ModelGroup(
            name="planner",
            primary_model=settings.planner_primary_model,
            fallback_model=settings.planner_fallback_model,
            output_type=QueryPlan,
        ),
        answer=ModelGroup(
            name="answer",
            primary_model=settings.answer_primary_model,
            fallback_model=settings.answer_fallback_model,
            output_type=AnswerOutput,
        ),
    )


class VLLMGateway:
    """OpenAI-compatible vLLM provider for the existing model-group router."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 60,
        client: Any | None = None,
        provider: str = "vllm",
        thinking_options: dict[str, Any] | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
        )
        self._provider = provider
        self._thinking_options = thinking_options

    @classmethod
    def from_settings(cls, settings: Settings) -> "VLLMGateway":
        return cls(
            base_url=settings.vllm_base_url,
            api_key=settings.vllm_api_key,
            timeout_seconds=settings.vllm_timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.close()

    async def is_ready(self, *, model: str) -> bool:
        """Check configured-model availability for readiness probes, not requests."""
        try:
            models = await self._client.models.list()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return any(candidate.id == model for candidate in models.data)

    async def generate[OutputT: BaseModel](
        self,
        *,
        model_group: ModelGroup[OutputT],
        system_prompt: str,
        user_prompt: str,
        generation_stage: str | None = None,
        database_dialect: SQLDialect | None = None,
    ) -> GenerationResult[OutputT]:
        """Generate with one retry per provider before moving to the fallback."""
        primary_failure: Prompt2InsightError | None = None
        started_at = perf_counter()

        try:
            output, actual_model, retry_count = await self._generate_with_correction(
                model_alias=model_group.primary_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=model_group.output_type,
            )
            return GenerationResult(
                output=output,
                metadata=self._metadata(
                    actual_model=actual_model,
                    configured_model=model_group.primary_model,
                    latency_ms=self._latency_ms(started_at),
                    retry_count=retry_count,
                    generation_stage=generation_stage,
                    database_dialect=database_dialect,
                ),
            )
        except Prompt2InsightError as exc:
            primary_failure = exc

        try:
            output, actual_model, retry_count = await self._generate_with_correction(
                model_alias=model_group.fallback_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=model_group.output_type,
            )
            return GenerationResult(
                output=output,
                metadata=self._metadata(
                    actual_model=actual_model,
                    configured_model=model_group.fallback_model,
                    latency_ms=self._latency_ms(started_at),
                    fallback_used=True,
                    fallback_reason=primary_failure.code.value,
                    retry_count=1 + retry_count,
                    generation_stage=generation_stage,
                    database_dialect=database_dialect,
                ),
            )
        except Prompt2InsightError as fallback_failure:
            if (
                primary_failure.code is ErrorCode.LLM_UNAVAILABLE
                and fallback_failure.code is ErrorCode.LLM_UNAVAILABLE
            ):
                raise Prompt2InsightError(
                    ErrorCode.LLM_UNAVAILABLE,
                    "Both primary and fallback model providers are unavailable.",
                    retryable=True,
                ) from fallback_failure
            raise fallback_failure

    async def plan(
        self,
        *,
        question: str,
        dialect: SQLDialect,
        catalog: AnalyticsCatalog,
        model_group: ModelGroup[QueryPlan],
        schema_snapshot: SchemaSnapshot | None = None,
    ) -> GenerationResult[QueryPlan]:
        """Plan from supplied semantic context only; never execute SQL here."""
        return await self.generate(
            model_group=model_group,
            system_prompt=_planner_system_prompt(dialect),
            user_prompt=(
                "User question:\n"
                f"{question}\n\n"
                "Approved semantic catalog and schema context (JSON):\n"
                f"{catalog.model_dump_json()}\n"
                f"{schema_snapshot.model_dump_json() if schema_snapshot is not None else '{}'}"
            ),
            generation_stage="planner",
            database_dialect=dialect,
        )

    async def _generate_with_correction[OutputT: BaseModel](
        self,
        *,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        output_type: type[OutputT],
    ) -> tuple[OutputT, str | None, int]:
        try:
            output, actual_model = await self._generate_once(
                model_alias=model_alias,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=output_type,
            )
            return output, actual_model, 0
        except Prompt2InsightError as first_failure:
            correction_prompt = user_prompt
            if first_failure.code is ErrorCode.LLM_INVALID_OUTPUT:
                correction_prompt = (
                    f"{user_prompt}\n\nYour previous response did not match the required JSON "
                    "schema. "
                    "Return only a corrected JSON object that exactly matches it."
                )
            try:
                output, actual_model = await self._generate_once(
                    model_alias=model_alias,
                    system_prompt=system_prompt,
                    user_prompt=correction_prompt,
                    output_type=output_type,
                )
                return output, actual_model, 1
            except Prompt2InsightError as second_failure:
                raise second_failure from first_failure

    async def _generate_once[OutputT: BaseModel](
        self,
        *,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        output_type: type[OutputT],
    ) -> tuple[OutputT, str | None]:
        try:
            response = await self._client.chat.completions.create(
                model=model_alias,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": output_type.__name__,
                        "strict": True,
                        "schema": output_type.model_json_schema(),
                    },
                },
                temperature=0,
                extra_body=self._thinking_options,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise Prompt2InsightError(
                ErrorCode.LLM_UNAVAILABLE,
                "The model gateway is unavailable.",
                retryable=True,
            ) from exc

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The model returned a malformed structured response.",
                retryable=True,
            ) from exc
        if not content:
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The model returned no structured output.",
                retryable=True,
            )

        try:
            output = output_type.model_validate_json(content)
        except ValidationError as exc:
            raise Prompt2InsightError(
                ErrorCode.LLM_INVALID_OUTPUT,
                "The model returned invalid structured output.",
                retryable=True,
            ) from exc

        return output, cast(Any, response).model

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return round((perf_counter() - started_at) * 1000)

    def _metadata(
        self,
        *,
        actual_model: str | None,
        configured_model: str,
        latency_ms: int,
        retry_count: int,
        generation_stage: str | None,
        database_dialect: SQLDialect | None,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
    ) -> ModelExecutionMetadata:
        return ModelExecutionMetadata(
            actual_model=actual_model,
            provider=self._provider,
            model=actual_model or configured_model,
            latency_ms=latency_ms,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            retry_count=retry_count,
            generation_stage=generation_stage,
            database_dialect=database_dialect,
        )


def _planner_system_prompt(dialect: SQLDialect) -> str:
    return f"""You are the Prompt2Insight query planner. The user may write in English,
Modern Standard Arabic, Egyptian Arabic, or mixed Arabic/English. Understand the request
directly; do not translate or alter database identifiers. Use only the supplied catalog and
schema context. Never invent metrics, dimensions, tables, columns, relationships, or joins.
Respect catalog definitions, approved joins, and the explicit database dialect: {dialect.value}.
Use {dialect.value} SQL syntax only. Do not execute SQL. Return only the required structured
result. Return needs_clarification when the request cannot safely map to the catalog, and
unsupported when appropriate. When a query contains metrics grouped by dimensions, honor the
catalog privacy policy. Do not expose or directly query sensitive privacy-unit columns; backend
enforcement adds mandatory minimum-group suppression before execution. Backend privacy
enforcement and application security validation remain authoritative."""


class LiteLLMGateway(VLLMGateway):
    """LiteLLM proxy gateway; provider-specific options live in the proxy config."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 60,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            client=client,
            provider="litellm",
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "LiteLLMGateway":
        return cls(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_master_key,
            timeout_seconds=settings.litellm_timeout_seconds,
        )
