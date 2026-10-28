import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.persistence.models import (
    AuditEvent,
    IpcRate,
    Operation,
    OperationStatus,
    OutboxEvent,
    Party,
    PeriodRiskValue,
    Policy,
    PolicyPeriod,
    Risk,
)
from app.domain.calculations import add_months, premium, renewed_rent
from app.domain.errors import ConflictError, NotFoundError, ValidationError

ACTIVE_OPERATIONS = (
    OperationStatus.PENDING.value,
    OperationStatus.WAITING_INPUT.value,
    OperationStatus.PROCESSING.value,
    OperationStatus.RECONCILING.value,
    OperationStatus.NEEDS_REVIEW.value,
)


def _canonical_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    details: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            details=details or {},
        )
    )


def create_party(session: Session, payload: dict) -> Party:
    party = Party(**payload)
    session.add(party)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("document_reference already exists") from exc
    session.refresh(party)
    return party


def _check_parties(session: Session, party_ids: set[uuid.UUID]) -> None:
    found = set(session.scalars(select(Party.id).where(Party.id.in_(party_ids))).all())
    missing = party_ids - found
    if missing:
        raise NotFoundError(f"party not found: {next(iter(missing))}")


def _get_locked_policy(session: Session, policy_id: uuid.UUID) -> Policy:
    policy = session.scalar(select(Policy).where(Policy.id == policy_id).with_for_update())
    if policy is None:
        raise NotFoundError("policy not found")
    if policy.status != "ACTIVE":
        raise ConflictError("policy is not active")
    return policy


def _ensure_no_pending_policy_operation(session: Session, policy_id: uuid.UUID) -> None:
    pending = session.scalar(
        select(Operation.id)
        .where(Operation.policy_id == policy_id, Operation.status.in_(ACTIVE_OPERATIONS))
        .limit(1)
    )
    if pending:
        raise ConflictError("another contractual operation for this policy is unresolved")


def _queue_event(
    session: Session, operation: Operation, event_type: str = "operation.requested"
) -> None:
    session.add(
        OutboxEvent(
            operation_id=operation.id,
            event_type=event_type,
            payload={"operation_id": str(operation.id)},
        )
    )


def _new_operation(
    session: Session,
    *,
    operation_type: str,
    consumer_scope: str,
    idempotency_key: str,
    request_payload: dict,
    request_hash: str,
    policy_id: uuid.UUID | None,
    correlation_id: str,
    actor: str,
    demo_scenario: str,
    status: str = OperationStatus.PENDING.value,
    target_period_start: date | None = None,
) -> tuple[Operation, bool]:
    existing = session.scalar(
        select(Operation).where(
            Operation.consumer_scope == consumer_scope,
            Operation.operation_type == operation_type,
            Operation.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise ConflictError("Idempotency-Key was already used with a different request")
        return existing, False

    if policy_id is not None:
        _get_locked_policy(session, policy_id)
        existing = session.scalar(
            select(Operation).where(
                Operation.consumer_scope == consumer_scope,
                Operation.operation_type == operation_type,
                Operation.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.request_hash != request_hash:
                raise ConflictError("Idempotency-Key was already used with a different request")
            return existing, False
        _ensure_no_pending_policy_operation(session, policy_id)

    operation = Operation(
        consumer_scope=consumer_scope,
        operation_type=operation_type,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        policy_id=policy_id,
        target_period_start=target_period_start,
        status=status,
        request_payload=request_payload,
        demo_scenario=demo_scenario,
        correlation_id=correlation_id,
    )
    session.add(operation)
    session.flush()
    if status == OperationStatus.PENDING.value:
        _queue_event(session, operation)
    _audit(
        session,
        actor=actor,
        action="operation.accepted",
        entity_type="operation",
        entity_id=str(operation.id),
        correlation_id=correlation_id,
        details={"operation_type": operation_type, "status": status},
    )
    return operation, True


def _check_existing_idempotent(
    session: Session, *, scope: str, operation_type: str, key: str, request_hash: str
) -> Operation | None:
    existing = session.scalar(
        select(Operation).where(
            Operation.consumer_scope == scope,
            Operation.operation_type == operation_type,
            Operation.idempotency_key == key,
        )
    )
    if existing and existing.request_hash != request_hash:
        raise ConflictError("Idempotency-Key was already used with a different request")
    return existing


def _commit_operation(session: Session, operation: Operation, created: bool) -> Operation:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = session.scalar(
            select(Operation).where(
                Operation.consumer_scope == operation.consumer_scope,
                Operation.operation_type == operation.operation_type,
                Operation.idempotency_key == operation.idempotency_key,
            )
        )
        if existing and existing.request_hash == operation.request_hash:
            return existing
        raise ConflictError("contractual operation conflicts with an existing reservation") from exc
    if created:
        session.refresh(operation)
    return operation


def request_policy_creation(
    session: Session,
    *,
    payload: dict,
    consumer_scope: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
    demo_scenario: str,
) -> Operation:
    request_hash = _canonical_hash(payload)
    existing = session.scalar(
        select(Operation).where(
            Operation.consumer_scope == consumer_scope,
            Operation.operation_type == "policy.create",
            Operation.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise ConflictError("Idempotency-Key was already used with a different request")
        return existing

    values = json.loads(json.dumps(payload, default=str))
    policy_id = uuid.uuid4()
    values["policy_id"] = str(policy_id)
    values["policy_number"] = f"AR-{date.today():%Y}-{policy_id.hex[:10].upper()}"
    party_ids = {uuid.UUID(values["holder_party_id"])}
    for risk in values["risks"]:
        party_ids.add(uuid.UUID(risk["tenant_party_id"]))
        party_ids.add(uuid.UUID(risk["landlord_party_id"]))
    _check_parties(session, party_ids)
    if (
        values["policy_type"] == "INDIVIDUAL"
        and values["risks"][0]["tenant_party_id"] != values["holder_party_id"]
    ):
        raise ValidationError(
            "for an individual policy the holder and tenant must be the same party"
        )
    for index, risk in enumerate(values["risks"], start=1):
        risk["risk_number"] = index
        risk["monthly_rent"] = str(Decimal(risk["monthly_rent"]))
    values["duration_months"] = int(values["duration_months"])
    operation, created = _new_operation(
        session,
        operation_type="policy.create",
        consumer_scope=consumer_scope,
        idempotency_key=idempotency_key,
        request_payload=values,
        request_hash=request_hash,
        policy_id=None,
        correlation_id=correlation_id,
        actor=actor,
        demo_scenario=demo_scenario,
    )
    operation.request_payload = values
    return _commit_operation(session, operation, created)


def request_risk_addition(
    session: Session,
    *,
    policy_id: uuid.UUID,
    payload: dict,
    consumer_scope: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
    demo_scenario: str,
) -> Operation:
    request_hash = _canonical_hash({"policy_id": str(policy_id), "risk": payload})
    existing = _check_existing_idempotent(
        session,
        scope=consumer_scope,
        operation_type="risk.create",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing:
        return existing
    _get_locked_policy(session, policy_id)
    _ensure_no_pending_policy_operation(session, policy_id)
    policy = session.get(Policy, policy_id)
    active_count = session.scalar(
        select(func.count())
        .select_from(Risk)
        .where(Risk.policy_id == policy_id, Risk.status == "ACTIVE")
    )
    if policy.policy_type == "INDIVIDUAL" and active_count:
        raise ConflictError("an individual policy can have exactly one active risk")
    current_period = session.scalar(
        select(PolicyPeriod).where(
            PolicyPeriod.policy_id == policy_id, PolicyPeriod.status == "CURRENT"
        )
    )
    if current_period is None or current_period.starts_on != date.today():
        raise ConflictError("demo risk additions are supported only on a policy period start date")
    _check_parties(
        session, {uuid.UUID(payload["tenant_party_id"]), uuid.UUID(payload["landlord_party_id"])}
    )
    values = dict(payload)
    values["policy_id"] = str(policy_id)
    values["risk_number"] = (
        session.scalar(
            select(func.coalesce(func.max(Risk.risk_number), 0)).where(Risk.policy_id == policy_id)
        )
        + 1
    )
    operation, created = _new_operation(
        session,
        operation_type="risk.create",
        consumer_scope=consumer_scope,
        idempotency_key=idempotency_key,
        request_payload=values,
        request_hash=request_hash,
        policy_id=policy_id,
        correlation_id=correlation_id,
        actor=actor,
        demo_scenario=demo_scenario,
    )
    return _commit_operation(session, operation, created)


def request_risk_cancellation(
    session: Session,
    *,
    policy_id: uuid.UUID,
    risk_id: uuid.UUID,
    consumer_scope: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
    demo_scenario: str,
) -> Operation:
    payload = {"policy_id": str(policy_id), "risk_id": str(risk_id)}
    request_hash = _canonical_hash(payload)
    existing = _check_existing_idempotent(
        session,
        scope=consumer_scope,
        operation_type="risk.cancel",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing:
        return existing
    _get_locked_policy(session, policy_id)
    _ensure_no_pending_policy_operation(session, policy_id)
    risk = session.scalar(
        select(Risk).where(Risk.id == risk_id, Risk.policy_id == policy_id).with_for_update()
    )
    if risk is None:
        raise NotFoundError("risk not found")
    if risk.status != "ACTIVE":
        raise ConflictError("risk is not active")
    active_count = session.scalar(
        select(func.count())
        .select_from(Risk)
        .where(Risk.policy_id == policy_id, Risk.status == "ACTIVE")
    )
    if active_count <= 1:
        raise ConflictError("the last active risk cannot be cancelled independently")
    operation, created = _new_operation(
        session,
        operation_type="risk.cancel",
        consumer_scope=consumer_scope,
        idempotency_key=idempotency_key,
        request_payload=payload,
        request_hash=request_hash,
        policy_id=policy_id,
        correlation_id=correlation_id,
        actor=actor,
        demo_scenario=demo_scenario,
    )
    return _commit_operation(session, operation, created)


def _renewal_snapshot(session: Session, policy_id: uuid.UUID, rate: IpcRate) -> dict:
    period = session.scalar(
        select(PolicyPeriod)
        .where(PolicyPeriod.policy_id == policy_id, PolicyPeriod.status.in_(("CURRENT", "EXPIRED")))
        .order_by(PolicyPeriod.sequence.desc())
        .limit(1)
    )
    if period is None:
        raise ConflictError("policy has no current period available for renewal")
    if rate.effective_on > period.ends_on:
        raise ValidationError("IPC rate is not effective by the renewal start date")
    active_risks = session.scalars(
        select(Risk)
        .where(Risk.policy_id == policy_id, Risk.status == "ACTIVE")
        .order_by(Risk.risk_number)
    ).all()
    previous_values = {
        row.risk_id: row
        for row in session.scalars(
            select(PeriodRiskValue).where(PeriodRiskValue.period_id == period.id)
        ).all()
    }
    risk_values = []
    for risk in active_risks:
        previous = previous_values.get(risk.id)
        if previous is None:
            raise ConflictError("current period is missing an active risk economic value")
        prior_rent = Decimal(previous.monthly_rent)
        new_rent = renewed_rent(prior_rent, Decimal(rate.rate))
        risk_values.append(
            {
                "risk_id": str(risk.id),
                "previous_monthly_rent": str(prior_rent),
                "monthly_rent": str(new_rent),
                "premium": str(premium(new_rent, period.duration_months)),
            }
        )
    return {
        "source_period_id": str(period.id),
        "sequence": period.sequence + 1,
        "starts_on": period.ends_on.isoformat(),
        "ends_on": add_months(period.ends_on, period.duration_months).isoformat(),
        "duration_months": period.duration_months,
        "ipc_rate_id": str(rate.id),
        "ipc_rate": str(rate.rate),
        "ipc_reference": rate.reference_period,
        "calculation_version": "rent-ipc-v1",
        "currency": "COP",
        "risk_values": risk_values,
    }


def request_renewal(
    session: Session,
    *,
    policy_id: uuid.UUID,
    ipc_rate_id: uuid.UUID | None,
    consumer_scope: str,
    idempotency_key: str,
    correlation_id: str,
    actor: str,
    demo_scenario: str,
) -> Operation:
    raw_payload = {
        "policy_id": str(policy_id),
        "ipc_rate_id": str(ipc_rate_id) if ipc_rate_id else None,
    }
    request_hash = _canonical_hash(raw_payload)
    existing = _check_existing_idempotent(
        session,
        scope=consumer_scope,
        operation_type="policy.renewal",
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing:
        return existing
    _get_locked_policy(session, policy_id)
    _ensure_no_pending_policy_operation(session, policy_id)
    scheduled = session.scalar(
        select(PolicyPeriod.id).where(
            PolicyPeriod.policy_id == policy_id, PolicyPeriod.status == "SCHEDULED"
        )
    )
    if scheduled:
        raise ConflictError("policy already has a scheduled renewal")
    current = session.scalar(
        select(PolicyPeriod)
        .where(PolicyPeriod.policy_id == policy_id, PolicyPeriod.status.in_(("CURRENT", "EXPIRED")))
        .order_by(PolicyPeriod.sequence.desc())
        .limit(1)
    )
    if current is None:
        raise ConflictError("policy has no confirmed period")
    target_start = current.ends_on
    values = dict(raw_payload)
    status = OperationStatus.WAITING_INPUT.value
    if ipc_rate_id:
        rate = session.get(IpcRate, ipc_rate_id)
        if rate is None:
            raise NotFoundError("IPC rate not found")
        values["renewal"] = _renewal_snapshot(session, policy_id, rate)
        status = OperationStatus.PENDING.value
    operation, created = _new_operation(
        session,
        operation_type="policy.renewal",
        consumer_scope=consumer_scope,
        idempotency_key=idempotency_key,
        request_payload=values,
        request_hash=request_hash,
        policy_id=policy_id,
        correlation_id=correlation_id,
        actor=actor,
        demo_scenario=demo_scenario,
        status=status,
        target_period_start=target_start,
    )
    return _commit_operation(session, operation, created)


def resume_renewal(
    session: Session,
    *,
    operation_id: uuid.UUID,
    ipc_rate_id: uuid.UUID,
    actor: str,
    correlation_id: str,
) -> Operation:
    operation = session.scalar(
        select(Operation).where(Operation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise NotFoundError("operation not found")
    if (
        operation.operation_type != "policy.renewal"
        or operation.status != OperationStatus.WAITING_INPUT.value
    ):
        raise ConflictError("operation is not waiting for an IPC rate")
    rate = session.get(IpcRate, ipc_rate_id)
    if rate is None:
        raise NotFoundError("IPC rate not found")
    assert operation.policy_id is not None
    payload = dict(operation.request_payload)
    payload["ipc_rate_id"] = str(ipc_rate_id)
    payload["renewal"] = _renewal_snapshot(session, operation.policy_id, rate)
    operation.request_payload = payload
    operation.status = OperationStatus.PENDING.value
    operation.error_code = None
    operation.error_message = None
    _queue_event(session, operation)
    _audit(
        session,
        actor=actor,
        action="operation.input_supplied",
        entity_type="operation",
        entity_id=str(operation.id),
        correlation_id=correlation_id,
        details={"input": "ipc_rate_id"},
    )
    session.commit()
    session.refresh(operation)
    return operation


def create_ipc_rate(session: Session, payload: dict) -> IpcRate:
    rate = IpcRate(**payload)
    session.add(rate)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("IPC reference period already exists") from exc
    session.refresh(rate)
    return rate


def list_due_renewals(session: Session, *, due_before: date) -> list[tuple[uuid.UUID, date]]:
    periods = session.scalars(
        select(PolicyPeriod)
        .join(Policy, Policy.id == PolicyPeriod.policy_id)
        .where(
            Policy.status == "ACTIVE",
            PolicyPeriod.status.in_(("CURRENT", "EXPIRED")),
            PolicyPeriod.ends_on <= due_before,
        )
        .order_by(PolicyPeriod.ends_on)
        .limit(100)
    ).all()
    return [
        (period.policy_id, period.ends_on)
        for period in periods
        if not session.scalar(
            select(PolicyPeriod.id).where(
                PolicyPeriod.policy_id == period.policy_id,
                PolicyPeriod.sequence > period.sequence,
            )
        )
    ]


def latest_applicable_rate(session: Session, on_date: date) -> IpcRate | None:
    return session.scalar(
        select(IpcRate)
        .where(IpcRate.effective_on <= on_date)
        .order_by(IpcRate.effective_on.desc(), IpcRate.created_at.desc())
        .limit(1)
    )
