from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/llm")
async def llm_readiness() -> dict[str, str]:
    from app.infrastructure.ai.litellm_gateway import LiteLLMGateway

    settings = get_settings()
    gateway = LiteLLMGateway.from_settings(settings)
    try:
        if await gateway.is_ready(model=settings.planner_primary_model):
            return {"status": "ok", "provider": "litellm", "model": settings.planner_primary_model}
        return {
            "status": "unavailable",
            "provider": "litellm",
            "model": settings.planner_primary_model,
        }
    finally:
        await gateway.close()


@router.get("/health/vllm")
async def vllm_readiness() -> dict[str, str]:
    from app.infrastructure.ai.litellm_gateway import VLLMGateway

    settings = get_settings()
    if not settings.vllm_enabled:
        return {"status": "disabled", "provider": "vllm"}
    gateway = VLLMGateway.from_settings(settings)
    try:
        status = "ok" if await gateway.is_ready(model=settings.vllm_model) else "unavailable"
        return {"status": status, "provider": "vllm", "model": settings.vllm_model}
    finally:
        await gateway.close()
