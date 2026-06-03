"""
Service startup validation and summary logging.

Validates required environment, config paths, DB connectivity, and critical
model imports before the main service loop starts. Fails fast with a clear
diagnostic if anything is wrong.

Usage::

    from pathoryx_enterprise.monitoring.startup import StartupValidator

    validator = StartupValidator(
        service_name="qc_runner",
        version="1.0.0",
        environment="development",
        required_env_vars=["DATABASE_URL", "QC_SERVICE_CONFIG"],
        config_paths={"QC_SERVICE_CONFIG": settings.qc_config_path},
        health_port=8082,
        metrics_port=9092,
    )
    validator.run()   # raises SystemExit(1) on failure with diagnostic output
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

_VERSION = "1.0.0"


class StartupValidator:
    """Validates service prerequisites and emits a startup summary."""

    def __init__(
        self,
        service_name: str,
        version: str = _VERSION,
        environment: str = "production",
        required_env_vars: Optional[list[str]] = None,
        config_paths: Optional[dict[str, str]] = None,
        health_port: Optional[int] = None,
        metrics_port: Optional[int] = None,
        runner_id: str = "",
        host_id: str = "",
        extra_info: Optional[dict[str, object]] = None,
    ) -> None:
        self.service_name = service_name
        self.version = version
        self.environment = environment
        self.required_env_vars = required_env_vars or []
        self.config_paths = config_paths or {}
        self.health_port = health_port
        self.metrics_port = metrics_port
        self.runner_id = runner_id
        self.host_id = host_id
        self.extra_info = extra_info or {}
        self._errors: list[str] = []

    def run(self) -> None:
        """
        Run all startup checks. Logs a summary then either continues or exits.
        Calls sys.exit(1) if any required check fails.
        """
        self._check_env_vars()
        self._check_config_paths()
        db_ok = self._check_db_connection()
        self._check_model_imports()
        self._print_summary(db_ok)

        if self._errors:
            log.error(
                "startup.failed",
                service=self.service_name,
                error_count=len(self._errors),
                errors=self._errors,
            )
            sys.exit(1)

    # ------------------------------------------------------------------

    def _check_env_vars(self) -> None:
        # Also check .env file values (matches Pydantic BaseSettings behavior).
        # In development, vars may exist only in .env and not be shell-exported.
        env_file_values: dict[str, str] = {}
        try:
            from dotenv import dotenv_values
            env_file_values = dotenv_values(".env")
        except Exception:
            pass

        missing = [
            v for v in self.required_env_vars
            if not os.environ.get(v) and not env_file_values.get(v)
        ]
        for v in missing:
            self._errors.append(f"required env var not set: {v}")

    def _check_config_paths(self) -> None:
        for env_name, path_str in self.config_paths.items():
            if not path_str:
                self._errors.append(f"config path is empty for {env_name}")
                continue
            if not Path(path_str).exists():
                self._errors.append(f"config file not found: {path_str} (from {env_name})")

    def _check_db_connection(self) -> bool:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            try:
                from dotenv import dotenv_values
                db_url = dotenv_values(".env").get("DATABASE_URL", "")
            except Exception:
                pass
        if not db_url:
            return False
        # Temporarily inject for the engine factory if not already set
        prev = os.environ.get("DATABASE_URL")
        if prev is None:
            os.environ["DATABASE_URL"] = db_url
        try:
            from sqlalchemy import text
            from pathoryx_enterprise.db.engine import get_shared_engine
            with get_shared_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            self._errors.append(f"DB connection failed: {exc}")
            return False
        finally:
            if prev is None:
                os.environ.pop("DATABASE_URL", None)

    def _check_model_imports(self) -> None:
        checks = [
            ("pathoryx_enterprise.db.models.qc", "QCResult"),
            ("pathoryx_enterprise.db.models.dicomizer", "ConversionResult"),
            ("pathoryx_enterprise.db.models.uploader", "UploadResult"),
            ("pathoryx_enterprise.db.models.core", "FileRecord"),
            ("pathoryx_enterprise.db.models.core", "ServiceTrigger"),
            ("pathoryx_enterprise.logging.setup", "configure_logging"),
            ("pathoryx_enterprise.logging.setup", "inject_context"),
        ]
        for module, attr in checks:
            try:
                mod = __import__(module, fromlist=[attr])
                if not hasattr(mod, attr):
                    self._errors.append(f"missing export: {module}.{attr}")
            except ImportError as exc:
                self._errors.append(f"import failed: {module} — {exc}")

    def _print_summary(self, db_ok: bool) -> None:
        status = "FAILED" if self._errors else "OK"
        summary: dict[str, object] = {
            "service": self.service_name,
            "version": self.version,
            "environment": self.environment,
            "runner_id": self.runner_id or "(not yet assigned)",
            "host_id": self.host_id or "(unknown)",
            "db_connection": "OK" if db_ok else "FAILED",
            "startup_status": status,
        }
        if self.health_port:
            summary["health_endpoint"] = f"http://0.0.0.0:{self.health_port}/health"
        if self.metrics_port:
            summary["metrics_port"] = self.metrics_port
        for k, v in self.config_paths.items():
            summary[f"config_{k.lower()}"] = v
        summary.update(self.extra_info)

        if self._errors:
            summary["errors"] = self._errors
            log.error("service.startup_failed", **summary)
        else:
            log.info("service.started", **summary)


def check_db_ok() -> bool:
    """Quick DB connectivity test. Returns True/False without raising."""
    try:
        from sqlalchemy import text
        from pathoryx_enterprise.db.engine import get_shared_engine
        with get_shared_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def fatal(message: str, exc: Optional[BaseException] = None, debug: bool = False) -> None:
    """
    Log a fatal startup error and exit. Preserves full traceback in debug mode.
    """
    if debug and exc is not None:
        tb = traceback.format_exc()
        print(f"\n[FATAL] {message}\n{tb}", file=sys.stderr)
    else:
        error_detail = str(exc) if exc else ""
        print(f"[FATAL] {message}" + (f": {error_detail}" if error_detail else ""), file=sys.stderr)
    sys.exit(1)
