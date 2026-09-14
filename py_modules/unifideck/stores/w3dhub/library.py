"""library.py — join the catalog and local install state into Games.

py_modules/unifideck/stores/w3dhub/library.py

Unlike every other store, there is no vendor-client catalog to read for
install/ownership state — see ``docs/w3d-hub-store-spec.md`` §5.
``ren`` (C&C Renegade) is excluded: it has no downloadable manifest (a
Windows-registry import from an existing Steam/GOG install, upstream —
see ``docs/w3d-hub-store-spec.md`` §6), so it isn't installable through
this store at all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.core.types.domain import Game

from . import installed_state
from .catalog import fetch_catalog, load_cached_catalog, save_cached_catalog
from .installer import install_dir_for

if TYPE_CHECKING:
    from unifideck.core.cache_manager import CacheManager

    from .api import W3DHubApi
    from .catalog import CatalogEntry, Channel
    from .config import W3DHubConfig
    from .installed_state import InstalledEntry

logger = logging.getLogger(__name__)

STORE_NAME = "w3dhub"
CHANNEL = "release"  # v1 only ever installs the public release channel
NOT_DOWNLOADABLE = frozenset({"ren"})  # see module docstring


def _game_id(app_id: str) -> str:
    return f"{app_id}-{CHANNEL}"


async def build_library(
    api: W3DHubApi,
    config: W3DHubConfig,
    cache: CacheManager,
    *,
    launcher_path: str,
    access_token: str | None = None,
) -> list[Game]:
    """Fetch/refresh the catalog, join it against local install state.

    Network failure degrades to the last cached catalog rather than an
    empty library — an offline sync shouldn't make installed games
    disappear from the UI.
    """
    catalog = await fetch_catalog(api, access_token=access_token)
    if catalog is not None:
        save_cached_catalog(cache, catalog)
    else:
        catalog = load_cached_catalog(cache)
    if catalog is None:
        logger.warning("[W3DHub] no catalog available (network + cache both empty)")
        return []

    installed = installed_state.all_entries(config.installed_state_path)
    games: list[Game] = []
    for entry in catalog.entries.values():
        if entry.id in NOT_DOWNLOADABLE:
            continue
        release = entry.channel(CHANNEL)
        if release is None:
            continue
        games.append(_build_game(entry, release, installed, launcher_path))
    return games


def _build_game(
    entry: CatalogEntry,
    release: Channel,
    installed: dict[str, InstalledEntry],
    launcher_path: str,
) -> Game:
    from unifideck.services.shortcut.games_map import generate_app_id

    game_id = _game_id(entry.id)
    marker = installed.get(game_id)
    exe_path = f"{marker.install_path}/{marker.exe_name}" if marker else None
    return Game(
        app_id=generate_app_id(launcher_path, f"{STORE_NAME}:{game_id}"),
        store=STORE_NAME,
        store_game_id=game_id,
        title=entry.name,
        installed=marker is not None,
        install_path=marker.install_path if marker else None,
        exe_path=exe_path,
        # Every W3D Hub title is free — no purchase/entitlement gate on the
        # public release channel (unlike Battle.net, where the tag marks a
        # genuine F2P/subscription distinction among otherwise-paid titles).
        tags=["free_to_play"],
        metadata={
            "channel": CHANNEL,
            "category": entry.category,
            "available_version": release.current_version,
            "installed_version": marker.version if marker else None,
            "uses_ren_folder": entry.uses_ren_folder,
        },
    )


def resolve_install_dir(config: W3DHubConfig, category: str, app_id: str) -> str:
    return str(install_dir_for(config, category, app_id, CHANNEL))
