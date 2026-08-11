from dataclasses import dataclass
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, ModelExecutionMetadata, QueryPlan


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


class LiteLLMGateway:
    def __init__(self, *, base_url: str, api_key: str, client: Any | None = None) -> None:
        self._client = client or AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def generate[OutputT: BaseModel](
        self,
        *,
        model_group: ModelGroup[OutputT],
        system_prompt: str,
        user_prompt: str,
    ) -> GenerationResult[OutputT]:
        """Generate with one retry per provider before moving to the fallback."""
        primary_failure: Prompt2InsightError | None = None

        try:
            output, actual_model = await self._generate_with_correction(
                model_alias=model_group.primary_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=model_group.output_type,
            )
            return GenerationResult(
                output=output,
                metadata=ModelExecutionMetadata(actual_model=actual_model),
            )
        except Prompt2InsightError as exc:
            primary_failure = exc

        try:
            output, actual_model = await self._generate_with_correction(
                model_alias=model_group.fallback_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=model_group.output_type,
            )
            return GenerationResult(
                output=output,
                metadata=ModelExecutionMetadata(
                    actual_model=actual_model,
                    fallback_used=True,
                    fallback_reason=primary_failure.code.value,
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

    async def _generate_with_correction[OutputT: BaseModel](
        self,
        *,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        output_type: type[OutputT],
    ) -> tuple[OutputT, str | None]:
        try:
            return await self._generate_once(
                model_alias=model_alias,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=output_type,
            )
        except Prompt2InsightError as first_failure:
            correction_prompt = user_prompt
            if first_failure.code is ErrorCode.LLM_INVALID_OUTPUT:
                correction_prompt = (
                    f"{user_prompt}\n\nYour previous response did not match the required JSON "
                    "schema. "
                    "Return only a corrected JSON object that exactly matches it."
                )
            try:
                return await self._generate_once(
                    model_alias=model_alias,
                    system_prompt=system_prompt,
                    user_prompt=correction_prompt,
                    output_type=output_type,
                )
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
            )
        except Exception as exc:
            raise Prompt2InsightError(
                ErrorCode.LLM_UNAVAILABLE,
                "The model gateway is unavailable.",
                retryable=True,
            ) from exc

        content = response.choices[0].message.content
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
