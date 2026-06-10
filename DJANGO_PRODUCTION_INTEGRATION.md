# Django Production Integration

Django project path:

```text
/home/dell-l-75/ZecdataPoral/TranningAndCertification
```

Celery project path:

```text
/home/dell-l-75/Desktop/akshay/Akshay/celery_task
```

## Contract

Django publishes this Celery task through the shared Redis broker:

```text
background_tasks.execute_task
queue: assessment
```

The task payload contains:

```json
{
  "task_name": "process_ai_assessment_s3_handoff",
  "payload": {
    "assignment_id": 123,
    "ai_assessment_id": 45,
    "candidate_id": 67,
    "input_s3_key": "<django-generated-input-key>",
    "output_prefix": "<django-generated-output-prefix>",
    "callback_url": "https://assessment.zecdata.com/v1/ai-assessment/celery-callback/?secret=..."
  }
}
```

## S3 Layout

Django keeps writing input handoff files to its existing S3 path. Do not change that path in Django.

Only the final Celery result is forced into this structure:

```text
ai_assessment/celery_op/<assignment-id>/<ai-assessment-id>/<candidate-id>/results/results_<ingestion-datetime>.json
```

## Run

Use the same Redis URL in Django and this Celery app. For local compose inside this repo:

```bash
docker compose up --build redis worker api
```

For a production worker process:

```bash
celery -A app.celery_app.celery_app worker --loglevel=info -Q assessment
```

Django remains the only database writer. This worker reads S3 input, transcribes audio, generates AI feedback, writes timestamped `results_<ingestion-datetime>.json`, then POSTs the result key to Django's callback endpoint.
