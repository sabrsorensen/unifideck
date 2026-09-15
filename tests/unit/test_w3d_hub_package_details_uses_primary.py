"""Package-details lookups must use the authenticated primary backend.

py_modules/unifideck/stores/w3dhub/installer.py — ``_fetch_package_details``

Confirmed live 2026-09-15: the alt/community-mirror backend answers every
*content*-package lookup with a blanket ``{"error": "not-found"}`` — not
just for genuinely missing packages, but for ones proven to exist (a
manifest-listed ``.mix`` file, and the exact package a real logged-in
install failed on). ``_fetch_package_details`` used to hardcode
``alt=True`` regardless of whether the caller was authenticated, so a
logged-in install never actually reached the primary backend at all —
every install failed identically whether logged in or out.

``install_game``'s own auth gate (see ``test_w3d_hub_install_requires_auth.py``)
means this function only ever runs with a real token in practice, but this
pins the underlying contract directly so a future refactor that reaches it
some other way can't silently regress back to always-alt.
"""
from __future__ import annotations

from typing import Any

from unifideck.stores.w3dhub.installer import _fetch_package_details
from unifideck.stores.w3dhub.manifest import PackageRef


class _RecordingApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_package_details(
        self, packages: list[dict[str, str]], *, access_token: str | None, alt: bool,
    ) -> list[dict[str, Any]]:
        self.calls.append({"access_token": access_token, "alt": alt})
        return [
            {**p, "download_url": "https://example.invalid/x", "checksum": None}
            for p in packages
        ]


def _refs() -> list[PackageRef]:
    return [
        PackageRef(
            category="games", subcategory="apb", name="binaries",
            version="3.8.1.0", is_patch=False,
        ),
    ]


async def _run(api: _RecordingApi, access_token: str | None) -> None:
    await _fetch_package_details(api, _refs(), access_token=access_token)  # type: ignore[arg-type]


def test_uses_primary_when_authenticated() -> None:
    import asyncio

    api = _RecordingApi()
    asyncio.run(_run(api, "fake-token"))
    assert api.calls == [{"access_token": "fake-token", "alt": False}]


def test_falls_back_to_alt_when_logged_out() -> None:
    import asyncio

    api = _RecordingApi()
    asyncio.run(_run(api, None))
    assert api.calls == [{"access_token": None, "alt": True}]
