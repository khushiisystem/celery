from app.ai_utils import get_gemini_client


def _answer_by_question_number(responses: list[dict]) -> dict:
    return {
        item["question_number"]: item.get("transcript") or item.get("answer_text") or ""
        for item in responses
        if item.get("question_number") is not None
    }


def _coding_answer(question: dict) -> str:
    passed = sum(
        1
        for result in question.get("code_execution_results", []) or []
        if result.get("passed")
    )
    total_tc = len(question.get("code_execution_results", []) or [])
    return (
        f"[CODING - {question.get('code_language', '')}] "
        f"Test cases: {passed}/{total_tc} passed "
        f"({question.get('code_marks_earned', 0)}/{question.get('code_marks_total', 0)} pts)\n"
        f"Code:\n{question.get('code_answer', '')}"
    )


def generate_ai_feedback_report(
    assessment_payload: dict,
    responses: list[dict],
) -> str:
    questions = []
    answers = []
    question_numbers = []
    response_by_q = _answer_by_question_number(responses)

    inner = assessment_payload.get("payload")
    payload = {**assessment_payload, **inner} if isinstance(inner, dict) else assessment_payload

    for question in payload.get("questions", []):
        question_number = question.get("question_number")
        if question_number is None:
            continue

        answer = response_by_q.get(question_number, "")
        if question.get("question_type") == "coding" and question.get("code_answer"):
            answer = _coding_answer(question)

        if not answer:
            continue

        questions.append(question.get("question_text") or "")
        answers.append(answer)
        question_numbers.append(question_number)

    total_questions = len(payload.get("generated_questions") or [])
    if not total_questions:
        total_questions = payload.get("assessment", {}).get(
            "num_questions",
            len(questions),
        )

    attempted = len(answers)
    unanswered = max(total_questions - attempted, 0)
    coverage_percent = round((attempted / total_questions) * 100) if total_questions else 0
    experience_level = payload.get("assessment", {}).get("experience_level", "")

    assessment_context = (
        "\n\nAssessment context:\n"
        f"Total interview questions: {total_questions}\n"
        f"Questions answered by the candidate: {attempted}\n"
        f"Unanswered questions: {unanswered}\n"
        f"Approximate question coverage: {coverage_percent}% of the assessment\n"
        f"Experience level: {experience_level}"
    )

    gemini_client = get_gemini_client()
    if not gemini_client.test_connection():
        return "Assessment completed successfully. AI feedback is currently unavailable."

    feedback = gemini_client.provide_feedback(
        questions=questions,
        answers=answers,
        resume_text=f"{payload.get('resume_text', '')}{assessment_context}",
        #gesture_analysis=(payload.get("analysis") or {}).get("gesture_analysis"),
        question_numbers=question_numbers,
    )

    return feedback or "Assessment completed successfully. Detailed feedback will be available soon."
