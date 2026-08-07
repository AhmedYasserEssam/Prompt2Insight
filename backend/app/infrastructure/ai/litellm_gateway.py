from typing import Any, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.errors import ErrorCode, Prompt2InsightError

OutputT = TypeVar("OutputT", bound=BaseModel)


class LiteLLMGateway:
    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def generate(
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
