from typing import Any
from pydantic import BaseModel, Field


class ChatMessagePart(BaseModel):
    type: str = "text"
    text: str | None = None
    image_url: dict[str, Any] | None = None


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str | None = None
    type: str = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    role: str
    content: str | list[ChatMessagePart | dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[ToolCall | dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = 1
    stream: bool | None = False
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, float] | None = None
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    reasoning_effort: str | None = None
    reasoning: dict[str, Any] | None = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = 1700000000
    owned_by: str = "qoder"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelCard]


class AccountRecord(BaseModel):
    id: str
    kind: str  # "oauth" | "pat"
    access_token: str
    refresh_token: str = ""
    pat: str = ""  # PAT accounts only
    user_id: str
    name: str = ""
    email: str = ""
    machine_id: str
    expires_at: int = 0  # ms
    enabled: bool = True
    skip_quota: bool = False
    skip_auth: bool = False
    quota_snapshot: dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""

    def public_dump(self) -> dict[str, Any]:
        data = self.model_dump()
        if data.get("access_token"):
            data["access_token"] = "***"
        if data.get("refresh_token"):
            data["refresh_token"] = "***"
        if data.get("pat"):
            data["pat"] = "***"
        return data


TokenRecord = AccountRecord
