from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, QueryPlan
from app.infrastructure.ai.litellm_gateway import (
    LiteLLMGateway,
    ModelGroup,
    build_analytics_model_groups,
)


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            model="resolved-model",
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
        )


def gateway_with(outcomes: list[object]) -> tuple[LiteLLMGateway, FakeCompletions]:
    completions = FakeCompletions(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LiteLLMGateway(base_url="http://unused", api_key="unused", client=client), completions


def answer_group() -> ModelGroup[AnswerOutput]:
    return ModelGroup(
        name="answer",
        primary_model="answer-primary",
        fallback_model="answer-fallback",
        output_type=AnswerOutput,
    )


def test_model_groups_bind_primary_and_fallback_to_one_output_schema() -> None:
    groups = build_analytics_model_groups(Settings())

    assert groups.planner.output_type is QueryPlan
    assert groups.answer.output_type is AnswerOutput


async def test_primary_provider_failure_retries_then_uses_fallback() -> None:
    gateway, completions = gateway_with(
        [
            RuntimeError("primary unavailable"),
            RuntimeError("still unavailable"),
            '{"answer":"Done"}',
        ]
    )

    result = await gateway.generate(
        model_group=answer_group(), system_prompt="system", user_prompt="question"
    )

    assert [call["model"] for call in completions.calls] == [
        "answer-primary",
        "answer-primary",
        "answer-fallback",
    ]
    assert result.output.answer == "Done"
    assert result.metadata.actual_model == "resolved-model"
    assert result.metadata.fallback_used is True
    assert result.metadata.fallback_reason == ErrorCode.LLM_UNAVAILABLE


async def test_invalid_structured_output_gets_one_correction_attempt() -> None:
    gateway, completions = gateway_with(['{"wrong":"shape"}', '{"answer":"Corrected"}'])

    result = await gateway.generate(
        model_group=answer_group(), system_prompt="system", user_prompt="question"
    )

    assert [call["model"] for call in completions.calls] == ["answer-primary", "answer-primary"]
    correction_message = completions.calls[1]["messages"]
    assert "previous response did not match" in correction_message[1]["content"]
    assert result.output.answer == "Corrected"


async def test_both_provider_failures_are_reported_as_llm_unavailable() -> None:
    gateway, completions = gateway_with([RuntimeError("down")] * 4)

    with pytest.raises(Prompt2InsightError) as raised:
        await gateway.generate(
            model_group=answer_group(), system_prompt="system", user_prompt="question"
        )

    assert raised.value.code is ErrorCode.LLM_UNAVAILABLE
    assert [call["model"] for call in completions.calls] == [
        "answer-primary",
        "answer-primary",
        "answer-fallback",
        "answer-fallback",
    ]
