from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_connection_profile_repository,
    get_conversation_question_service,
    get_conversation_repository,
)
from app.application.conversations.submit_question import (
    QuestionSubmission,
    SubmissionConflictError,
)
from app.domain.analytics.models import AnalyticsResponse, AnalyticsStatus
from app.main import app
from app.persistence.models import ConversationRecord, MessageRecord


def _conversation() -> ConversationRecord:
    now = datetime.now(UTC)
    return ConversationRecord(
        id=uuid4(),
        connection_profile_id=uuid4(),
        title="Revenue",
        language="en",
        context_state={},
        created_at=now,
        updated_at=now,
    )


class ConversationRepositoryStub:
    def __init__(self, record: ConversationRecord | None) -> None:
        self.record = record

    async def create_conversation(self, **_: object) -> ConversationRecord:
        assert self.record is not None
        return self.record

    async def list_conversations(self, **_: object) -> list[ConversationRecord]:
        return [self.record] if self.record is not None else []

    async def get_conversation(self, *_: object, **__: object) -> ConversationRecord | None:
        return self.record

    async def list_messages(self, *_: object) -> list[MessageRecord]:
        return []


class ConnectionRepositoryStub:
    def __init__(self, exists: bool) -> None:
        self.exists = exists

    async def get_input(self, *_: object) -> object | None:
        return object() if self.exists else None


class QuestionServiceStub:
    def __init__(self, result: QuestionSubmission | Exception) -> None:
        self.result = result

    async def submit(self, **_: object) -> QuestionSubmission:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_create_conversation_validates_connection_and_returns_public_contract() -> None:
    record = _conversation()
    app.dependency_overrides[get_conversation_repository] = lambda: ConversationRepositoryStub(
        record
    )
    app.dependency_overrides[get_connection_profile_repository] = lambda: ConnectionRepositoryStub(
        True
    )
    try:
        response = TestClient(app).post(
            "/api/v1/conversations",
            json={"connection_id": str(record.connection_profile_id), "language": "en"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["id"] == str(record.id)
    assert "context_state" not in response.json()


def test_create_rejects_missing_connection_and_list_validates_pagination() -> None:
    record = _conversation()
    app.dependency_overrides[get_conversation_repository] = lambda: ConversationRepositoryStub(
        record
    )
    app.dependency_overrides[get_connection_profile_repository] = lambda: ConnectionRepositoryStub(
        False
    )
    try:
        client = TestClient(app)
        missing = client.post(
            "/api/v1/conversations", json={"connection_id": str(record.connection_profile_id)}
        )
        invalid = client.get("/api/v1/conversations?limit=0")
    finally:
        app.dependency_overrides.clear()

    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_unknown_conversation_and_empty_patch_are_rejected() -> None:
    app.dependency_overrides[get_conversation_repository] = lambda: ConversationRepositoryStub(None)
    try:
        client = TestClient(app)
        unknown = client.get(f"/api/v1/conversations/{uuid4()}")
        empty = client.patch(f"/api/v1/conversations/{uuid4()}", json={})
    finally:
        app.dependency_overrides.clear()

    assert unknown.status_code == 404
    assert empty.status_code == 422


def test_submission_filters_metadata_and_maps_conflicts() -> None:
    now = datetime.now(UTC)
    conversation_id = uuid4()
    user = MessageRecord(
        id=uuid4(),
        conversation_id=conversation_id,
        sequence_number=1,
        role="user",
        content="Revenue",
        message_metadata={"status": "completed", "secret": "do-not-expose"},
        created_at=now,
    )
    assistant = MessageRecord(
        id=uuid4(),
        conversation_id=conversation_id,
        sequence_number=2,
        role="assistant",
        content="Revenue is 10.",
        message_metadata={"request_id": str(user.id)},
        created_at=now,
    )
    submission = QuestionSubmission(
        user,
        assistant,
        AnalyticsResponse(
            status=AnalyticsStatus.SUCCESS,
            request_id=user.id,
            language="en",
            answer=assistant.content,
        ),
    )
    app.dependency_overrides[get_conversation_question_service] = lambda: QuestionServiceStub(
        submission
    )
    try:
        response = TestClient(app).post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Revenue", "client_message_id": str(user.id)},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert "secret" not in response.text

    app.dependency_overrides[get_conversation_question_service] = lambda: QuestionServiceStub(
        SubmissionConflictError()
    )
    try:
        conflict = TestClient(app).post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Revenue", "client_message_id": str(user.id)},
        )
    finally:
        app.dependency_overrides.clear()
    assert conflict.status_code == 409
