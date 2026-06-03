#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py — CLI for pasnet_validator.

Semantics (compatible with old monolith):
    python pasnet_validator.py validate --config config.yaml --log-level INFO
    python pasnet_validator.py run      --config config.yaml --log-level INFO
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .io_adapters import load_config, setup_logging, logger
from .validator import ValidatorConfig, cmd_validate, run_audit, run_pre_rename


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pasnet_validator", description="Pasnet/LIS validator (standalone/pipeline).")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run validator.")
    p_run.add_argument("--config", required=True, help="Path to YAML config.")
    p_run.add_argument("--log-level", default=None, help="Override log_level.")

    p_val = sub.add_parser("validate", help="Validate config.")
    p_val.add_argument("--config", required=True, help="Path to YAML config.")
    p_val.add_argument("--log-level", default=None, help="Override log_level.")

    sub.add_parser("version", help="Show version.")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_cli().parse_args(argv)
    setup_logging("INFO")

    if args.cmd == "version":
        print("pasnet_validator 1.2.0", flush=True)
        return

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    log_level = args.log_level or cfg.get("log_level", "INFO")
    setup_logging(str(log_level))

    if args.cmd == "validate":
        rc = cmd_validate(cfg_path)
        sys.exit(rc)

    vcfg = ValidatorConfig.from_yaml(cfg)
    if not vcfg.enabled:
        logger.info("[SKIP] pasnet_validator.enabled=false")
        return

    if vcfg.mode == "pre_rename":
        run_pre_rename(cfg, vcfg)
    else:
        run_audit(cfg, vcfg)


if __name__ == "__main__":
    main()
