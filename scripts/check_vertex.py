import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_utils import get_gemini_client
from app.config import get_settings


def main() -> int:
    settings = get_settings()
    print(f"GOOGLE_CLOUD_PROJECT={settings.google_cloud_project or ''}")
    print(f"GOOGLE_CLOUD_LOCATION={settings.google_cloud_location}")
    print(f"GOOGLE_APPLICATION_CREDENTIALS={settings.google_credentials_path or ''}")
    print(f"GEMINI_MODEL={settings.gemini_model}")

    client = get_gemini_client()
    if not client.configured:
        print("Vertex AI is not configured.")
        return 1

    if not client.test_connection():
        print("Vertex AI configured, but Gemini test request failed.")
        return 2

    print("Vertex AI Gemini test succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
