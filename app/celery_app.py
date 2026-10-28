from celery import Celery

from app.config import settings

celery = Celery("rental_policies", broker=settings.rabbitmq_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"confirm_publish": True},
    task_default_queue="policy.operations",
    task_default_delivery_mode="persistent",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "advance-policy-periods": {
            "task": "app.tasks.advance_policy_periods",
            "schedule": 60.0,
        },
        "scan-due-renewals": {
            "task": "app.tasks.scan_due_renewals",
            "schedule": 3600.0,
        },
        "recover-abandoned-operations": {
            "task": "app.tasks.recover_abandoned_operations",
            "schedule": 30.0,
        },
    },
    imports=["app.tasks"],
)
