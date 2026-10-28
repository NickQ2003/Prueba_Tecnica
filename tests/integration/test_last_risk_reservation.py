import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.adapters.persistence.models import Operation, Party, Policy, Risk
from app.adapters.persistence.session import SessionLocal
from app.application.policy_operations import request_risk_cancellation
from app.domain.errors import ConflictError
from sqlalchemy import select


def _cancel(
    policy_id: uuid.UUID, risk_id: uuid.UUID, barrier: threading.Barrier
) -> tuple[str, str]:
    with SessionLocal() as session:
        barrier.wait(timeout=5)
        try:
            operation = request_risk_cancellation(
                session,
                policy_id=policy_id,
                risk_id=risk_id,
                consumer_scope="concurrent-test",
                idempotency_key=f"cancel-{risk_id}",
                correlation_id="concurrent-test",
                actor="test-operator",
                demo_scenario="success",
            )
            return "accepted", str(operation.id)
        except ConflictError as exc:
            return "conflict", str(exc)


def test_concurrent_cancellations_reserve_policy_and_protect_last_risk():
    with SessionLocal() as session:
        suffix = uuid.uuid4().hex
        parties = [
            Party(
                party_type="PERSON",
                display_name=f"Synthetic party {index}",
                document_reference=f"LAST-RISK-{suffix}-{index}",
            )
            for index in range(3)
        ]
        session.add_all(parties)
        session.flush()
        policy = Policy(
            policy_number=f"TEST-{suffix[:12]}",
            policy_type="COLLECTIVE",
            holder_party_id=parties[0].id,
            status="ACTIVE",
        )
        session.add(policy)
        session.flush()
        risks = [
            Risk(
                policy_id=policy.id,
                risk_number=index,
                property_address=f"Synthetic {index}",
                tenant_party_id=parties[index].id,
                landlord_party_id=parties[2].id,
                status="ACTIVE",
            )
            for index in (1, 2)
        ]
        session.add_all(risks)
        session.commit()
        policy_id = policy.id
        risk_ids = [risk.id for risk in risks]

    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda risk_id: _cancel(policy_id, risk_id, barrier), risk_ids))

    accepted = [value for state, value in results if state == "accepted"]
    conflicts = [value for state, value in results if state == "conflict"]
    assert len(accepted) == 1
    assert len(conflicts) == 1
    assert "unresolved" in conflicts[0]

    with SessionLocal() as session:
        operation = session.scalar(select(Operation).where(Operation.id == uuid.UUID(accepted[0])))
        assert operation is not None
        cancelled_risk_id = uuid.UUID(operation.request_payload["risk_id"])
        session.get(Risk, cancelled_risk_id).status = "CANCELLED"
        operation.status = "SUCCEEDED"
        session.commit()
        remaining_risk_id = next(risk_id for risk_id in risk_ids if risk_id != cancelled_risk_id)

    with SessionLocal() as session:
        with pytest.raises(ConflictError, match="last active risk"):
            request_risk_cancellation(
                session,
                policy_id=policy_id,
                risk_id=remaining_risk_id,
                consumer_scope="concurrent-test",
                idempotency_key=f"last-risk-{uuid.uuid4()}",
                correlation_id="last-risk-test",
                actor="test-operator",
                demo_scenario="success",
            )
