import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.analytics.models import QueryPlan
from app.domain.databases.models import SQLDialect
from app.infrastructure.ai.litellm_gateway import ModelGroup, VLLMGateway
from app.infrastructure.catalogs.loader import load_catalog

pytestmark = pytest.mark.skipif(
    os.getenv("P2I_RUN_VLLM_INTEGRATION") != "1",
    reason="set P2I_RUN_VLLM_INTEGRATION=1 to use the separately running vLLM service",
)

CATALOG_PATH = Path(__file__).parents[3] / "catalogs" / "analytics_catalog.example.yaml"


@pytest.mark.parametrize(
    "question",
    [
        "Show total revenue by month in 2025.",
        "اعرض إجمالي الإيرادات لكل شهر في سنة 2025",
    ],
)
async def test_vllm_qwen_plans_with_the_existing_structured_schema(question: str) -> None:
    settings = Settings()
    catalog, _ = load_catalog(CATALOG_PATH)
    gateway = VLLMGateway.from_settings(settings)

    try:
        result = await gateway.plan(
            question=question,
            dialect=SQLDialect.POSTGRES,
            catalog=catalog,
            model_group=ModelGroup(
                name="planner",
                primary_model=settings.vllm_model,
                fallback_model=settings.vllm_model,
                output_type=QueryPlan,
            ),
        )
    finally:
        await gateway.close()

    assert result.metadata.provider == "vllm"
    assert result.metadata.model == settings.vllm_model
    assert result.metadata.database_dialect is SQLDialect.POSTGRES
    assert result.output.database_dialect is SQLDialect.POSTGRES
