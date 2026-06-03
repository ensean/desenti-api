"""请求/响应数据模型。"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    company_name = "company_name"
    person_name = "person_name"
    credit_code = "credit_code"
    id_card = "id_card"
    phone = "phone"
    address = "address"
    bank_name = "bank_name"
    bank_account = "bank_account"
    email = "email"
    account_name = "account_name"


class Role(str, Enum):
    party_a = "party_a"
    party_b = "party_b"
    party_c = "party_c"
    unknown = "unknown"


# ---------- 请求 ----------

class NerOptions(BaseModel):
    entity_types: list[str] = Field(default_factory=lambda: ["all"])
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    context_window: int = Field(default=100, ge=0, le=2000)


class NerRequest(BaseModel):
    text: str
    options: NerOptions = Field(default_factory=NerOptions)


# ---------- 响应 ----------

class Entity(BaseModel):
    value: str
    type: EntityType
    role: Role
    start: int
    end: int
    confidence: float
    context_field: str


class Statistics(BaseModel):
    total_entities: int
    by_type: dict[str, int]


class NerData(BaseModel):
    entities: list[Entity]
    statistics: Statistics


class Meta(BaseModel):
    model_version: str
    processing_time_ms: int


class NerResponse(BaseModel):
    success: bool = True
    data: NerData
    meta: Meta


# ---------- 错误 ----------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
