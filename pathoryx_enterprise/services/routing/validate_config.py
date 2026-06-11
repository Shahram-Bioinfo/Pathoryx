"""
Routing policy config validator — run as:

    python -m pathoryx_enterprise.services.routing.validate_config

Reads the active babelshark config (via BABELSHARK_CONFIG_PATH env var or the
default configs/babelshark_config.yaml), loads the routing_policies section,
and prints a human-readable status report with the active mode, next switch,
validation issues, and dry-run status.

Exit codes:
  0 — config loaded successfully, no errors
  1 — routing_policies section missing
  2 — config loaded but validation errors detected
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


def _load_config(config_path: str | None = None) -> tuple[dict, str]:
    """Load babelshark YAML. Returns (config_dict, resolved_path)."""
    import yaml

    path_str = config_path or os.environ.get("BABELSHARK_CONFIG_PATH") or os.environ.get("BABELSHARK_CONFIG")
    if not path_str:
        for candidate in ("configs/babelshark_config.yaml", "./configs/babelshark_config.yaml"):
            if Path(candidate).exists():
                path_str = candidate
                break
    if not path_str:
        raise FileNotFoundError(
            "No babelshark config found. Set BABELSHARK_CONFIG_PATH or run from the repo root."
        )
    with open(path_str) as fh:
        data = yaml.safe_load(fh) or {}
    return data, str(Path(path_str).resolve())


def validate(config_path: str | None = None) -> int:
    """Print routing policy status. Returns exit code."""
    try:
        cfg, resolved = _load_config(config_path)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR loading config: {exc}")
        return 1

    print(f"Config file : {resolved}")

    policies = cfg.get("routing_policies")
    if not policies:
        print("Status     : NO routing_policies section found in config")
        print()
        print("Fix: add a routing_policies block to the config file.")
        print("See docs/operational_policy_configuration_guide.md — section 9.")
        return 1

    from pathoryx_enterprise.services.routing.engine import RoutingPolicyEngine

    try:
        engine = RoutingPolicyEngine(policies)
    except Exception as exc:
        print(f"ERROR initialising RoutingPolicyEngine: {exc}")
        return 2

    now = datetime.now()
    summary = engine.get_status_summary()

    issues = engine.validate()
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    print(f"Dry-run     : {summary['dry_run']} (Stage 1 — no real destinations changed)")
    print(f"Timezone    : {summary['timezone']}")
    print(f"Fallback    : {summary['fallback_destination']}")
    print()
    print(f"Active mode : {summary['active_mode'] or '(none — using default_mode fallback)'}")
    print(f"Profile     : {summary['active_profile'] or '—'}")
    print(f"Default dst : {summary['active_default_destination'] or '—'}")

    next_m = summary.get("next_mode")
    if next_m:
        print(f"Next switch : {next_m['name']} at {next_m['starts_at']}")
    else:
        print("Next switch : (no upcoming mode change found)")

    print()
    modes = summary.get("modes", [])
    print(f"Modes       : {len(modes)}")
    for m in modes:
        active_marker = " ← ACTIVE" if m["is_active"] else ""
        print(f"  {m['name']:20s}  {m['active_start']}–{m['active_end']}  "
              f"profile={m['profile']}  dst={m['default_destination']}{active_marker}")
        for sc in m.get("scanner_destinations", []):
            print(f"    {sc['scanner_id']:16s} → {sc['destination']}")

    rules = summary.get("color_dot_rules", [])
    print()
    print(f"Color-dot   : {len(rules)}")
    for r in rules:
        print(f"  {r['color']:10s} → {r['destination']}")

    print()
    if errors:
        print(f"Errors      : {len(errors)}")
        for e in errors:
            print(f"  [ERROR]   {e['field'] or ''}: {e['message']}")
    else:
        print("Errors      : 0")

    if warnings:
        print(f"Warnings    : {len(warnings)}")
        for w in warnings:
            print(f"  [WARNING] {w['field'] or ''}: {w['message']}")
    else:
        print("Warnings    : 0")

    print()
    if errors:
        print("Result      : INVALID — fix errors before proceeding")
        return 2
    print("Result      : OK")
    return 0


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate and display the active DPARS routing policy configuration."
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="Path to babelshark_config.yaml (default: BABELSHARK_CONFIG_PATH env var or auto-detect)",
    )
    args = parser.parse_args()
    sys.exit(validate(args.config))


if __name__ == "__main__":
    main()
