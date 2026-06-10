import os
import sys
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Dict, List, Optional

from google import genai
from google.genai.types import HttpOptions

import tiktoken
from faster_whisper import WhisperModel

try:
    from app.config import get_settings
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.config import get_settings

_vertex_configured = False


def _configure_vertex_ai() -> bool:
    global _vertex_configured
    if _vertex_configured:
        return True

    settings = get_settings()
    creds_path = settings.google_credentials_path
    default_creds_path = Path(__file__).resolve().parents[2] / "credentials" / "google-credentials.json"
    if (not creds_path or not os.path.exists(creds_path)) and default_creds_path.exists():
        creds_path = str(default_creds_path)

    if (not creds_path or not os.path.exists(creds_path)) and settings.google_credentials_json:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(settings.google_credentials_json)
        tmp.close()
        creds_path = tmp.name
        print("[INFO] Wrote GOOGLE_CREDENTIALS_JSON to temp credentials file")

    if not settings.google_cloud_project:
        print("[WARN] Vertex AI not configured. Missing: GOOGLE_CLOUD_PROJECT")
        return False
    if not creds_path or not os.path.exists(creds_path):
        print("[WARN] Vertex AI not configured. Missing: GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_CREDENTIALS_JSON")
        return False

    try:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
        _vertex_configured = True
        print(f"[INFO] Vertex AI configured (project: {settings.google_cloud_project}, model: {settings.gemini_model})")
        return True
    except Exception as exc:
        print(f"[ERROR] Failed to configure Vertex AI: {exc}")
        return False


class TokenCounter:
    def __init__(self, model_name: str = "gpt-3.5-turbo"):
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(self.encoding.encode(text))

    def count_tokens_in_messages(self, messages: List[Dict[str, str]]) -> int:
        total_tokens = 0
        for message in messages:
            total_tokens += 4
            for value in message.values():
                total_tokens += self.count_tokens(str(value))
        return total_tokens + 2


def get_token_counter() -> TokenCounter:
    return TokenCounter()


class GeminiAIClient:
    def __init__(self):
        self.configured = False
        self.client = None
        self.configure()

    def configure(self) -> bool:
        if not _configure_vertex_ai():
            return False
        try:
            settings = get_settings()
            self.client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
                http_options=HttpOptions(api_version="v1"),
            )
            self.configured = True
            return True
        except Exception as exc:
            print(f"[ERROR] Failed to instantiate Gemini model: {exc}")
            return False

    def test_connection(self) -> bool:
        if not self.configured:
            return False
        try:
            settings = get_settings()
            self.client.models.generate_content(
                model=settings.gemini_model,
                contents="Reply with exactly: OK",
                config={
                    "temperature": 0,
                    "max_output_tokens": 16,
                },
            )
            return True
        except Exception as exc:
            print(f"[WARN] Gemini connection test failed: {exc}")
            return False

    def provide_feedback(
        self,
        questions: List[str],
        answers: List[str],
        resume_text: str,
        gesture_analysis: Optional[Dict] = None,
        question_numbers: Optional[List[int]] = None,
    ) -> str:
        if not self.configured:
            return ""

        settings = get_settings()
        counter = get_token_counter()
        qa_blocks = []
        for index, (question, answer) in enumerate(zip(questions, answers)):
            question_number = (
                question_numbers[index]
                if question_numbers and index < len(question_numbers)
                else index + 1
            )
            qa_blocks.append(f"Question {question_number}: {question}\nAnswer: {answer}")
        qa_pairs = "\n\n".join(qa_blocks)

        gesture_context = ""
        if gesture_analysis:
            engagement = gesture_analysis.get("average_engagement", 0)
            eye_contact = gesture_analysis.get("eye_contact_percentage", 0)
            posture = gesture_analysis.get("posture_score", 0)
            gesture_context = (
                "\n\nCommunication Metrics:\n"
                f"- Engagement Score: {engagement:.1f}/10\n"
                f"- Eye Contact: {eye_contact:.0f}%\n"
                f"- Posture Score: {posture:.1f}/10"
            )

        total_questions = len(questions)
        answered_questions = len(answers)
        unanswered_count = max(total_questions - answered_questions, 0)
        experience_level = "Not specified"
        exp_match = re.search(r"Experience level[:\s]+([^\n]+)", resume_text)
        if exp_match:
            experience_level = exp_match.group(1).strip()

        total_match = re.search(r"Total interview questions[:\s]+(\d+)", resume_text)
        if total_match:
            total_questions = int(total_match.group(1))
            unanswered_count = max(total_questions - answered_questions, 0)

        coverage_percent = (
            round((answered_questions / total_questions) * 100)
            if total_questions
            else 0
        )

        prompt = f"""
You are a strict and experienced technical interviewer evaluating a candidate's interview performance.

Assessment context:
- Total questions asked: {total_questions}
- Questions answered by candidate: {answered_questions}
- Questions not answered: {unanswered_count}
- Completion rate: {coverage_percent}%
- Experience level expected: {experience_level}

Candidate background:
{resume_text}

Interview questions and answers:
{qa_pairs}{gesture_context}

Provide feedback in this exact format:

**QUESTION-WISE VERIFICATION:**
Q1: [Question text]
[CHECK] Covered: [What candidate correctly addressed]
[X] Missing: [What candidate missed or got wrong]
Score: [X/10] - [Reason]

**OVERALL ASSESSMENT:**
**Technical Competency**: [Feedback] Rating: [X/10]
**Communication Skills**: [Feedback] Rating: [X/10]
**Problem-Solving Approach**: [Feedback] Rating: [X/10]
**Strengths**: [Specific strengths]
**Areas for Improvement**: [Specific gaps]
**Overall Assessment**: [Concise overall evaluation] Rating: [X/10]
"""
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config={
                    "temperature": 0.2,
                    "max_output_tokens": 2048,
                    "top_p": 0.95,
                    "top_k": 40,
                },
            )
            feedback = getattr(response, "text", "") or ""
            if feedback:
                total_tokens = (
                    sum(counter.count_tokens(item) for item in questions)
                    + sum(counter.count_tokens(item) for item in answers)
                    + counter.count_tokens(resume_text)
                    + counter.count_tokens(feedback)
                )
                print(f"[STATS] Gemini feedback tokens used: {total_tokens}")
            return feedback
        except Exception as exc:
            print(f"[ERROR] Error generating feedback: {exc}")
            return ""


def get_gemini_client() -> GeminiAIClient:
    return GeminiAIClient()


_model = None


def get_faster_whisper_model():
    global _model
    if _model is None:
        model_size = os.environ.get("WHISPER_MODEL_SIZE", "base")
        device = os.environ.get("WHISPER_DEVICE", "cpu")
        compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        _model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=int(os.environ.get("WHISPER_CPU_THREADS", "4")),
            num_workers=1,
        )
    return _model


def convert_webm_to_wav(input_path: str) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if os.path.getsize(input_path) < 100:
        raise ValueError(f"File too small to be valid audio: {input_path}")

    wav_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    wav_path = wav_file.name
    wav_file.close()

    cmd = [
        "ffmpeg",
        "-i",
        input_path,
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts+discardcorrupt",
        "-ignore_unknown",
        "-y",
        wav_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            return wav_path
        print(f"[WARN] ffmpeg conversion failed: {result.stderr}")
    except FileNotFoundError:
        print("[WARN] ffmpeg not found; trying original audio file")

    try:
        os.unlink(wav_path)
    except OSError:
        pass
    return input_path


def post_process_transcript(transcript: str) -> str:
    if not transcript:
        return transcript

    transcript = re.sub(r"\s+", " ", transcript)
    words = transcript.split()
    deduped_words = []
    prev_word = None
    repeat_count = 0

    for word in words:
        word_lower = word.lower()
        if word_lower != (prev_word.lower() if prev_word else None):
            deduped_words.append(word)
            prev_word = word
            repeat_count = 0
        else:
            repeat_count += 1
            if repeat_count <= 1:
                deduped_words.append(word)

    transcript = " ".join(deduped_words)
    transcript = re.sub(
        r"\b(\w+(?:\s+\w+){0,3})\s+(?:\1\s+){2,}",
        r"\1 ",
        transcript,
        flags=re.IGNORECASE,
    )
    fixes = {
        r"\bapi\b": "API",
        r"\bhttp\b": "HTTP",
        r"\bjson\b": "JSON",
        r"\bhtml\b": "HTML",
        r"\bcss\b": "CSS",
        r"\bjs\b": "JavaScript",
        r"\bsql\b": "SQL",
    }
    for pattern, replacement in fixes.items():
        transcript = re.sub(pattern, replacement, transcript, flags=re.IGNORECASE)

    transcript = re.sub(r"\s+", " ", transcript).strip()
    if transcript:
        transcript = transcript[0].upper() + transcript[1:] if len(transcript) > 1 else transcript.upper()
    return transcript


def transcribe_audio_with_whisper(audio_file_path: str, language_hint: str | None = None) -> str:
    converted_path = None
    try:
        converted_path = convert_webm_to_wav(audio_file_path)
        model = get_faster_whisper_model()
        segments, _info = model.transcribe(
            converted_path,
            language=language_hint or "en",
            beam_size=5,
            best_of=5,
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
            compression_ratio_threshold=2.8,
            log_prob_threshold=-2.0,
            no_speech_threshold=0.3,
            condition_on_previous_text=False,
            vad_filter=False,
            without_timestamps=False,
        )
        transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        return post_process_transcript(transcript)
    except Exception as exc:
        print(f"[ERROR] Whisper transcription failed: {exc}")
        return ""
    finally:
        if converted_path and converted_path != audio_file_path:
            try:
                os.unlink(converted_path)
            except OSError:
                pass


def transcribe_audio(audio_file_path: str, method: str = "whisper") -> str:
    return transcribe_audio_with_whisper(audio_file_path)
