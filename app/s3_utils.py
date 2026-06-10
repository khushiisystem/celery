from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
import os
import tempfile
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from app.config import get_settings
from app.schemas import ProcessingResult, S3ObjectRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class S3Location:
    bucket: str
    key: str


def parse_s3_uri(uri: str) -> S3Location:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {uri}")

    return S3Location(bucket=parsed.netloc, key=parsed.path.lstrip("/"))


def s3_client():
    settings = get_settings()
    region = settings.aws_s3_region_name or settings.aws_region
    kwargs = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "region_name": region,
        "config": Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    }
    if settings.aws_s3_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_s3_endpoint_url
    return boto3.client("s3", **kwargs)


def default_s3_bucket() -> str:
    settings = get_settings()
    if not settings.aws_storage_bucket_name:
        raise RuntimeError(
            "AWS_STORAGE_BUCKET_NAME is required when S3 bucket is not in payload"
        )
    return settings.aws_storage_bucket_name


def public_s3_url(bucket: str, key: str) -> str:
    settings = get_settings()
    region = settings.aws_s3_region_name or settings.aws_region
    if region == "us-east-1":
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def read_s3_bytes(s3_path: str) -> bytes:
    location = parse_s3_uri(s3_path)
    response = s3_client().get_object(Bucket=location.bucket, Key=location.key)
    return response["Body"].read()


def read_s3_json(s3_ref: S3ObjectRef) -> dict:
    logger.debug("Reading S3 JSON: bucket=%s key=%s", s3_ref.bucket, s3_ref.key)
    response = s3_client().get_object(Bucket=s3_ref.bucket, Key=s3_ref.key)
    payload = json.loads(response["Body"].read())
    logger.info(
        "Loaded S3 input manifest: bucket=%s key=%s keys=%s",
        s3_ref.bucket,
        s3_ref.key,
        sorted(payload.keys()),
    )
    return payload


def build_result_key(
    output_prefix: str,
    assignment_id: int,
    ai_assessment_id: int | str,
    candidate_id: int | str | None = None,
    ingestion_datetime: datetime | None = None,
) -> str:
    if candidate_id not in (None, ""):
        return f"assessment/{ai_assessment_id}/{candidate_id}/celery_op/results/result.json"

    prefix = output_prefix.rstrip("/")
    return f"{prefix}/result.json" if prefix else "result.json"


def download_s3_file(bucket: str, key: str, suffix: str = ".webm") -> str:
    local = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    local.close()
    try:
        logger.debug(
            "Downloading S3 file: bucket=%s key=%s temp=%s", bucket, key, local.name
        )
        s3_client().download_file(bucket, key, local.name)
        logger.info(
            "Downloaded S3 file: bucket=%s key=%s temp=%s", bucket, key, local.name
        )
        return local.name
    except Exception:
        try:
            os.unlink(local.name)
        except OSError:
            pass
        raise


def upload_result_json(
    result: ProcessingResult,
    bucket: str,
    output_prefix: str,
    assignment_id: int,
    ai_assessment_id: int | str,
    candidate_id: int | str | None = None,
) -> str:
    settings = get_settings()
    ingestion_datetime = datetime.now(timezone.utc)
    result_key = build_result_key(
        output_prefix,
        assignment_id,
        ai_assessment_id,
        candidate_id=candidate_id,
        ingestion_datetime=ingestion_datetime,
    )
    result_body = result.model_dump(mode="json", exclude_none=True)
    result_body.setdefault("metadata", {})[
        "ingestion_datetime"
    ] = ingestion_datetime.isoformat()
    result_body["metadata"]["result_s3_key"] = result_key

    put_kwargs = {
        "Bucket": bucket,
        "Key": result_key,
        "Body": json.dumps(result_body).encode("utf-8"),
        "ContentType": "application/json",
        "Metadata": {key: str(value) for key, value in result.metadata.items()},
    }

    if settings.s3_output_acl:
        put_kwargs["ACL"] = settings.s3_output_acl

    logger.info(
        "Uploading result JSON: bucket=%s key=%s assignment_id=%s ai_assessment_id=%s candidate_id=%s",
        bucket,
        result_key,
        assignment_id,
        ai_assessment_id,
        candidate_id,
    )
    s3_client().put_object(**put_kwargs)
    logger.info("Uploaded result JSON: bucket=%s key=%s", bucket, result_key)

    return result_key
