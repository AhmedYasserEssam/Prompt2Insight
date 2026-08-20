import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.conversations.submit_question import ConversationQuestionService
from app.core.config import Settings
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    QueryPlan,
    ResultTable,
)
from app.domain.databases.models import SQLDialect
from app.persistence.models import ConnectionProfileRecord, ConversationRecord, MessageRecord
from app.persistence.repositories import ConnectionProfileRepository, ConversationRepository

pytestmark = pytest.mark.skipif(
    os.getenv("P2I_RUN_PERSISTENCE_INTEGRATION") != "1",
    reason=(
        "set P2I_RUN_PERSISTENCE_INTEGRATION=1 and P2I_APP_DATABASE_URL to run persistence tests"
    ),
)


@pytest.fixture
async def repository() -> AsyncIterator[ConversationRepository]:
    engine = create_async_engine(os.environ["P2I_APP_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield ConversationRepository(factory)
    finally:
        await engine.dispose()


async def _profile(repository: ConversationRepository) -> UUID:
    profile_id = uuid4()
    async with repository._session_factory() as session, session.begin():
        session.add(
            ConnectionProfileRecord(
                id=profile_id,
                name=str(profile_id),
                dialect="postgres",
                host="db",
                port=5432,
                database_name="analytics",
                username="analytics",
                credential_reference="P2I_TEST_DB_URL",
            )
        )
    return profile_id


async def _set_updated_at(
    repository: ConversationRepository, conversation_id: UUID, updated_at: datetime
) -> None:
    async with repository._session_factory() as session, session.begin():
        await session.execute(
            update(ConversationRecord)
            .where(ConversationRecord.id == conversation_id)
            .values(updated_at=updated_at)
        )


async def test_creates_retrieves_updates_archives_and_updates_state(
    repository: ConversationRepository,
) -> None:
    profile_id = await _profile(repository)
    created = await repository.create_conversation(
        connection_id=profile_id,
        title="Revenue review",
        language="ar",
    )

    assert created.connection_profile_id == profile_id
    assert created.title == "Revenue review"
    assert created.language == "ar"
    assert created.context_state == {}
    assert await repository.get_conversation(created.id) is not None
    assert await repository.get_conversation(uuid4()) is None

    updated = await repository.update_conversation(
        created.id, title="Updated", language="en", summary="Summary"
    )
    state_updated = await repository.update_conversation_state(
        created.id, context_state={"last_request_id": str(uuid4())}
    )
    assert updated is not None
    assert (updated.title, updated.language, updated.summary) == ("Updated", "en", "Summary")
    assert state_updated is not None
    assert state_updated.context_state["last_request_id"]

    archived = await repository.archive_conversation(created.id)
    assert archived is not None and archived.archived_at is not None
    assert await repository.get_conversation(created.id) is None
    assert await repository.get_conversation(created.id, include_archived=True) is not None
    assert created.id not in {record.id for record in await repository.list_conversations()}
    assert created.id in {
        record.id for record in await repository.list_conversations(include_archived=True)
    }


async def test_lists_conversations_in_updated_order_with_pagination(
    repository: ConversationRepository,
) -> None:
    profile_id = await _profile(repository)
    records = [
        await repository.create_conversation(connection_id=profile_id, title=str(index))
        for index in range(3)
    ]
    base = datetime(2100, 1, 1, tzinfo=UTC)
    for index, record in enumerate(records):
        await _set_updated_at(repository, record.id, base + timedelta(seconds=index))

    listed = await repository.list_conversations()
    listed_ids = [record.id for record in listed]
    positions = [listed_ids.index(record.id) for record in reversed(records)]
    assert positions == sorted(positions)
    assert [record.id for record in await repository.list_conversations(limit=2, offset=1)] == [
        record.id for record in listed[1:3]
    ]
    with pytest.raises(ValueError):
        await repository.list_conversations(limit=0)


async def test_messages_are_ordered_recent_and_isolated(repository: ConversationRepository) -> None:
    profile_id = await _profile(repository)
    first = await repository.create_conversation(connection_id=profile_id)
    second = await repository.create_conversation(connection_id=profile_id)
    before = (await repository.get_conversation(first.id)).updated_at  # type: ignore[union-attr]

    messages = [
        await repository.add_message(conversation_id=first.id, role="user", content="one"),
        await repository.add_message(conversation_id=first.id, role="assistant", content="two"),
        await repository.add_message(conversation_id=first.id, role="user", content="three"),
    ]
    other = await repository.add_message(conversation_id=second.id, role="user", content="other")
    assert all(message is not None for message in messages)
    assert other is not None and other.sequence_number == 1
    assert [message.sequence_number for message in messages if message is not None] == [1, 2, 3]
    assert [message.content for message in await repository.list_messages(first.id)] == [
        "one",
        "two",
        "three",
    ]
    recent = await repository.get_recent_messages(first.id, limit=2)
    assert [message.content for message in recent] == ["two", "three"]
    assert [message.content for message in await repository.list_messages(second.id)] == ["other"]
    assert (await repository.get_conversation(first.id)).updated_at >= before  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        await repository.get_recent_messages(first.id, limit=0)
    missing = await repository.add_message(conversation_id=uuid4(), role="user", content="missing")
    assert missing is None


async def test_delete_cascades_messages_and_failed_message_rolls_back(
    repository: ConversationRepository,
) -> None:
    profile_id = await _profile(repository)
    conversation = await repository.create_conversation(connection_id=profile_id)
    before = (await repository.get_conversation(conversation.id)).updated_at  # type: ignore[union-attr]
    with pytest.raises(IntegrityError):
        await repository.add_message(conversation_id=conversation.id, role="invalid", content="bad")
    assert await repository.list_messages(conversation.id) == []
    assert (await repository.get_conversation(conversation.id)).updated_at == before  # type: ignore[union-attr]

    message = await repository.add_message(
        conversation_id=conversation.id, role="user", content="ok"
    )
    assert message is not None
    assert await repository.delete_conversation(conversation.id)
    assert await repository.get_conversation(conversation.id, include_archived=True) is None
    async with repository._session_factory() as session:
        assert (
            await session.scalar(
                select(MessageRecord).where(MessageRecord.conversation_id == conversation.id)
            )
            is None
        )
    assert not await repository.delete_conversation(conversation.id)


async def test_concurrent_message_adds_use_unique_sequence_numbers(
    repository: ConversationRepository,
) -> None:
    conversation = await repository.create_conversation(connection_id=await _profile(repository))
    created = await asyncio.gather(
        repository.add_message(conversation_id=conversation.id, role="user", content="one"),
        repository.add_message(conversation_id=conversation.id, role="assistant", content="two"),
    )

    assert all(message is not None for message in created)
    messages = await repository.list_messages(conversation.id)
    assert [message.sequence_number for message in messages] == [1, 2]


class _DeterministicAnalytics:
    def __init__(self) -> None:
        self.responses: dict[UUID, AnalyticsResponse] = {}
        self.calls = 0

    async def run(self, *, conversation_id: UUID, request: AnalyticsRequest) -> AnalyticsResponse:
        self.calls += 1
        if request.question == "fail":
            response = AnalyticsResponse(
                status=AnalyticsStatus.FAILED,
                request_id=request.request_id,
                language="en",
            )
        else:
            filters = []
            sql = "SELECT order_month, SUM(sales) AS total_sales FROM analytics.sales GROUP BY 1"
            if "2017" in request.question:
                filters = [{"dimension_id": "order_year", "operator": "eq", "value": "2017"}]
                sql += " HAVING EXTRACT(YEAR FROM order_month) = 2017"
            if "2018" in request.question:
                filters = [{"dimension_id": "order_year", "operator": "in", "value": "2017,2018"}]
                sql += " HAVING EXTRACT(YEAR FROM order_month) IN (2017, 2018)"
            response = AnalyticsResponse(
                status=AnalyticsStatus.SUCCESS,
                request_id=request.request_id,
                language="en",
                answer="Monthly sales returned.",
                table=ResultTable(columns=["order_month", "total_sales"], rows=[["2017-01", 10]]),
                sql=sql,
                query_plan=QueryPlan(
                    status="ready",
                    response_language="en",
                    database_dialect=SQLDialect.POSTGRES,
                    interpretation="monthly sales",
                    metric_ids=["total_sales"],
                    dimension_ids=["order_month"],
                    filters=filters,  # type: ignore[arg-type]
                    sql=sql,
                ),
            )
        self.responses[request.request_id] = response
        return response

    async def get(self, request_id: UUID) -> AnalyticsResponse | None:
        return self.responses.get(request_id)

    async def title_for(self, *, question: str, language: str) -> str | None:
        return "Monthly sales"


async def test_question_lifecycle_is_durable_idempotent_and_preserves_valid_state(
    repository: ConversationRepository,
) -> None:
    profile_id = await _profile(repository)
    conversation = await repository.create_conversation(connection_id=profile_id)
    analytics = _DeterministicAnalytics()
    settings = Settings(conversation_summary_threshold_tokens=100_000)
    service = ConversationQuestionService(
        repository,
        ConnectionProfileRepository(repository._session_factory),
        analytics,  # type: ignore[arg-type]
        settings,
    )
    first_id, second_id, third_id = uuid4(), uuid4(), uuid4()

    first = await service.submit(
        conversation_id=conversation.id,
        client_message_id=first_id,
        content="Show monthly sales.",
    )
    # A fresh service/session simulates application restart before the continuation.
    restarted = ConversationQuestionService(
        ConversationRepository(repository._session_factory),
        ConnectionProfileRepository(repository._session_factory),
        analytics,  # type: ignore[arg-type]
        settings,
    )
    second = await restarted.submit(
        conversation_id=conversation.id,
        client_message_id=second_id,
        content="Show only 2017.",
    )
    third = await restarted.submit(
        conversation_id=conversation.id,
        client_message_id=third_id,
        content="Compare that with 2018.",
    )

    assert (
        first.analytics is not None and second.analytics is not None and third.analytics is not None
    )
    for response in (second.analytics, third.analytics):
        assert response.query_plan is not None
        assert response.query_plan.metric_ids == ["total_sales"]
        assert response.query_plan.dimension_ids == ["order_month"]
    assert "2017" in (second.analytics.sql or "")
    assert "2017, 2018" in (third.analytics.sql or "")

    duplicated = await asyncio.gather(
        restarted.submit(
            conversation_id=conversation.id,
            client_message_id=third_id,
            content="Compare that with 2018.",
        ),
        restarted.submit(
            conversation_id=conversation.id,
            client_message_id=third_id,
            content="Compare that with 2018.",
        ),
    )
    assert all(item.assistant_message is not None for item in duplicated)
    assert analytics.calls == 3

    await repository.update_conversation(
        conversation.id, title="Finance review", title_is_manual=True
    )
    manual_title_submission = await restarted.submit(
        conversation_id=conversation.id,
        client_message_id=uuid4(),
        content="Show monthly sales again.",
    )
    assert manual_title_submission.assistant_message is not None
    assert (await repository.get_conversation(conversation.id)).title == "Finance review"  # type: ignore[union-attr]

    before_failure = (await repository.get_conversation(conversation.id)).context_state  # type: ignore[union-attr]
    failed = await restarted.submit(
        conversation_id=conversation.id, client_message_id=uuid4(), content="fail"
    )
    reloaded = await repository.get_conversation(conversation.id)
    assert failed.failure_code == "analysis_failed"
    assert reloaded is not None and reloaded.context_state == before_failure
    assert reloaded.title == "Finance review"
    messages = await repository.list_messages(conversation.id)
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
