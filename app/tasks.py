import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.adapters.persistence.models import (
    Operation,
    OperationStatus,
    OutboxEvent,
    PolicyPeriod,
)
from app.adapters.persistence.session import SessionLocal
from app.application.operation_processor import (
    deliver_notification,
    process_operation,
)
from app.application.policy_operations import (
    latest_applicable_rate,
    list_due_renewals,
    request_renewal,
)
from app.celery_app import celery
from app.config import settings

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.process_operation")
def process_operation_task(operation_id: str) -> dict:
    try:
        return process_operation(operation_id)
    except Exception as exc:
        logger.exception("operation worker failed operation_id=%s", operation_id)
        try:
            from app.application.operation_processor import _set_failed

            return _set_failed(
                uuid.UUID(operation_id),
                OperationStatus.NEEDS_REVIEW.value,
                "WORKER_UNEXPECTED_ERROR",
                type(exc).__name__,
            )
        except Exception:
            raise


@celery.task(name="app.tasks.deliver_notification")
def deliver_notification_task(event_id: str) -> dict:
    return deliver_notification(event_id)


@celery.task(name="app.tasks.advance_policy_periods")
def advance_policy_periods() -> dict:
    today = date.today()
    advanced = 0
    with SessionLocal() as session:
        periods = session.scalars(
            select(PolicyPeriod)
            .where(PolicyPeriod.status == "SCHEDULED", PolicyPeriod.starts_on <= today)
            .with_for_update(skip_locked=True)
        ).all()
        for period in periods:
            if period.source_period_id:
                source = session.get(PolicyPeriod, period.source_period_id)
                if source:
                    source.status = "EXPIRED"
            period.status = "CURRENT"
            advanced += 1
        expired = session.scalars(
            select(PolicyPeriod)
            .where(PolicyPeriod.status == "CURRENT", PolicyPeriod.ends_on <= today)
            .with_for_update(skip_locked=True)
        ).all()
        for period in expired:
            period.status = "EXPIRED"
        session.commit()
    return {"activated": advanced, "expired": len(expired)}


@celery.task(name="app.tasks.scan_due_renewals")
def scan_due_renewals() -> dict:
    cutoff = date.today() + timedelta(days=settings.renewal_days_ahead)
    created_or_existing = 0
    with SessionLocal() as session:
        due = list_due_renewals(session, due_before=cutoff)
    for policy_id, period_end in due:
        with SessionLocal() as session:
            rate = latest_applicable_rate(session, period_end)
            try:
                operation = request_renewal(
                    session,
                    policy_id=policy_id,
                    ipc_rate_id=rate.id if rate else None,
                    consumer_scope="renewal-scheduler",
                    idempotency_key=f"renewal:{policy_id}:{period_end.isoformat()}",
                    correlation_id=f"scheduler-{policy_id}",
                    actor="renewal-scheduler",
                    demo_scenario="success",
                )
                created_or_existing += 1
                logger.info(
                    "renewal scan policy_id=%s operation_id=%s status=%s",
                    policy_id,
                    operation.id,
                    operation.status,
                )
            except Exception:
                session.rollback()
                logger.exception("renewal scan rejected policy_id=%s", policy_id)
    return {"operations": created_or_existing, "cutoff": cutoff.isoformat()}


@celery.task(name="app.tasks.recover_abandoned_operations")
def recover_abandoned_operations() -> dict:
    now = datetime.now(UTC)
    recovered = 0
    with SessionLocal() as session:
        operations = session.scalars(
            select(Operation)
            .where(
                Operation.status == OperationStatus.PROCESSING.value,
                Operation.lease_until < now,
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for operation in operations:
            operation.status = OperationStatus.RECONCILING.value
            operation.lease_until = None
            session.add(
                OutboxEvent(
                    operation_id=operation.id,
                    event_type="operation.reconcile",
                    payload={"operation_id": str(operation.id)},
                )
            )
            recovered += 1
        session.commit()
    return {"recovery_events": recovered}
