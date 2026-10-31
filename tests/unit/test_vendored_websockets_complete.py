"""Regression: the vendored ``py_modules/websockets`` must be importable in full.

``.gitignore`` ignores ``py_modules/*``, so files websockets added after it
was first force-committed were silently left out — notably ``proxy.py``,
which ``websockets.asyncio.client`` imports at module load. Release zips
re-install deps with pip and never noticed; a build straight from the git
tree (e.g. Nix) shipped the partial copy, and every CDP page-content read
failed with ``No module named 'websockets.proxy'``. Epic sign-in, which
reads the authorizationCode from the final JSON page, then always timed out.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_PY_MODULES = Path(__file__).resolve().parents[2] / "py_modules"


@pytest.mark.parametrize("module", [
    "websockets.asyncio.client",
    "websockets.sync.client",
    "websockets.proxy",
])
def test_vendored_websockets_module_imports(module):
    mod = importlib.import_module(module)
    assert Path(mod.__file__).resolve().is_relative_to(_PY_MODULES)
