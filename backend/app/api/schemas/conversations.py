from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.analytics.models import AnalyticsResponse, ResponseLanguage


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    connection_id: UUID
    language: ResponseLanguage = ResponseLanguage.AUTO
    title: str | None = Field(default=None, min_length=1, max_length=255)


class UpdateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    language: ResponseLanguage | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def has_update(self) -> "UpdateConversationRequest":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self


class MessageSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=4000)
    client_message_id: UUID


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence_number: int
    role: str
    content: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class ConversationSummaryResponse(BaseModel):
    id: UUID
    connection_id: UUID | None
    title: str
    language: ResponseLanguage
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ConversationResponse(ConversationSummaryResponse):
    messages: list[MessageResponse] = Field(default_factory=list)


class PaginatedConversationsResponse(BaseModel):
    items: list[ConversationSummaryResponse]
    limit: int
    offset: int


class FailedQuestionResponse(BaseModel):
    status: str = "failed"
    code: str
    message: str


class QuestionSubmissionResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse | None = None
    analytics: AnalyticsResponse | None = None
    failure: FailedQuestionResponse | None = None
