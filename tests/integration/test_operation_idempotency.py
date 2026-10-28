import uuid
from datetime import date
from decimal import Decimal

import pytest
from app.adapters.persistence.models import AuditEvent, OutboxEvent, Party
from app.adapters.persistence.session import SessionLocal
from app.application.policy_operations import request_policy_creation
from app.domain.errors import ConflictError
from sqlalchemy import select
from sqlalchemy.orm import Session


def _parties(session: Session) -> tuple[Party, Party]:
    suffix = uuid.uuid4().hex
    tenant = Party(
        party_type="PERSON",
        display_name="Synthetic tenant",
        document_reference=f"TEST-T-{suffix}",
    )
    landlord = Party(
        party_type="PERSON",
        display_name="Synthetic landlord",
        document_reference=f"TEST-L-{suffix}",
    )
    session.add_all([tenant, landlord])
    session.commit()
    return tenant, landlord


def test_same_key_and_payload_returns_same_durable_operation():
    with SessionLocal() as session:
        tenant, landlord = _parties(session)
        payload = {
            "policy_type": "INDIVIDUAL",
            "holder_party_id": str(tenant.id),
            "starts_on": date.today().isoformat(),
            "duration_months": 12,
            "risks": [
                {
                    "property_address": "Synthetic address",
                    "tenant_party_id": str(tenant.id),
                    "landlord_party_id": str(landlord.id),
                    "monthly_rent": str(Decimal("1000000.00")),
                }
            ],
        }
        kwargs = {
            "payload": payload,
            "consumer_scope": "test-client",
            "idempotency_key": f"create-{uuid.uuid4()}",
            "correlation_id": "test-correlation",
            "actor": "test-operator",
            "demo_scenario": "success",
        }
        first = request_policy_creation(session, **kwargs)
        second = request_policy_creation(session, **kwargs)
        assert first.id == second.id
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.operation_id == first.id)
        ).all()
        assert [event.event_type for event in events] == ["operation.requested"]
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == str(first.id))
        ).all()
        assert len(audit) == 1


def test_same_key_with_different_request_conflicts():
    with SessionLocal() as session:
        tenant, landlord = _parties(session)
        key = f"create-{uuid.uuid4()}"
        base = {
            "policy_type": "INDIVIDUAL",
            "holder_party_id": str(tenant.id),
            "starts_on": date.today().isoformat(),
            "duration_months": 12,
            "risks": [
                {
                    "property_address": "Synthetic address",
                    "tenant_party_id": str(tenant.id),
                    "landlord_party_id": str(landlord.id),
                    "monthly_rent": "1000000.00",
                }
            ],
        }
        kwargs = {
            "consumer_scope": "test-client",
            "idempotency_key": key,
            "correlation_id": "test-correlation",
            "actor": "test-operator",
            "demo_scenario": "success",
        }
        request_policy_creation(session, payload=base, **kwargs)
        changed = {**base, "duration_months": 6}
        with pytest.raises(ConflictError, match="different request"):
            request_policy_creation(session, payload=changed, **kwargs)
