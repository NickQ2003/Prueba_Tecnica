from datetime import date
from decimal import Decimal

from app.adapters.persistence.models import IpcRate, Operation, Party
from app.adapters.persistence.session import SessionLocal
from app.application.policy_operations import create_party, request_policy_creation
from sqlalchemy import select


def party(party_type: str, display_name: str, reference: str) -> Party:
    with SessionLocal() as session:
        found = session.scalar(select(Party).where(Party.document_reference == reference))
        if found:
            return found
    with SessionLocal() as session:
        return create_party(
            session,
            {
                "party_type": party_type,
                "display_name": display_name,
                "document_reference": reference,
            },
        )


def ipc_rate() -> IpcRate:
    with SessionLocal() as session:
        found = session.scalar(select(IpcRate).where(IpcRate.reference_period == "DEMO-2026"))
        if found:
            return found
        rate = IpcRate(
            reference_period="DEMO-2026",
            effective_on=date.today(),
            rate=Decimal("0.08000"),
            source="Fictional local demonstration value",
            is_fictional=True,
        )
        session.add(rate)
        session.commit()
        session.refresh(rate)
        return rate


def queue_policy(policy_type: str, holder: Party, risks: list[dict]) -> str:
    key = f"seed-{policy_type.lower()}-v1"
    with SessionLocal() as session:
        existing = session.scalar(
            select(Operation).where(
                Operation.consumer_scope == "synthetic-seed",
                Operation.operation_type == "policy.create",
                Operation.idempotency_key == key,
            )
        )
        if existing:
            return str(existing.id)
    payload = {
        "policy_type": policy_type,
        "holder_party_id": str(holder.id),
        "starts_on": date.today().isoformat(),
        "duration_months": 12,
        "risks": risks,
    }
    with SessionLocal() as session:
        operation = request_policy_creation(
            session,
            payload=payload,
            consumer_scope="synthetic-seed",
            idempotency_key=key,
            correlation_id=f"seed-{policy_type.lower()}-v1",
            actor="synthetic-seed",
            demo_scenario="success",
        )
        return str(operation.id)


def main() -> None:
    tenant = party("PERSON", "Arrendatario Demo", "DEMO-PERSON-001")
    landlord = party("PERSON", "Arrendador Demo", "DEMO-PERSON-002")
    holder = party("ORGANIZATION", "Administradora Demo", "DEMO-ORG-001")
    tenant_two = party("PERSON", "Arrendatario Demo 2", "DEMO-PERSON-003")
    landlord_two = party("PERSON", "Arrendador Demo 2", "DEMO-PERSON-004")
    rate = ipc_rate()
    individual_operation = queue_policy(
        "INDIVIDUAL",
        tenant,
        [
            {
                "property_address": "Calle Ficticia 123",
                "tenant_party_id": str(tenant.id),
                "landlord_party_id": str(landlord.id),
                "monthly_rent": "1500000.00",
            }
        ],
    )
    collective_operation = queue_policy(
        "COLLECTIVE",
        holder,
        [
            {
                "property_address": "Carrera Ficticia 10-20",
                "tenant_party_id": str(tenant.id),
                "landlord_party_id": str(landlord.id),
                "monthly_rent": "1700000.00",
            },
            {
                "property_address": "Avenida Ficticia 50-60",
                "tenant_party_id": str(tenant_two.id),
                "landlord_party_id": str(landlord_two.id),
                "monthly_rent": "2100000.00",
            },
        ],
    )
    print(f"fictional IPC {rate.reference_period}={rate.rate}; marked fictional")
    print(f"individual operation: {individual_operation}")
    print(f"collective operation: {collective_operation}")
    print("Operations are queued; poll them through GET /api/v1/operations/{operation_id}.")


if __name__ == "__main__":
    main()
