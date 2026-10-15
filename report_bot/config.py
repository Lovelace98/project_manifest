from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_prefix="REPORT_BOT_",
        extra="ignore",
    )

    app_name: str = "Blueprint DNA Report Bot"
    environment: str = "development"
    app_password_hash: str = ""
    dev_password: str = "change-me-now"
    session_secret: str = "development-only-session-secret-change-me"
    pdf_owner_password: str = "development-owner-password-change-me"
    secure_cookies: bool = False
    session_idle_seconds: int = 1800
    session_absolute_seconds: int = 28800
    job_ttl_seconds: int = 3600
    cleanup_interval_seconds: int = 30
    max_batch_files: int = 25
    max_file_bytes: int = 25 * 1024 * 1024
    max_batch_bytes: int = 250 * 1024 * 1024

    data_dir: Path = Field(default=BASE_DIR / "data")
    job_dir: Path = Field(default=BASE_DIR / "data" / "jobs")
    settings_path: Path = Field(default=BASE_DIR / "data" / "settings.json")
    logo_path: Path = Field(default=BASE_DIR / "blueprint_logo.png")
    contact_footer_path: Path = Field(
        default=BASE_DIR / "flow" / "static" / "assets" / "Contact PNG.png"
    )
    verification_template_path: Path = Field(
        default=BASE_DIR / "verification page template.docx"
    )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment.lower() == "production":
            if not self.app_password_hash.startswith("$argon2"):
                raise ValueError("REPORT_BOT_APP_PASSWORD_HASH must be an Argon2 hash")
            if len(self.session_secret) < 32:
                raise ValueError("REPORT_BOT_SESSION_SECRET must be at least 32 characters")
            if len(self.pdf_owner_password) < 16:
                raise ValueError("REPORT_BOT_PDF_OWNER_PASSWORD must be at least 16 characters")
            if not self.secure_cookies:
                raise ValueError("REPORT_BOT_SECURE_COOKIES must be true in production")
        return self

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.chmod(0o700)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
