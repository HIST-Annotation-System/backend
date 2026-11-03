"""Only features both Redis and SQS support, so the broker can be swapped through the environment alone: no result
backend, no remote control, no events, no ETA tasks."""

from celery import Celery

from app.core.config import Settings


def build_celery_app(settings: Settings) -> Celery:
    app = Celery("backend", broker=settings.CELERY_BROKER_URL)
    app.conf.update(
        broker_transport_options=settings.CELERY_BROKER_TRANSPORT_OPTIONS,
        broker_connection_retry_on_startup=True,
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
        enable_utc=True,
    )
    return app
