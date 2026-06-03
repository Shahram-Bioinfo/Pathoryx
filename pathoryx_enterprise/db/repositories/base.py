"""
Base repository with unit-of-work helpers.
All repositories accept a Session and operate within the caller's transaction.
"""
from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
