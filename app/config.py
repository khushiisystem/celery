from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    log_level: str = "INFO"

    api_auth_token: str = "change-me"
    callback_auth_token: str | None = None

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_default_queue: str = "assessment"
    celery_task_soft_time_limit_seconds: int = Field(default=60 * 30, ge=1)
    celery_task_time_limit_seconds: int = Field(default=60 * 35, ge=1)
    celery_task_max_retries: int = Field(default=3, ge=0)

    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "ap-south-1"
    aws_s3_region_name: str | None = None
    aws_storage_bucket_name: str | None = None
    aws_s3_endpoint_url: str | None = None
    s3_output_acl: str | None = None

    callback_timeout_seconds: int = Field(default=10, ge=1)
    callback_max_retries: int = Field(default=3, ge=1)

    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_credentials_path: str | None = Field(default=None, alias="GOOGLE_APPLICATION_CREDENTIALS")
    google_credentials_json: str | None = Field(default=None, alias="GOOGLE_CREDENTIALS_JSON")
    gemini_model: str = "gemini-2.5-flash"
    gemini_connection_timeout_seconds: int = Field(default=30, ge=1)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
