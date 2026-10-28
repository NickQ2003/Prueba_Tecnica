import argparse
import json
import shlex
import time
import uuid
from datetime import date
from decimal import Decimal

from app.adapters.persistence.models import IpcRate, Operation, Party, Policy, Risk
from app.adapters.persistence.session import SessionLocal
from app.application.policy_operations import create_party, request_policy_creation
from sqlalchemy import select

SEED_SCOPE = "synthetic-seed"
WAIT_SECONDS = 45


def get_or_create_party(party_type: str, display_name: str, reference: str) -> Party:
    with SessionLocal() as session:
        existing = session.scalar(select(Party).where(Party.document_reference == reference))
        if existing:
            return existing
    with SessionLocal() as session:
        return create_party(
            session,
            {
                "party_type": party_type,
                "display_name": display_name,
                "document_reference": reference,
            },
        )


def get_or_create_ipc_rate() -> IpcRate:
    with SessionLocal() as session:
        existing = session.scalar(select(IpcRate).where(IpcRate.reference_period == "DEMO-2026"))
        if existing:
            return existing
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


def _find_seed_operation(key: str) -> Operation | None:
    with SessionLocal() as session:
        return session.scalar(
            select(Operation).where(
                Operation.consumer_scope == SEED_SCOPE,
                Operation.operation_type == "policy.create",
                Operation.idempotency_key == key,
            )
        )


def queue_policy(
    *,
    policy_type: str,
    holder: Party,
    risks: list[dict],
    key: str,
    scenario: str = "success",
) -> str:
    existing = _find_seed_operation(key)
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
            consumer_scope=SEED_SCOPE,
            idempotency_key=key,
            correlation_id=key,
            actor="synthetic-seed",
            demo_scenario=scenario,
        )
        return str(operation.id)


def wait_for_operation(operation_id: str, expected_status: str) -> Operation:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        with SessionLocal() as session:
            operation = session.get(Operation, uuid.UUID(operation_id))
            if operation is None:
                raise RuntimeError(f"seed operation not found: {operation_id}")
            if operation.status in {"SUCCEEDED", "REJECTED", "NEEDS_REVIEW"}:
                if operation.status != expected_status:
                    raise RuntimeError(
                        f"seed operation {operation_id} finished as {operation.status}, "
                        f"expected {expected_status}"
                    )
                return operation
        time.sleep(0.2)
    raise TimeoutError(f"seed operation did not finish within {WAIT_SECONDS} seconds")


def _policy_and_risks(operation_id: str) -> tuple[Policy, list[Risk]]:
    operation = wait_for_operation(operation_id, "SUCCEEDED")
    if operation.policy_id is None:
        raise RuntimeError(f"confirmed seed operation has no local policy: {operation_id}")
    with SessionLocal() as session:
        policy = session.get(Policy, operation.policy_id)
        if policy is None:
            raise RuntimeError(f"seed policy not found for operation {operation_id}")
        risks = session.scalars(
            select(Risk).where(Risk.policy_id == policy.id).order_by(Risk.risk_number)
        ).all()
        return policy, risks


def create_mock_data() -> dict[str, str]:
    tenant = get_or_create_party("PERSON", "Arrendatario Demo", "DEMO-PERSON-001")
    landlord = get_or_create_party("PERSON", "Arrendador Demo", "DEMO-PERSON-002")
    holder = get_or_create_party("ORGANIZATION", "Administradora Demo", "DEMO-ORG-001")
    tenant_two = get_or_create_party("PERSON", "Arrendatario Demo 2", "DEMO-PERSON-003")
    landlord_two = get_or_create_party("PERSON", "Arrendador Demo 2", "DEMO-PERSON-004")
    review_tenant = get_or_create_party("PERSON", "Arrendatario Revisión", "DEMO-PERSON-005")
    review_landlord = get_or_create_party("PERSON", "Arrendador Revisión", "DEMO-PERSON-006")
    rate = get_or_create_ipc_rate()

    individual_operation_id = queue_policy(
        policy_type="INDIVIDUAL",
        holder=tenant,
        key="seed-individual-v1",
        risks=[
            {
                "property_address": "Calle Ficticia 123",
                "tenant_party_id": str(tenant.id),
                "landlord_party_id": str(landlord.id),
                "monthly_rent": "1500000.00",
            }
        ],
    )
    collective_operation_id = queue_policy(
        policy_type="COLLECTIVE",
        holder=holder,
        key="seed-collective-v1",
        risks=[
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
    review_operation_id = queue_policy(
        policy_type="INDIVIDUAL",
        holder=review_tenant,
        key="seed-review-operation-v1",
        scenario="unavailable",
        risks=[
            {
                "property_address": "Pasaje Ficticio 9",
                "tenant_party_id": str(review_tenant.id),
                "landlord_party_id": str(review_landlord.id),
                "monthly_rent": "950000.00",
            }
        ],
    )

    individual, individual_risks = _policy_and_risks(individual_operation_id)
    collective, collective_risks = _policy_and_risks(collective_operation_id)
    review_operation = wait_for_operation(review_operation_id, "NEEDS_REVIEW")

    return {
        "party_tenant_id": str(tenant.id),
        "party_landlord_id": str(landlord.id),
        "party_holder_id": str(holder.id),
        "party_tenant_2_id": str(tenant_two.id),
        "party_landlord_2_id": str(landlord_two.id),
        "policy_individual_id": str(individual.id),
        "risk_individual_id": str(individual_risks[0].id),
        "policy_collective_id": str(collective.id),
        "risk_collective_1_id": str(collective_risks[0].id),
        "risk_collective_2_id": str(collective_risks[1].id),
        "ipc_rate_id": str(rate.id),
        "operation_create_individual_id": individual_operation_id,
        "operation_create_collective_id": collective_operation_id,
        "operation_needs_review_id": str(review_operation.id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create synthetic policy API fixtures")
    parser.add_argument(
        "--shell",
        action="store_true",
        help="print MOCK_* exports for Bash command substitution",
    )
    args = parser.parse_args()
    data = create_mock_data()
    if args.shell:
        for key, value in sorted(data.items()):
            print(f"export MOCK_{key.upper()}={shlex.quote(value)}")
    else:
        print(json.dumps(data, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
