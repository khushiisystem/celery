# Celery S3 Assessment Worker

Small Celery worker service for the Django AI assessment S3 handoff flow.

The Django app sends a small Celery message. The large assessment payload stays in S3.

## Flow

1. Django writes `assessment_payload.json` to S3.
2. Django sends Celery task `background_tasks.execute_task` on queue `assessment`.
3. This worker downloads the input JSON from S3.
4. This worker runs assessment processing logic inside this repo.
5. This worker uploads result JSON to S3 under `output_prefix`.
6. This worker calls Django `callback_url` with `result_s3_key`.
7. Django downloads the result JSON and applies it to its database.

## Project Structure

```text
.
├── app/
│   ├── api.py              # Optional local/manual task submit endpoint
│   ├── ai_utils.py         # Gemini + Faster Whisper utilities
│   ├── callback.py         # Sends result key back to Django
│   ├── celery_app.py       # Celery app, queue, routing, worker config
│   ├── config.py           # Environment settings
│   ├── processor.py        # Assessment processing/scoring/feedback logic
│   ├── report_generation.py # Gemini report/feedback generation
│   ├── schemas.py          # Shared payload/result models
│   ├── s3_utils.py         # S3 JSON read/write helpers
│   └── tasks.py            # Celery task entrypoint and orchestration
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Celery Contract

Django should send:

```text
task name: background_tasks.execute_task
queue: assessment
kwargs:
  task_name: process_ai_assessment_s3_handoff
  payload: {...}
```

Payload shape:

```json
{
  "task_name": "process_ai_assessment_s3_handoff",
  "payload": {
    "candidate_id": 1,
    "candidate_name": "Candidate Name",
    "candidate_email": "candidate@example.com",
    "assignment_id": 123,
    "ai_assessment_id": 456,
    "input_s3": {
      "bucket": "my-bucket",
      "key": "path/input/assessment_payload.json",
      "url": "https://optional-url"
    },
    "input_s3_key": "path/input/assessment_payload.json",
    "output_prefix": "path/results",
    "callback_url": "https://django.example.com/v1/ai-assessment/celery-callback/?secret=...",
    "metadata": {}
  }
}
```

## Django Callback

On success, this worker sends:

```json
{
  "result_s3_key": "path/results/assessment-456-assignment-123-result.json",
  "metadata": {
    "assignment_id": 123,
    "ai_assessment_id": 456
  }
}
```

On failure, this worker sends:

```json
{
  "error": "error details",
  "metadata": {
    "assignment_id": 123,
    "ai_assessment_id": 456
  }
}
```

## Result JSON Written To S3

This worker writes the result to:

```text
{output_prefix}/result.json
```

Example:

```text
s3://my-bucket/candidate/celery_op/assignments/123/results/result.json
```

The result JSON looks like:

```json
{
  "result": {
    "assignment_id": 123,
    "responses": [
      {
        "question_id": 10,
        "question_number": 1,
        "question_text": "Explain your Python experience.",
        "transcript": "candidate answer text",
        "answer_text": "candidate answer text",
        "score": 7.5,
        "status": "average"
      }
    ],
    "ai_feedback": "generated feedback text",
    "overall_score": 7.5,
    "technical_score": 7.5,
    "communication_score": 10.0,
    "problem_solving_score": 7.5
  },
  "question_results": [
    {
      "question_id": 10,
      "question_number": 1,
      "question_text": "Explain your Python experience.",
      "question_type": "text",
      "answer_text": "candidate answer text",
      "transcript": "candidate answer text",
      "score": 7.5,
      "status": "average",
      "audio_s3_key": "path/audio.webm"
    }
  ],
  "metadata": {
    "processor": "local_assessment_processor",
    "questions_received": 1,
    "answers_processed": 1
  }
}
```

## Where To Add Real Logic

Most future work should go in `app/processor.py`. This repo should own the
processing. Do not import Django models here unless you intentionally want to
couple the worker back to Django.

Keep this function signature stable:

```python
def process_assessment_payload(
    assessment_payload: dict[str, Any],
    metadata: dict[str, Any],
) -> ProcessingResult:
```

Current processor behavior:

```text
1. Read questions/responses from the S3 manifest.
2. If answer text is missing and audio.key exists, download audio from S3.
3. Transcribe audio with Faster Whisper.
4. Normalize question text, answer text, transcript, question number, and audio key.
5. Calculate deterministic baseline scores.
6. Generate Gemini feedback when Google credentials are configured.
7. Fall back to local feedback text if Gemini is unavailable.
8. Return result + question_results for upload to S3.
```

Replace the scoring helpers in `app/processor.py` when you add your final
business rules. Gemini feedback logic lives in `app/report_generation.py`.

Return a `ProcessingResult` with the result shape Django expects:

```python
return ProcessingResult(
    result={
        "assignment_id": assessment_payload["assignment_id"],
        "responses": processed_responses,
        "ai_feedback": generated_feedback,
        "overall_score": overall_score,
        "technical_score": technical_score,
        "communication_score": communication_score,
        "problem_solving_score": problem_solving_score,
    },
    question_results=question_results,
    metadata={"processor": "real_assessment_processor"},
)
```

Only change `app/tasks.py` if the workflow changes, such as adding more task
names, callbacks, or multiple output files.

## Local Setup

Copy env file:

```bash
cp .env.example .env
```

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install system dependency for audio conversion:

```bash
sudo apt-get install ffmpeg
```

Start Redis:

```bash
docker compose up redis
```

Run worker:

```bash
celery -A app.celery_app.celery_app worker --loglevel=info -Q assessment
```

Optional local API for manual testing:

```bash
uvicorn app.api:app --reload --host 0.0.0.0 --port 8000
```

Or run everything with Docker Compose:

```bash
docker compose up --build
```
