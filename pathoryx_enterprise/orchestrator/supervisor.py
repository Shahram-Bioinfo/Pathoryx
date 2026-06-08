"""
Process supervisor for the Palantir Enterprise pipeline.

Manages all service processes as subprocesses. Each service is launched
via its CLI entry point (registered in pyproject.toml). The supervisor:

  - Starts all configured services.
  - Monitors health by reading exit codes.
  - Restarts a crashed service up to `max_restarts` times.
  - On SIGTERM: broadcasts SIGTERM to all children, waits for graceful exit.

Intended for single-machine deployments. For Kubernetes, use the individual
service images instead — each pod runs one service directly.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ServiceSpec:
    name: str
    command: list[str]
    env_extra: dict[str, str] = field(default_factory=dict)
    max_restarts: int = 5
    restart_delay_seconds: int = 5


@dataclass
class _ServiceState:
    spec: ServiceSpec
    process: subprocess.Popen | None = None
    restart_count: int = 0
    stopped: bool = False


class ProcessSupervisor:
    """
    Supervise a set of services as OS subprocesses.

    Usage::

        supervisor = ProcessSupervisor([
            ServiceSpec("babelshark", ["pathoryx-babelshark"]),
            ServiceSpec("qc", ["pathoryx-qc"]),
        ])
        supervisor.start_all()
        supervisor.wait_forever()
    """

    def __init__(self, services: list[ServiceSpec]) -> None:
        self._states = [_ServiceState(spec=s) for s in services]
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    def start_all(self) -> None:
        """Launch all services."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        for state in self._states:
            self._start(state)

    def wait_forever(self) -> None:
        """Block until all services have stopped (or supervisor receives SIGTERM)."""
        monitor_thread = threading.Thread(
            target=self._monitor_loop, name="supervisor-monitor", daemon=True
        )
        monitor_thread.start()
        self._stopping.wait()
        monitor_thread.join(timeout=30)

    def stop_all(self) -> None:
        """Send SIGTERM to all children and wait for them to exit."""
        self._stopping.set()
        for state in self._states:
            self._terminate(state)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start(self, state: _ServiceState) -> None:
        env = {**os.environ, **state.spec.env_extra}
        try:
            proc = subprocess.Popen(
                state.spec.command,
                env=env,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            state.process = proc
            state.stopped = False
            logger.info(
                "service started",
                name=state.spec.name,
                pid=proc.pid,
                restart_count=state.restart_count,
            )
        except Exception as exc:
            logger.error(
                "failed to start service",
                name=state.spec.name,
                error=str(exc),
            )

    def _terminate(self, state: _ServiceState) -> None:
        if state.process is not None and state.process.poll() is None:
            logger.info("sending SIGTERM", name=state.spec.name, pid=state.process.pid)
            state.process.send_signal(signal.SIGTERM)
            try:
                state.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("sending SIGKILL (no graceful exit)", name=state.spec.name)
                state.process.kill()

    def _monitor_loop(self) -> None:
        """Poll all processes and restart any that have crashed."""
        while not self._stopping.is_set():
            time.sleep(2)
            all_stopped = True

            for state in self._states:
                if state.stopped:
                    continue

                proc = state.process
                if proc is None:
                    state.stopped = True
                    continue

                rc = proc.poll()
                if rc is None:
                    all_stopped = False
                    continue

                # Process has exited
                if self._stopping.is_set():
                    state.stopped = True
                    continue

                logger.warning(
                    "service exited",
                    name=state.spec.name,
                    returncode=rc,
                    restart_count=state.restart_count,
                )

                if state.restart_count >= state.spec.max_restarts:
                    logger.error(
                        "max restarts reached — not restarting",
                        name=state.spec.name,
                        max_restarts=state.spec.max_restarts,
                    )
                    state.stopped = True
                    continue

                state.restart_count += 1
                time.sleep(state.spec.restart_delay_seconds)
                self._start(state)
                all_stopped = False

            if all_stopped and not self._stopping.is_set():
                logger.info("all services stopped — supervisor exiting")
                self._stopping.set()

    def _handle_signal(self, signum: int, _frame: object) -> None:
        logger.info("received signal %d — stopping all services", signum)
        self.stop_all()
