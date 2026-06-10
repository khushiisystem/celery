import logging
import time

import httpx

from app.config import get_settings
from app.schemas import CallbackPayload

logger = logging.getLogger(__name__)


def send_callback(callback_url: str, payload: CallbackPayload) -> None:
    settings = get_settings()
    headers = {
        "Content-Type": "application/json",
    }
    if settings.callback_auth_token:
        headers["Authorization"] = f"Bearer {settings.callback_auth_token}"

    callback_body = payload.model_dump(mode="json", exclude_none=True)
    logger.info("Posting callback: url=%s keys=%s", callback_url, sorted(callback_body.keys()))
    last_error: Exception | None = None

    for attempt in range(1, settings.callback_max_retries + 1):
        try:
            response = httpx.post(
                callback_url,
                json=callback_body,
                headers=headers,
                timeout=settings.callback_timeout_seconds,
            )
            response.raise_for_status()
            logger.info("Callback accepted: url=%s status_code=%s", callback_url, response.status_code)
            return
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("Callback attempt failed: attempt=%s/%s url=%s error=%s", attempt, settings.callback_max_retries, callback_url, exc)
            if attempt == settings.callback_max_retries:
                break
            time.sleep(attempt)

    if last_error is not None:
        raise last_error
