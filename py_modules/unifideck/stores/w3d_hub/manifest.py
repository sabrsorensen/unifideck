"""manifest.py — W3D Hub install manifests: parsing and the install plan.

py_modules/unifideck/stores/w3d_hub/manifest.py

A manifest is an XML document (``BHP_Game_Manifest``) describing one
app/channel/version's file set. Ported from
``application_manager/manifest.rb`` + the file-accumulation half of
``application_manager/tasks/task.rb``'s ``build_package_list`` — traced
and live-verified 2026-09-14 against real manifests (see
``docs/w3d-hub-store-spec.md`` §13 and ``tests/fixtures/w3d_hub/``, saved
from the real API).

**The chain, and why it can't be skipped.** A manifest is either
``type="Full"`` (every file listed is a fresh, standalone copy) or
``type="Patch"`` (``baseVersion`` names the manifest to walk back to next).
Live-verified: only one of the catalog's 6 downloadable titles (``woa``)
has a directly-fetchable Full manifest at its current release version —
the rest require walking back 1 to 29 patch steps, and ``version: ""``
does not shortcut this (`{"error": "not-found"}`, confirmed live).

**What "patch" means at the file level, live-verified against real
manifest XML (`apb` 3.8.0.0 -> 3.8.1.0):** a `<File>` element with no
nested `<Patch>` child is a plain full-file replacement, delivered in an
ordinary zip package, same as a Full manifest's files. A `<File>` element
*with* a nested `<Patch from="X" package="Y">` child means this file
already exists on disk (from an earlier full/patch step) and ``Y`` is a
small patch package to merge into it (see ``ww_mix.py``'s ``apply_patch``
docstring for what "merge" means — a WWMix container edit, not a binary
diff). Confirmed on real data: `apb`'s Full baseline (3.8.0.0) has *zero*
`<Patch>` children — every file starts as a plain full copy; only later
Patch-type manifests introduce `<Patch>` children for files that already
exist.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import W3DHubApi

logger = logging.getLogger(__name__)

MANIFEST_PACKAGE_NAME = "manifest.xml"

# Bounds the base-version walk — live-measured chain depths top out at 30
# (tsr); this is a generous ceiling against a runaway/circular baseVersion,
# never expected to bind in practice.
_MAX_CHAIN_DEPTH = 60


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """One ``<File>`` entry, with its nested ``<Patch>`` resolved."""

    name: str
    version: str  # the manifest this entry came from, not the patch "from"
    checksum: str | None = None
    package: str | None = None
    removed_since: str | None = None
    is_patch: bool = False
    patch_from: str | None = None

    @property
    def removed(self) -> bool:
        return self.removed_since is not None


@dataclass(frozen=True, slots=True)
class Manifest:
    """One parsed ``BHP_Game_Manifest`` document."""

    game: str
    type: str  # "Full" | "Patch"
    version: str
    base_version: str | None
    files: tuple[ManifestFile, ...]

    @property
    def is_full(self) -> bool:
        return self.type == "Full"


@dataclass(frozen=True, slots=True)
class PackageRef:
    """One package to fetch, in the order it must be applied.

    ``patch_file`` is set only when ``is_patch`` — patch packages are
    1:1 with the file they patch (confirmed by the naming convention:
    ``<basename>.patch.<from-version>``), unlike full packages, which
    commonly bundle several files (e.g. ``binaries``) and don't need a
    single-file association since the whole archive is just extracted.
    """

    category: str
    subcategory: str
    name: str
    version: str
    is_patch: bool
    patch_file: ManifestFile | None = None


def parse_manifest_xml(data: bytes) -> Manifest:
    """Parse a manifest document. Raises ``ET.ParseError`` on malformed XML.

    Unlike the aiohttp-facing code in this store, this is a pure parser —
    callers own error handling (a malformed manifest for a title the user
    is actively installing is worth surfacing, not silently swallowing).

    ``ET.fromstring`` over ``defusedxml``: the input is a small document
    (a few KB, no DOCTYPE/entities in any live-fetched sample) served over
    HTTPS from W3D Hub's own CDN — not arbitrary user-supplied XML — and
    ``defusedxml`` isn't a dependency anywhere else in this repo. Adding
    it for one call site trades a real new dependency for a threat this
    input doesn't present.
    """
    root = ET.fromstring(data)  # noqa: S314 — see docstring; trusted CDN source, not user input
    files = []
    for el in root.findall("File"):
        patch_el = el.find("Patch")
        if patch_el is not None:
            package = patch_el.get("package")
            patch_from = patch_el.get("from")
            is_patch = True
        else:
            package = el.get("package")
            patch_from = None
            is_patch = False
        files.append(
            ManifestFile(
                name=el.get("name", ""),
                version=root.get("version", ""),
                checksum=el.get("checksum"),
                package=package,
                removed_since=el.get("removedsince"),
                is_patch=is_patch,
                patch_from=patch_from,
            ),
        )
    return Manifest(
        game=root.get("game", ""),
        type=root.get("type", ""),
        version=root.get("version", ""),
        base_version=root.get("baseVersion"),
        files=tuple(files),
    )


async def fetch_manifest_chain(
    api: W3DHubApi,
    app_id: str,
    target_version: str,
    *,
    access_token: str | None = None,
    alt: bool = True,
) -> list[Manifest] | None:
    """Walk ``baseVersion`` from ``target_version`` back to a Full manifest.

    Returns the chain **oldest to newest** (ready for
    :func:`build_install_plan`), or ``None`` on any failure — a missing
    manifest, an unparseable one, or a chain that doesn't bottom out in a
    Full manifest within :data:`_MAX_CHAIN_DEPTH` steps.
    """
    newest_first: list[Manifest] = []
    version = target_version
    for _ in range(_MAX_CHAIN_DEPTH):
        details = await api.get_package_details(
            [{
                "category": "games", "subcategory": app_id,
                "name": MANIFEST_PACKAGE_NAME, "version": version,
            }],
            access_token=access_token, alt=alt,
        )
        if not details:
            logger.warning(
                "[W3DHub] manifest chain: no package-details for %s:%s",
                app_id, version,
            )
            return None
        entry = details[0]
        if entry.get("error") or not entry.get("download_url"):
            logger.warning(
                "[W3DHub] manifest chain: %s:%s -> %s",
                app_id, version, entry.get("error") or "no download_url",
            )
            return None
        raw = await _fetch_bytes(entry["download_url"])
        if raw is None:
            return None
        try:
            manifest = parse_manifest_xml(raw)
        except ET.ParseError as exc:
            logger.warning(
                "[W3DHub] manifest chain: %s:%s unparseable: %s",
                app_id, version, exc,
            )
            return None
        newest_first.append(manifest)
        if manifest.is_full:
            return list(reversed(newest_first))
        if not manifest.base_version:
            logger.warning(
                "[W3DHub] manifest chain: %s:%s is a Patch with no "
                "baseVersion", app_id, version,
            )
            return None
        version = manifest.base_version
    logger.warning(
        "[W3DHub] manifest chain: %s exceeded %d steps without reaching Full",
        app_id, _MAX_CHAIN_DEPTH,
    )
    return None


async def _fetch_bytes(url: str) -> bytes | None:
    """One-shot GET, ephemeral session — manifests are small (<10 KB)."""
    import aiohttp as _aiohttp

    try:
        async with _aiohttp.ClientSession() as session, session.get(
            url, timeout=_aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                logger.warning("[W3DHub] GET %s: HTTP %d", url, resp.status)
                return None
            return await resp.read()
    except _aiohttp.ClientError as exc:
        logger.warning("[W3DHub] GET %s failed: %s", url, exc)
        return None


def build_install_plan(chain: list[Manifest]) -> list[ManifestFile]:
    """Accumulate a manifest chain (oldest to newest) into the final file set.

    Ported from ``build_package_list``'s file-accumulation half
    (``application_manager/tasks/task.rb``). A non-patch (plain full-copy)
    entry for a filename **supersedes** every earlier entry for that name
    — patch or not. A patch entry **accumulates** on top of whatever's
    already there instead of replacing it, because each one is a step
    that must be applied in order, not a final answer on its own. A
    ``removedsince`` entry drops the name entirely (unless a later
    manifest reintroduces it, which un-drops it naturally by appending
    again).
    """
    files: list[ManifestFile] = []
    for manifest in chain:
        for file in manifest.files:
            lname = file.name.lower()
            if file.removed:
                files = [f for f in files if f.name.lower() != lname]
                continue
            if not file.is_patch:
                files = [f for f in files if f.name.lower() != lname]
            files.append(file)
    return files


def build_package_refs(app_id: str, files: list[ManifestFile]) -> list[PackageRef]:
    """Dedup the accumulated file list into an ordered, unique package fetch list.

    Preserves the files' accumulation order (oldest-completing step
    first) so a caller processing this list in order always applies a
    file's base/full package before any patch package that touches it —
    ``build_install_plan`` guarantees the full entry for a name comes
    before any of its patch entries in its own output.
    """
    refs: list[PackageRef] = []
    seen: set[tuple[str, str]] = set()
    for file in files:
        if not file.package:
            continue
        key = (file.package, file.version)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            PackageRef(
                category="games",
                subcategory=app_id,
                name=file.package,
                version=file.version,
                is_patch=file.is_patch,
                patch_file=file if file.is_patch else None,
            ),
        )
    return refs
