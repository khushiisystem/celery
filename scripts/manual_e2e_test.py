import argparse
import json
import mimetypes
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.s3_utils import default_s3_bucket, public_s3_url, s3_client
from app.tasks import execute_task


def upload_file(bucket: str, key: str, path: Path) -> dict:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    s3_client().upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return {
        "bucket": bucket,
        "key": key,
        "url": public_s3_url(bucket, key),
    }


def upload_json(bucket: str, key: str, payload: dict) -> dict:
    s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return {
        "bucket": bucket,
        "key": key,
        "url": public_s3_url(bucket, key),
    }


def build_assessment_payload(
    *,
    assignment_id: int,
    ai_assessment_id: int,
    audio_ref: dict,
) -> dict:
    return {
        "schema_version": "manual-e2e-v1",
        "candidate_id": 999,
        "candidate_name": "Manual Test Candidate",
        "candidate_email": "manual.test@example.com",
        "assignment_id": assignment_id,
        "ai_assessment_id": ai_assessment_id,
        "resume_text": "Python developer with Django, Celery, Redis, S3, and REST API experience.",
        "generated_questions": [
            {
                "question_number": 1,
                "question_text": "Explain how Celery works with Redis and why S3 is useful in this architecture.",
                "question_type": "text",
            }
        ],
        "assessment": {
            "title": "Manual E2E Assessment",
            "role_type": "Software Engineer",
            "experience_level": "2-5 years",
            "num_questions": 1,
        },
        "analysis": {
            "gesture_analysis": None,
            "communication_metrics": {},
            "communication_score": 0,
        },
        "questions": [
            {
                "candidate_id": 999,
                "assignment_id": assignment_id,
                "ai_assessment_id": ai_assessment_id,
                "question_id": "manual-q1",
                "question_number": 1,
                "question_text": "Explain how Celery works with Redis and why S3 is useful in this architecture.",
                "question_type": "text",
                "answer_text": "",
                "audio": audio_ref,
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Upload a production-like S3 handoff payload and dispatch the Celery task."
    )
    parser.add_argument(
        "--audio-file",
        required=True,
        help="Path to a local .webm/.wav/.mp3 audio answer file",
    )
    parser.add_argument(
        "--callback-url",
        required=True,
        help="Callback URL, e.g. http://127.0.0.1:9000/callback",
    )
    parser.add_argument("--assignment-id", type=int, default=123)
    parser.add_argument("--ai-assessment-id", type=int, default=55)
    parser.add_argument(
        "--bucket", default=None, help="S3 bucket. Defaults to AWS_STORAGE_BUCKET_NAME"
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="S3 prefix. Defaults to candidate/celery_op/assignments/<assignment-id>",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio_file).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    bucket = args.bucket or default_s3_bucket()
    prefix = args.prefix or f"candidate/celery_op/assignments/{args.assignment_id}"
    input_prefix = f"{prefix.rstrip('/')}/input"
    output_prefix = f"{prefix.rstrip('/')}/results"

    audio_key = f"{input_prefix}/audio/q1{audio_path.suffix or '.webm'}"
    input_s3_key = f"{input_prefix}/assessment_payload.json"

    print(f"Uploading audio to s3://{bucket}/{audio_key}")
    audio_ref = upload_file(bucket, audio_key, audio_path)

    assessment_payload = build_assessment_payload(
        assignment_id=args.assignment_id,
        ai_assessment_id=args.ai_assessment_id,
        audio_ref=audio_ref,
    )

    print(f"Uploading manifest to s3://{bucket}/{input_s3_key}")
    input_ref = upload_json(bucket, input_s3_key, assessment_payload)

    task_payload = {
        "assignment_id": args.assignment_id,
        "ai_assessment_id": args.ai_assessment_id,
        "input_s3": input_ref,
        "input_s3_key": input_s3_key,
        "output_prefix": output_prefix,
        "callback_url": args.callback_url,
        "metadata": {
            "manual_test": True,
        },
    }

    print("Dispatching Celery task to queue 'assessment'")
    task = execute_task.apply_async(
        kwargs={
            "task_name": "process_ai_assessment_s3_handoff",
            "payload": task_payload,
        },
        queue="assessment",
    )

    result_key = f"{output_prefix}/result.json"
    print("\nSubmitted.")
    print(f"task_id: {task.id}")
    print(f"input_s3_key: {input_s3_key}")
    print(f"expected_result_s3_key: {result_key}")
    print(f"expected_result_s3_url: {public_s3_url(bucket, result_key)}")


if __name__ == "__main__":
    main()
