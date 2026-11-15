
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class HomeResponse(BaseModel):
    message: str
    status: str


class HealthResponse(BaseModel):
    status: str


class QueryRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=3, max_length=1000)
    purpose: Literal["academic", "research", "general"]
    knowledge_scope: list[
        Annotated[str, Field(min_length=1, max_length=64)]
    ] | None = Field(default=None, max_length=10)

    @field_validator("user_id", "question", mode="before")
    @classmethod
    def strip_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class QueryResponse(BaseModel):
    status: Literal["answered", "refused", "needs_review"]
    answer: str | None = None
    citations: list = Field(default_factory=list)
    refusal_reason: str | None = None
    trace_id: UUID
