#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
io_adapters.py — External side-effects and IO for pasnet_validator.

Contains:
- logging setup
- YAML config loading
- atomic excel writing
- SQLite helpers (portable)
- filesystem move/rename helpers
- Pasnet connection + per-case fetch helper

SECURITY / RELIABILITY (ENV-ONLY Credentials):
- NEVER read pasnet server/username/password from YAML.
- Read ONLY from environment variables:
    PASNET_SERVER
    PASNET_USERNAME
    PASNET_PASSWORD
- Optional: load those env vars from a .env file whose PATH is stored in YAML
  (YAML stores only a file path, not the secrets).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

logger = logging.getLogger("pasnet_validator")

# -------------------------
# Convenience aliases (for your debug prints)
# -------------------------
__all__ = [
    "logger",
    "setup_logging",
    "now_utc_iso",
    "load_config",
    "atomic_write_excel_two_sheets",
    "ensure_db",
    "ensure_ops_table_minimal",
    "ensure_post_validation_schema",
    "lookup_op_id",
    "auto_register_op_row",
    "update_ops_summary",
    "insert_log",
    "read_pasnet_credentials",
    "connect_pasnet",
    "fetch_case_slide_infos",
    "resolve_processed_file",
    "safe_rename_in_place",
    "safe_move_to_suspicious",
    "load_env_file_if_configured",
    "file",
    "file_",
    "_file_",
]

# some users do: print(module.file)
file = __file__
file_ = __file__
_file_ = __file__

# ---------------------------------------------------------------------
# Pasnet import — package-safe + standalone-safe
# ---------------------------------------------------------------------

_PASNET_IMPORT_ERROR: Optional[str] = None

try:
    # Preferred when running as a package: babel_shark/pasnet_utilities/...
    from .pasnet_lib import PasnetInfoRetriever  # type: ignore
except Exception as e1:
    try:
        # Fallback for standalone execution (repo root)
        from pasnet_lib import PasnetInfoRetriever  # type: ignore
    except Exception as e2:
        PasnetInfoRetriever = None  # type: ignore
        _PASNET_IMPORT_ERROR = f"relative_import_error={repr(e1)} | absolute_import_error={repr(e2)}"


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------
# YAML config IO
# ---------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


# ---------------------------------------------------------------------
# Optional: load env vars from a .env file (path stored in YAML)
# ---------------------------------------------------------------------

def _parse_env_line(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "=" not in s:
        return None
    k, v = s.split("=", 1)
    k = k.strip()
    v = v.strip().strip('"').strip("'")
    if not k:
        return None
    return k, v


def load_env_file_if_configured(cfg: Dict[str, Any]) -> None:
    """
    If cfg contains:
      pasnet_validator:
        env_file: "C:/secure/pasnet.env"
    then read it and set os.environ for missing keys.

    This keeps secrets OUT of YAML, but still allows daily pipeline without manual set.
    """
    pv = cfg.get("pasnet_validator") if isinstance(cfg.get("pasnet_validator"), dict) else {}
    env_file = pv.get("env_file") if isinstance(pv, dict) else None
    if not env_file:
        return

    p = Path(str(env_file)).expanduser()
    if not p.exists():
        logger.warning(f"[PASNET] env_file configured but not found: {p}")
        return

    try:
        txt = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        loaded = 0
        for line in txt:
            kv = _parse_env_line(line)
            if not kv:
                continue
            k, v = kv
            if k not in os.environ or not os.environ.get(k):
                os.environ[k] = v
                loaded += 1
        logger.info(f"[PASNET] Loaded {loaded} env var(s) from env_file: {p}")
    except Exception as exc:
        logger.exception(f"[PASNET] Failed reading env_file {p}: {exc}")


# ---------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------

def atomic_write_excel_two_sheets(
    dst: Path,
    df1: pd.DataFrame,
    sheet1: str,
    df2: pd.DataFrame,
    sheet2: str,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False, suffix=".xlsx") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pd.ExcelWriter(tmp_path) as writer:
            df1.to_excel(writer, index=False, sheet_name=sheet1)
            df2.to_excel(writer, index=False, sheet_name=sheet2)
        os.replace(tmp_path, dst)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------

OPS_TABLE_DEFAULT = "WSI_Babel_Shark_ops"
LOG_TABLE = "WSI_Babel_Shark_post_validation_log"


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table,),
    ).fetchone()
    return row is not None


def ensure_ops_table_minimal(conn: sqlite3.Connection, ops_table: str) -> None:
    if table_exists(conn, ops_table):
        return
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ops_table} (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id                  TEXT,
            new_file_name           TEXT,
            case_id                 TEXT,
            slide_id                TEXT,
            stain                   TEXT,
            output_path             TEXT,
            status                  TEXT,
            inserted_at             TEXT DEFAULT (datetime('now')),
            post_validation_status  TEXT,
            post_validation_last_time_utc TEXT
        );
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS ix_ops_new_file_name ON {ops_table}(new_file_name);")
    conn.commit()


def get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return [r[1] for r in rows]


def ensure_post_validation_schema(conn: sqlite3.Connection, ops_table: str) -> None:
    ensure_ops_table_minimal(conn, ops_table)

    cols = set(get_table_columns(conn, ops_table))
    if "post_validation_status" not in cols:
        conn.execute(f"ALTER TABLE {ops_table} ADD COLUMN post_validation_status TEXT;")
    if "post_validation_last_time_utc" not in cols:
        conn.execute(f"ALTER TABLE {ops_table} ADD COLUMN post_validation_last_time_utc TEXT;")

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
            id                         INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id                      INTEGER NOT NULL,
            validation_time_utc        TEXT NOT NULL,
            validation_mode            TEXT NOT NULL,
            validation_status          TEXT NOT NULL,
            reason_summary             TEXT,
            pasnet_connection          TEXT,
            pasnet_case_exists         TEXT,
            pasnet_slide_match_type    TEXT,
            pasnet_slide_id            TEXT,
            pasnet_stain_raw           TEXT,
            pasnet_stain_canonical     TEXT,
            extracted_slide_id         TEXT,
            extracted_stain            TEXT,
            extracted_stain_confidence TEXT,
            final_slide_id             TEXT,
            final_stain                TEXT,
            rename_source              TEXT,
            file_action                TEXT,
            details_json               TEXT,
            inserted_at                TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(op_id) REFERENCES {ops_table}(id)
        );
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS ix_postval_op_id ON {LOG_TABLE}(op_id);")
    conn.commit()


def lookup_op_id(conn: sqlite3.Connection, ops_table: str, new_file_name: str) -> Optional[int]:
    s = str(new_file_name or "").strip()
    if not s:
        return None
    row = conn.execute(
        f"SELECT id FROM {ops_table} WHERE new_file_name=? LIMIT 1;",
        (s,),
    ).fetchone()
    return int(row["id"]) if row else None


def auto_register_op_row(conn: sqlite3.Connection, ops_table: str, row: Dict[str, Any]) -> int:
    new_file_name = str(row.get("NewFileName") or row.get("OriginalFileName") or "").strip()
    run_id = str(row.get("RunTime") or "")
    conn.execute(
        f"""
        INSERT INTO {ops_table} (run_id, new_file_name, case_id, slide_id, stain, output_path, status)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            run_id,
            new_file_name,
            str(row.get("CaseID") or ""),
            str(row.get("SlideID") or ""),
            str(row.get("Stain") or ""),
            str(row.get("OutputPath") or ""),
            str(row.get("status") or ""),
        ),
    )
    conn.commit()

    op_id = lookup_op_id(conn, ops_table, new_file_name)
    if op_id is None:
        op_id = int(conn.execute("SELECT last_insert_rowid();").fetchone()[0])
    return op_id


def update_ops_summary(conn: sqlite3.Connection, ops_table: str, op_id: int, status: str, time_utc: str) -> None:
    conn.execute(
        f"""
        UPDATE {ops_table}
        SET post_validation_status=?, post_validation_last_time_utc=?
        WHERE id=?;
        """,
        (status, time_utc, op_id),
    )
    conn.commit()


def insert_log(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    cols = [
        "op_id",
        "validation_time_utc",
        "validation_mode",
        "validation_status",
        "reason_summary",
        "pasnet_connection",
        "pasnet_case_exists",
        "pasnet_slide_match_type",
        "pasnet_slide_id",
        "pasnet_stain_raw",
        "pasnet_stain_canonical",
        "extracted_slide_id",
        "extracted_stain",
        "extracted_stain_confidence",
        "final_slide_id",
        "final_stain",
        "rename_source",
        "file_action",
        "details_json",
    ]
    vals = [payload.get(c) for c in cols]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO {LOG_TABLE} ({','.join(cols)}) VALUES ({placeholders});", vals)
    conn.commit()


# ---------------------------------------------------------------------
# Pasnet credentials & connection (ENV-ONLY)
# ---------------------------------------------------------------------

def _redact(s: Optional[str]) -> str:
    if not s:
        return ""
    if len(s) <= 2:
        return "*" * len(s)
    return s[0] + ("*" * (len(s) - 2)) + s[-1]


def read_pasnet_credentials(cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    ENV-ONLY (Permanent):
    - Never read server/username/password from YAML.
    - Only read from environment variables.
    - Optionally load env from cfg['pasnet_validator']['env_file'] first.
    """
    # optional: bring env vars from a .env file if configured
    load_env_file_if_configured(cfg)

    srv = os.environ.get("PASNET_SERVER")
    usr = os.environ.get("PASNET_USERNAME")
    pw = os.environ.get("PASNET_PASSWORD")

    if srv and usr and pw:
        return (srv, usr, pw)

    return (None, None, None)


def connect_pasnet(cfg: Dict[str, Any]) -> Tuple[Optional[Any], str]:
    """
    Returns: (connection_or_None, "OK"|"FAILED")
    """
    if PasnetInfoRetriever is None:
        logger.error(
            "[PASNET] PasnetInfoRetriever import failed. "
            f"import_error={_PASNET_IMPORT_ERROR}"
        )
        return None, "FAILED"

    server, username, password = read_pasnet_credentials(cfg)

    if not (server and username and password):
        logger.error(
            "[PASNET] Missing ENV credentials. Need PASNET_SERVER, PASNET_USERNAME, PASNET_PASSWORD. "
            f"seen: PASNET_SERVER={bool(os.environ.get('PASNET_SERVER'))} "
            f"PASNET_USERNAME={bool(os.environ.get('PASNET_USERNAME'))} "
            f"PASNET_PASSWORD_SET={bool(os.environ.get('PASNET_PASSWORD'))}"
        )
        return None, "FAILED"

    logger.info(
        f"[PASNET] Attempting connection: server={server!r} username={username!r} password={_redact(password)!r}"
    )

    try:
        con = PasnetInfoRetriever(server=server, username=username, password=password)
        logger.info("[PASNET] Connection established.")
        return con, "OK"
    except Exception as exc:
        logger.exception(f"[PASNET] Connection failed with exception: {exc}")
        return None, "FAILED"


def fetch_case_slide_infos(pasnet_con: Any, pasnet_case_id: str) -> Tuple[bool, pd.DataFrame]:
    try:
        df = pasnet_con.get_slide_infos_from_case(pasnet_case_id)
        if df is None or df.empty:
            return False, pd.DataFrame(columns=["slide_id", "staining"])

        cols = {c.lower(): c for c in df.columns}
        if "slide_id" not in df.columns and "slide_id" in cols:
            df = df.rename(columns={cols["slide_id"]: "slide_id"})
        if "staining" not in df.columns and "staining" in cols:
            df = df.rename(columns={cols["staining"]: "staining"})

        return True, df
    except Exception:
        return False, pd.DataFrame(columns=["slide_id", "staining"])


# ---------------------------------------------------------------------
# Filesystem actions
# ---------------------------------------------------------------------

def resolve_processed_file(row: Dict[str, Any]) -> Optional[Path]:
    out_root = str(row.get("OutputPath") or "").strip()
    case_id = str(row.get("CaseID") or "").strip()
    new_name = str(row.get("NewFileName") or "").strip()

    if not out_root or not new_name:
        return None

    p1 = Path(out_root) / case_id / new_name if case_id else Path(out_root) / new_name
    if p1.exists():
        return p1

    p2 = Path(out_root) / new_name
    if p2.exists():
        return p2

    return None


def safe_rename_in_place(src: Path, new_name: str) -> Path:
    dst = src.with_name(new_name)
    if dst.exists():
        stem, suf = dst.stem, dst.suffix
        dst = src.with_name(f"{stem}__dup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suf}")
    os.replace(src, dst)
    return dst


def safe_move_to_suspicious(src: Path, suspicious_root: Path, case_id: str) -> Path:
    dst_dir = suspicious_root / (case_id or "unknown_case")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        stem, suf = dst.stem, dst.suffix
        dst = dst_dir / f"{stem}__dup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suf}"
    shutil.move(str(src), str(dst))
    return dst
