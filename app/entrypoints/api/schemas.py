import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PartyCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "party_type": "PERSON",
                    "display_name": "Arrendatario Demo",
                    "document_reference": "SYNTHETIC-PERSON-001",
                }
            ]
        }
    )

    party_type: Literal["PERSON", "ORGANIZATION"]
    display_name: str = Field(min_length=1, max_length=160)
    document_reference: str = Field(min_length=1, max_length=80)


class PartyRead(PartyCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


class RiskInput(BaseModel):
    property_address: str = Field(min_length=1, max_length=240)
    tenant_party_id: uuid.UUID
    landlord_party_id: uuid.UUID
    monthly_rent: Decimal = Field(gt=0, max_digits=14, decimal_places=2)

    @field_validator("monthly_rent")
    @classmethod
    def rent_must_have_cents_precision(cls, value: Decimal) -> Decimal:
        if value.as_tuple().exponent < -2:
            raise ValueError("monthly_rent supports at most two decimal places")
        return value


class PolicyCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "policy_type": "INDIVIDUAL",
                    "holder_party_id": "11111111-1111-4111-8111-111111111111",
                    "starts_on": "2099-01-01",
                    "duration_months": 12,
                    "risks": [
                        {
                            "property_address": "Dirección ficticia 123",
                            "tenant_party_id": "11111111-1111-4111-8111-111111111111",
                            "landlord_party_id": "22222222-2222-4222-8222-222222222222",
                            "monthly_rent": "1500000.00",
                        }
                    ],
                }
            ]
        }
    )

    policy_type: Literal["INDIVIDUAL", "COLLECTIVE"]
    holder_party_id: uuid.UUID
    starts_on: date
    duration_months: int = Field(ge=1, le=36)
    risks: list[RiskInput] = Field(min_length=1, max_length=100)

    @field_validator("starts_on")
    @classmethod
    def policy_cannot_start_in_the_past(cls, value: date) -> date:
        if value < date.today():
            raise ValueError("starts_on must be today or a future date")
        return value

    @model_validator(mode="after")
    def validate_risk_count(self) -> "PolicyCreate":
        if self.policy_type == "INDIVIDUAL" and len(self.risks) != 1:
            raise ValueError("an individual policy must start with exactly one risk")
        if (
            self.policy_type == "INDIVIDUAL"
            and self.risks[0].tenant_party_id != self.holder_party_id
        ):
            raise ValueError(
                "for an individual policy the holder and tenant must be the same party"
            )
        return self


class RiskCreate(RiskInput):
    pass


class PolicyPatch(BaseModel):
    notes: str | None = Field(default=None, max_length=500)


class RenewalCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"ipc_rate_id": "33333333-3333-4333-8333-333333333333"}]}
    )
    ipc_rate_id: uuid.UUID | None = None


class IpcRateCreate(BaseModel):
    reference_period: str = Field(min_length=1, max_length=20)
    effective_on: date
    rate: Decimal = Field(gt=Decimal("-1"), le=Decimal("10"), max_digits=8, decimal_places=5)
    source: str = Field(min_length=1, max_length=120)
    is_fictional: bool = True


class ResumeOperation(BaseModel):
    ipc_rate_id: uuid.UUID


class RiskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    risk_number: int
    property_address: str
    tenant_party_id: uuid.UUID
    landlord_party_id: uuid.UUID
    status: str


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    policy_number: str
    policy_type: str
    holder_party_id: uuid.UUID
    status: str
    notes: str | None
    created_at: datetime


class PolicyDetail(PolicyRead):
    risks: list[RiskRead]


class PeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sequence: int
    starts_on: date
    ends_on: date
    duration_months: int
    status: str
    ipc_rate_snapshot: Decimal | None
    ipc_reference: str | None
    calculation_version: str
    core_reference: str | None


class IpcRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reference_period: str
    effective_on: date
    rate: Decimal
    source: str
    is_fictional: bool


class OperationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    operation_id: uuid.UUID = Field(validation_alias="id")
    operation_type: str
    policy_id: uuid.UUID | None
    status: str
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    result: dict | None = Field(validation_alias="result_payload")


class PaginatedPolicies(BaseModel):
    items: list[PolicyRead]
    limit: int
    offset: int
    total: int


class DemoScenario(BaseModel):
    scenario: Literal["success", "reject", "unavailable", "slow", "timeout_after_apply", "mismatch"]
