"""HTTP request and response contracts for the CortexFlow API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """Backward-compatible request body for POST /api/chat."""

    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str = Field(min_length=1, max_length=128)

    @field_validator("message", "conversation_id")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ConversationMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    display_content: str | None = Field(default=None, max_length=20000)

    @field_validator("message", "display_content")
    @classmethod
    def strip_message_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty.")
        return value


class ConversationCreateResponse(BaseModel):
    thread_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationSummary(BaseModel):
    thread_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageRecord(BaseModel):
    id: int
    thread_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationRecord(BaseModel):
    thread_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationMessagesResponse(BaseModel):
    conversation: ConversationRecord
    messages: list[MessageRecord]


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    title: str | None = None
    status: Literal["completed", "completed_with_tool_errors", "failed"] = "completed"
