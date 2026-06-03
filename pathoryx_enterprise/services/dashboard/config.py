"""Dashboard service configuration — resolved entirely from environment variables."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", env_file=".env", extra="ignore")

    host: str = Field(default="127.0.0.1", description="Bind address.")
    port: int = Field(default=8090, ge=1024, le=65535, description="Listen port.")
    log_level: str = Field(default="info")
    # Reload only for development; leave False in production.
    reload: bool = Field(default=False)
