from typing import Any

from celery.utils.log import get_task_logger

from app.callback import send_callback
from app.celery_app import PROCESS_TASK_NAME, celery_app
from app.config import get_settings
from app.processor import process_assessment_payload
from app.s3_utils import default_s3_bucket, read_s3_json, upload_result_json
from app.schemas import AssessmentHandoffPayload, CallbackPayload, S3ObjectRef

logger = get_task_logger(__name__)
settings = get_settings()


def run_assessment_handoff(payload: AssessmentHandoffPayload) -> str:
    logger.info("Starting assessment handoff: assignment_id=%s ai_assessment_id=%s candidate_id=%s input_s3_key=%s output_prefix=%s", payload.assignment_id, payload.ai_assessment_id, payload.candidate_id, payload.input_s3_key or (payload.input_s3.key if payload.input_s3 else None), payload.output_prefix)
    if payload.input_s3 is None and not payload.input_s3_key:
        raise ValueError("Payload must include either input_s3 or input_s3_key")
    input_s3 = payload.input_s3 or S3ObjectRef(
        bucket=default_s3_bucket(),
        key=payload.input_s3_key,
    )
    assessment_payload = read_s3_json(input_s3)
    logger.debug("Assessment payload loaded: assignment_id=%s question_count=%s", assessment_payload.get("assignment_id"), len(assessment_payload.get("questions") or []))
    metadata = {
        **payload.metadata,
        "candidate_id": payload.candidate_id,
        "assignment_id": payload.assignment_id,
        "ai_assessment_id": payload.ai_assessment_id,
        "input_bucket": input_s3.bucket,
        "input_s3_key": input_s3.key,
    }
    result = process_assessment_payload(
        assessment_payload=assessment_payload,
        metadata=metadata,
    )

    result_s3_key = upload_result_json(
        result=result,
        bucket=input_s3.bucket,
        output_prefix=payload.output_prefix,
        assignment_id=payload.assignment_id,
        ai_assessment_id=payload.ai_assessment_id,
        candidate_id=payload.candidate_id,
    )
    logger.info("Assessment handoff completed: assignment_id=%s result_s3_key=%s", payload.assignment_id, result_s3_key)
    return result_s3_key


TASK_HANDLERS = {
    "process_ai_assessment_s3_handoff": run_assessment_handoff,
}


def resolve_task_handler(task_name: str):
    try:
        return TASK_HANDLERS[task_name]
    except KeyError as exc:
        supported = ", ".join(sorted(TASK_HANDLERS))
        raise ValueError(f"Unsupported task_name '{task_name}'. Supported tasks: {supported}") from exc


def notify_django_success(payload: AssessmentHandoffPayload, result_s3_key: str) -> None:
    send_callback(
        str(payload.callback_url),
        CallbackPayload(
            result_s3_key=result_s3_key,
            metadata={
                "assignment_id": payload.assignment_id,
                "ai_assessment_id": payload.ai_assessment_id,
            },
        ),
    )


def notify_django_failure(payload: AssessmentHandoffPayload, exc: Exception) -> None:
    try:
        send_callback(
            str(payload.callback_url),
            CallbackPayload(
                error=str(exc),
                metadata={
                    "assignment_id": payload.assignment_id,
                    "ai_assessment_id": payload.ai_assessment_id,
                },
            ),
        )
    except Exception:
        logger.exception(
            "Failed to send failure callback for assignment %s",
            payload.assignment_id,
        )


@celery_app.task(
    bind=True,
    name=PROCESS_TASK_NAME,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.celery_task_max_retries,
)
def execute_task(self, task_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("Received Celery task: task_name=%s celery_task_id=%s payload_keys=%s", task_name, self.request.id, sorted((payload or {}).keys()))
    handler = resolve_task_handler(task_name)
    handoff_payload = AssessmentHandoffPayload.model_validate(payload)

    try:
        result_s3_key = handler(handoff_payload)
        logger.info("Sending success callback: assignment_id=%s result_s3_key=%s", handoff_payload.assignment_id, result_s3_key)
        notify_django_success(handoff_payload, result_s3_key)
        return {
            "status": "success",
            "assignment_id": handoff_payload.assignment_id,
            "ai_assessment_id": handoff_payload.ai_assessment_id,
            "result_s3_key": result_s3_key,
        }
    except Exception as exc:
        logger.exception(
            "Task %s failed for assignment %s",
            task_name,
            handoff_payload.assignment_id,
        )
        notify_django_failure(handoff_payload, exc)
        raise
