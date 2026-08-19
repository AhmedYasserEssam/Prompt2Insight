from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.core.errors import Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ResponseLanguage,
)
from app.persistence.models import MessageRecord
from app.persistence.repositories import ConnectionProfileRepository, ConversationRepository


class ConversationNotFoundError(Exception):
    pass


class SubmissionConflictError(Exception):
    pass


@dataclass(frozen=True)
class QuestionSubmission:
    user_message: MessageRecord
    assistant_message: MessageRecord | None
    analytics: AnalyticsResponse | None
    failure_code: str | None = None


class ConversationQuestionService:
    """Persist question lifecycle records around the existing analytics workflow."""

    def __init__(
        self,
        conversations: ConversationRepository,
        connections: ConnectionProfileRepository,
        analytics: AnalyticsRequestService,
    ) -> None:
        self._conversations = conversations
        self._connections = connections
        self._analytics = analytics

    async def submit(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
    ) -> QuestionSubmission:
        conversation = await self._conversations.get_conversation(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError
        if (
            conversation.connection_profile_id is None
            or await self._connections.get_input(conversation.connection_profile_id) is None
        ):
            raise ConversationNotFoundError

        existing = await self._conversations.get_message(client_message_id)
        if existing is not None:
            return await self._existing(existing, conversation_id, content)

        pending_metadata: dict[str, object] = {"status": "pending"}
        try:
            user_message = await self._conversations.add_message(
                message_id=client_message_id,
                conversation_id=conversation_id,
                role="user",
                content=content,
                message_metadata=pending_metadata,
            )
        except IntegrityError:
            existing = await self._conversations.get_message(client_message_id)
            if existing is None:
                raise
            return await self._existing(existing, conversation_id, content)
        if user_message is None:
            raise ConversationNotFoundError

        try:
            response = await self._analytics.run(
                conversation_id=conversation_id,
                request=AnalyticsRequest(
                    request_id=client_message_id,
                    question=content,
                    response_language=ResponseLanguage(conversation.language),
                ),
            )
        except Prompt2InsightError as error:
            failed = await self._mark_failed(user_message, error.code.value)
            return QuestionSubmission(failed, None, None, error.code.value)
        except Exception:
            failed = await self._mark_failed(user_message, "analysis_failed")
            return QuestionSubmission(failed, None, None, "analysis_failed")

        if response.status is AnalyticsStatus.FAILED:
            failed = await self._mark_failed(
                user_message,
                response.error_code.value if response.error_code is not None else "analysis_failed",
            )
            return QuestionSubmission(
                failed, None, response, str(failed.message_metadata["error_code"])
            )

        assistant = await self._conversations.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=response.answer or "Analysis completed.",
            message_metadata={"request_id": str(client_message_id), "status": "completed"},
        )
        assert assistant is not None
        completed = await self._conversations.update_message_metadata(
            user_message.id,
            message_metadata={
                "status": "completed",
                "assistant_message_id": str(assistant.id),
            },
        )
        assert completed is not None
        return QuestionSubmission(completed, assistant, response)

    async def _existing(
        self, message: MessageRecord, conversation_id: UUID, content: str
    ) -> QuestionSubmission:
        if (
            message.conversation_id != conversation_id
            or message.role != "user"
            or message.content != content
        ):
            raise SubmissionConflictError
        lifecycle = message.message_metadata.get("status")
        if lifecycle == "pending":
            raise SubmissionConflictError
        if lifecycle == "failed":
            return QuestionSubmission(
                message,
                None,
                await self._analytics.get(message.id),
                str(message.message_metadata.get("error_code", "analysis_failed")),
            )
        if lifecycle != "completed":
            raise SubmissionConflictError
        assistant_id = message.message_metadata.get("assistant_message_id")
        assistant = (
            await self._conversations.get_message(UUID(str(assistant_id)))
            if assistant_id is not None
            else None
        )
        return QuestionSubmission(message, assistant, await self._analytics.get(message.id))

    async def _mark_failed(self, message: MessageRecord, code: str) -> MessageRecord:
        updated = await self._conversations.update_message_metadata(
            message.id, message_metadata={"status": "failed", "error_code": code}
        )
        assert updated is not None
        return updated
