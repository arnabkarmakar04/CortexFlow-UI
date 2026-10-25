"""HTTP request and response contracts for the CortexFlow chat API."""
from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str = Field(min_length=1, max_length=128)

    @field_validator("message", "conversation_id")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
