import os
import uuid
from datetime import date

import pytest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.mark.unit
def test_policy_creation_contract_requires_exactly_one_individual_risk():
    party = str(uuid.uuid4())
    response = client.post(
        "/api/v1/policies",
        headers={
            "Authorization": f"Bearer {os.environ['API_OPERATOR_TOKEN']}",
            "Idempotency-Key": "bad-individual",
        },
        json={
            "policy_type": "INDIVIDUAL",
            "holder_party_id": party,
            "starts_on": date.today().isoformat(),
            "duration_months": 12,
            "risks": [
                {
                    "property_address": "A",
                    "tenant_party_id": party,
                    "landlord_party_id": str(uuid.uuid4()),
                    "monthly_rent": "100.00",
                },
                {
                    "property_address": "B",
                    "tenant_party_id": party,
                    "landlord_party_id": str(uuid.uuid4()),
                    "monthly_rent": "100.00",
                },
            ],
        },
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_policy_operation_requires_authentication():
    response = client.get("/api/v1/policies")
    assert response.status_code == 401
