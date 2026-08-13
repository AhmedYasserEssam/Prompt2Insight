from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.analytics.models import AnswerOutput, QueryPlan
from app.domain.databases.models import SQLDialect
from app.infrastructure.ai.litellm_gateway import (
    LiteLLMGateway,
    ModelGroup,
    VLLMGateway,
    build_analytics_model_groups,
)
from app.infrastructure.catalogs.loader import load_catalog

CATALOG_PATH = Path(__file__).parents[2] / "catalogs" / "analytics_catalog.example.yaml"


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, SimpleNamespace):
            return outcome
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

    assert groups.planner.primary_model == "sql-planner-primary"
    assert groups.planner.output_type is QueryPlan
    assert groups.answer.primary_model == "answer-primary"
    assert groups.answer.fallback_model == "answer-fallback"
    assert groups.answer.output_type is AnswerOutput


def test_litellm_answer_aliases_use_groq_provider_model_variables() -> None:
    config_path = Path(__file__).parents[2] / "infra" / "litellm" / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    models = {model["model_name"]: model["litellm_params"] for model in config["model_list"]}

    assert models["answer-primary"]["model"] == "os.environ/LITELLM_ANSWER_PRIMARY_MODEL"
    assert models["answer-fallback"]["model"] == "os.environ/LITELLM_ANSWER_FALLBACK_MODEL"
    assert models["answer-primary"]["api_key"] == "os.environ/GROQ_API_KEY"
    assert models["answer-fallback"]["api_key"] == "os.environ/GROQ_API_KEY"
    assert models["answer-primary"]["reasoning_effort"] == "none"
    assert models["answer-primary"]["reasoning_format"] == "hidden"
    assert models["answer-fallback"]["reasoning_effort"] == "low"
    assert models["answer-fallback"]["reasoning_effort"] != "none"
    assert models["answer-fallback"]["reasoning_format"] == "hidden"
    assert models["sql-planner-primary"]["reasoning_effort"] == "none"
    assert models["sql-planner-fallback"]["reasoning_effort"] == "low"
    assert config["router_settings"]["fallbacks"][-1] == {
        "answer-primary": ["answer-fallback"]
    }


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
    assert all(call["extra_body"] is None for call in completions.calls)
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


@pytest.mark.parametrize(
    ("question", "dialect"),
    [
        ("Show total revenue by month in 2025.", SQLDialect.POSTGRES),
        ("اعرض إجمالي الإيرادات لكل شهر في سنة 2025", SQLDialect.POSTGRES),
        ("وريني إجمالي الإيرادات لكل شهر في 2025", SQLDialect.POSTGRES),
        ("وريني revenue لكل شهر في 2025", SQLDialect.POSTGRES),
        ("Show total revenue by month in 2025.", SQLDialect.MYSQL),
    ],
)
async def test_qwen_planner_uses_catalog_context_and_explicit_dialect(
    question: str, dialect: SQLDialect
) -> None:
    plan = (
        '{"status":"ready","response_language":"ar","database_dialect":"'
        f'{dialect.value}","interpretation":"revenue by month","metric_ids":["revenue"],'
        '"dimension_ids":["order_month"],"filters":[],"sql":null,"parameters":{},'
        '"clarification_question":null}'
    )
    gateway, completions = gateway_with([plan])
    catalog, _ = load_catalog(CATALOG_PATH)

    result = await gateway.plan(
        question=question,
        dialect=dialect,
        catalog=catalog,
        model_group=build_analytics_model_groups(Settings()).planner,
    )

    request = completions.calls[0]
    assert request["model"] == "sql-planner-primary"
    assert question in request["messages"][1]["content"]
    assert dialect.value in request["messages"][0]["content"]
    assert "revenue" in request["messages"][1]["content"]
    assert request["extra_body"] is None
    assert result.metadata.provider == "litellm"
    assert result.metadata.model == "resolved-model"
    assert result.metadata.generation_stage == "planner"
    assert result.metadata.database_dialect is dialect


@pytest.mark.parametrize(
    "outcome",
    [
        RuntimeError("connection refused"),
        TimeoutError(),
        RuntimeError("HTTP 500"),
        RuntimeError("404"),
    ],
)
async def test_vllm_transport_errors_normalize_to_llm_unavailable(outcome: Exception) -> None:
    gateway, _ = gateway_with([outcome] * 4)

    with pytest.raises(Prompt2InsightError, match="Both primary") as raised:
        await gateway.generate(
            model_group=answer_group(), system_prompt="system", user_prompt="question"
        )

    assert raised.value.code is ErrorCode.LLM_UNAVAILABLE


async def test_malformed_vllm_response_is_retried_as_invalid_output() -> None:
    malformed = SimpleNamespace(model="Qwen/Qwen3.5-9B", choices=[])
    gateway, completions = gateway_with([malformed, '{"answer":"Recovered"}'])

    result = await gateway.generate(
        model_group=answer_group(), system_prompt="system", user_prompt="question"
    )

    assert len(completions.calls) == 2
    assert result.output.answer == "Recovered"
    assert result.metadata.retry_count == 1


async def test_vllm_readiness_requires_the_configured_model() -> None:
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: _model_list(["Qwen/Qwen3.5-9B"]),
        )
    )
    gateway = VLLMGateway(base_url="http://unused", api_key="EMPTY", client=client)

    assert await gateway.is_ready(model="Qwen/Qwen3.5-9B")
    assert not await gateway.is_ready(model="other-model")


async def _model_list(model_ids: list[str]) -> object:
    return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in model_ids])
