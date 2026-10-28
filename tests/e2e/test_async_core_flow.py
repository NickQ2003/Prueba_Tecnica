import os
import time
import uuid
from datetime import date

import httpx
from app.adapters.persistence.models import NotificationDelivery, Operation, OutboxEvent
from app.adapters.persistence.session import SessionLocal
from sqlalchemy import select


def _wait_for_operation(operation_id: str, terminal_status: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        with SessionLocal() as session:
            operation = session.get(Operation, uuid.UUID(operation_id))
            if operation and operation.status in {"SUCCEEDED", "REJECTED", "NEEDS_REVIEW"}:
                assert operation.status == terminal_status
                return
        time.sleep(0.2)
    raise TimeoutError(f"operation did not finish within 45 seconds: {operation_id}")


def _notification_delivered(operation_id: str) -> bool:
    with SessionLocal() as session:
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.operation_id == uuid.UUID(operation_id),
                OutboxEvent.event_type == "notification.confirmed",
            )
        )
        if event is None:
            return False
        return (
            session.scalar(
                select(NotificationDelivery.id)
                .where(NotificationDelivery.event_id == event.id)
                .limit(1)
            )
            is not None
        )


def test_confirmed_change_survives_core_response_timeout_and_reconciles():
    base_url = os.environ.get("E2E_BASE_URL")
    if not base_url:
        return
    headers = {"Authorization": f"Bearer {os.environ['API_OPERATOR_TOKEN']}"}
    suffix = uuid.uuid4().hex
    with httpx.Client(base_url=base_url, timeout=5) as client:
        tenant = client.post(
            "/api/v1/parties",
            headers=headers,
            json={
                "party_type": "PERSON",
                "display_name": "E2E synthetic tenant",
                "document_reference": f"E2E-T-{suffix}",
            },
        )
        landlord = client.post(
            "/api/v1/parties",
            headers=headers,
            json={
                "party_type": "PERSON",
                "display_name": "E2E synthetic landlord",
                "document_reference": f"E2E-L-{suffix}",
            },
        )
        assert tenant.status_code == 201
        assert landlord.status_code == 201
        listed_parties = client.get(
            f"/api/v1/parties?search=E2E-T-{suffix}", headers=headers
        )
        assert listed_parties.status_code == 200
        assert listed_parties.json()["total"] == 1
        created = client.post(
            "/api/v1/policies",
            headers={
                **headers,
                "Idempotency-Key": f"e2e-create-{suffix}",
                "X-Demo-Core-Scenario": "timeout_after_apply",
            },
            json={
                "policy_type": "INDIVIDUAL",
                "holder_party_id": tenant.json()["id"],
                "starts_on": date.today().isoformat(),
                "duration_months": 12,
                "risks": [
                    {
                        "property_address": "E2E synthetic address",
                        "tenant_party_id": tenant.json()["id"],
                        "landlord_party_id": landlord.json()["id"],
                        "monthly_rent": "1250000.00",
                    }
                ],
            },
        )
        assert created.status_code == 202
        assert created.headers["Location"].endswith(created.json()["operation_id"])

        create_operation_id = created.json()["operation_id"]
        _wait_for_operation(create_operation_id, "SUCCEEDED")
        operation_response = client.get(
            f"/api/v1/operations/{create_operation_id}", headers=headers
        )
        assert operation_response.status_code == 200
        operation = operation_response.json()
        assert operation["status"] == "SUCCEEDED"
        policy_id = operation["policy_id"]
        policy_response = client.get(f"/api/v1/policies/{policy_id}", headers=headers)
        assert policy_response.status_code == 200
        assert policy_response.json()["policy_type"] == "INDIVIDUAL"

        rate = client.post(
            "/api/v1/ipc-rates",
            headers={"Authorization": f"Bearer {os.environ['API_REVIEWER_TOKEN']}"},
            json={
                "reference_period": f"E2E-{suffix[:12]}",
                "effective_on": date.today().isoformat(),
                "rate": "0.08000",
                "source": "Fictional test rate",
                "is_fictional": True,
            },
        )
        assert rate.status_code == 201, rate.text
        renewal = client.post(
            f"/api/v1/policies/{policy_id}/renewals",
            headers={
                **headers,
                "Idempotency-Key": f"e2e-renewal-{suffix}",
                "X-Demo-Core-Scenario": "timeout_after_apply",
            },
            json={"ipc_rate_id": None},
        )
        assert renewal.status_code == 202
        assert renewal.json()["status"] == "WAITING_INPUT"
        resumed = client.post(
            f"/api/v1/operations/{renewal.json()['operation_id']}/resume",
            headers={"Authorization": f"Bearer {os.environ['API_REVIEWER_TOKEN']}"},
            json={"ipc_rate_id": rate.json()["id"]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "PENDING"
        renewal_operation_id = renewal.json()["operation_id"]
        _wait_for_operation(renewal_operation_id, "SUCCEEDED")
        renewal_response = client.get(
            f"/api/v1/operations/{renewal_operation_id}", headers=headers
        )
        assert renewal_response.status_code == 200
        renewal_operation = renewal_response.json()
        assert renewal_operation["status"] == "SUCCEEDED"
        assert renewal_operation["result"]["core_status"] == "CONFIRMED"
        replay = client.post(
            f"/api/v1/policies/{policy_id}/renewals",
            headers={**headers, "Idempotency-Key": f"e2e-renewal-{suffix}"},
            json={"ipc_rate_id": None},
        )
        assert replay.status_code == 202
        assert replay.json()["operation_id"] == renewal_operation["operation_id"]
        for operation_id in [operation["operation_id"], renewal_operation["operation_id"]]:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not _notification_delivered(operation_id):
                time.sleep(0.2)
            assert _notification_delivered(operation_id)
        periods = client.get(f"/api/v1/policies/{policy_id}/periods", headers=headers)
        assert periods.status_code == 200
        assert len(periods.json()) == 2
