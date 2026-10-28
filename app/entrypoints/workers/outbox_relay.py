import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.adapters.persistence.models import OutboxEvent
from app.adapters.persistence.session import SessionLocal
from app.celery_app import celery

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("outbox_relay")
BATCH_SIZE = 50
LEASE_SECONDS = 60


def claim_batch() -> list[dict]:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.available_at <= now,
                or_(OutboxEvent.lease_until.is_(None), OutboxEvent.lease_until < now),
            )
            .order_by(OutboxEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(BATCH_SIZE)
        ).all()
        claimed = []
        for event in events:
            event.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            event.attempts += 1
            claimed.append(
                {"id": str(event.id), "event_type": event.event_type, "payload": event.payload}
            )
        session.commit()
        return claimed


def publish(event: dict) -> None:
    if event["event_type"] in {"operation.requested", "operation.reconcile"}:
        task_name = "app.tasks.process_operation"
        args = [event["payload"]["operation_id"]]
    elif event["event_type"] == "notification.confirmed":
        task_name = "app.tasks.deliver_notification"
        args = [event["id"]]
    else:
        raise ValueError(f"unsupported outbox event type: {event['event_type']}")
    celery.send_task(task_name, args=args, queue="policy.operations", retry=False)


def mark_published(event_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        event = session.get(OutboxEvent, event_id)
        if event and event.published_at is None:
            event.published_at = datetime.now(UTC)
            event.lease_until = None
            session.commit()


def release_for_retry(event_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        event = session.get(OutboxEvent, event_id)
        if event and event.published_at is None:
            delay = min(300, 2 ** min(event.attempts, 8))
            event.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            event.lease_until = None
            session.commit()


def run() -> None:
    logger.info(json.dumps({"event": "outbox_relay_started"}))
    while True:
        events = claim_batch()
        if not events:
            time.sleep(1)
            continue
        for event in events:
            try:
                publish(event)
                mark_published(uuid.UUID(event["id"]))
            except Exception:
                logger.exception(
                    "outbox publish failed event_id=%s type=%s",
                    event["id"],
                    event["event_type"],
                )
                release_for_retry(uuid.UUID(event["id"]))


if __name__ == "__main__":
    run()
