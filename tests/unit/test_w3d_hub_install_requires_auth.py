"""``install_game`` must refuse fast, and clearly, when logged out.

py_modules/unifideck/stores/w3dhub/store.py

Confirmed live 2026-09-15: catalog browsing and the manifest itself need no
sign-in (``is_available()`` is unconditionally ``True``), but every actual
content-package lookup does — ``get-package-details`` answers a blanket
``{"error": "not-found"}`` when unauthenticated, for every package name and
version tried, not just genuinely missing ones. Without a guard, an install
attempted while logged out used to fail deep inside ``install_title`` with
"Package binaries:X has no download_url (not-found)" — reads like an
upstream data gap, not "you need to log in."

Pinned against the real ``W3DHubStore.install_game`` (not a stub), with the
session file pointed at an empty ``tmp_path`` so "logged out" is the actual
on-disk state ``_access_token()`` reads, the same way it is on a real Deck
after a logout.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from unifideck.stores.w3dhub.config import W3DHubConfig
from unifideck.stores.w3dhub.store import W3DHubStore


class _Bus:
    async def emit(self, event: Any, **kwargs: Any) -> None:
        pass


class _Cache:
    def get(self, *a: Any, **k: Any) -> None:
        return None

    def set(self, *a: Any, **k: Any) -> None:
        pass


class _EmptyCatalogApi:
    """Reachable but answers with nothing — lets the auth-gate test assert
    the call never happens, and the past-the-gate test assert it does."""

    def __init__(self) -> None:
        self.called = False

    async def get_applications(self, **kwargs: Any) -> None:
        self.called = True
        return


@pytest.fixture
def store(tmp_path: Path) -> W3DHubStore:
    s = W3DHubStore(bus=_Bus(), cache=_Cache())  # type: ignore[arg-type]
    s.config = dataclasses.replace(W3DHubConfig(), data_dir=str(tmp_path))
    s._api = _EmptyCatalogApi()  # type: ignore[assignment]
    return s


async def _install(s: W3DHubStore) -> Any:
    return await s.install_game("apb-release")


def test_refuses_fast_when_logged_out(store: W3DHubStore) -> None:
    import asyncio

    assert store._access_token() is None  # sanity: no session file exists
    result = asyncio.run(_install(store))
    assert result.success is False
    assert result.error == "not_authenticated"
    assert store._api.called is False  # gate ran before any network call


def test_proceeds_past_the_auth_gate_once_a_session_exists(
    store: W3DHubStore,
) -> None:
    """A false positive here would block every logged-in install too."""
    import asyncio
    import json

    store.config.refresh_token_path.parent.mkdir(parents=True, exist_ok=True)
    store.config.refresh_token_path.write_text(
        json.dumps({"accessToken": "fake-token"}), encoding="utf-8",
    )

    result = asyncio.run(_install(store))

    assert store._api.called is True
    assert result.error != "not_authenticated"
