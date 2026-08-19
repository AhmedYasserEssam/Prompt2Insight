from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import (
    get_connection_profile_repository,
    get_conversation_question_service,
    get_conversation_repository,
)
from app.api.schemas.conversations import (
    ConversationResponse,
    ConversationSummaryResponse,
    CreateConversationRequest,
    FailedQuestionResponse,
    MessageResponse,
    MessageSubmissionRequest,
    PaginatedConversationsResponse,
    QuestionSubmissionResponse,
    UpdateConversationRequest,
)
from app.application.conversations.submit_question import (
    ConversationNotFoundError,
    ConversationQuestionService,
    QuestionSubmission,
    SubmissionConflictError,
)
from app.persistence.models import ConversationRecord, MessageRecord
from app.persistence.repositories import ConnectionProfileRepository, ConversationRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])
ConversationRepositoryDependency = Annotated[
    ConversationRepository, Depends(get_conversation_repository)
]
ConnectionRepositoryDependency = Annotated[
    ConnectionProfileRepository, Depends(get_connection_profile_repository)
]
QuestionServiceDependency = Annotated[
    ConversationQuestionService, Depends(get_conversation_question_service)
]


def _metadata(record: MessageRecord) -> dict[str, object]:
    allowed = {"status", "request_id", "error_code", "assistant_message_id"}
    return {key: value for key, value in record.message_metadata.items() if key in allowed}


def _message(record: MessageRecord) -> MessageResponse:
    return MessageResponse(
        id=record.id,
        conversation_id=record.conversation_id,
        sequence_number=record.sequence_number,
        role=record.role,
        content=record.content,
        metadata=_metadata(record),
        created_at=record.created_at,
    )


def _summary(record: ConversationRecord) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        id=record.id,
        connection_id=record.connection_profile_id,
        title=record.title,
        language=record.language,
        created_at=record.created_at,
        updated_at=record.updated_at,
        archived_at=record.archived_at,
    )


async def _detail(
    repository: ConversationRepository, record: ConversationRecord
) -> ConversationResponse:
    return ConversationResponse(
        **_summary(record).model_dump(),
        messages=[_message(message) for message in await repository.list_messages(record.id)],
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: CreateConversationRequest,
    conversations: ConversationRepositoryDependency,
    connections: ConnectionRepositoryDependency,
) -> ConversationResponse:
    if await connections.get_input(request.connection_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    record = await conversations.create_conversation(
        connection_id=request.connection_id,
        title=request.title or "New conversation",
        language=request.language.value,
    )
    return await _detail(conversations, record)


@router.get("", response_model=PaginatedConversationsResponse)
async def list_conversations(
    conversations: ConversationRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_archived: bool = False,
) -> PaginatedConversationsResponse:
    records = await conversations.list_conversations(
        include_archived=include_archived, limit=limit, offset=offset
    )
    return PaginatedConversationsResponse(
        items=[_summary(record) for record in records], limit=limit, offset=offset
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID, conversations: ConversationRepositoryDependency
) -> ConversationResponse:
    record = await conversations.get_conversation(conversation_id, include_archived=True)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return await _detail(conversations, record)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    request: UpdateConversationRequest,
    conversations: ConversationRepositoryDependency,
) -> ConversationResponse:
    record = await conversations.get_conversation(conversation_id, include_archived=True)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if "archived" in request.model_fields_set:
        record = (
            await conversations.archive_conversation(conversation_id)
            if request.archived
            else await conversations.restore_conversation(conversation_id)
        )
        assert record is not None
    if {"title", "language"} & request.model_fields_set:
        record = await conversations.update_conversation(
            conversation_id,
            title=request.title if "title" in request.model_fields_set else None,
            language=request.language.value if request.language is not None else None,
        )
        assert record is not None
    return await _detail(conversations, record)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID, conversations: ConversationRepositoryDependency
) -> Response:
    if not await conversations.delete_conversation(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=QuestionSubmissionResponse)
async def submit_message(
    conversation_id: UUID,
    request: MessageSubmissionRequest,
    service: QuestionServiceDependency,
) -> QuestionSubmissionResponse:
    try:
        submission = await service.submit(
            conversation_id=conversation_id,
            client_message_id=request.client_message_id,
            content=request.content,
        )
    except ConversationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        ) from None
    except SubmissionConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Question submission conflicts"
        ) from None
    return _submission(submission)


def _submission(submission: QuestionSubmission) -> QuestionSubmissionResponse:
    failure = (
        FailedQuestionResponse(
            code=submission.failure_code,
            message="The analysis could not be completed.",
        )
        if submission.failure_code is not None
        else None
    )
    return QuestionSubmissionResponse(
        user_message=_message(submission.user_message),
        assistant_message=(
            _message(submission.assistant_message)
            if submission.assistant_message is not None
            else None
        ),
        analytics=submission.analytics,
        failure=failure,
    )
