"""``install_title`` falls back to the direct-stream download.

py_modules/unifideck/stores/w3dhub/installer.py

Confirmed live 2026-09-15, using the reference W3D Hub Linux launcher's own
source as ground truth (``~/src/w3d_hub_linux_launcher/lib/cache.rb``): a
package with no ``download_url`` in its ``get-package-details`` answer is
not necessarily unavailable — the reference's own fallback for exactly this
case is to stream the bytes straight from ``POST .../get-package`` with the
package identity + a bearer token, instead of treating the missing URL as
fatal. This store used to raise ``InstallError`` immediately instead.

Exercises ``install_title`` end-to-end with ``fetch_manifest_chain`` /
``build_install_plan`` / ``build_package_refs`` monkeypatched to a single
controlled package (real network calls would need a live manifest chain,
which is exactly what these three already have live-verified coverage for
elsewhere — ``test_w3d_hub_manifest.py``). What's under test here is purely
the download-strategy branch in the loop that consumes their output.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

import unifideck.stores.w3dhub.installer as installer_mod
from unifideck.stores.w3dhub.config import W3DHubConfig
from unifideck.stores.w3dhub.installer import InstallError, install_title


class _Ref:
    category = "games"
    subcategory = "apb"
    name = "binaries"
    version = "3.8.1.0"
    is_patch = False
    patch_file = None


class _FakeApi:
    def __init__(self, *, detail: dict[str, Any]) -> None:
        self._detail = detail
        self.download_package_calls: list[tuple[Any, ...]] = []
        self.download_package_direct_calls: list[dict[str, Any]] = []

    async def get_package_details(
        self, packages: list[dict[str, str]], *, access_token: str | None, alt: bool,
    ) -> list[dict[str, Any]]:
        return [{**p, **self._detail} for p in packages]

    async def download_package(self, download_url: str, dest_path: str, **kw: Any) -> bool:
        self.download_package_calls.append((download_url, dest_path))
        _write_empty_zip(dest_path)
        return True

    async def download_package_direct(
        self, category: str, subcategory: str, name: str, version: str,
        dest_path: str, *, access_token: str, **kw: Any,
    ) -> bool:
        self.download_package_direct_calls.append({
            "category": category, "subcategory": subcategory,
            "name": name, "version": version,
            "dest_path": dest_path, "access_token": access_token,
        })
        _write_empty_zip(dest_path)
        return True


def _write_empty_zip(dest_path: str) -> None:
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_path, "w"):
        pass


@pytest.fixture(autouse=True)
def _stub_manifest_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_mod, "fetch_manifest_chain", _fake_fetch_manifest_chain)
    monkeypatch.setattr(installer_mod, "build_install_plan", lambda chain: [])
    monkeypatch.setattr(installer_mod, "build_package_refs", lambda app_id, files: [_Ref()])


async def _fake_fetch_manifest_chain(*a: Any, **kw: Any) -> list[Any]:
    return []


def _config(tmp_path: Path) -> W3DHubConfig:
    import dataclasses

    return dataclasses.replace(W3DHubConfig(), data_dir=str(tmp_path))


async def _install(api: _FakeApi, config: W3DHubConfig, *, access_token: str | None) -> Any:
    return await install_title(
        api, config,
        app_id="apb", channel_id="release", category="games",
        target_version="3.8.1.0", uses_ren_folder=False,
        access_token=access_token,
    )


def test_uses_the_resolved_download_url_when_present(tmp_path: Path) -> None:
    import asyncio

    api = _FakeApi(detail={"download_url": "https://example.invalid/x.zip", "checksum": None})
    asyncio.run(_install(api, _config(tmp_path), access_token="tok"))

    assert len(api.download_package_calls) == 1
    assert api.download_package_calls[0][0] == "https://example.invalid/x.zip"
    assert api.download_package_direct_calls == []


def test_falls_back_to_direct_stream_when_no_download_url(tmp_path: Path) -> None:
    import asyncio

    api = _FakeApi(detail={"download_url": None, "error": "not-found", "checksum": None})
    asyncio.run(_install(api, _config(tmp_path), access_token="tok"))

    assert api.download_package_calls == []
    assert len(api.download_package_direct_calls) == 1
    call = api.download_package_direct_calls[0]
    assert call["category"] == "games"
    assert call["subcategory"] == "apb"
    assert call["name"] == "binaries"
    assert call["version"] == "3.8.1.0"
    assert call["access_token"] == "tok"


def test_raises_clearly_instead_of_fetching_unauthenticated(tmp_path: Path) -> None:
    import asyncio

    api = _FakeApi(detail={"download_url": None, "error": "not-found", "checksum": None})
    with pytest.raises(InstallError, match="no session"):
        asyncio.run(_install(api, _config(tmp_path), access_token=None))

    assert api.download_package_calls == []
    assert api.download_package_direct_calls == []
