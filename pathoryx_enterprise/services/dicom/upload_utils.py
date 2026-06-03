"""
Enterprise DICOM upload utilities.

Key fix over original:
  build_cstore_commands() returns a LIST of commands, each capped at
  `batch_size` DCM files, preventing ARG_MAX overflow on large DICOM series.

  Original: one call with all files as args → OS pipe/argv limit hit on 500+ files.
  Fixed:    chunk into batches, each safely under ARG_MAX.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator, Sequence


def _chunked(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def build_cstore_commands(
    input_path: Path,
    host: str,
    port: int,
    local_ae: str,
    remote_ae: str,
    cstore_bin: str = "storescu",
    batch_size: int = 500,
) -> list[list[str]]:
    """
    Build one or more storescu commands, each with at most `batch_size` DCM files.

    Returns a list of commands. The caller invokes them sequentially.
    For a single file (already DICOM), returns a single 1-file command.
    """
    base = [cstore_bin, "-aec", remote_ae, "-aet", local_ae, host, str(port)]

    if input_path.is_file():
        return [base + [str(input_path)]]

    dcm_files = sorted(str(p) for p in input_path.rglob("*.dcm"))
    if not dcm_files:
        return []

    return [base + chunk for chunk in _chunked(dcm_files, batch_size)]


def run_cstore_command(
    command: Sequence[str],
    timeout_seconds: int = 1800,
) -> tuple[int, str, str]:
    """Run a single storescu command, capturing stdout/stderr (capped at 4 KB each)."""
    proc = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return proc.returncode, proc.stdout[-4000:], proc.stderr[-4000:]


def run_all_cstore_batches(
    commands: list[list[str]],
    timeout_seconds: int = 1800,
) -> tuple[bool, list[dict]]:
    """
    Execute all storescu commands in sequence.

    Returns (all_ok, per_batch_results).
    Stops immediately on first failure.
    """
    results = []
    for i, cmd in enumerate(commands):
        rc, stdout, stderr = run_cstore_command(cmd, timeout_seconds=timeout_seconds)
        results.append({
            "batch_index": i,
            "returncode": rc,
            "stdout": stdout,
            "stderr": stderr,
        })
        if rc != 0:
            return False, results
    return True, results
