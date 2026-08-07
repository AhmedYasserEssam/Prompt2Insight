from functools import lru_cache

from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.core.config import get_settings
from app.persistence.database import create_session_factory
from app.persistence.repositories import AnalyticsRequestRepository


@lru_cache
def get_analytics_request_service() -> AnalyticsRequestService:
    settings = get_settings()
    return AnalyticsRequestService(
        mock_mode=settings.mock_mode,
        repository=AnalyticsRequestRepository(create_session_factory(settings.app_database_url)),
    )
