from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.application.conversations.conversation_context import redact_sensitive_text
from app.core.config import Settings
from app.core.errors import Prompt2InsightError
from app.domain.analytics.models import (
    AnalyticsRequest,
    AnalyticsResponse,
    AnalyticsStatus,
    ResponseLanguage,
)
from app.persistence.models import ConversationRecord, MessageRecord
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
        settings: Settings | None = None,
    ) -> None:
        self._conversations = conversations
        self._connections = connections
        self._analytics = analytics
        self._settings = settings or Settings()

    async def submit(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
    ) -> QuestionSubmission:
        content = redact_sensitive_text(content)
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

        if response.status in {AnalyticsStatus.SUCCESS, AnalyticsStatus.EMPTY_RESULT}:
            state = _bi_state(content, response, self._settings)
            watermark = conversation.context_state.get("summary_through_sequence")
            if isinstance(watermark, int):
                state["summary_through_sequence"] = watermark
        else:
            state = dict(conversation.context_state)
        automatic_title = await self._automatic_title(conversation, content, response)
        completed_pair = await self._conversations.complete_success(
            user_message_id=user_message.id,
            conversation_id=conversation_id,
            assistant_content=response.answer or "Analysis completed.",
            request_id=client_message_id,
            analytics=response,
            context_state=state,
            automatic_title=automatic_title,
        )
        assert completed_pair is not None
        completed, assistant = completed_pair
        # Compaction is best effort and happens after the committed analytical result;
        # it can never turn a successful answer into a failure.
        try:
            await self._conversations.summarize_if_needed(
                conversation_id,
                threshold_tokens=self._settings.conversation_summary_threshold_tokens,
                keep_messages=self._settings.conversation_summary_keep_messages,
                max_chars=self._settings.conversation_summary_max_chars,
            )
        except Exception:
            pass
        return QuestionSubmission(completed, assistant, response)

    async def _automatic_title(
        self,
        conversation: ConversationRecord,
        question: str,
        response: AnalyticsResponse,
    ) -> str | None:
        if response.status not in {AnalyticsStatus.SUCCESS, AnalyticsStatus.EMPTY_RESULT}:
            return None
        title = conversation.title
        state = conversation.context_state
        language = conversation.language
        if title != "New conversation" or state.get("title_source") == "manual":
            return None
        try:
            generated = await self._analytics.title_for(question=question, language=language)
            if generated:
                return generated[:80]
        except Exception:
            pass
        return " ".join(question.split())[:80]

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


def _bi_state(question: str, response: AnalyticsResponse, settings: Settings) -> dict[str, object]:
    """Persist only bounded facts from a completed, validated analytical result."""
    plan = response.query_plan
    table = response.table
    assert plan is not None and table is not None
    table_data = table.model_dump(mode="json")
    sample = table_data["rows"][: settings.conversation_result_sample_rows]
    return {
        "last_question": question,
        "last_sql": (response.sql or "")[:8000],
        "metrics": plan.metric_ids[:50],
        "dimensions": plan.dimension_ids[:50],
        "filters": [item.model_dump(mode="json") for item in plan.filters[:50]],
        "result_columns": table.columns[:100],
        "chart_type": response.chart.chart_type if response.chart else None,
        "result_sample": sample,
    }
