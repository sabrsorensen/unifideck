"""Regression: ``bin/unifideck-launcher`` must restore a missing ``PATH``.

Steam's containerised (steamrt64) client launches shortcuts alongside
itself via steam-runtime-supervisor, which drops ``PATH`` entirely. With
no ``PATH``, ``shutil.which`` falls back to ``os.confstr("CS_PATH")`` but
``subprocess`` falls back to ``os.defpath``; on NixOS those differ, so the
Edge auth browser was found by detection and then failed to spawn with
``FileNotFoundError: 'microsoft-edge'``. The wrapper now seeds ``PATH``
from ``CS_PATH`` so both lookups agree.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_WRAPPER = Path(__file__).resolve().parents[2] / "bin" / "unifideck-launcher"

# Load the wrapper as a module (not ``__main__``) so its top-level env
# sanitising runs but ``main()`` / the dispatcher import do not.
_PROBE = f"""
import importlib.machinery, importlib.util, os
loader = importlib.machinery.SourceFileLoader("wrapper", {str(_WRAPPER)!r})
spec = importlib.util.spec_from_loader("wrapper", loader)
loader.exec_module(importlib.util.module_from_spec(spec))
print(os.environ.get("PATH"))
"""


def _run_probe(env: dict[str, str]) -> str:
    return subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_missing_path_is_restored_from_cs_path():
    env = {k: v for k, v in os.environ.items() if k != "PATH"}
    expected = os.confstr("CS_PATH") or os.defpath
    assert _run_probe(env) == expected


def test_existing_path_is_left_untouched():
    env = dict(os.environ, PATH="/custom/bin:/other/bin")
    assert _run_probe(env) == "/custom/bin:/other/bin"
