from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse
from app.persistence.models import AnalyticalRequestRecord, ConversationRecord, QueryExecutionRecord


class AnalyticsRequestRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        async with self._session_factory() as session:
            record = await session.get(AnalyticalRequestRecord, request_id)
            if record is None:
                return None
            return AnalyticsResponse.model_validate(record.response)

    async def save(
        self,
        *,
        conversation_id: UUID,
        request: AnalyticsRequest,
        response: AnalyticsResponse,
    ) -> AnalyticsResponse:
        async with self._session_factory() as session:
            existing = await session.get(AnalyticalRequestRecord, request.request_id)
            if existing is not None:
                return AnalyticsResponse.model_validate(existing.response)

            if await session.get(ConversationRecord, conversation_id) is None:
                session.add(ConversationRecord(id=conversation_id))
                await session.flush()

            session.add(
                AnalyticalRequestRecord(
                    id=request.request_id,
                    conversation_id=conversation_id,
                    question=request.question,
                    response_language=request.response_language.value,
                    status=response.status.value,
                    response=response.model_dump(mode="json"),
                )
            )
            session.add(
                QueryExecutionRecord(
                    request_id=request.request_id,
                    status=response.status.value,
                    latency_ms=0,
                    model_metadata=(
                        response.model_metadata.model_dump(mode="json")
                        if response.model_metadata is not None
                        else {"mode": "mock"}
                    ),
                )
            )
            await session.commit()
            return response
