"""Create the initial rental policy domain."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("party_type", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("document_reference", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("party_type IN ('PERSON', 'ORGANIZATION')", name="ck_party_type"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_reference"),
    )
    op.create_table(
        "ipc_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reference_period", sa.String(20), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(8, 5), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("is_fictional", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rate > -1 AND rate <= 10", name="ck_ipc_rate"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_period", name="uq_ipc_reference_period"),
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_number", sa.String(40), nullable=False),
        sa.Column("policy_type", sa.String(20), nullable=False),
        sa.Column("holder_party_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), server_default="ACTIVE", nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("policy_type IN ('INDIVIDUAL', 'COLLECTIVE')", name="ck_policy_type"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_policy_status"),
        sa.ForeignKeyConstraint(["holder_party_id"], ["parties.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_number"),
    )
    op.create_index("ix_policies_status", "policies", ["status"])
    op.create_table(
        "risks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("risk_number", sa.Integer(), nullable=False),
        sa.Column("property_address", sa.String(240), nullable=False),
        sa.Column("tenant_party_id", sa.Uuid(), nullable=False),
        sa.Column("landlord_party_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), server_default="ACTIVE", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_risk_status"),
        sa.ForeignKeyConstraint(["landlord_party_id"], ["parties.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"]),
        sa.ForeignKeyConstraint(["tenant_party_id"], ["parties.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "risk_number", name="uq_risks_policy_number"),
    )
    op.create_index("ix_risks_policy_status", "risks", ["policy_id", "status"])
    op.create_table(
        "policy_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_period_id", sa.Uuid(), nullable=True),
        sa.Column("ipc_rate_id", sa.Uuid(), nullable=True),
        sa.Column("ipc_rate_snapshot", sa.Numeric(8, 5), nullable=True),
        sa.Column("ipc_reference", sa.String(40), nullable=True),
        sa.Column("calculation_version", sa.String(30), nullable=False),
        sa.Column("core_reference", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ends_on > starts_on", name="ck_period_dates"),
        sa.CheckConstraint("duration_months > 0", name="ck_period_duration"),
        sa.CheckConstraint(
            "status IN ('CURRENT', 'SCHEDULED', 'EXPIRED')", name="ck_period_status"
        ),
        sa.ForeignKeyConstraint(["ipc_rate_id"], ["ipc_rates.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"]),
        sa.ForeignKeyConstraint(["source_period_id"], ["policy_periods.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "sequence", name="uq_policy_period_sequence"),
        sa.UniqueConstraint("policy_id", "starts_on", name="uq_policy_period_start"),
    )
    op.create_table(
        "period_risk_values",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("monthly_rent", sa.Numeric(14, 2), nullable=False),
        sa.Column("premium", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="COP", nullable=False),
        sa.CheckConstraint("monthly_rent >= 0 AND premium >= 0", name="ck_period_risk_amounts"),
        sa.ForeignKeyConstraint(["period_id"], ["policy_periods.id"]),
        sa.ForeignKeyConstraint(["risk_id"], ["risks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_id", "risk_id", name="uq_period_risk"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("consumer_scope", sa.String(80), nullable=False),
        sa.Column("operation_type", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("target_period_start", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("demo_scenario", sa.String(40), server_default="success", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'WAITING_INPUT', 'PROCESSING', 'RECONCILING', "
            "'SUCCEEDED', 'REJECTED', 'NEEDS_REVIEW')",
            name="ck_operation_status",
        ),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer_scope", "operation_type", "idempotency_key", name="uq_operation_idempotency"
        ),
        sa.UniqueConstraint(
            "policy_id", "operation_type", "target_period_start", name="uq_operation_renewal_period"
        ),
    )
    op.create_index("ix_operations_pending", "operations", ["status", "next_attempt_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "event_type", name="uq_outbox_operation_event"),
    )
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "available_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(160), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "channel", "recipient", name="uq_notification_delivery"),
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("audit_events")
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_operations_pending", table_name="operations")
    op.drop_table("operations")
    op.drop_table("period_risk_values")
    op.drop_table("policy_periods")
    op.drop_index("ix_risks_policy_status", table_name="risks")
    op.drop_table("risks")
    op.drop_index("ix_policies_status", table_name="policies")
    op.drop_table("policies")
    op.drop_table("ipc_rates")
    op.drop_table("parties")
