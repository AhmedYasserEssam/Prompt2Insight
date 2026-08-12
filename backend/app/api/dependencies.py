from functools import lru_cache

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.core.config import get_settings
from app.infrastructure.ai.litellm_gateway import LiteLLMGateway, build_analytics_model_groups
from app.infrastructure.databases.resolver import EnvironmentConnectorResolver
from app.persistence.database import create_session_factory
from app.persistence.repositories import AnalyticsRequestRepository


@lru_cache
def get_analytics_request_service() -> AnalyticsRequestService:
    settings = get_settings()
    repository = AnalyticsRequestRepository(create_session_factory(settings.app_database_url))
    return AnalyticsRequestService(
        mock_mode=settings.mock_mode,
        repository=repository,
        planning_context_store=repository,
        planner=LiteLLMGateway.from_settings(settings),
        planner_model_group=build_analytics_model_groups(settings).planner,
        query_executor=QueryPlanExecutor(),
        connector_resolver=EnvironmentConnectorResolver(),
        settings=settings,
    )
