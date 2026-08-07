from typing import Protocol
from uuid import UUID

from app.application.analytics.resolve_language import resolve_response_language
from app.core.errors import ErrorCode
from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse, AnalyticsStatus


class AnalyticsRequestStore(Protocol):
    async def get(self, request_id: UUID) -> AnalyticsResponse | None: ...

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
    ) -> AnalyticsResponse: ...


class AnalyticsRequestService:
    """Replace mock behavior with the deterministic production pipeline."""

    def __init__(self, *, mock_mode: bool, repository: AnalyticsRequestStore) -> None:
        self._mock_mode = mock_mode
        self._repository = repository

    async def run(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
    ) -> AnalyticsResponse:
        existing = await self._repository.get(request.request_id)
        if existing is not None:
            return existing

        language = resolve_response_language(request.question, request.response_language)

        if self._mock_mode:
            answer = (
                "اربط ملف اتصال بقاعدة بيانات ثم فعّل مخطط الاستعلام."
                if language == "ar"
                else "Register a database connection profile and enable the query planner."
            )
            response = AnalyticsResponse(
                status=AnalyticsStatus.FAILED,
                request_id=request.request_id,
                language=language,
                answer=answer,
                error_code=ErrorCode.NOT_CONFIGURED,
            )
            return await self._repository.save(
                conversation_id=conversation_id, request=request, response=response
            )

        raise NotImplementedError(
            "Implement the pipeline in IMPLEMENTATION_CHECKLIST.md."
        )

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return await self._repository.get(request_id)
