"""
Print the most recent routing decisions from the database.

    python -m pathoryx_enterprise.services.routing.preview_recent [--limit N]

Requires DATABASE_URL to be set. Reads from routing.routing_decisions.
Useful for verifying that Phase 4.8B pipeline hooks are recording decisions.

Exit codes:
  0 — printed successfully (zero rows is still OK)
  1 — database connection or query error
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone


def _fmt_dt(val: object) -> str:
    if val is None:
        return "—"
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(val)


def preview_recent(limit: int = 20) -> int:
    """Print recent routing decisions. Returns exit code."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        return 1

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(database_url, pool_pre_ping=True)
    except Exception as exc:
        print(f"ERROR: Cannot create DB engine: {exc}")
        return 1

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, created_at, slide_id, scanner_id, mode, profile,
                           color_dot, color_dot_confidence, destination,
                           routing_reason, override_id, dry_run
                      FROM routing.routing_decisions
                     ORDER BY created_at DESC
                     LIMIT :limit
                """),
                {"limit": limit},
            ).mappings().all()
    except Exception as exc:
        print(f"ERROR querying routing.routing_decisions: {exc}")
        return 1

    if not rows:
        print("No routing decisions recorded yet.")
        print()
        print("If the pipeline is running and routing_policies is configured,")
        print("decisions appear here as each slide completes BabelShark intake.")
        return 0

    # Stats
    total_dry = sum(1 for r in rows if r["dry_run"])
    total_override = sum(1 for r in rows if r["override_id"] is not None)
    destinations = {r["destination"] for r in rows}
    scanners = {r["scanner_id"] for r in rows if r["scanner_id"]}

    print(f"Recent routing decisions (latest {len(rows)}, max {limit})")
    print(f"  dry_run={total_dry}/{len(rows)}  overrides={total_override}  "
          f"destinations={len(destinations)}  scanners={len(scanners)}")
    print()
    print(f"{'ID':>6}  {'Time (UTC)':>19}  {'Slide':30}  {'Scanner':12}  "
          f"{'Mode':16}  {'Dot':6}  {'Conf':5}  {'Destination':28}  {'Reason':30}  DR")
    print("─" * 165)

    for r in rows:
        conf_str = f"{r['color_dot_confidence']:.2f}" if r['color_dot_confidence'] is not None else "  —  "
        slide = (r["slide_id"] or "—")[:30]
        scanner = (r["scanner_id"] or "—")[:12]
        mode = (r["mode"] or "—")[:16]
        dot = (r["color_dot"] or "—")[:6]
        dest = (r["destination"] or "—")[:28]
        reason = (r["routing_reason"] or "—")[:30]
        dr = "YES" if r["dry_run"] else "LIVE"
        dt = _fmt_dt(r["created_at"])
        print(
            f"{r['id']:>6}  {dt:>19}  {slide:30}  {scanner:12}  "
            f"{mode:16}  {dot:6}  {conf_str:5}  {dest:28}  {reason:30}  {dr}"
        )

    print()
    print(f"dry_run=YES for all {total_dry} shown — actual destinations unchanged (Stage 1)")
    return 0


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Print recent DPARS routing decisions from the database."
    )
    parser.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="Maximum number of rows to show (default: 20)",
    )
    args = parser.parse_args()
    sys.exit(preview_recent(args.limit))


if __name__ == "__main__":
    main()
