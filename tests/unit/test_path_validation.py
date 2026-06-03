"""Unit tests for path traversal protection."""
from __future__ import annotations

from pathlib import Path

import pytest

from pathoryx_enterprise.utils.path_validation import PathTraversalError, validate_path_under_roots


def test_valid_path_allowed(tmp_path: Path) -> None:
    allowed = tmp_path / "data"
    allowed.mkdir()
    file = allowed / "slide.svs"
    file.write_bytes(b"data")
    result = validate_path_under_roots(file, [allowed])
    assert result == file.resolve()


def test_traversal_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "data"
    allowed.mkdir()
    # /etc/passwd is outside `allowed`
    with pytest.raises(PathTraversalError):
        validate_path_under_roots(Path("/etc/passwd"), [allowed])


def test_multiple_roots(tmp_path: Path) -> None:
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()
    f = root2 / "slide.svs"
    f.write_bytes(b"data")
    result = validate_path_under_roots(f, [root1, root2])
    assert result == f.resolve()
