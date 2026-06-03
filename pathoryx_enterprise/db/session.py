"""
Session management.

Always use get_session() as a context manager. It guarantees:
  - the session is committed on clean exit
  - the session is rolled back on exception
  - the session is always closed (returned to pool)

Example::

    from pathoryx_enterprise.db.session import get_session

    with get_session() as session:
        repo = TriggerRepository(session)
        trigger = repo.dequeue_next("qc_adapter")
        # ... process ...
        # session.commit() is called automatically on exit
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session, sessionmaker

from pathoryx_enterprise.db.engine import get_shared_engine

_SessionFactory: sessionmaker[Session] | None = None


def _get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_shared_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,  # prevents lazy-load after commit in background tasks
        )
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context-manager that yields a Session and handles commit/rollback/close.

    On success (no exception): commits and closes.
    On exception: rolls back and closes, then re-raises.
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def make_session() -> Session:
    """
    Return a raw Session for callers that manage their own lifecycle
    (e.g. long-running runner loops that commit per-batch).
    Caller is responsible for commit(), rollback(), and close().
    """
    return _get_session_factory()()
