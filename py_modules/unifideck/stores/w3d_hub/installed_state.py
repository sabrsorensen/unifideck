"""installed_state.py — the local install-state marker.

py_modules/unifideck/stores/w3d_hub/installed_state.py

There is no vendor-client catalog to read for install/ownership state
(unlike Ubisoft's ``ubisoft_id_map.json``, which bridges IDs across
systems that separately know about a game) — W3D Hub is the one thing
that knows a title is installed at all, so this is the source of truth.
See ``docs/w3d-hub-store-spec.md`` §8.

Keyed on ``"<app_id>-<channel>"`` (matching the reference's own
``Store.settings[:games]`` key shape, and the ``paths.ini``
``RegClient``/``FileClient`` naming — see ``prefix.py``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InstalledEntry:
    """One installed title's local state."""

    version: str
    install_path: str
    exe_name: str
    installed_at: str


def _key(app_id: str, channel: str) -> str:
    return f"{app_id}-{channel}"


def load(path: Path) -> dict[str, InstalledEntry]:
    """Read the state file. Never raises — a missing/corrupt file is empty state."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, InstalledEntry] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            result[key] = InstalledEntry(
                version=str(value["version"]),
                install_path=str(value["install_path"]),
                exe_name=str(value["exe_name"]),
                installed_at=str(value["installed_at"]),
            )
        except KeyError:
            logger.warning("[W3DHub] installed_state: dropping malformed entry %r", key)
    return result


def _save(path: Path, state: dict[str, InstalledEntry]) -> None:
    """Atomic write — a reader must never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: asdict(entry) for key, entry in state.items()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def set_entry(path: Path, app_id: str, channel: str, entry: InstalledEntry) -> None:
    """Record (or overwrite) one title's install state."""
    state = load(path)
    state[_key(app_id, channel)] = entry
    _save(path, state)


def remove_entry(path: Path, app_id: str, channel: str) -> None:
    """Drop one title's install state, if present. No-op otherwise."""
    state = load(path)
    if state.pop(_key(app_id, channel), None) is not None:
        _save(path, state)


def get_entry(path: Path, app_id: str, channel: str) -> InstalledEntry | None:
    return load(path).get(_key(app_id, channel))


def all_entries(path: Path) -> dict[str, InstalledEntry]:
    return load(path)
