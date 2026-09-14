"""installer.py — download, extract, patch-apply, and record one install.

py_modules/unifideck/stores/w3dhub/installer.py

Orchestrates ``manifest.py`` (what to fetch and in what order) + ``api.py``
(fetch it) + ``ww_mix.py`` (apply the patch-type entries) into one
title's on-disk install, then records it via ``installed_state.py``. See
``docs/w3d-hub-store-spec.md`` §6 for the algorithm this ports, and §13
for why patch application can't be skipped.

The one thing this deliberately does NOT do (v1 scope, spec §12):
chunked/resumable download verification — a whole-file checksum only,
full re-download on any mismatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import installed_state
from .manifest import build_install_plan, build_package_refs, fetch_manifest_chain
from .ww_mix import WWMixError, apply_patch

if TYPE_CHECKING:
    from .api import W3DHubApi
    from .config import W3DHubConfig

logger = logging.getLogger(__name__)

# The one app whose exe isn't "game.exe" — see paths.py-equivalent note in
# docs/w3d-hub-store-spec.md §8. A lookup table rather than the reference's
# inline `app_id == "ecw"` check buried in launch code.
_EXE_OVERRIDES: dict[str, str] = {"ecw": "game500.exe"}
_DEFAULT_EXE = "game.exe"


@dataclass(frozen=True, slots=True)
class InstallProgress:
    phase: str  # "manifest" | "downloading" | "extracting" | "patching"
    package: str | None = None
    bytes_downloaded: int = 0
    bytes_total: int = 0


ProgressCallback = Callable[[InstallProgress], Awaitable[None]]


class InstallError(Exception):
    """Raised on any install-pipeline failure. The message is user-facing."""


def exe_name(app_id: str) -> str:
    return _EXE_OVERRIDES.get(app_id, _DEFAULT_EXE)


def install_dir_for(config: W3DHubConfig, category: str, app_id: str, channel_id: str) -> Path:
    return config.install_base_path / category / f"{app_id}-{channel_id}"


async def install_title(
    api: W3DHubApi,
    config: W3DHubConfig,
    *,
    app_id: str,
    channel_id: str,
    category: str,
    target_version: str,
    uses_ren_folder: bool,
    access_token: str | None = None,
    progress_cb: ProgressCallback | None = None,
) -> installed_state.InstalledEntry:
    """Install (or fully reinstall, for an update) one title. Raises :class:`InstallError`.

    Never partially updates ``installed_state`` — the entry is only
    written after every package has downloaded, verified, and been
    extracted/patched successfully.
    """
    await _emit(progress_cb, InstallProgress(phase="manifest"))
    chain = await fetch_manifest_chain(
        api, app_id, target_version, access_token=access_token, alt=True,
    )
    if chain is None:
        raise InstallError(f"Could not resolve the manifest chain for {app_id}:{target_version}")

    files = build_install_plan(chain)
    refs = build_package_refs(app_id, files)
    if not refs:
        raise InstallError(f"Manifest chain for {app_id}:{target_version} named no packages")

    details_by_key = await _fetch_package_details(api, refs, access_token=access_token)

    install_dir = install_dir_for(config, category, app_id, channel_id)
    install_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = config.package_cache_dir_path
    cache_dir.mkdir(parents=True, exist_ok=True)

    for ref in refs:
        key = (ref.category, ref.subcategory, ref.name, ref.version)
        detail = details_by_key.get(key)
        if detail is None:
            raise InstallError(f"No package details for {ref.name}:{ref.version}")
        download_url = detail.get("download_url")
        checksum = detail.get("checksum")
        if not download_url:
            raise InstallError(f"Package {ref.name}:{ref.version} has no download_url ({detail.get('error')})")

        zip_path = cache_dir / f"{app_id}-{ref.name}-{ref.version}.zip"
        await _download_and_verify(
            api, download_url, zip_path, checksum, progress_cb=progress_cb, package_name=ref.name,
        )

        if ref.is_patch:
            assert ref.patch_file is not None
            await _apply_patch_package(zip_path, install_dir, ref.patch_file.name, progress_cb)
        else:
            await _emit(progress_cb, InstallProgress(phase="extracting", package=ref.name))
            await asyncio.to_thread(_extract_zip, zip_path, install_dir)

    await asyncio.to_thread(
        _write_paths_ini, install_dir, category, app_id, channel_id, uses_ren_folder,
    )

    entry = installed_state.InstalledEntry(
        version=target_version,
        install_path=str(install_dir),
        exe_name=exe_name(app_id),
        installed_at=datetime.now(UTC).isoformat(),
    )
    await asyncio.to_thread(
        installed_state.set_entry, config.installed_state_path, app_id, channel_id, entry,
    )
    return entry


async def uninstall_title(config: W3DHubConfig, app_id: str, channel_id: str) -> None:
    """Remove the install directory and its state entry. Never raises."""
    import shutil

    entry = installed_state.get_entry(config.installed_state_path, app_id, channel_id)
    if entry is not None:
        try:
            await asyncio.to_thread(shutil.rmtree, entry.install_path, True)
        except OSError:
            logger.warning("[W3DHub] uninstall: could not remove %s", entry.install_path)
    await asyncio.to_thread(
        installed_state.remove_entry, config.installed_state_path, app_id, channel_id,
    )


async def _emit(progress_cb: ProgressCallback | None, progress: InstallProgress) -> None:
    if progress_cb is not None:
        await progress_cb(progress)


async def _fetch_package_details(
    api: W3DHubApi, refs: list[Any], *, access_token: str | None,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    payload = [
        {"category": r.category, "subcategory": r.subcategory, "name": r.name, "version": r.version}
        for r in refs
    ]
    details = await api.get_package_details(payload, access_token=access_token, alt=True)
    if details is None:
        raise InstallError("Could not fetch package details")
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for d in details:
        key = (d.get("category", ""), d.get("subcategory", ""), d.get("name", ""), d.get("version", ""))
        result[key] = d
    return result


async def _download_and_verify(
    api: W3DHubApi,
    download_url: str,
    dest: Path,
    checksum: str | None,
    *,
    progress_cb: ProgressCallback | None,
    package_name: str,
    max_attempts: int = 2,
) -> None:
    # No per-chunk progress callback: download_package's on_progress runs
    # synchronously inside the worker thread asyncio.to_thread spawns for
    # the blocking urllib download (api.py's _download_blocking), so it
    # cannot safely schedule a coroutine on this loop from there. v1 emits
    # one "downloading" event per package instead of live byte counts —
    # a coarser but thread-safe granularity; per-chunk progress would need
    # asyncio.run_coroutine_threadsafe against a captured loop reference,
    # not worth the complexity for a single-file-per-package download.
    last_error: str | None = None
    for _attempt in range(max_attempts):
        await _emit(progress_cb, InstallProgress(phase="downloading", package=package_name))
        ok = await api.download_package(download_url, str(dest))
        if not ok:
            last_error = "download failed"
            continue
        if checksum and not await asyncio.to_thread(_sha256_matches, dest, checksum):
            last_error = "checksum mismatch"
            continue
        return
    raise InstallError(f"Could not fetch {package_name}: {last_error}")


def _sha256_matches(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(32 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper() == expected.upper()


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    # Package source is the W3D Hub CDN, verified by checksum before this
    # runs — not arbitrary user input.
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)


async def _apply_patch_package(
    zip_path: Path, install_dir: Path, target_file_name: str, progress_cb: ProgressCallback | None,
) -> None:
    """Extract a patch package and apply it against the already-installed target.

    The zip contains one file, ``<target_file_name>.patch`` (mirroring the
    target's own relative path) — see ``ww_mix.py``'s module docstring.
    """
    await _emit(progress_cb, InstallProgress(phase="patching", package=target_file_name))
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        await asyncio.to_thread(_extract_zip, zip_path, tmp_path)
        patch_file = tmp_path / f"{target_file_name}.patch"
        target_file = install_dir / target_file_name
        if not patch_file.is_file():
            raise InstallError(f"Patch package for {target_file_name} had no {patch_file.name}")
        if not target_file.is_file():
            raise InstallError(
                f"Cannot patch {target_file_name}: no base copy installed yet "
                "(a full package for it should have run earlier in this install)",
            )
        try:
            await asyncio.to_thread(apply_patch, str(patch_file), str(target_file))
        except WWMixError as exc:
            raise InstallError(f"Patch application failed for {target_file_name}: {exc}") from exc


def _write_paths_ini(
    install_dir: Path, category: str, app_id: str, channel_id: str, uses_ren_folder: bool,
) -> None:
    """Port of the reference's ``write_paths_ini`` — format is fully known."""
    data_dir = install_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    client = f"{category}\\{app_id}-{channel_id}"
    fds = f"{category}\\{app_id}-{channel_id}-server"
    lines = [
        "[paths]",
        "RegBase=W3D Hub",
        f"RegClient={client}",
        f"RegFDS={fds}",
        "FileBase=W3D Hub",
        f"FileClient={client}",
        f"FileFDS={fds}",
        f"UseRenFolder={uses_ren_folder}",
    ]
    (data_dir / "paths.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")
