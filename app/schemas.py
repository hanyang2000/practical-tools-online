from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security import normalize_username


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str: return normalize_username(value)


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str; username: str; display_name: str; role: str; active: bool; avatar_custom: bool


class AuthStatus(BaseModel):
    authenticated: bool
    user: UserView | None = None


class ShopRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=1000)


class AgentPairRequest(BaseModel):
    code: str = Field(default="", max_length=128)
    device_id: str = Field(min_length=1, max_length=120)
    name: str = Field(default="", max_length=120)
    platform: str = Field(default="unknown", max_length=30)
    version: str = Field(default="", max_length=50)
    protocol_version: int = Field(default=1, ge=1, le=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class AgentHeartbeat(BaseModel):
    agent_id: str = Field(default="", max_length=120)
    platform: str = Field(default="unknown", max_length=30)
    status: str = Field(default="online", max_length=30)
    version: str = Field(default="", max_length=50)
    protocol_version: int = Field(default=1, ge=1, le=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    meta: dict = Field(default_factory=dict)


class JobRequest(BaseModel):
    kind: str = Field(default="capture_all", min_length=1, max_length=50)
    payload: dict = Field(default_factory=dict)
