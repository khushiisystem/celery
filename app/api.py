from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.config import Settings, get_settings
from app.schemas import DispatchPayload, TaskAcceptedResponse, TaskStatus
from app.tasks import execute_task


app = FastAPI(title="Celery S3 Processing Service")


def verify_auth(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.api_auth_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization token",
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/tasks",
    response_model=TaskAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_auth)],
)
def submit_task(payload: DispatchPayload) -> TaskAcceptedResponse:
    task = execute_task.delay(
        task_name=payload.task_name,
        payload=payload.payload.model_dump(mode="json"),
    )
    return TaskAcceptedResponse(
        task_id=task.id,
        task_name=payload.task_name,
        status=TaskStatus.QUEUED,
    )
