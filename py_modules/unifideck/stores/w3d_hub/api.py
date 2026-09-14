"""api.py — thin HTTP client for the W3D Hub launcher API.

py_modules/unifideck/stores/w3d_hub/api.py

W3D Hub has no vendor client and no official API docs. This mirrors the
documented request/response shapes in ``cyberarm/w3d_hub_linux_launcher``'s
``lib/api.rb`` (the reference, open-source Linux launcher this store
replaces the need for) and adds live-verified corrections where the
reference's inline comments turned out incomplete — see
``docs/w3d-hub-store-spec.md`` §13.

Two independent backends exist:

* ``api_endpoint`` (``secure.w3dhub.com``) — the official backend. Adds
  entries gated by the signed-in account's access level.
* ``alt_api_endpoint`` (a community-run mirror) — works fully
  unauthenticated (confirmed live 2026-09-14: ``get-applications`` and
  ``get-package-details`` both returned real data with zero
  ``Authorization`` header). Used for browsing/installing public
  ``release``-channel content without requiring sign-in first.

Every method here is defensive — network/parse failures return ``None``
(or an empty result) and log, never raise, matching this repo's other
unofficial-API clients (e.g. ``stores/battlenet/ownership/game_accounts.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

    from .config import W3DHubConfig

logger = logging.getLogger(__name__)

_USER_AGENT = "Unifideck (W3D Hub store)"

_DEFAULT_HEADERS = {"accept": "application/json", "user-agent": _USER_AGENT}
_FORM_HEADERS = {
    "accept": "application/json",
    "user-agent": _USER_AGENT,
    "content-type": "application/x-www-form-urlencoded",
}


def _form_body(payload: dict[str, Any]) -> str:
    """Encode ``{"data": json.dumps(payload)}`` the way the reference does.

    Every POST endpoint takes a single ``data`` form field carrying a JSON
    string — not a JSON request body. Confirmed live 2026-09-14.
    """
    return urllib.parse.urlencode({"data": json.dumps(payload)})


class W3DHubApi:
    """Async client for the W3D Hub launcher + GSH server-list APIs."""

    def __init__(
        self,
        config: W3DHubConfig,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._config = config
        self._session = session

    async def _session_or_ephemeral(self) -> tuple[aiohttp.ClientSession, bool]:
        import aiohttp as _aiohttp

        if self._session is not None:
            return self._session, False
        return _aiohttp.ClientSession(), True

    async def _post(
        self, base_url: str, path: str, payload: dict[str, Any] | None,
        *, access_token: str | None = None, form: bool = True,
    ) -> dict[str, Any] | list[Any] | None:
        import aiohttp as _aiohttp

        session, owns = await self._session_or_ephemeral()
        headers = dict(_FORM_HEADERS if form else _DEFAULT_HEADERS)
        if access_token:
            headers["authorization"] = f"Bearer {access_token}"
        body = _form_body(payload) if (form and payload is not None) else None
        try:
            async with session.post(
                f"{base_url}{path}",
                data=body,
                headers=headers,
                timeout=_aiohttp.ClientTimeout(total=self._config.request_timeout_seconds),
            ) as resp:
                if resp.status != 200:
                    logger.info("[W3DHub] POST %s: HTTP %d", path, resp.status)
                    return None
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    logger.warning("[W3DHub] POST %s: response was not valid JSON", path)
                    return None
                return data if isinstance(data, (dict, list)) else None
        except _aiohttp.ClientError as exc:
            logger.info("[W3DHub] POST %s: request failed: %s", path, exc)
            return None
        finally:
            if owns:
                await session.close()

    async def _post_dict(
        self, base_url: str, path: str, payload: dict[str, Any] | None,
        *, access_token: str | None = None, form: bool = True,
    ) -> dict[str, Any] | None:
        """:meth:`_post`, narrowed to the (far more common) dict-response case."""
        result = await self._post(base_url, path, payload, access_token=access_token, form=form)
        return result if isinstance(result, dict) else None

    async def _get(
        self, url: str,
    ) -> dict[str, Any] | list[Any] | None:
        import aiohttp as _aiohttp

        session, owns = await self._session_or_ephemeral()
        try:
            async with session.get(
                url,
                headers=_DEFAULT_HEADERS,
                timeout=_aiohttp.ClientTimeout(total=self._config.request_timeout_seconds),
            ) as resp:
                if resp.status != 200:
                    logger.info("[W3DHub] GET %s: HTTP %d", url, resp.status)
                    return None
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    logger.warning("[W3DHub] GET %s: response was not valid JSON", url)
                    return None
                return data if isinstance(data, (dict, list)) else None
        except _aiohttp.ClientError as exc:
            logger.info("[W3DHub] GET %s: request failed: %s", url, exc)
            return None
        finally:
            if owns:
                await session.close()

    # ── Auth ────────────────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> dict[str, Any] | None:
        """``POST /apis/launcher/1/user-login`` with credentials.

        Returns the raw response dict on HTTP 200 — callers must still
        check for an ``"error"`` key (a failed login is still a 200).
        ``None`` only on a transport/parse failure.
        """
        return await self._post_dict(
            self._config.api_endpoint,
            "/apis/launcher/1/user-login",
            {"username": username, "password": password},
        )

    async def refresh_login(self, refresh_token: str) -> dict[str, Any] | None:
        """``POST /apis/launcher/1/user-login`` with a stored refresh token."""
        return await self._post_dict(
            self._config.api_endpoint,
            "/apis/launcher/1/user-login",
            {"refreshToken": refresh_token},
        )

    # ── Catalog ─────────────────────────────────────────────────────────

    async def get_applications(
        self, *, access_token: str | None = None, alt: bool = False,
    ) -> dict[str, Any] | None:
        """``POST /apis/launcher/1/get-applications``.

        The alt backend answers fully unauthenticated (confirmed live);
        the primary backend adds account-gated entries when
        ``access_token`` is supplied. Callers building the full catalog
        should merge both, mirroring the reference's own
        ``Api._applications``.
        """
        base = self._config.alt_api_endpoint if alt else self._config.api_endpoint
        result = await self._post(base, "/apis/launcher/1/get-applications", {}, access_token=access_token)
        return result if isinstance(result, dict) else None

    # ── Packages ────────────────────────────────────────────────────────

    async def get_package_details(
        self,
        packages: list[dict[str, str]],
        *,
        access_token: str | None = None,
        alt: bool = False,
    ) -> list[dict[str, Any]] | None:
        """``POST /apis/launcher/1/get-package-details``.

        ``packages`` entries: ``{"category", "subcategory", "name", "version"}``.
        Returns the ``packages`` array from the response (each entry may
        itself carry an ``"error"`` key, e.g. ``"not-found"`` — a
        nonexistent manifest version is a 200 with a per-package error,
        not an HTTP failure; confirmed live).
        """
        base = self._config.alt_api_endpoint if alt else self._config.api_endpoint
        result = await self._post(
            base, "/apis/launcher/1/get-package-details",
            {"packages": packages}, access_token=access_token,
        )
        if not isinstance(result, dict):
            return None
        pkgs = result.get("packages")
        return pkgs if isinstance(pkgs, list) else None

    async def download_package(
        self, download_url: str, dest_path: str,
        *, on_progress: Any = None,
    ) -> bool:
        """Download ``download_url`` to ``dest_path``. Never raises.

        Runs a blocking ``urllib`` download in a worker thread rather than
        streaming through aiohttp — matches
        ``stores/ubisoft/installer/cache.py``'s pattern (this repo's
        established way to write a downloaded file without an async-unsafe
        blocking ``open()`` inside an ``async def``). ``on_progress(bytes_downloaded,
        total_bytes)`` is called after each chunk if given; ``total_bytes``
        may be ``0`` if the server omits ``Content-Length``.
        """
        return await asyncio.to_thread(
            _download_blocking, download_url, dest_path, on_progress,
        )

    # ── Server list (GSH — unauthenticated, separate backend) ──────────

    async def server_list(self, *, status_level: int = 1) -> list[dict[str, Any]] | None:
        """``GET /listings/getAll/v2?statusLevel=N`` on the GSH endpoint."""
        result = await self._get(
            f"{self._config.gsh_endpoint}/listings/getAll/v2?statusLevel={status_level}",
        )
        return result if isinstance(result, list) else None

    async def server_details(
        self, server_id: str, *, status_level: int = 1,
    ) -> dict[str, Any] | None:
        """``GET /listings/getStatus/v2/:id?statusLevel=N`` on the GSH endpoint."""
        result = await self._get(
            f"{self._config.gsh_endpoint}/listings/getStatus/v2/{server_id}?statusLevel={status_level}",
        )
        return result if isinstance(result, dict) else None


_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _download_blocking(
    download_url: str, dest_path: str, on_progress: Any,
) -> bool:
    """The synchronous half of :meth:`W3DHubApi.download_package`.

    Runs on a worker thread via ``asyncio.to_thread``. Writes to
    ``<dest_path>.part`` then atomically renames — a failed/interrupted
    download never leaves a file at ``dest_path`` for a caller to
    mistake as complete.
    """
    from unifideck.core.net.ssl_helpers import ssl_ctx_permissive

    tmp_path = f"{dest_path}.part"
    try:
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        ctx = ssl_ctx_permissive("W3D Hub package download — outdated Deck cert store")
        req = urllib.request.Request(download_url, headers=_DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=300, context=ctx) as response:
            if response.status != 200:
                logger.warning(
                    "[W3DHub] download %s: HTTP %d", download_url, response.status,
                )
                return False
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
        os.replace(tmp_path, dest_path)
        return True
    except Exception:
        logger.exception("[W3DHub] download %s failed", download_url)
        if os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False
