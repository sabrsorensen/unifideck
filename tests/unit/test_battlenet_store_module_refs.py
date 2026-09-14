"""Regression: every ``<module>.<name>`` that battlenet/store.py uses must exist.

The #447 game_accounts commit switched ``install_state_mod.read_install_state``
and ``install_state_mod.install_row`` to ``library_mod.*``, which defines
neither. Nothing reaches those lines until an account is signed in, so the
first real library sync crashed with ``AttributeError: module
'unifideck.stores.battlenet.library' has no attribute 'read_install_state'``
(surfaced as a "Battle.net integration failed" toast).
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import unifideck.stores.battlenet.store as store_mod

_STORE_PY = Path(store_mod.__file__)


def _module_aliases(tree: ast.Module) -> dict[str, str]:
    """``from . import x as y`` aliases → absolute module names."""
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
            for alias in node.names:
                aliases[alias.asname or alias.name] = (
                    f"unifideck.stores.battlenet.{alias.name}"
                )
    return aliases


def test_store_module_attribute_references_resolve():
    tree = ast.parse(_STORE_PY.read_text(encoding="utf-8"))
    aliases = _module_aliases(tree)
    missing = sorted({
        f"{node.value.id}.{node.attr} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in aliases
        and not hasattr(importlib.import_module(aliases[node.value.id]), node.attr)
    })
    assert not missing, f"store.py references undefined module attributes: {missing}"
