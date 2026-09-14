"""W3D Hub store — the ``StoreBase`` implementation.

py_modules/unifideck/stores/w3dhub/store.py

No vendor client at all — the backend is a plain JSON/HTTPS API, so
Unifideck is its own client: this store owns auth (a plain username/
password POST, no OAuth/browser), the catalog fetch, package download +
WWMix patch application, and launch (via the shared
``launcher/proton/handlers/generic.py`` dispatch — see
``docs/w3d-hub-store-spec.md`` §7). Every title shares one Proton prefix
(§3); see ``launcher/proton/fixes/game_fixes.py``'s W3D Hub entries for
the one-time winetricks bootstrap and
``launcher/proton/infrastructure/core.py``'s ``_w3d_hub_prefix_path`` for
the constant path.

**v1 scope** (see spec §6/§12): installs the public ``release`` channel
only, always a full (re)install on update — no incremental patch-chain
reuse across installs, no restricted-channel access-level UI. ``ren``
(C&C Renegade) has no downloadable manifest and isn't installable through
this store.

**Known simplification, not yet hardened:** the refresh token is stored
in a plain JSON file (``config.refresh_token_path``), not through
``security.SecureTokenStore`` the way GOG/Microsoft encrypt theirs. A W3D
Hub session token is lower-value than a GOG/Microsoft OAuth grant, but
this is still worth revisiting before this store is considered done.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types.domain import Game, StoreInfo
from unifideck.core.types.results import AuthResult, InstallResult, Result
from unifideck.stores.shared.store_base import StoreBase

from . import config as store_config
from . import installed_state
from .api import W3DHubApi
from .catalog import fetch_catalog
from .installer import InstallError, install_title, uninstall_title
from .library import CHANNEL, STORE_NAME, build_library

if TYPE_CHECKING:
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus import EventBus

logger = logging.getLogger(__name__)


class W3DHubStore(StoreBase):
    """W3D Hub — own API client, own downloader, shared Proton prefix."""

    # ``name`` is a literal, not ``STORE_NAME``: check 3 in
    # ``scripts/validate_architecture.py`` reads this value statically and
    # matches it against the directory name, and it cannot resolve a
    # constant reference (same reason GameVaultStore's store_info docstring
    # gives).
    store_info = StoreInfo(
        name="w3dhub",
        display_name="W3D Hub",
        auth_method="manual",
        icon_asset="w3dhub.png",
        supports_install=True,
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: Any | None = None,
    ) -> None:
        super().__init__(bus, cache, plugin_dir, config)
        self.config = store_config.from_config_manager(config)
        self._api = W3DHubApi(self.config)

    # ── helpers ─────────────────────────────────────────────────────────

    def _launcher_path(self) -> str:
        from pathlib import Path

        base = Path(self._plugin_dir) if self._plugin_dir else Path()
        return str(base / "bin" / "unifideck-launcher")

    def _load_session(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config.refresh_token_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_session(self, session: dict[str, Any]) -> None:
        try:
            self.config.refresh_token_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config.refresh_token_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(session), encoding="utf-8")
            tmp.replace(self.config.refresh_token_path)
        except OSError:
            logger.warning("[W3DHub] could not persist session")

    def _access_token(self) -> str | None:
        token = self._load_session().get("accessToken")
        return token if isinstance(token, str) and token else None

    def _parse_game_id(self, game_id: str) -> tuple[str, str] | None:
        """``"<app_id>-<channel>"`` -> ``(app_id, channel)``. ``None`` if malformed."""
        suffix = f"-{CHANNEL}"
        if not game_id.endswith(suffix):
            return None
        app_id = game_id[: -len(suffix)]
        return (app_id, CHANNEL) if app_id else None

    # ── StoreBase: auth ─────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """The public catalog needs no sign-in — always browsable/installable."""
        return True

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """Plain username/password login. See spec §4 on why there's no OAuth step."""
        username = str(kwargs.get("username", ""))
        password = str(kwargs.get("password", ""))
        if not username or not password:
            return AuthResult(success=False, error="missing_credentials", store=STORE_NAME)
        response = await self._api.login(username, password)
        if response is None:
            return AuthResult(success=False, error="request_failed", store=STORE_NAME)
        if response.get("error"):
            return AuthResult(success=False, error=str(response["error"]), store=STORE_NAME)
        self._save_session(response)
        return AuthResult(success=True, action="authenticated", tokens_cached=True, store=STORE_NAME)

    async def complete_auth(self, **kwargs: Any) -> AuthResult:
        """Single-step auth (see :meth:`start_auth`) — reports the cached session."""
        session = self._load_session()
        if session.get("accessToken"):
            return AuthResult(success=True, action="authenticated", tokens_cached=True, store=STORE_NAME)
        return AuthResult(success=False, error="not_authenticated", store=STORE_NAME)

    async def logout(self) -> Result:
        try:
            self.config.refresh_token_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("[W3DHub] logout: could not remove session file")
        return Result(success=True, store=STORE_NAME)

    # ── StoreBase: library ───────────────────────────────────────────────

    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        return await build_library(
            self._api, self.config, self._cache,
            launcher_path=self._launcher_path(),
            access_token=self._access_token(),
        )

    # ── StoreBase: install ───────────────────────────────────────────────

    async def install_game(self, game_id: str, **kwargs: Any) -> InstallResult:
        parsed = self._parse_game_id(game_id)
        if parsed is None:
            return InstallResult(success=False, error="invalid_game_id", game_id=game_id)
        app_id, channel_id = parsed

        catalog = await fetch_catalog(self._api, access_token=self._access_token())
        entry = catalog.get(app_id) if catalog else None
        if entry is None:
            return InstallResult(success=False, error="not_in_catalog", game_id=game_id)
        release = entry.channel(channel_id)
        if release is None:
            return InstallResult(success=False, error="channel_not_found", game_id=game_id)

        try:
            installed = await install_title(
                self._api, self.config,
                app_id=app_id, channel_id=channel_id, category=entry.category,
                target_version=release.current_version,
                uses_ren_folder=entry.uses_ren_folder,
                access_token=self._access_token(),
            )
        except InstallError as exc:
            logger.warning("[W3DHub] install %s failed: %s", game_id, exc)
            return InstallResult(success=False, error=str(exc), game_id=game_id)

        return InstallResult(
            success=True, game_id=game_id, install_path=installed.install_path,
        )

    async def uninstall_game(self, game_id: str, **kwargs: Any) -> Result:
        parsed = self._parse_game_id(game_id)
        if parsed is None:
            return Result(success=False, error="invalid_game_id")
        app_id, channel_id = parsed
        await uninstall_title(self.config, app_id, channel_id)
        return Result(success=True, store=STORE_NAME)

    async def update_game(self, game_id: str, **kwargs: Any) -> InstallResult:
        """A full reinstall — see spec §6/§12 on why v1 has no incremental update."""
        return await self.install_game(game_id, **kwargs)

    async def check_for_updates(self) -> list[str]:
        catalog = await fetch_catalog(self._api, access_token=self._access_token())
        if catalog is None:
            return []
        outdated: list[str] = []
        for entry in catalog.entries.values():
            release = entry.channel(CHANNEL)
            if release is None:
                continue
            game_id = f"{entry.id}-{CHANNEL}"
            marker = installed_state.get_entry(self.config.installed_state_path, entry.id, CHANNEL)
            if marker is not None and marker.version != release.current_version:
                outdated.append(game_id)
        return outdated

    async def get_game_size(self, game_id: str) -> int | None:
        parsed = self._parse_game_id(game_id)
        if parsed is None:
            return None
        app_id, channel_id = parsed
        marker = installed_state.get_entry(self.config.installed_state_path, app_id, channel_id)
        if marker is None:
            return None
        return await _dir_size(marker.install_path)

    async def get_installed_path(self, game_id: str) -> str | None:
        parsed = self._parse_game_id(game_id)
        if parsed is None:
            return None
        app_id, channel_id = parsed
        marker = installed_state.get_entry(self.config.installed_state_path, app_id, channel_id)
        return marker.install_path if marker else None

    # get_prefix_path: deliberately NOT overridden. Every title's
    # install_path is a specific subdirectory of the shared prefix, not
    # the prefix itself — uninstall must reclaim only that subdirectory,
    # never the prefix other titles still depend on. W3D Hub is not in
    # launcher.wrapper_stores.WRAPPER_STORES for the same reason (spec §7):
    # prefix_owns_game_install() being False for this store is what keeps
    # resolve_size_root/uninstall using install_path as the root instead
    # of the whole prefix.


async def _dir_size(path: str) -> int:
    import asyncio
    from pathlib import Path

    def _walk() -> int:
        total = 0
        root = Path(path)
        if not root.is_dir():
            return 0
        for entry in root.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total

    return await asyncio.to_thread(_walk)
