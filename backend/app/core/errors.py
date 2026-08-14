from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AUTHENTICATION_FAILED = "authentication_failed"
    SCHEMA_INTROSPECTION_FAILED = "schema_introspection_failed"
    CATALOG_NOT_READY = "catalog_not_ready"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    QUESTION_AMBIGUOUS = "question_ambiguous"
    METRIC_UNDEFINED = "metric_undefined"
    SQL_POLICY_REJECTED = "sql_policy_rejected"
    SQL_PARSE_FAILED = "sql_parse_failed"
    UNAUTHORIZED_TABLE = "unauthorized_table"
    UNAUTHORIZED_COLUMN = "unauthorized_column"
    # Legacy-only values retained so persisted responses from the removed semantic
    # policy architecture remain deserializable. New request paths do not raise them.
    UNDECLARED_JOIN = "undeclared_join"
    METRIC_POLICY_VIOLATION = "metric_policy_violation"
    PRIVACY_POLICY_VIOLATION = "privacy_policy_violation"
    QUERY_TOO_EXPENSIVE = "query_too_expensive"
    DATABASE_UNAVAILABLE = "database_unavailable"
    QUERY_TIMEOUT = "query_timeout"
    LOCK_TIMEOUT = "lock_timeout"
    EXECUTION_FAILED = "execution_failed"
    CATALOG_STALE = "catalog_stale"
    SCHEMA_CHANGED = "schema_changed"


class Prompt2InsightError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
