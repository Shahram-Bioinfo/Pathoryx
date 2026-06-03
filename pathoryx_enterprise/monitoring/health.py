"""
Health check logic for Pathoryx services.

Provides three standard Kubernetes probe semantics:
  - liveness  (/live)  — is the process alive and not deadlocked?
  - readiness (/ready) — is the service ready to accept work?
  - health    (/health) — full dependency report (DB, filesystem, env)

Each check returns a HealthStatus dataclass. HTTP transport is in http_health.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pathoryx_enterprise.utils.datetime_utils import utc_now


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class HealthStatus:
    healthy: bool
    checks: list[CheckResult]
    timestamp: str = field(default_factory=lambda: utc_now().isoformat())

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "timestamp": self.timestamp,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "message": c.message,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def check_env_vars(required: list[str]) -> CheckResult:
    """Verify that mandatory environment variables are present and non-empty."""
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        return CheckResult(
            name="env_vars",
            ok=False,
            message=f"Missing required env vars: {missing}",
        )
    return CheckResult(name="env_vars", ok=True, message="all required env vars present")


def check_database(session_factory: Callable) -> CheckResult:
    """
    Verify DB connectivity by executing a lightweight query.
    Uses the provided session factory to stay decoupled from engine details.
    """
    try:
        from sqlalchemy import text

        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return CheckResult(name="database", ok=True, message="connection ok")
    except Exception as exc:
        return CheckResult(name="database", ok=False, message=str(exc))


def check_paths_readable(paths: list[str | Path], label: str = "input_paths") -> CheckResult:
    """Verify that all required input/output directories are accessible."""
    unreadable = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            unreadable.append(f"{p}: does not exist")
        elif not os.access(path, os.R_OK):
            unreadable.append(f"{p}: not readable")
    if unreadable:
        return CheckResult(
            name=label,
            ok=False,
            message="path check failed",
            detail={"unreadable": unreadable},
        )
    return CheckResult(name=label, ok=True, message="all paths accessible")


def check_paths_writable(paths: list[str | Path], label: str = "output_paths") -> CheckResult:
    """Verify that output directories are writable."""
    not_writable = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            not_writable.append(f"{p}: does not exist")
        elif not os.access(path, os.W_OK):
            not_writable.append(f"{p}: not writable")
    if not_writable:
        return CheckResult(
            name=label,
            ok=False,
            message="write path check failed",
            detail={"not_writable": not_writable},
        )
    return CheckResult(name=label, ok=True, message="all output paths writable")


def check_model_weights(weight_paths: dict[str, str | Path]) -> CheckResult:
    """
    Verify that ML model weight files exist and have non-zero size.
    weight_paths: {model_name: path}
    """
    missing = {}
    for name, path in weight_paths.items():
        p = Path(path)
        if not p.exists():
            missing[name] = f"{path}: file not found"
        elif p.stat().st_size == 0:
            missing[name] = f"{path}: file is empty"
    if missing:
        return CheckResult(
            name="model_weights",
            ok=False,
            message="one or more model weight files unavailable",
            detail=missing,
        )
    return CheckResult(name="model_weights", ok=True, message="all model weights present")


# ---------------------------------------------------------------------------
# Composite probe builders
# ---------------------------------------------------------------------------

def liveness_probe() -> HealthStatus:
    """
    Minimal liveness check — returns healthy unless the process is clearly broken.
    Intentionally avoids I/O so it never blocks.
    """
    return HealthStatus(
        healthy=True,
        checks=[CheckResult(name="alive", ok=True, message="process is running")],
    )


def build_readiness_probe(
    *,
    session_factory: Callable | None = None,
    required_env_vars: list[str] | None = None,
) -> Callable[[], HealthStatus]:
    """
    Returns a readiness probe function.

    Readiness = DB reachable + required env vars present.
    When False, Kubernetes stops routing traffic to the pod without killing it.
    """
    def probe() -> HealthStatus:
        checks: list[CheckResult] = []

        if required_env_vars:
            checks.append(check_env_vars(required_env_vars))

        if session_factory is not None:
            checks.append(check_database(session_factory))

        healthy = all(c.ok for c in checks)
        return HealthStatus(healthy=healthy, checks=checks)

    return probe


def build_health_probe(
    *,
    session_factory: Callable | None = None,
    required_env_vars: list[str] | None = None,
    input_paths: list[str | Path] | None = None,
    output_paths: list[str | Path] | None = None,
    model_weights: dict[str, str | Path] | None = None,
    extra_checks: list[Callable[[], CheckResult]] | None = None,
) -> Callable[[], HealthStatus]:
    """
    Returns a full health probe function.

    Full health includes all dependency checks. Intended for /health endpoint
    (operator dashboards, not Kubernetes liveness/readiness probes).
    """
    def probe() -> HealthStatus:
        checks: list[CheckResult] = []

        if required_env_vars:
            checks.append(check_env_vars(required_env_vars))

        if session_factory is not None:
            checks.append(check_database(session_factory))

        if input_paths:
            checks.append(check_paths_readable(input_paths))

        if output_paths:
            checks.append(check_paths_writable(output_paths))

        if model_weights:
            checks.append(check_model_weights(model_weights))

        for fn in extra_checks or []:
            checks.append(fn())

        healthy = all(c.ok for c in checks)
        return HealthStatus(healthy=healthy, checks=checks)

    return probe
