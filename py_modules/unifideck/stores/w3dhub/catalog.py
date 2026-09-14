"""catalog.py — the W3D Hub application catalog, cached.

py_modules/unifideck/stores/w3dhub/catalog.py

``get-applications`` on its own answers unauthenticated (confirmed live
2026-09-14 — see ``docs/w3d-hub-store-spec.md`` §13), so this is simpler
than Battle.net's PUB-catalog cache: no local-file freshness question, no
"has the client populated its cache yet" race. Cached via
``CacheManager`` purely to avoid a network round trip on every sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.core.cache_manager import CacheManager

    from .api import W3DHubApi

logger = logging.getLogger(__name__)

CACHE_NAMESPACE = "w3dhub"
CACHE_KEY = "catalog"


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    name: str
    current_version: str
    user_level: str


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One catalog application — a game, in practice (category == "games")."""

    id: str
    name: str
    category: str
    type: str
    channels: tuple[Channel, ...]
    uses_ren_folder: bool = False
    uses_engine_cfg: bool = False

    def channel(self, channel_id: str) -> Channel | None:
        return next((c for c in self.channels if c.id == channel_id), None)


def _parse_channel(raw: Any) -> Channel | None:
    if not isinstance(raw, dict):
        return None
    channel_id = raw.get("id")
    if not isinstance(channel_id, str):
        return None
    return Channel(
        id=channel_id,
        name=str(raw.get("name") or channel_id),
        current_version=str(raw.get("current-version") or ""),
        user_level=str(raw.get("user-level") or "public"),
    )


def _extended_flag(extended_data: list[Any], name: str) -> bool:
    for item in extended_data:
        if isinstance(item, dict) and item.get("name") == name:
            return str(item.get("value", "")).strip().lower() == "true"
    return False


def _parse_entry(raw: dict[str, Any]) -> CatalogEntry | None:
    app_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(app_id, str) or not isinstance(name, str):
        return None
    raw_channels = raw.get("channels")
    channels = tuple(
        c for c in (
            _parse_channel(entry) for entry in raw_channels
        ) if c is not None
    ) if isinstance(raw_channels, list) else ()
    extended_data = raw.get("extended-data")
    extended_data = extended_data if isinstance(extended_data, list) else []
    return CatalogEntry(
        id=app_id,
        name=name,
        category=str(raw.get("category") or ""),
        type=str(raw.get("type") or ""),
        channels=channels,
        uses_ren_folder=_extended_flag(extended_data, "usesRenFolder"),
        uses_engine_cfg=_extended_flag(extended_data, "usesEngineCfg"),
    )


def parse_applications(response: dict[str, Any]) -> list[CatalogEntry]:
    """Parse a ``get-applications`` response into game catalog entries.

    Non-game applications (the launcher itself, ``category: "core-apps"``)
    are dropped — never surfaced as installable titles. Malformed entries
    are skipped individually rather than failing the whole catalog.
    """
    apps = response.get("applications")
    if not isinstance(apps, list):
        return []
    entries = []
    for raw in apps:
        if not isinstance(raw, dict) or raw.get("category") != "games":
            continue
        entry = _parse_entry(raw)
        if entry is not None:
            entries.append(entry)
    return entries


@dataclass
class Catalog:
    entries: dict[str, CatalogEntry] = field(default_factory=dict)

    def get(self, app_id: str) -> CatalogEntry | None:
        return self.entries.get(app_id)


async def fetch_catalog(
    api: W3DHubApi, *, access_token: str | None = None,
) -> Catalog | None:
    """Fetch and merge both backends' application lists.

    The alt backend (community mirror) answers unauthenticated and
    carries every public title; the primary backend adds anything gated
    by the signed-in account's access level. A title present in both is
    taken from the primary backend (it reflects the account's real
    access), falling back to alt-only entries otherwise — mirrors the
    reference's own ``Api._applications`` merge, simplified since v1 has
    no per-channel access-level UI yet (§12: every live channel observed
    so far is ``"public"``).
    """
    alt = await api.get_applications(alt=True)
    primary = await api.get_applications(access_token=access_token, alt=False) if access_token else None
    if alt is None and primary is None:
        logger.warning("[W3DHub] catalog fetch failed on both backends")
        return None
    merged: dict[str, CatalogEntry] = {}
    for entry in parse_applications(alt or {}):
        merged[entry.id] = entry
    for entry in parse_applications(primary or {}):
        merged[entry.id] = entry  # primary wins on conflict
    return Catalog(entries=merged)


def load_cached_catalog(cache: CacheManager) -> Catalog | None:
    try:
        raw = cache.get(CACHE_NAMESPACE, CACHE_KEY)
    except Exception:  # cache miss must never break a library read
        return None
    if not isinstance(raw, dict):
        return None
    entries: dict[str, CatalogEntry] = {}
    for app_id, entry_dict in raw.items():
        if not isinstance(entry_dict, dict):
            continue
        channels = tuple(
            Channel(**c) for c in entry_dict.get("channels", []) if isinstance(c, dict)
        )
        entries[app_id] = CatalogEntry(
            id=str(entry_dict.get("id", app_id)),
            name=str(entry_dict.get("name", app_id)),
            category=str(entry_dict.get("category", "")),
            type=str(entry_dict.get("type", "")),
            channels=channels,
            uses_ren_folder=bool(entry_dict.get("uses_ren_folder", False)),
            uses_engine_cfg=bool(entry_dict.get("uses_engine_cfg", False)),
        )
    return Catalog(entries=entries)


def save_cached_catalog(cache: CacheManager, catalog: Catalog) -> None:
    from dataclasses import asdict

    try:
        cache.set(CACHE_NAMESPACE, CACHE_KEY, {k: asdict(v) for k, v in catalog.entries.items()})
    except Exception:
        logger.warning("[W3DHub] catalog cache write failed")
