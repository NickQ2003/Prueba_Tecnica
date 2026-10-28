import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.adapters.core.client import CoreClient, CoreRejected, CoreUncertain
from app.adapters.persistence.models import (
    AuditEvent,
    NotificationDelivery,
    Operation,
    OperationStatus,
    OutboxEvent,
    PeriodRiskValue,
    Policy,
    PolicyPeriod,
    Risk,
)
from app.adapters.persistence.session import SessionLocal
from app.domain.calculations import add_months, premium

logger = logging.getLogger(__name__)
TERMINAL = {OperationStatus.SUCCEEDED.value, OperationStatus.REJECTED.value}


def _now() -> datetime:
    return datetime.now(UTC)


def _claim(operation_id: uuid.UUID) -> tuple[dict, bool] | None:
    with SessionLocal() as session:
        operation = session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if (
            operation is None
            or operation.status in TERMINAL
            or operation.status == OperationStatus.WAITING_INPUT.value
        ):
            return None
        now = _now()
        reconcile_only = operation.status == OperationStatus.RECONCILING.value
        if operation.status == OperationStatus.NEEDS_REVIEW.value:
            return None
        if operation.status == OperationStatus.PROCESSING.value:
            if operation.lease_until and operation.lease_until > now:
                return None
            reconcile_only = True
        operation.status = OperationStatus.PROCESSING.value
        operation.attempts += 1
        operation.lease_until = now + timedelta(seconds=60)
        session.commit()
        return (
            {
                "id": str(operation.id),
                "operation_type": operation.operation_type,
                "policy_id": str(operation.policy_id) if operation.policy_id else None,
                "payload": operation.request_payload,
                "scenario": operation.demo_scenario,
                "correlation_id": operation.correlation_id,
            },
            reconcile_only,
        )


def _set_failed(operation_id: uuid.UUID, status: str, code: str, message: str) -> dict:
    with SessionLocal() as session:
        operation = session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if operation is None:
            return {"status": "missing"}
        if operation.status in TERMINAL:
            return {"status": operation.status}
        operation.status = status
        operation.error_code = code
        operation.error_message = message[:500]
        operation.lease_until = None
        session.add(
            AuditEvent(
                actor="core-worker",
                action=f"operation.{status.lower()}",
                entity_type="operation",
                entity_id=str(operation.id),
                correlation_id=operation.correlation_id,
                details={"error_code": code},
            )
        )
        session.commit()
        logger.warning(
            json.dumps(
                {
                    "event": "operation_result",
                    "operation_id": str(operation.id),
                    "correlation_id": operation.correlation_id,
                    "status": status,
                    "error_code": code,
                }
            )
        )
        return {"status": status}


def _values_match(expected: list[dict], confirmed: list[dict] | None) -> bool:
    if confirmed is None or len(expected) != len(confirmed):
        return False
    by_risk = {row["risk_id"]: row for row in confirmed}
    for item in expected:
        actual = by_risk.get(item["risk_id"])
        if not actual:
            return False
        if Decimal(item["monthly_rent"]) != Decimal(actual["monthly_rent"]):
            return False
        if Decimal(item["premium"]) != Decimal(actual["premium"]):
            return False
    return True


def _apply_confirmed(operation_id: uuid.UUID, core_result: dict) -> dict:
    if core_result.get("status") != "CONFIRMED":
        return _set_failed(
            operation_id,
            OperationStatus.REJECTED.value,
            "CORE_REJECTED",
            "CORE returned a terminal rejection during reconciliation",
        )
    with SessionLocal() as session:
        operation = session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if operation is None:
            return {"status": "missing"}
        if operation.status in TERMINAL:
            return {"status": operation.status}
        if operation.result_payload and operation.result_payload.get("core_status") == "CONFIRMED":
            return {"status": operation.status}

        payload = operation.request_payload
        policy: Policy | None = None
        mismatch = False
        if operation.operation_type == "policy.create":
            policy_id = uuid.UUID(payload["policy_id"])
            policy = Policy(
                id=policy_id,
                policy_number=payload["policy_number"],
                policy_type=payload["policy_type"],
                holder_party_id=uuid.UUID(payload["holder_party_id"]),
                status="ACTIVE",
            )
            session.add(policy)
            session.flush()
            start = date.fromisoformat(payload["starts_on"])
            months = int(payload["duration_months"])
            end = add_months(start, months)
            period = PolicyPeriod(
                policy_id=policy.id,
                sequence=1,
                starts_on=start,
                ends_on=end,
                duration_months=months,
                status="CURRENT" if start <= date.today() < end else "SCHEDULED",
                calculation_version="rent-ipc-v1",
            )
            session.add(period)
            session.flush()
            for index, item in enumerate(payload["risks"], start=1):
                risk = Risk(
                    policy_id=policy.id,
                    risk_number=index,
                    property_address=item["property_address"],
                    tenant_party_id=uuid.UUID(item["tenant_party_id"]),
                    landlord_party_id=uuid.UUID(item["landlord_party_id"]),
                    status="ACTIVE",
                )
                session.add(risk)
                session.flush()
                rent = Decimal(item["monthly_rent"])
                session.add(
                    PeriodRiskValue(
                        period_id=period.id,
                        risk_id=risk.id,
                        monthly_rent=rent,
                        premium=premium(rent, months),
                        currency="COP",
                    )
                )
            operation.policy_id = policy.id
            operation.result_payload = {
                "policy_id": str(policy.id),
                "policy_number": policy.policy_number,
                "core_status": "CONFIRMED",
                "core_reference": core_result["core_reference"],
            }

        elif operation.operation_type == "risk.create":
            session.scalar(select(Policy).where(Policy.id == operation.policy_id).with_for_update())
            item = payload
            risk = Risk(
                policy_id=operation.policy_id,
                risk_number=item["risk_number"],
                property_address=item["property_address"],
                tenant_party_id=uuid.UUID(item["tenant_party_id"]),
                landlord_party_id=uuid.UUID(item["landlord_party_id"]),
                status="ACTIVE",
            )
            session.add(risk)
            session.flush()
            period = session.scalar(
                select(PolicyPeriod)
                .where(
                    PolicyPeriod.policy_id == operation.policy_id, PolicyPeriod.status == "CURRENT"
                )
                .with_for_update()
            )
            if period:
                rent = Decimal(item["monthly_rent"])
                session.add(
                    PeriodRiskValue(
                        period_id=period.id,
                        risk_id=risk.id,
                        monthly_rent=rent,
                        premium=premium(rent, period.duration_months),
                        currency="COP",
                    )
                )
            operation.result_payload = {"risk_id": str(risk.id), "core_status": "CONFIRMED"}

        elif operation.operation_type == "risk.cancel":
            risk_id = uuid.UUID(payload["risk_id"])
            risk = session.scalar(select(Risk).where(Risk.id == risk_id).with_for_update())
            if risk is None:
                operation.status = OperationStatus.NEEDS_REVIEW.value
                operation.error_code = "LOCAL_RISK_MISSING"
                operation.error_message = "CORE confirmed cancellation but local risk was not found"
            else:
                risk.status = "CANCELLED"
                operation.result_payload = {"risk_id": str(risk.id), "core_status": "CONFIRMED"}
                policy = session.get(Policy, risk.policy_id)

        elif operation.operation_type == "policy.renewal":
            policy = session.scalar(
                select(Policy).where(Policy.id == operation.policy_id).with_for_update()
            )
            snapshot = payload["renewal"]
            expected = snapshot["risk_values"]
            confirmed = core_result.get("applied_values") or []
            mismatch = not _values_match(expected, confirmed)
            period = PolicyPeriod(
                policy_id=operation.policy_id,
                sequence=snapshot["sequence"],
                starts_on=date.fromisoformat(snapshot["starts_on"]),
                ends_on=date.fromisoformat(snapshot["ends_on"]),
                duration_months=snapshot["duration_months"],
                status=(
                    "CURRENT"
                    if date.fromisoformat(snapshot["starts_on"])
                    <= date.today()
                    < date.fromisoformat(snapshot["ends_on"])
                    else "EXPIRED"
                    if date.fromisoformat(snapshot["ends_on"]) <= date.today()
                    else "SCHEDULED"
                ),
                source_period_id=uuid.UUID(snapshot["source_period_id"]),
                ipc_rate_id=uuid.UUID(snapshot["ipc_rate_id"]),
                ipc_rate_snapshot=Decimal(snapshot["ipc_rate"]),
                ipc_reference=snapshot["ipc_reference"],
                calculation_version=snapshot["calculation_version"],
                core_reference=core_result["core_reference"],
            )
            session.add(period)
            session.flush()
            source_period = session.get(PolicyPeriod, uuid.UUID(snapshot["source_period_id"]))
            if period.starts_on <= date.today() and source_period is not None:
                source_period.status = "EXPIRED"
            confirmed_by_risk = {row["risk_id"]: row for row in confirmed}
            for expected_value in expected:
                actual = confirmed_by_risk.get(expected_value["risk_id"], expected_value)
                rent = Decimal(actual["monthly_rent"])
                session.add(
                    PeriodRiskValue(
                        period_id=period.id,
                        risk_id=uuid.UUID(expected_value["risk_id"]),
                        monthly_rent=rent,
                        premium=Decimal(actual["premium"]),
                        currency=snapshot["currency"],
                    )
                )
            operation.result_payload = {
                "period_id": str(period.id),
                "starts_on": snapshot["starts_on"],
                "ends_on": snapshot["ends_on"],
                "expected_values": expected,
                "core_values": confirmed,
                "core_status": "CONFIRMED",
                "core_reference": core_result["core_reference"],
            }
        else:
            operation.status = OperationStatus.NEEDS_REVIEW.value
            operation.error_code = "UNKNOWN_OPERATION_TYPE"
            operation.error_message = "CORE applied an operation type unknown to this application"

        if operation.status != OperationStatus.NEEDS_REVIEW.value:
            operation.status = (
                OperationStatus.NEEDS_REVIEW.value if mismatch else OperationStatus.SUCCEEDED.value
            )
            operation.error_code = "CORE_VALUE_MISMATCH" if mismatch else None
            operation.error_message = (
                "CORE confirmed values different from the requested snapshot" if mismatch else None
            )
        operation.lease_until = None
        if (
            operation.operation_type in {"policy.create", "policy.renewal"}
            and operation.result_payload
        ):
            event = OutboxEvent(
                operation_id=operation.id,
                event_type="notification.confirmed",
                payload={
                    "operation_id": str(operation.id),
                    "policy_id": str(operation.policy_id),
                    "notification_type": operation.operation_type,
                },
            )
            session.add(event)
        session.add(
            AuditEvent(
                actor="core-worker",
                action="operation.core_confirmed",
                entity_type="operation",
                entity_id=str(operation.id),
                correlation_id=operation.correlation_id,
                details={
                    "core_reference": core_result.get("core_reference"),
                    "discrepancy": mismatch,
                },
            )
        )
        session.commit()
        logger.info(
            json.dumps(
                {
                    "event": "operation_result",
                    "operation_id": str(operation.id),
                    "policy_id": str(operation.policy_id) if operation.policy_id else None,
                    "correlation_id": operation.correlation_id,
                    "status": operation.status,
                    "discrepancy": mismatch,
                }
            )
        )
        return {"status": operation.status, "operation_id": str(operation.id)}


def process_operation(operation_id: str) -> dict:
    parsed_id = uuid.UUID(operation_id)
    claimed = _claim(parsed_id)
    if claimed is None:
        return {"status": "skipped"}
    snapshot, reconcile_only = claimed
    client = CoreClient()
    result = None
    try:
        if reconcile_only:
            result = client.lookup(operation_id)
            if result is None:
                return _set_failed(
                    parsed_id,
                    OperationStatus.NEEDS_REVIEW.value,
                    "CORE_RESULT_NOT_FOUND",
                    "No automatic resend was attempted because the original "
                    "CORE outcome is uncertain",
                )
        else:
            result = client.submit(
                operation_id,
                snapshot["operation_type"],
                snapshot["payload"],
                snapshot["scenario"],
            )
    except CoreRejected as exc:
        return _set_failed(parsed_id, OperationStatus.REJECTED.value, exc.code, str(exc))
    except CoreUncertain as exc:
        try:
            result = client.lookup(operation_id)
        except CoreUncertain:
            result = None
        if result is None:
            return _set_failed(
                parsed_id,
                OperationStatus.NEEDS_REVIEW.value,
                "CORE_OUTCOME_UNCERTAIN",
                str(exc),
            )
    return _apply_confirmed(parsed_id, result)


def reconcile_operation(operation_id: uuid.UUID) -> dict:
    with SessionLocal() as session:
        operation = session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if operation is None:
            return {"status": "missing"}
        if operation.status != OperationStatus.NEEDS_REVIEW.value:
            return {"status": operation.status}
        if operation.result_payload and operation.result_payload.get("core_status") == "CONFIRMED":
            return {"status": operation.status, "result": operation.result_payload}
        operation_id_text = str(operation.id)
        operation.status = OperationStatus.RECONCILING.value
        operation.lease_until = None
        session.commit()
    client = CoreClient()
    try:
        result = client.lookup(operation_id_text)
    except CoreUncertain as exc:
        return _set_failed(
            operation_id,
            OperationStatus.NEEDS_REVIEW.value,
            "CORE_RECONCILIATION_UNAVAILABLE",
            str(exc),
        )
    if result is None:
        return _set_failed(
            operation_id,
            OperationStatus.NEEDS_REVIEW.value,
            "CORE_RESULT_NOT_FOUND",
            "CORE has no result; operation remains in review and was not resent",
        )
    return _apply_confirmed(operation_id, result)


def deliver_notification(event_id: str) -> dict:
    event_uuid = uuid.UUID(event_id)
    with SessionLocal() as session:
        event = session.get(OutboxEvent, event_uuid)
        if event is None or event.event_type != "notification.confirmed":
            return {"status": "skipped"}
        operation = session.get(Operation, event.operation_id)
        if operation is None or operation.policy_id is None:
            return {"status": "skipped"}
        policy = session.get(Policy, operation.policy_id)
        if policy is None:
            return {"status": "skipped"}
        recipient = f"party:{policy.holder_party_id}"
        delivery = session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.event_id == event.id,
                NotificationDelivery.channel == "SIMULATED",
                NotificationDelivery.recipient == recipient,
            )
        )
        if delivery is None:
            session.add(
                NotificationDelivery(
                    event_id=event.id,
                    channel="SIMULATED",
                    recipient=recipient,
                    delivered_at=_now(),
                )
            )
            session.commit()
            logger.info(
                json.dumps(
                    {
                        "event": "notification_simulated",
                        "event_id": str(event.id),
                        "operation_id": str(operation.id),
                        "policy_id": str(policy.id),
                        "recipient_ref": recipient,
                    }
                )
            )
        return {"status": "delivered"}
