import logging
import os
from typing import Any

from app.ai_utils import transcribe_audio
from app.report_generation import generate_ai_feedback_report
from app.schemas import ProcessingResult
from app.s3_utils import download_s3_file

logger = logging.getLogger(__name__)


def _first_present(source: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return default


def _merged_assessment_payload(assessment_payload: dict[str, Any]) -> dict[str, Any]:
    inner = assessment_payload.get("payload")
    if isinstance(inner, dict):
        return {**assessment_payload, **inner}
    return assessment_payload


def _collect_items(inner: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Support the common payload shapes Django may write to S3.
    Prefer `questions`, then `responses`, then `question_results`.
    """
    for key in ("questions", "responses", "question_results"):
        value = inner.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _answer_text(item: dict[str, Any]) -> str:
    value = _first_present(
        item,
        (
            "answer_text",
            "transcript",
            "text_response",
            "candidate_answer",
            "code_response",
            "selected_option",
        ),
        "",
    )
    if isinstance(value, list):
        return ", ".join(str(part) for part in value if part not in (None, ""))
    return str(value or "").strip()


def _audio_key(item: dict[str, Any]) -> str | None:
    audio = item.get("audio")
    if isinstance(audio, dict):
        return audio.get("key")
    return item.get("audio_s3_key")


def _transcribe_if_needed(item: dict[str, Any], input_bucket: str | None) -> tuple[str, str | None]:
    existing_answer = _answer_text(item)
    audio_key = _audio_key(item)
    if existing_answer:
        logger.info("Existing answer present; skipping transcription for question_number=%s", item.get("question_number"))
        return existing_answer, None
    if not audio_key:
        logger.info("No audio key; skipping transcription for question_number=%s", item.get("question_number"))
        return existing_answer, None
    if not input_bucket:
        return "", "Missing input bucket for audio transcription"

    local_audio = None
    try:
        logger.info("Transcribing audio: question_number=%s bucket=%s key=%s", item.get("question_number"), input_bucket, audio_key)
        local_audio = download_s3_file(input_bucket, audio_key, suffix=".webm")
        transcript = transcribe_audio(local_audio, method="whisper") or ""
        logger.info("Transcription finished: question_number=%s transcript_chars=%s", item.get("question_number"), len(transcript))
        return transcript, None
    except Exception as exc:
        logger.exception("Transcription failed: question_number=%s bucket=%s key=%s", item.get("question_number"), input_bucket, audio_key)
        return "", str(exc)
    finally:
        if local_audio and os.path.exists(local_audio):
            try:
                os.unlink(local_audio)
            except OSError:
                pass


def _question_text(item: dict[str, Any]) -> str:
    return str(
        _first_present(
            item,
            ("question_text", "question", "title", "prompt"),
            "",
        )
        or ""
    ).strip()


def _question_number(item: dict[str, Any], fallback: int) -> int:
    value = _first_present(item, ("question_number", "number", "index"), fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _keyword_overlap_score(question: str, answer: str) -> float:
    """
    Lightweight placeholder scoring.

    This is intentionally deterministic and dependency-free. Replace this with
    Gemini/model scoring later, but keep the returned result shape the same.
    """
    if not answer:
        return 0.0

    words = [word.strip(".,!?;:()[]{}").lower() for word in answer.split()]
    word_count = len([word for word in words if word])
    length_score = min(word_count / 45, 1.0) * 6.0

    question_terms = {
        word.strip(".,!?;:()[]{}").lower()
        for word in question.split()
        if len(word.strip(".,!?;:()[]{}")) >= 4
    }
    answer_terms = {word for word in words if len(word) >= 4}
    overlap = len(question_terms & answer_terms)
    overlap_score = min(overlap / 5, 1.0) * 4.0 if question_terms else 2.0

    return round(min(length_score + overlap_score, 10.0), 2)


def _score_label(score: float) -> str:
    if score >= 8:
        return "strong"
    if score >= 5:
        return "average"
    if score > 0:
        return "needs_improvement"
    return "not_answered"


def _build_question_result(
    item: dict[str, Any],
    position: int,
    input_bucket: str | None,
) -> dict[str, Any]:
    question = _question_text(item)
    answer, transcription_error = _transcribe_if_needed(item, input_bucket)
    score = _keyword_overlap_score(question, answer)

    return {
        "question_id": item.get("question_id") or item.get("id"),
        "question_number": _question_number(item, position),
        "question_text": question,
        "question_type": item.get("question_type") or item.get("type") or "text",
        "answer_text": answer,
        "transcript": answer or None,
        "transcription_status": "success"
        if answer
        else ("failed" if transcription_error else "no_audio_or_answer"),
        "transcription_error": transcription_error,
        "score": score,
        "status": _score_label(score),
        "audio_s3_key": _audio_key(item),
    }


def _aggregate_scores(question_results: list[dict[str, Any]]) -> dict[str, float]:
    if not question_results:
        return {
            "overall_score": 0.0,
            "technical_score": 0.0,
            "communication_score": 0.0,
            "problem_solving_score": 0.0,
        }

    scores = [float(item.get("score") or 0.0) for item in question_results]
    overall = round(sum(scores) / len(scores), 2)
    answered_count = len([item for item in question_results if item.get("answer_text")])
    communication = round((answered_count / len(question_results)) * 10, 2)

    return {
        "overall_score": overall,
        "technical_score": overall,
        "communication_score": communication,
        "problem_solving_score": overall,
    }


def _feedback_text(
    assessment_payload: dict[str, Any],
    question_results: list[dict[str, Any]],
    scores: dict[str, float],
) -> str:
    candidate_name = (
        assessment_payload.get("candidate_name")
        or (assessment_payload.get("candidate") or {}).get("name")
        or "The candidate"
    )
    total = len(question_results)
    answered = len([item for item in question_results if item.get("answer_text")])
    strong = len([item for item in question_results if item.get("status") == "strong"])
    missing = total - answered

    lines = [
        f"{candidate_name} completed {answered} out of {total} questions.",
        f"Overall score: {scores['overall_score']}/10.",
    ]
    if strong:
        lines.append(f"{strong} answer(s) were strong and well covered.")
    if missing:
        lines.append(f"{missing} question(s) had no usable answer.")
    lines.append(
        "This feedback was generated by the local Celery processor. Replace the "
        "scoring helpers in app/processor.py when model-based evaluation is added."
    )
    return "\n".join(lines)


def process_assessment_payload(
    assessment_payload: dict[str, Any],
    metadata: dict[str, Any],
) -> ProcessingResult:
    """
    Replace this function with your real AI assessment processing logic.

    This is the main function you should modify later. Keep its contract stable:
    receive the JSON loaded from Django's input S3 object and return a
    Django-compatible ProcessingResult.

    Django expects the uploaded result JSON to contain either:
    - result.ai_feedback and result.responses
    - question_results
    """
    payload = _merged_assessment_payload(assessment_payload)
    if not isinstance(payload, dict):
        raise ValueError("Invalid assessment payload")
    assignment_id = payload.get("assignment_id") or metadata.get("assignment_id")
    logger.info("Processing assessment payload: assignment_id=%s ai_assessment_id=%s candidate_id=%s", assignment_id, payload.get("ai_assessment_id") or metadata.get("ai_assessment_id"), payload.get("candidate_id") or metadata.get("candidate_id"))
    items = _collect_items(payload)
    input_bucket = metadata.get("input_bucket")
    logger.info("Collected assessment items: assignment_id=%s count=%s", assignment_id, len(items))
    question_results = [
        _build_question_result(item, position, input_bucket)
        for position, item in enumerate(items, start=1)
    ]
    scores = _aggregate_scores(question_results)
    logger.info("Generating AI feedback: assignment_id=%s", assignment_id)
    ai_feedback = generate_ai_feedback_report(payload, question_results)
    logger.info("AI feedback generated: assignment_id=%s chars=%s", assignment_id, len(ai_feedback or ""))
    if not ai_feedback:
        ai_feedback = _feedback_text(payload, question_results, scores)

    responses = [
        {
            "question_id": item.get("question_id"),
            "question_number": item.get("question_number"),
            "question_text": item.get("question_text"),
            "transcript": item.get("transcript"),
            "answer_text": item.get("answer_text"),
            "score": item.get("score"),
            "status": item.get("status"),
        }
        for item in question_results
    ]

    return ProcessingResult(
        result={
            "assignment_id": assignment_id,
            "ai_assessment_id": payload.get("ai_assessment_id")
            or metadata.get("ai_assessment_id"),
            "candidate_id": payload.get("candidate_id")
            or metadata.get("candidate_id"),
            "responses": responses,
            "ai_feedback": ai_feedback,
            **scores,
        },
        question_results=question_results,
        metadata={
            "processor": "local_assessment_processor",
            "candidate_id": metadata.get("candidate_id"),
            "questions_received": len(items),
            "answers_processed": len([item for item in question_results if item.get("answer_text")]),
        },
    )
