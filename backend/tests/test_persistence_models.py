from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.persistence.models import (
    ConnectionProfileRecord,
    ConversationRecord,
    MessageRecord,
)


def test_conversation_and_message_metadata() -> None:
    conversation = ConversationRecord.__table__
    message = MessageRecord.__table__

    assert conversation.c.context_state.type.__class__.__name__ == "JSONB"
    assert message.c.metadata.type.__class__.__name__ == "JSONB"
    assert {foreign_key.target_fullname for foreign_key in conversation.foreign_keys} == {
        "connection_profiles.id"
    }
    assert {foreign_key.target_fullname for foreign_key in message.foreign_keys} == {
        "conversations.id"
    }
    assert {foreign_key.constraint.name for foreign_key in conversation.foreign_keys} == {
        "fk_conversations_connection_profile_id"
    }
    assert {foreign_key.constraint.name for foreign_key in message.foreign_keys} == {
        "fk_messages_conversation_id"
    }
    assert next(iter(message.foreign_keys)).ondelete == "CASCADE"
    assert {constraint.name for constraint in message.constraints} >= {
        "ck_messages_role_allowed",
        "uq_messages_conversation_sequence_number",
    }
    assert {index.name for index in conversation.indexes} == {"ix_conversations_updated_at"}
    assert {index.name for index in message.indexes} == {
        "ix_messages_conversation_id_sequence_number"
    }
    assert ConversationRecord.messages.property.mapper.class_ is MessageRecord
    assert MessageRecord.conversation.property.mapper.class_ is ConversationRecord
    assert ConnectionProfileRecord.conversations.property.mapper.class_ is ConversationRecord
    assert callable(conversation.c.context_state.default.arg)
    assert callable(message.c.metadata.default.arg)

    ddl = str(CreateTable(message).compile(dialect=postgresql.dialect()))
    assert "UUID" in ddl
    assert "JSONB" in ddl
