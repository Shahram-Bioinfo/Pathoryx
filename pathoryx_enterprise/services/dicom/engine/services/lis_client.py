"""Optional LIS (Laboratory Information System) metadata client.

Ported from tool_WSIDicomizer/utils/LIS_utils.py.

IMPORTANT RULES (from original — do not violate):
  - NEVER write to the LIS database. Read-only queries only.
  - Keep the number of requests low — batch case IDs in a single query.
  - The Nexus LIS is a high-priority production system; do not overload it.
  - Never hardcode credentials. Use environment variables or config only.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_metadata_from_LIS(
    case_id_list: list[str],
    metadata_to_collect: list[str],
    cursor,
) -> dict[str, dict[str, str | None]]:
    """
    Batch-fetch metadata from the LIS database via an existing SQL cursor.

    Parameters
    ----------
    case_id_list:
        List of case IDs (e.g. ["E2024047257", "N2024002861"]).
    metadata_to_collect:
        Column names to retrieve (e.g. ["GUID", "FirstName", "LastName"]).
    cursor:
        An active pyodbc (or compatible) DB cursor. The caller is responsible
        for connection lifecycle and must never share credentials in logs.

    Returns
    -------
    dict mapping each normalised case_id to a dict of {column: value}.
    Values are None when the LIS has no record for that case.
    """
    # Normalise case IDs: "E2024047257" → "E/2024/047257", trim to 13 chars
    normalised = [
        (case if "/" in case else case[0] + "/" + case[1:5] + "/" + case[5:])[:13]
        for case in case_id_list
    ]
    cases_metadata: dict[str, dict] = {k: {} for k in normalised}

    for case_id in normalised:
        sql = (
            f"SELECT {', '.join(metadata_to_collect)} "
            f"FROM dbo.Patient WHERE GUID IN "
            f"(SELECT Patient FROM dbo.MedCase WHERE GUID IN "
            f"(SELECT MedCase FROM dbo.MedOrder WHERE OrderNumber = '{case_id}'))"
        )
        try:
            cursor.execute(sql)
            rows = cursor.fetchall()
            for i, col in enumerate(metadata_to_collect):
                cases_metadata[case_id][col] = rows[0][i] if rows else None
        except Exception as exc:
            logger.warning("LIS query failed for case_id=%s: %s", case_id, exc)
            for col in metadata_to_collect:
                cases_metadata[case_id][col] = None

    return cases_metadata


def open_lis_connection(sql_server: str, username: str, password: str):
    """
    Open a pyodbc connection to the LIS SQL Server.

    Returns a (connection, cursor) tuple, or raises RuntimeError if pyodbc
    is not installed or the connection cannot be established.
    """
    try:
        import pyodbc  # optional dependency
    except ImportError as exc:
        raise RuntimeError(
            "pyodbc is required for LIS connectivity. "
            "Install it with: pip install pyodbc"
        ) from exc

    conn_str = f"Driver={{SQL Server}};Server={sql_server};uid={username};Pwd={password}"
    try:
        conn = pyodbc.connect(conn_str)
        return conn, conn.cursor()
    except Exception as exc:
        raise RuntimeError(f"LIS connection failed ({sql_server}): {exc}") from exc
