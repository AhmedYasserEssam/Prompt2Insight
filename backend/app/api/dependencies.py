from functools import lru_cache

from app.application.analytics.execute_query_plan import QueryPlanExecutor
from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.application.conversations.submit_question import ConversationQuestionService
from app.application.databases.configure_catalog import CatalogConfigurationService
from app.application.databases.setup_connection import ConnectionSetupService
from app.core.config import get_settings
from app.infrastructure.ai.litellm_gateway import LiteLLMGateway, build_analytics_model_groups
from app.infrastructure.databases.resolver import EnvironmentConnectorResolver
from app.persistence.database import create_session_factory
from app.persistence.repositories import (
    AnalyticsRequestRepository,
    ConnectionProfileRepository,
    ConversationRepository,
)


@lru_cache
def get_analytics_request_service() -> AnalyticsRequestService:
    settings = get_settings()
    repository = AnalyticsRequestRepository(create_session_factory(settings.app_database_url))
    gateway = LiteLLMGateway.from_settings(settings)
    model_groups = build_analytics_model_groups(settings)
    return AnalyticsRequestService(
        mock_mode=settings.mock_mode,
        repository=repository,
        planning_context_store=repository,
        planner=gateway,
        planner_model_group=model_groups.planner,
        answerer=gateway,
        answer_model_group=model_groups.answer,
        query_executor=QueryPlanExecutor(),
        connector_resolver=EnvironmentConnectorResolver(),
        settings=settings,
    )


@lru_cache
def get_connection_setup_service() -> ConnectionSetupService:
    settings = get_settings()
    repository = ConnectionProfileRepository(create_session_factory(settings.app_database_url))
    return ConnectionSetupService(repository)


@lru_cache
def get_conversation_repository() -> ConversationRepository:
    settings = get_settings()
    return ConversationRepository(create_session_factory(settings.app_database_url))


@lru_cache
def get_connection_profile_repository() -> ConnectionProfileRepository:
    settings = get_settings()
    return ConnectionProfileRepository(create_session_factory(settings.app_database_url))


@lru_cache
def get_conversation_question_service() -> ConversationQuestionService:
    return ConversationQuestionService(
        get_conversation_repository(),
        get_connection_profile_repository(),
        get_analytics_request_service(),
        get_settings(),
    )


@lru_cache
def get_catalog_configuration_service() -> CatalogConfigurationService:
    settings = get_settings()
    repository = ConnectionProfileRepository(create_session_factory(settings.app_database_url))
    return CatalogConfigurationService(repository)
