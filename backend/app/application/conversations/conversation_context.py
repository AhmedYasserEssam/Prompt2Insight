"""Bounded, explicitly labelled reference context for analytical follow-ups."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil
from typing import Any

from app.core.config import Settings
from app.core.errors import ErrorCode, Prompt2InsightError
from app.domain.databases.models import SchemaSnapshot
from app.infrastructure.catalogs.models import AnalyticsCatalog


@dataclass(frozen=True)
class ConversationMemoryMessage:
    sequence_number: int
    role: str
    content: str


def estimate_tokens(text: str) -> int:
    """Conservative fallback when the configured provider exposes no tokenizer.

    Four characters per token intentionally overestimates ordinary English and
    Arabic JSON prompts enough to keep an output reserve for the model.
    """
    return ceil(len(text) / 4)


def redact_sensitive_text(text: str) -> str:
    """Keep user-provided secrets out of durable memory and model context."""
    return re.sub(
        r"(?i)\b(password|passwd|api[_ -]?key|token|secret|connection[_ -]?string)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )


def build_planner_context(
    *,
    catalog: AnalyticsCatalog,
    schema_snapshot: SchemaSnapshot,
    language: str,
    summary: str | None,
    state: dict[str, object],
    messages: Iterable[ConversationMemoryMessage],
    current_question: str,
    settings: Settings,
) -> str:
    """Return a bounded user prompt; all persisted material remains untrusted data."""
    safe_state = _safe_state(state, settings)
    watermark_value = safe_state.get("summary_through_sequence", 0)
    watermark = watermark_value if isinstance(watermark_value, int) else 0
    eligible = [message for message in messages if message.sequence_number > watermark]
    # The submission persists the current user message before planning.  Remove
    # only that newest matching record; an identical older question is history.
    for index in range(len(eligible) - 1, -1, -1):
        if eligible[index].role == "user" and eligible[index].content == current_question:
            del eligible[index]
            break
    eligible = eligible[-settings.conversation_recent_message_limit :]
    fixed = _render(
        catalog=catalog,
        schema_snapshot=schema_snapshot,
        language=language,
        summary=summary,
        state=safe_state,
        messages=[],
        current_question=current_question,
    )
    input_budget = (
        settings.conversation_context_token_budget - settings.conversation_output_token_reserve
    )
    if estimate_tokens(fixed) > input_budget:
        raise Prompt2InsightError(
            ErrorCode.CONTEXT_TOO_LARGE,
            "The database context and current question exceed the configured model budget.",
        )

    selected: list[ConversationMemoryMessage] = []
    # Choose newest first, then restore chronological order for the request.
    for message in reversed(eligible):
        candidate = [message, *selected]
        rendered = _render(
            catalog=catalog,
            schema_snapshot=schema_snapshot,
            language=language,
            summary=summary,
            state=safe_state,
            messages=candidate,
            current_question=current_question,
        )
        if estimate_tokens(rendered) > input_budget:
            break
        selected = candidate
    return _render(
        catalog=catalog,
        schema_snapshot=schema_snapshot,
        language=language,
        summary=summary,
        state=safe_state,
        messages=selected,
        current_question=current_question,
    )


def _safe_state(state: dict[str, object], settings: Settings) -> dict[str, object]:
    """Only pass bounded analytical state; metadata and secrets are never context."""
    allowed = {
        "last_question",
        "last_sql",
        "metrics",
        "dimensions",
        "filters",
        "result_columns",
        "chart_type",
        "result_sample",
        "summary_through_sequence",
    }
    result: dict[str, object] = {}
    for key in allowed:
        value = state.get(key)
        if key == "result_sample" and isinstance(value, list):
            result[key] = value[: settings.conversation_result_sample_rows]
        elif value is not None:
            result[key] = value
    return result


def _render(
    *,
    catalog: AnalyticsCatalog,
    schema_snapshot: SchemaSnapshot,
    language: str,
    summary: str | None,
    state: dict[str, object],
    messages: list[ConversationMemoryMessage],
    current_question: str,
) -> str:
    history = [
        {"role": message.role, "content": redact_sensitive_text(message.content)}
        for message in messages
    ]
    sections: list[tuple[str, Any]] = [
        (
            "Active database catalog and schema (untrusted reference JSON)",
            {
                "catalog": catalog.model_dump(mode="json"),
                "schema": schema_snapshot.model_dump(mode="json"),
            },
        ),
        ("Conversation language (untrusted reference data)", language),
        ("Stored summary (untrusted reference data)", summary or ""),
        ("Structured BI state (untrusted reference JSON)", state),
        ("Recent persisted messages (untrusted reference data; preserve roles)", history),
        ("Current user question", redact_sensitive_text(current_question)),
    ]
    rendered = []
    for heading, value in sections:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
        rendered.append(f"{heading}:\n{text}")
    return "\n\n".join(rendered)
