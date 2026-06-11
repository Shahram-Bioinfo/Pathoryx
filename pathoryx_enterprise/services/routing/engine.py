"""
RoutingPolicyEngine — Phase 4.8 Stage 1 (dry-run only).

Stage 1 contract: this engine COMPUTES routing decisions and RECORDS them
for audit/preview but does NOT alter upload destinations.
Real destination switching is Stage 2, gated on Stage 1 validation.

Priority order (highest → lowest):
  1. Emergency dashboard override (target_type=scanner|file)
  2. Color-dot routing
  3. Scanner routing inside active mode
  4. Mode default destination
  5. Global fallback destination
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# ── Routing reason constants ─────────────────────────────────────────────────

REASON_MANUAL_OVERRIDE = "manual_override"
REASON_COLOR_DOT = "color_dot"
REASON_SCANNER_POLICY = "scanner_policy"
REASON_MODE_DEFAULT = "mode_default"
REASON_FALLBACK = "fallback"
REASON_NO_POLICY = "no_policy"


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class RoutingResult:
    destination: str
    mode: Optional[str]
    profile: Optional[str]
    routing_reason: str
    color_dot: Optional[str] = None
    scanner_id: Optional[str] = None
    override_id: Optional[int] = None
    dry_run: bool = True


@dataclass
class ModeWindow:
    name: str
    start: time
    end: time
    profile: str
    default_destination: str
    scanner_destinations: dict[str, str] = field(default_factory=dict)
    is_overnight: bool = False


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    message: str
    field: Optional[str] = None


# ── Engine ───────────────────────────────────────────────────────────────────

class RoutingPolicyEngine:
    """
    Config-driven routing policy engine.

    Pass the `routing_policies` sub-dict from the BabelShark config:

        engine = RoutingPolicyEngine(config["routing_policies"])
        result = engine.get_routing_decision(scanner_id="HOMEONE", ...)
    """

    def __init__(self, config: dict) -> None:
        self._raw = config
        self._dry_run: bool = config.get("dry_run", True)  # Stage 1: always True
        self._fallback: str = config.get("fallback_destination", "")
        self._default_mode_name: str = config.get("default_mode", "")

        tz_name = config.get("timezone", "UTC")
        try:
            self._tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            logger.warning("routing: unknown timezone %r — defaulting to UTC", tz_name)
            self._tz = zoneinfo.ZoneInfo("UTC")

        self._modes: dict[str, ModeWindow] = {}
        for mode_name, mode_cfg in config.get("modes", {}).items():
            active = mode_cfg.get("active", {})
            start = self._parse_time(active.get("start", "00:00"))
            end = self._parse_time(active.get("end", "23:59"))
            is_overnight = end <= start  # e.g., 16:00–07:00 crosses midnight

            scanner_dests: dict[str, str] = {}
            for sid, sc in mode_cfg.get("scanner_destinations", {}).items():
                if isinstance(sc, dict):
                    scanner_dests[sid] = sc.get("destination", self._fallback)
                elif isinstance(sc, str):
                    scanner_dests[sid] = sc

            self._modes[mode_name] = ModeWindow(
                name=mode_name,
                start=start,
                end=end,
                profile=mode_cfg.get("profile", "default"),
                default_destination=mode_cfg.get("default_destination", self._fallback),
                scanner_destinations=scanner_dests,
                is_overnight=is_overnight,
            )

        self._color_rules: dict[str, str] = {}
        for color, rule in config.get("color_dot_rules", {}).items():
            if isinstance(rule, dict):
                self._color_rules[color.lower()] = rule.get("destination", self._fallback)
            elif isinstance(rule, str):
                self._color_rules[color.lower()] = rule

    @staticmethod
    def _parse_time(s: str) -> time:
        parts = s.strip().split(":")
        h = int(parts[0]) % 24
        m = int(parts[1]) % 60 if len(parts) > 1 else 0
        return time(h, m)

    def _now_local(self) -> datetime:
        return datetime.now(tz=self._tz)

    def get_active_mode(self, now: Optional[datetime] = None) -> Optional[ModeWindow]:
        """Return the ModeWindow currently active (respects overnight schedules)."""
        if now is None:
            now = self._now_local()
        local = now.astimezone(self._tz)
        t = local.time().replace(second=0, microsecond=0)

        for mode in self._modes.values():
            if mode.is_overnight:
                if t >= mode.start or t < mode.end:
                    return mode
            else:
                if mode.start <= t < mode.end:
                    return mode

        return self._modes.get(self._default_mode_name)

    def get_routing_decision(
        self,
        scanner_id: Optional[str] = None,
        color_dot: Optional[str] = None,
        overrides: Optional[list[dict]] = None,
        now: Optional[datetime] = None,
        file_id: Optional[str] = None,
    ) -> RoutingResult:
        """
        Compute the routing decision for a slide.

        `overrides` is a list of active routing_overrides rows (plain dicts).
        Stage 1: dry_run is always True; no real destination is changed.
        """
        if overrides is None:
            overrides = []
        if now is None:
            now = self._now_local()

        mode = self.get_active_mode(now)

        # 1. Emergency dashboard override
        for ov in overrides:
            if not ov.get("is_active"):
                continue
            tt = ov.get("target_type")
            tv = ov.get("target_value")
            match = (tt == "scanner" and tv == scanner_id) or \
                    (tt == "file" and tv == file_id)
            if match:
                return RoutingResult(
                    destination=ov["destination"],
                    mode=mode.name if mode else None,
                    profile=mode.profile if mode else None,
                    routing_reason=REASON_MANUAL_OVERRIDE,
                    color_dot=color_dot,
                    scanner_id=scanner_id,
                    override_id=ov.get("id"),
                    dry_run=True,
                )

        # 2. Color-dot routing
        if color_dot:
            dest = self._color_rules.get(color_dot.lower())
            if dest:
                return RoutingResult(
                    destination=dest,
                    mode=mode.name if mode else None,
                    profile=mode.profile if mode else None,
                    routing_reason=f"{REASON_COLOR_DOT}_{color_dot.lower()}",
                    color_dot=color_dot,
                    scanner_id=scanner_id,
                    dry_run=True,
                )

        if mode is None:
            return RoutingResult(
                destination=self._fallback or "unknown",
                mode=None,
                profile=None,
                routing_reason=REASON_FALLBACK,
                color_dot=color_dot,
                scanner_id=scanner_id,
                dry_run=True,
            )

        # 3. Scanner routing inside active mode
        if scanner_id and scanner_id in mode.scanner_destinations:
            return RoutingResult(
                destination=mode.scanner_destinations[scanner_id],
                mode=mode.name,
                profile=mode.profile,
                routing_reason=f"{REASON_SCANNER_POLICY}_{mode.name}",
                color_dot=color_dot,
                scanner_id=scanner_id,
                dry_run=True,
            )

        # 4. Mode default destination
        if mode.default_destination:
            return RoutingResult(
                destination=mode.default_destination,
                mode=mode.name,
                profile=mode.profile,
                routing_reason=f"{REASON_MODE_DEFAULT}_{mode.name}",
                color_dot=color_dot,
                scanner_id=scanner_id,
                dry_run=True,
            )

        # 5. Global fallback
        return RoutingResult(
            destination=self._fallback or "unknown",
            mode=mode.name,
            profile=mode.profile,
            routing_reason=REASON_FALLBACK,
            color_dot=color_dot,
            scanner_id=scanner_id,
            dry_run=True,
        )

    def validate(self) -> list[ValidationIssue]:
        """Return a list of configuration issues (errors + warnings)."""
        issues: list[ValidationIssue] = []

        if not self._modes:
            issues.append(ValidationIssue("error", "No routing modes defined.", "modes"))

        # Check raw config so we catch missing keys before fallback resolution
        for mode_name, mode_cfg in self._raw.get("modes", {}).items():
            if not mode_cfg.get("default_destination"):
                issues.append(ValidationIssue(
                    "error",
                    f"Mode '{mode_name}' has no default_destination.",
                    f"modes.{mode_name}.default_destination",
                ))

        # Check for overlapping non-overnight modes
        non_overnight = [m for m in self._modes.values() if not m.is_overnight]
        for i, m1 in enumerate(non_overnight):
            for m2 in non_overnight[i + 1:]:
                if m1.start < m2.end and m2.start < m1.end:
                    issues.append(ValidationIssue(
                        "warning",
                        f"Modes '{m1.name}' and '{m2.name}' have overlapping windows.",
                        "modes",
                    ))

        if not self._fallback:
            issues.append(ValidationIssue(
                "warning", "No fallback_destination configured.", "fallback_destination"
            ))

        return issues

    def get_status_summary(self, now: Optional[datetime] = None) -> dict:
        """Return a JSON-serialisable status snapshot for the dashboard."""
        if now is None:
            now = self._now_local()
        active = self.get_active_mode(now)

        modes_info = []
        for mode in self._modes.values():
            modes_info.append({
                "name": mode.name,
                "profile": mode.profile,
                "default_destination": mode.default_destination,
                "active_start": mode.start.strftime("%H:%M"),
                "active_end": mode.end.strftime("%H:%M"),
                "is_overnight": mode.is_overnight,
                "is_active": active is not None and active.name == mode.name,
                "scanner_destinations": [
                    {"scanner_id": sid, "destination": dest}
                    for sid, dest in mode.scanner_destinations.items()
                ],
            })

        next_mode = self._get_next_mode(now, active)

        return {
            "active_mode": active.name if active else None,
            "active_profile": active.profile if active else None,
            "active_default_destination": active.default_destination if active else None,
            "next_mode": next_mode,
            "timezone": str(self._tz),
            "dry_run": True,  # Stage 1: always True
            "fallback_destination": self._fallback,
            "modes": modes_info,
            "color_dot_rules": [
                {"color": c, "destination": d}
                for c, d in self._color_rules.items()
            ],
            "validation_issues": [
                {"severity": v.severity, "message": v.message, "field": v.field}
                for v in self.validate()
            ],
            "as_of": now.isoformat(),
        }

    def _get_next_mode(
        self, now: datetime, active: Optional[ModeWindow]
    ) -> Optional[dict]:
        """Find which mode activates next (for dashboard 'next mode switch' display)."""
        local = now.astimezone(self._tz)
        t = local.time().replace(second=0, microsecond=0)
        candidates = []
        for mode in self._modes.values():
            if active and mode.name == active.name:
                continue
            if mode.is_overnight:
                candidates.append((mode.start, mode))
            else:
                if mode.start > t:
                    candidates.append((mode.start, mode))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        _t, next_m = candidates[0]
        return {"name": next_m.name, "starts_at": _t.strftime("%H:%M")}
