"""
Process and system resource metrics via psutil.

Used by all service runners to populate TechnicalMetrics rows.
Gracefully degrades if psutil is unavailable (returns None for all fields).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import psutil  # type: ignore[import-untyped]
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False


@dataclass(slots=True)
class ProcessSnapshot:
    """Resource usage snapshot for a single process at a point in time."""

    cpu_percent_avg: Optional[float] = None
    cpu_percent_peak: Optional[float] = None
    cpu_time_user: Optional[float] = None
    cpu_time_system: Optional[float] = None

    memory_rss_mb: Optional[float] = None
    memory_peak_mb: Optional[float] = None
    memory_percent: Optional[float] = None

    disk_read_mb: Optional[float] = None
    disk_write_mb: Optional[float] = None
    read_count: Optional[int] = None
    write_count: Optional[int] = None

    gpu_name: Optional[str] = None
    gpu_index: Optional[int] = None
    gpu_memory_allocated_mb: Optional[float] = None
    gpu_memory_reserved_mb: Optional[float] = None
    gpu_memory_peak_mb: Optional[float] = None
    gpu_utilization_percent: Optional[float] = None
    gpu_temperature_celsius: Optional[float] = None


class ResourceMonitor:
    """
    Tracks resource usage for the current process between start() and stop().
    Usage::

        monitor = ResourceMonitor()
        monitor.start()
        # ... do work ...
        snapshot = monitor.stop()
    """

    def __init__(self) -> None:
        self._proc: Optional[object] = None
        self._io_start: Optional[object] = None
        self._cpu_samples: list[float] = []

    def start(self) -> None:
        if not _PSUTIL_AVAILABLE:
            return
        self._proc = psutil.Process(os.getpid())
        try:
            self._io_start = self._proc.io_counters()  # type: ignore[union-attr]
        except (psutil.AccessDenied, AttributeError):
            self._io_start = None
        self._cpu_samples = []

    def sample_cpu(self) -> None:
        """Call periodically from within a long-running step to track CPU."""
        if not _PSUTIL_AVAILABLE or self._proc is None:
            return
        try:
            self._cpu_samples.append(self._proc.cpu_percent(interval=None))  # type: ignore[union-attr]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def stop(self) -> ProcessSnapshot:
        if not _PSUTIL_AVAILABLE or self._proc is None:
            return ProcessSnapshot()

        snap = ProcessSnapshot()
        try:
            mem = self._proc.memory_info()  # type: ignore[union-attr]
            snap.memory_rss_mb = mem.rss / (1024 * 1024)
            snap.memory_percent = self._proc.memory_percent()  # type: ignore[union-attr]

            cpu_times = self._proc.cpu_times()  # type: ignore[union-attr]
            snap.cpu_time_user = cpu_times.user
            snap.cpu_time_system = cpu_times.system

            if self._cpu_samples:
                snap.cpu_percent_avg = sum(self._cpu_samples) / len(self._cpu_samples)
                snap.cpu_percent_peak = max(self._cpu_samples)

            if self._io_start is not None:
                try:
                    io_end = self._proc.io_counters()  # type: ignore[union-attr]
                    snap.disk_read_mb = (io_end.read_bytes - self._io_start.read_bytes) / (1024 * 1024)  # type: ignore[union-attr]
                    snap.disk_write_mb = (io_end.write_bytes - self._io_start.write_bytes) / (1024 * 1024)  # type: ignore[union-attr]
                    snap.read_count = io_end.read_count - self._io_start.read_count  # type: ignore[union-attr]
                    snap.write_count = io_end.write_count - self._io_start.write_count  # type: ignore[union-attr]
                except (psutil.AccessDenied, AttributeError):
                    pass

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        snap = _try_attach_gpu_metrics(snap)
        return snap


def _try_attach_gpu_metrics(snap: ProcessSnapshot) -> ProcessSnapshot:
    """Attach GPU metrics if torch is available and a GPU is present."""
    try:
        import torch  # type: ignore[import-untyped]

        if not torch.cuda.is_available():
            return snap

        idx = torch.cuda.current_device()
        snap.gpu_index = idx
        snap.gpu_name = torch.cuda.get_device_name(idx)
        snap.gpu_memory_allocated_mb = torch.cuda.memory_allocated(idx) / (1024 * 1024)
        snap.gpu_memory_reserved_mb = torch.cuda.memory_reserved(idx) / (1024 * 1024)
        snap.gpu_memory_peak_mb = torch.cuda.max_memory_allocated(idx) / (1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    return snap
