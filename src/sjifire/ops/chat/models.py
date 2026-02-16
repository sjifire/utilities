"""Pydantic models for chat conversations and usage budgets in Cosmos DB."""

import uuid
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

MAX_MESSAGES = 200
MAX_TURNS = 50


class ConversationMessage(BaseModel):
    """A single message in a chat conversation."""

    role: Literal["user", "assistant"]
    content: str
    tool_use: list[dict] | None = None
    tool_results: list[dict] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_tokens: int = 0
    output_tokens: int = 0


class ConversationDocument(BaseModel):
    """Chat conversation document stored in Cosmos DB.

    Partition key is ``incident_id`` so all conversations for an incident
    are co-located. Each incident has at most one conversation.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    incident_id: str  # Partition key
    user_email: str
    messages: list[ConversationMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turn_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> Self:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)


class UserBudget(BaseModel):
    """Monthly usage budget for a user, stored in Cosmos DB.

    The ``id`` is ``{user_email}:{month}`` and partition key is ``month``.
    Daily usage is tracked in ``daily_tokens`` keyed by ISO date string.
    """

    id: str  # "{email}:{month}" e.g. "user@sjifire.org:2026-02"
    month: str  # Partition key "2026-02"
    user_email: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    daily_tokens: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_cosmos(self) -> dict:
        """Serialize for Cosmos DB storage."""
        return self.model_dump(mode="json")

    @classmethod
    def from_cosmos(cls, data: dict) -> Self:
        """Deserialize from Cosmos DB document."""
        return cls.model_validate(data)
