"""
Scanner fleet configuration loader.

Maps operational scanner IDs ("scanner-01") to display names ("homeone")
for the dashboard. Provides graceful degradation: an unknown scanner_id
returns its raw value so newly connected scanners appear immediately
without requiring a config update.

Config discovery order:
  1. SCANNER_FLEET_CONFIG environment variable (explicit path)
  2. configs/scanner_fleet.yaml relative to CWD
  3. Empty fleet — scanner IDs shown as-is
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_ENV_VAR = "SCANNER_FLEET_CONFIG"
_DEFAULT_PATHS = ("configs/scanner_fleet.yaml", "./configs/scanner_fleet.yaml")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScannerEntry:
    """Single scanner definition from the fleet config."""
    scanner_id: str
    display_name: str
    location: str = ""
    vendor: str = "unknown"
    model: str = ""
    serial_number: str = ""
    aliases: tuple[str, ...] = ()
    enabled: bool = True


# ---------------------------------------------------------------------------
# Fleet object
# ---------------------------------------------------------------------------


class ScannerFleet:
    """
    Resolved scanner fleet. Immutable; safe for module-level caching.

    Usage::

        fleet = ScannerFleet.load_default()
        label = fleet.display_name("scanner-01")   # → "homeone"
        label = fleet.display_name("scanner-99")   # → "scanner-99" (fallback)
    """

    def __init__(self, entries: list[ScannerEntry]) -> None:
        self._entries: list[ScannerEntry] = list(entries)
        self._by_id: dict[str, ScannerEntry] = {}
        for e in entries:
            self._by_id[e.scanner_id] = e
            for alias in e.aliases:
                self._by_id[alias] = e

    # ── Name resolution ──────────────────────────────────────────────────────

    def display_name(self, scanner_id: str) -> str:
        """
        Return the display name for scanner_id, or the raw scanner_id when
        not found in the config. Never returns None.
        """
        entry = self._by_id.get(scanner_id)
        return entry.display_name if entry is not None else scanner_id

    def get(self, scanner_id: str) -> Optional[ScannerEntry]:
        """Return the ScannerEntry for scanner_id, or None if unknown."""
        return self._by_id.get(scanner_id)

    def is_known(self, scanner_id: str) -> bool:
        return scanner_id in self._by_id

    # ── Enumeration ──────────────────────────────────────────────────────────

    def all(self) -> list[ScannerEntry]:
        return list(self._entries)

    def enabled(self) -> list[ScannerEntry]:
        return [e for e in self._entries if e.enabled]

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def enabled_count(self) -> int:
        return sum(1 for e in self._entries if e.enabled)

    # ── Factory methods ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str) -> "ScannerFleet":
        """
        Load fleet from YAML at path.
        Returns an empty (non-None) fleet on any I/O or parse error.
        """
        try:
            with open(path) as fh:
                raw = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            logger.debug("ScannerFleet.load: config not found at %s", path)
            return cls([])
        except Exception as exc:
            logger.warning("ScannerFleet.load: failed to parse %s: %s", path, exc)
            return cls([])

        entries: list[ScannerEntry] = []
        for item in raw.get("scanners", []) or []:
            try:
                sid = str(item["scanner_id"])
                entries.append(ScannerEntry(
                    scanner_id=sid,
                    display_name=str(item.get("display_name") or sid),
                    location=str(item.get("location") or ""),
                    vendor=str(item.get("vendor") or "unknown"),
                    model=str(item.get("model") or ""),
                    serial_number=str(item.get("serial_number") or ""),
                    aliases=tuple(str(a) for a in (item.get("aliases") or [])),
                    enabled=bool(item.get("enabled", True)),
                ))
            except (KeyError, TypeError) as exc:
                logger.warning("ScannerFleet.load: skipping invalid entry %r: %s", item, exc)

        logger.debug("ScannerFleet.load: loaded %d scanner(s) from %s", len(entries), path)
        return cls(entries)

    @classmethod
    def load_default(cls) -> "ScannerFleet":
        """
        Load from SCANNER_FLEET_CONFIG env var, or default discovery paths.
        Returns an empty fleet when no config is found.
        """
        env_path = os.environ.get(_ENV_VAR, "").strip()
        if env_path:
            return cls.load(env_path)

        for candidate in _DEFAULT_PATHS:
            if Path(candidate).exists():
                return cls.load(candidate)

        logger.debug(
            "ScannerFleet.load_default: no scanner fleet config found; "
            "scanner IDs will be shown as-is"
        )
        return cls([])
