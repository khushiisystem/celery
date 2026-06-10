import logging

from celery import Celery

from app.config import get_settings

APP_NAME = "s3_processor"
TASK_MODULES = [
    "app.tasks",
]
PROCESS_TASK_NAME = "background_tasks.execute_task"


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    )



def create_celery_app() -> Celery:
    """
    Create the Celery application used by this service.

    This function owns Celery's duties only:
    - connect to the broker
    - discover task modules
    - configure queue behavior
    - accept JSON payloads from the API
    - avoid storing task results because S3 + Django callback are the source of truth
    """
    settings = get_settings()
    default_queue = settings.celery_default_queue

    app = Celery(
        APP_NAME,
        broker=settings.celery_broker_url,
        include=TASK_MODULES,
    )

    app.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue=default_queue,
        task_routes={
            PROCESS_TASK_NAME: {"queue": default_queue},
        },
        task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
        task_time_limit=settings.celery_task_time_limit_seconds,
        timezone="UTC",
        enable_utc=True,
    )

    return app


configure_logging()
celery_app = create_celery_app()
