#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_slides.py — Babel-Shark WSI Collector with multi-watch support

Backward compatible config:
  watch_dir: single folder

New multi-folder config:
  watch_dirs:
    - D:/scanner1
    - D:/scanner2
    - D:/scanner3

"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import yaml

try:
    from .database_manager import DatabaseManager
    from .metadata_intake import extract_and_normalize_metadata
except ImportError:
    from database_manager import DatabaseManager
    from metadata_intake import extract_and_normalize_metadata


__version__ = "1.3.0-multi-watch"


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("collect_slides_multi")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def load_config(path: Union[str, Path]) -> Dict:
    with open(Path(path), "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


def validate_config(conf: Dict) -> List[str]:
    required_keys = ["staging_dir", "wsi_types"]
    issues: List[str] = []

    for key in required_keys:
        if key not in conf:
            issues.append(f"Missing required config key: {key}")

    has_single = bool(conf.get("watch_dir"))
    has_multi = bool(conf.get("watch_dirs"))

    if not has_single and not has_multi:
        issues.append("Missing required config key: watch_dir or watch_dirs")

    if has_multi and not isinstance(conf.get("watch_dirs"), list):
        issues.append("watch_dirs must be a list of folder paths")

    return issues


def _watch_dirs_from_config(conf: Dict) -> List[Path]:
    if isinstance(conf.get("watch_dirs"), list) and conf.get("watch_dirs"):
        return [Path(str(p)) for p in conf["watch_dirs"]]

    if conf.get("watch_dir"):
        return [Path(str(conf["watch_dir"]))]

    raise KeyError("Config must contain either 'watch_dir' or 'watch_dirs'.")


def _temp_in(dest_dir: Path, final_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    prefix = f".{final_name}.tmp."
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, dir=dest_dir)
    os.close(fd)
    return Path(tmp_path)


def atomic_copy(src: Path, dest: Path) -> None:
    tmp = _temp_in(dest.parent, dest.name)
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def atomic_move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(src, dest)
    except OSError:
        tmp = _temp_in(dest.parent, dest.name)
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)

        try:
            os.remove(src)
        except FileNotFoundError:
            pass


def _valid_extensions(conf: Dict) -> Tuple[str, ...]:
    raw_exts = conf.get("wsi_types", [])
    return tuple(str(ext).lower() for ext in raw_exts)


def _file_format(path: Path) -> str:
    return path.suffix.lstrip(".").upper()


def collect_slides(conf: Dict, logger: logging.Logger) -> None:
    source_dirs = _watch_dirs_from_config(conf)
    dest_dir = Path(conf["staging_dir"])
    dest_dir.mkdir(parents=True, exist_ok=True)

    valid_exts: Tuple[str, ...] = _valid_extensions(conf)
    operation_mode: str = conf.get("operation_mode", "copy").lower()

    if operation_mode not in {"copy", "move"}:
        raise ValueError(
            f"Invalid operation_mode={operation_mode!r}. Expected 'copy' or 'move'."
        )

    moved_or_copied = 0
    skipped_duplicate = 0
    metadata_errors = 0
    missing_watch_dirs = 0

    db = DatabaseManager()

    try:
        for source_dir in source_dirs:
            if not source_dir.exists():
                missing_watch_dirs += 1
                logger.info(f"[WARN] Watch folder does not exist: {source_dir}")
                continue

            logger.info(f"[WATCH] Scanning {source_dir}")

            for root, _, files in os.walk(source_dir):
                for file in files:
                    if not file.lower().endswith(valid_exts):
                        continue

                    src = Path(root) / file
                    dest = dest_dir / file

                    try:
                        decision = db.classify_intake(src)

                        if decision["action"] == "skip_duplicate":
                            skipped_duplicate += 1
                            logger.info(f"[SKIP] Duplicate {src}")
                            continue

                        if operation_mode == "move":
                            atomic_move(src, dest)
                            logger.info(f"[Moved] {src} TO {dest}")
                        else:
                            atomic_copy(src, dest)
                            logger.info(f"[Copied] {src} TO {dest}")

                        stat = dest.stat()

                        raw_metadata = {}
                        normalized_metadata = {}

                        try:
                            raw_metadata, normalized_metadata = extract_and_normalize_metadata(
                                dest,
                                conf,
                            )
                            logger.info(f"[META] Extracted metadata for {dest}")
                        except Exception as meta_exc:
                            metadata_errors += 1
                            logger.info(f"[META ERROR] Failed metadata extraction for {dest}: {meta_exc}")

                        full_pipeline = bool(conf.get("enable_full_pipeline", False))
                        defer_trigger = full_pipeline and bool(conf.get("defer_trigger", True))

                        registration = db.register_collected_file(
                            source_path=src,
                            staged_path=dest,
                            file_name=file,
                            file_format=_file_format(dest),
                            file_size=stat.st_size,
                            intake_decision=decision,
                            raw_metadata=raw_metadata,
                            normalized_metadata=normalized_metadata,
                            defer_trigger=defer_trigger,
                        )

                        logger.info(
                            f"[DB] Registered {dest} "
                            f"record_id={registration['record_id']} "
                            f"global_artifact_id={registration['global_artifact_id']} "
                            f"decision={decision['intake_decision']}"
                        )

                        # Full enrichment pipeline (feature-flagged)
                        if full_pipeline:
                            try:
                                from pathoryx_enterprise.services.babelshark.stage_runner import (
                                    BabelSharkStageRunner,
                                )
                                stage_runner = BabelSharkStageRunner(conf, logger)
                                stage_runner.run_enrichment_pipeline(
                                    staged_path=dest,
                                    file_record_id=registration["record_id"],
                                    global_artifact_id=registration["global_artifact_id"],
                                )
                            except Exception as pipeline_exc:
                                logger.error(
                                    f"[PIPELINE] Enrichment failed for {dest}: {pipeline_exc}"
                                )

                        moved_or_copied += 1

                    except Exception as exc:
                        logger.info(f"[ERROR] Failed to {operation_mode} {src}: {exc}")

    finally:
        db.close()

    logger.info(f"[SUMMARY] {moved_or_copied} files {operation_mode}d.")
    logger.info(f"[SUMMARY] {skipped_duplicate} duplicate files skipped.")
    logger.info(f"[SUMMARY] {metadata_errors} metadata extraction errors.")
    logger.info(f"[SUMMARY] {missing_watch_dirs} missing watch folders.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and copy/move new WSI files from one or more watch folders."
    )

    parser.add_argument(
        "--config",
        type=str,
        required=False,
        help="Path to the YAML configuration file. (legacy compatibility)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (e.g., INFO, DEBUG, ERROR).",
    )

    subparsers = parser.add_subparsers(dest="command")

    p_run = subparsers.add_parser("run", help="Run slide collection.")
    p_run.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the YAML configuration file.",
    )

    p_val = subparsers.add_parser("validate", help="Validate configuration file.")
    p_val.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the YAML configuration file.",
    )

    subparsers.add_parser("version", help="Show version and exit.")

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    logger = setup_logging(level=getattr(args, "log_level", "INFO"))

    if args.command is None:
        if args.config:
            config_path = Path(args.config).resolve()
            conf = load_config(config_path)

            logger.info("=" * 50)
            logger.info(" Babel-Shark WSI Data Collection - Multi Watch")
            logger.info("=" * 50)

            collect_slides(conf, logger)
            logger.info("Data collection completed successfully.")
            return 0

        parser.print_help()
        return 2

    if args.command == "run":
        config_path = Path(args.config).resolve()
        conf = load_config(config_path)

        logger.info("=" * 50)
        logger.info(" Babel-Shark WSI Data Collection - Multi Watch")
        logger.info("=" * 50)

        collect_slides(conf, logger)
        logger.info("Data collection completed successfully.")
        return 0

    if args.command == "validate":
        config_path = Path(args.config).resolve()
        conf = load_config(config_path)
        issues = validate_config(conf)

        if issues:
            for msg in issues:
                logger.info(f"[CONFIG] {msg}")
            return 1

        logger.info("[CONFIG] OK")
        return 0

    if args.command == "version":
        logger.info(__version__)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
