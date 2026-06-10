from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class S3ObjectRef(BaseModel):
    bucket: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1)
    url: str | None = None


class AssessmentHandoffPayload(BaseModel):
    candidate_id: int | str | None = None
    candidate_name: str | None = None
    candidate_email: str | None = None
    assignment_id: int
    ai_assessment_id: int | str
    input_s3: S3ObjectRef | None = None
    input_s3_key: str | None = None
    output_prefix: str = Field(..., min_length=1)
    callback_url: HttpUrl
    metadata: dict[str, Any] = Field(default_factory=dict)


class DispatchPayload(BaseModel):
    task_name: str = Field(..., min_length=1)
    payload: AssessmentHandoffPayload


class TaskAcceptedResponse(BaseModel):
    task_id: str | None = None
    task_name: str
    status: TaskStatus


class CallbackPayload(BaseModel):
    result_s3_key: str | None = None
    output_s3_key: str | None = None
    s3_key: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessingResult(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
    question_results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
