from uuid import UUID, uuid4

from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse


class InMemoryRequestRepository:
    def __init__(self) -> None:
        self.responses: dict[UUID, AnalyticsResponse] = {}
        self.saved_conversation_id: UUID | None = None

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return self.responses.get(request_id)

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
    ) -> AnalyticsResponse:
        self.saved_conversation_id = conversation_id
        self.responses[request.request_id] = response
        return response


async def test_request_is_persisted_and_recovered_by_global_id() -> None:
    repository = InMemoryRequestRepository()
    service = AnalyticsRequestService(mock_mode=True, repository=repository)
    conversation_id = uuid4()
    request = AnalyticsRequest(request_id=uuid4(), question="Revenue by month")

    created = await service.run(conversation_id=conversation_id, request=request)
    recovered = await service.get(request.request_id)

    assert created.request_id == request.request_id
    assert recovered == created
    assert repository.saved_conversation_id == conversation_id
