from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import EmailStr, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./ivory-saas.db"
    redis_url: str = "redis://redis:6379/0"
    session_pepper: str = "development-session-pepper-change-before-production"
    field_encryption_key: str = ""
    platform_admin_email: EmailStr = "mark@example.com"
    platform_admin_name: str = "Mark Powell"
    platform_admin_password: str = "DevelopmentPassword!123"
    manager_url: str = "http://localhost:30061"
    studio_url: str = "http://localhost:30060"
    client_url: str = "http://localhost:30062"
    cookie_secure: bool = False
    trial_days: int = Field(default=30, ge=1, le=365)
    session_hours: int = Field(default=12, ge=1, le=168)
    invitation_hours: int = Field(default=72, ge=1, le=336)
    platform_storage_root: Path = Path("./platform-storage")
    tenant_storage_root: Path = Path("./tenant-data")
    google_calendar_client_id: str = ""
    google_calendar_client_secret: str = ""
    google_calendar_redirect_uri: str = ""
    google_calendar_timeout_seconds: int = Field(default=20, ge=5, le=60)

    @model_validator(mode="after")
    def validate_production_security(self):
        if not self.field_encryption_key:
            self.field_encryption_key = Fernet.generate_key().decode()
        else:
            try:
                Fernet(self.field_encryption_key.encode())
            except Exception as exc:
                raise ValueError("FIELD_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if self.app_env.lower() == "production":
            if len(self.session_pepper) < 48 or "change" in self.session_pepper.lower():
                raise ValueError("SESSION_PEPPER must contain at least 48 random characters")
            if len(self.platform_admin_password) < 16 or "change" in self.platform_admin_password.lower():
                raise ValueError("PLATFORM_ADMIN_PASSWORD must be a strong unique password")
            for label, value in {
                "MANAGER_URL": self.manager_url,
                "STUDIO_URL": self.studio_url,
                "CLIENT_URL": self.client_url,
            }.items():
                if not value.startswith("https://"):
                    raise ValueError(f"{label} must use https:// in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
