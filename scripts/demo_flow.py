import os
import time
import uuid
from datetime import date

import httpx

BASE_URL = os.environ.get("DEMO_API_URL", "http://api:8000")
OPERATOR_TOKEN = os.environ.get("API_OPERATOR_TOKEN", "demo-operator-token")


def wait_for_operation(client: httpx.Client, operation_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/operations/{operation_id}")
        response.raise_for_status()
        operation = response.json()
        if operation["status"] in {"SUCCEEDED", "REJECTED", "NEEDS_REVIEW"}:
            return operation
        time.sleep(0.2)
    raise TimeoutError(f"operation did not finish within 30 seconds: {operation_id}")


def main() -> None:
    headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    suffix = uuid.uuid4().hex[:10]
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=5) as client:
        tenant = client.post(
            "/api/v1/parties",
            json={
                "party_type": "PERSON",
                "display_name": "Arrendatario de demostración",
                "document_reference": f"FLOW-TENANT-{suffix}",
            },
        )
        tenant.raise_for_status()
        landlord = client.post(
            "/api/v1/parties",
            json={
                "party_type": "PERSON",
                "display_name": "Arrendador de demostración",
                "document_reference": f"FLOW-LANDLORD-{suffix}",
            },
        )
        landlord.raise_for_status()
        policy = client.post(
            "/api/v1/policies",
            headers={
                "Idempotency-Key": f"demo-policy-{suffix}",
                "X-Demo-Core-Scenario": "timeout_after_apply",
            },
            json={
                "policy_type": "INDIVIDUAL",
                "holder_party_id": tenant.json()["id"],
                "starts_on": date.today().isoformat(),
                "duration_months": 12,
                "risks": [
                    {
                        "property_address": "Dirección ficticia 123",
                        "tenant_party_id": tenant.json()["id"],
                        "landlord_party_id": landlord.json()["id"],
                        "monthly_rent": "1500000.00",
                    }
                ],
            },
        )
        policy.raise_for_status()
        operation = wait_for_operation(client, policy.json()["operation_id"])
        if operation["status"] != "SUCCEEDED":
            raise RuntimeError(f"policy creation did not reconcile: {operation}")
        policy_id = operation["policy_id"]

        rate = client.post(
            "/api/v1/ipc-rates",
            json={
                "reference_period": f"DEMO-{suffix}",
                "effective_on": date.today().isoformat(),
                "rate": "0.08000",
                "source": "Fictional demo rate",
                "is_fictional": True,
            },
            headers={"Authorization": "Bearer demo-reviewer-token"},
        )
        rate.raise_for_status()
        renewal = client.post(
            f"/api/v1/policies/{policy_id}/renewals",
            headers={
                "Idempotency-Key": f"demo-renewal-{suffix}",
                "X-Demo-Core-Scenario": "timeout_after_apply",
            },
            json={"ipc_rate_id": rate.json()["id"]},
        )
        renewal.raise_for_status()
        renewed = wait_for_operation(client, renewal.json()["operation_id"])
        if renewed["status"] != "SUCCEEDED":
            raise RuntimeError(f"renewal did not reconcile: {renewed}")
        periods = client.get(f"/api/v1/policies/{policy_id}/periods")
        periods.raise_for_status()
        print(
            {
                "policy_id": policy_id,
                "creation_status": operation["status"],
                "renewal_status": renewed["status"],
                "period_count": len(periods.json()),
                "scenario": "CORE applied, response timed out, lookup reconciled",
            }
        )


if __name__ == "__main__":
    main()
