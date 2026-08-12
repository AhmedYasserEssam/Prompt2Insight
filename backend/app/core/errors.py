from enum import StrEnum


class ErrorCode(StrEnum):
    NOT_CONFIGURED = "not_configured"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_INVALID_OUTPUT = "llm_invalid_output"
    QUESTION_AMBIGUOUS = "question_ambiguous"
    METRIC_UNDEFINED = "metric_undefined"
    SQL_POLICY_REJECTED = "sql_policy_rejected"
    SQL_PARSE_FAILED = "sql_parse_failed"
    UNAUTHORIZED_TABLE = "unauthorized_table"
    UNAUTHORIZED_COLUMN = "unauthorized_column"
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
