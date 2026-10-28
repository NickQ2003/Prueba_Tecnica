import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class PolicyType(str, enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    COLLECTIVE = "COLLECTIVE"


class PolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


class OperationStatus(str, enum.Enum):
    PENDING = "PENDING"
    WAITING_INPUT = "WAITING_INPUT"
    PROCESSING = "PROCESSING"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Party(Base):
    __tablename__ = "parties"
    __table_args__ = (
        CheckConstraint("party_type IN ('PERSON', 'ORGANIZATION')", name="ck_party_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    party_type: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    document_reference: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        CheckConstraint("policy_type IN ('INDIVIDUAL', 'COLLECTIVE')", name="ck_policy_type"),
        CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_policy_status"),
        Index("ix_policies_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    policy_type: Mapped[str] = mapped_column(String(20), nullable=False)
    holder_party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    notes: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Risk(Base):
    __tablename__ = "risks"
    __table_args__ = (
        UniqueConstraint("policy_id", "risk_number", name="uq_risks_policy_number"),
        CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_risk_status"),
        Index("ix_risks_policy_status", "policy_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id"), nullable=False)
    risk_number: Mapped[int] = mapped_column(Integer, nullable=False)
    property_address: Mapped[str] = mapped_column(String(240), nullable=False)
    tenant_party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    landlord_party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyPeriod(Base):
    __tablename__ = "policy_periods"
    __table_args__ = (
        UniqueConstraint("policy_id", "sequence", name="uq_policy_period_sequence"),
        UniqueConstraint("policy_id", "starts_on", name="uq_policy_period_start"),
        CheckConstraint("ends_on > starts_on", name="ck_period_dates"),
        CheckConstraint("duration_months > 0", name="ck_period_duration"),
        CheckConstraint("status IN ('CURRENT', 'SCHEDULED', 'EXPIRED')", name="ck_period_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policies.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_period_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policy_periods.id"))
    ipc_rate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ipc_rates.id"))
    ipc_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    ipc_reference: Mapped[str | None] = mapped_column(String(40))
    calculation_version: Mapped[str] = mapped_column(String(30), nullable=False)
    core_reference: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IpcRate(Base):
    __tablename__ = "ipc_rates"
    __table_args__ = (
        UniqueConstraint("reference_period", name="uq_ipc_reference_period"),
        CheckConstraint("rate > -1 AND rate <= 10", name="ck_ipc_rate"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reference_period: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    is_fictional: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PeriodRiskValue(Base):
    __tablename__ = "period_risk_values"
    __table_args__ = (
        UniqueConstraint("period_id", "risk_id", name="uq_period_risk"),
        CheckConstraint("monthly_rent >= 0 AND premium >= 0", name="ck_period_risk_amounts"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_periods.id"), nullable=False)
    risk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risks.id"), nullable=False)
    monthly_rent: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    premium: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="COP")


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "consumer_scope", "operation_type", "idempotency_key", name="uq_operation_idempotency"
        ),
        UniqueConstraint(
            "policy_id", "operation_type", "target_period_start", name="uq_operation_renewal_period"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'WAITING_INPUT', 'PROCESSING', 'RECONCILING', "
            "'SUCCEEDED', 'REJECTED', 'NEEDS_REVIEW')",
            name="ck_operation_status",
        ),
        Index("ix_operations_pending", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    consumer_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policies.id"))
    target_period_start: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result_payload: Mapped[dict | None] = mapped_column(JSONB)
    demo_scenario: Mapped[str] = mapped_column(String(40), nullable=False, server_default="success")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_unpublished", "published_at", "available_at"),
        UniqueConstraint("operation_id", "event_type", name="uq_outbox_operation_event"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    operation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operations.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", "recipient", name="uq_notification_delivery"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outbox_events.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(160), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
