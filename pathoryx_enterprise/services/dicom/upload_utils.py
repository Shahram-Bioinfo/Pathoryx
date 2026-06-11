"""
Enterprise DICOM upload utilities.

Key fix over original:
  build_cstore_commands() returns a LIST of commands, each capped at
  `batch_size` DCM files, preventing ARG_MAX overflow on large DICOM series.

  Original: one call with all files as args → OS pipe/argv limit hit on 500+ files.
  Fixed:    chunk into batches, each safely under ARG_MAX.

DIMSE status parsing (added):
  parse_dimse_statuses() extracts 0xNNNN codes from storescu/dcmsendim stdout+stderr.
  verify_dimse_success() decides whether the extracted statuses are acceptable.

  Standard success:       0x0000
  Acceptable warnings:    0xB000 (coercion), 0xB006 (elements discarded), 0xB007
  Failure (non-exhaustive): 0xA700 (refused — out of resources)
                            0xA900 (refused — data set does not match SOP class)
                            0xC000-0xCFFF (cannot understand)

  If no status codes appear in the output (some PACS implementations are silent),
  verify_dimse_success() trusts the process exit code 0 as authoritative.

Production logging (added):
  run_all_cstore_batches() emits one structured log line per batch with the full
  command, return code, raw stdout, raw stderr, and parsed DIMSE codes.
  These lines flow through the stdlib root logger and are captured by whatever
  RotatingFileHandler was attached via logging.setup.add_file_handler().

Windows compatibility:
  subprocess.run() with a list (not a shell string) correctly handles paths that
  contain spaces on Windows — no manual quoting is needed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterator, Sequence

import structlog

_log = structlog.get_logger(__name__)

# ── DIMSE status constants ────────────────────────────────────────────────────

DIMSE_SUCCESS = 0x0000

# These warning statuses mean the PACS accepted the image with minor modifications.
# Treat as success: the data is in the PACS.
DIMSE_ACCEPTABLE_WARNINGS: frozenset[int] = frozenset({
    0xB000,  # Coercion of data elements
    0xB006,  # Elements discarded
    0xB007,  # Dataset does not match SOP class
})

# Regex matches both output formats:
#   storescu:   I: Received Store Response (Status=0x0000: Success)
#   dcmsendim:  Status: 0000
# The 0x prefix is optional as a non-capturing group so that bare 4-digit hex
# codes (dcmsendim style) are captured correctly.
_DIMSE_RE = re.compile(r'[Ss]tatus[=:\s]+(?:0[xX])?([0-9A-Fa-f]{4})', re.MULTILINE)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _chunked(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── Public API ────────────────────────────────────────────────────────────────

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

    Returns a list of commands (each is a list[str] suitable for subprocess.run).
    The caller invokes them sequentially and stops on the first failure.

    Single-file input → returns exactly one command with one file argument.
    Directory input   → enumerates *.dcm recursively, chunks into batches.
    Empty directory   → returns [] (caller must treat as error).

    Windows note: cstore_bin may be a full absolute path (e.g.
    "C:\\Program Files\\dcmtk-3.7.0-win64-dynamic\\bin\\storescu.exe").
    Using a list rather than a shell string means subprocess handles spaces correctly.
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
    """
    Run a single storescu command and return (returncode, stdout, stderr).

    Full stdout and stderr are returned (not truncated) so that
    run_all_cstore_batches() can log the complete output to the rotating file.
    The caller (run_all_cstore_batches) caps what it stores in the result dict.
    """
    proc = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_dimse_statuses(text: str) -> list[int]:
    """
    Extract all DIMSE status codes from combined storescu/dcmsendim output text.

    Handles both formats:
      storescu:   I: Received Store Response (Status=0x0000: Success)
      dcmsendim:  Status: 0000

    Returns a list of integer status codes (empty list if none are found).
    An empty list is not an error — some PACS implementations are silent.
    """
    return [int(m.group(1), 16) for m in _DIMSE_RE.finditer(text)]


def verify_dimse_success(
    statuses: list[int],
) -> tuple[bool, str]:
    """
    Decide whether a list of DIMSE status codes constitutes overall success.

    Rules (in order):
      1. Empty list → (True,  "no DIMSE status in output — trusting exit code 0")
         Rationale: some PACS implementations never print a status line; if
         storescu exited 0 the association closed cleanly.
      2. Any code outside {0x0000} ∪ DIMSE_ACCEPTABLE_WARNINGS → (False, reason)
      3. Any acceptable warning present → (True, "DIMSE success with warning(s): …")
      4. All codes are 0x0000 → (True, "DIMSE 0x0000 Success")

    Returns (ok: bool, human_readable_reason: str).
    """
    if not statuses:
        return True, "no DIMSE status found in output — trusting exit-code-0 as success"

    acceptable = {DIMSE_SUCCESS} | DIMSE_ACCEPTABLE_WARNINGS
    failures = [s for s in statuses if s not in acceptable]
    if failures:
        codes = ", ".join(f"0x{s:04X}" for s in failures)
        return False, f"DIMSE failure status(es) received: {codes}"

    warnings = [s for s in statuses if s in DIMSE_ACCEPTABLE_WARNINGS]
    if warnings:
        codes = ", ".join(f"0x{s:04X}" for s in warnings)
        return True, f"DIMSE success with warning(s): {codes}"

    return True, "DIMSE 0x0000 Success"


def run_all_cstore_batches(
    commands: list[list[str]],
    timeout_seconds: int = 1800,
) -> tuple[bool, list[dict]]:
    """
    Execute all storescu commands in sequence.

    Stops immediately on the first batch that fails (non-zero exit code OR
    unacceptable DIMSE status).  Returns (all_ok, per_batch_results).

    Each element of per_batch_results is a dict with keys:
      batch_index    int       — 0-based position in commands list
      returncode     int       — storescu process exit code
      stdout         str       — full stdout (also emitted to log file)
      stderr         str       — full stderr (also emitted to log file)
      dimse_statuses list[str] — e.g. ["0x0000", "0x0000"]
      dimse_ok       bool      — True if all DIMSE statuses are acceptable
      dimse_reason   str       — human-readable summary
      batch_ok       bool      — True iff returncode==0 AND dimse_ok

    Every batch execution is logged as a structured event at INFO (success) or
    ERROR (failure) level.  The log line includes the full command, return code,
    complete stdout, complete stderr, and parsed DIMSE codes.  These events flow
    through stdlib logging and are captured by the service's rotating file handler.
    """
    results: list[dict] = []
    for i, cmd in enumerate(commands):
        rc, stdout, stderr = run_cstore_command(cmd, timeout_seconds=timeout_seconds)
        combined = stdout + "\n" + stderr
        statuses = parse_dimse_statuses(combined)
        dimse_ok, dimse_reason = verify_dimse_success(statuses)
        batch_ok = (rc == 0) and dimse_ok

        log_fn = _log.info if batch_ok else _log.error
        log_fn(
            "cstore_batch_executed",
            batch_index=i,
            command=cmd,
            returncode=rc,
            stdout=stdout,
            stderr=stderr,
            dimse_statuses=[f"0x{s:04X}" for s in statuses],
            dimse_ok=dimse_ok,
            dimse_reason=dimse_reason,
            batch_ok=batch_ok,
        )

        results.append({
            "batch_index":    i,
            "returncode":     rc,
            "stdout":         stdout[-4000:],
            "stderr":         stderr[-4000:],
            "dimse_statuses": [f"0x{s:04X}" for s in statuses],
            "dimse_ok":       dimse_ok,
            "dimse_reason":   dimse_reason,
            "batch_ok":       batch_ok,
        })
        if not batch_ok:
            return False, results
    return True, results
