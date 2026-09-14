"""ww_mix.py — WWMix (MIX1/MIX2) container reader/writer + patch application.

py_modules/unifideck/stores/w3d_hub/ww_mix.py

Westwood's classic MIX archive format, as W3D Hub still uses it: a
header (magic + three int32 offsets), an entry table (CRC32-of-uppercased-
name + content offset + length, sorted by CRC32), and an entry-names
table. No compression. "Encrypted" (``MIX2`` vs ``MIX1`` magic) is a
header-only distinction in the reference implementation — no cipher code
exists anywhere in it — so this port carries the flag through for
round-trip fidelity but never touches any actual encryption.

Ported from ``lib/ww_mix.rb`` (287 LOC total, reader + writer) in
`cyberarm/w3d_hub_linux_launcher` — read from the observed binary layout
(offsets, field sizes, sort order), not translated line-by-line; see
``docs/w3d-hub-store-spec.md`` §12 on why (no upstream LICENSE).

**Patch application** (the actual reason this module has to exist for
v1 — see ``docs/w3d-hub-store-spec.md`` §6/§13): a "patch package" is a
downloaded zip containing one file, ``<target-name>.patch``, itself a
WWMix archive. That archive's entries are one special metadata entry
(``.w3dhub.patch`` or ``.bhppatch``, a JSON blob:
``{"removedFiles": [...], "updatedFiles": [...]}``) plus one whole fresh
copy of every updated file. Applying it is a **container edit, not a
binary diff**: open the target mix, delete the named ``removedFiles``
entries, copy in the fresh ``updatedFiles`` entries by name (replacing
any existing entry of the same name), and rewrite. Traced fully in the
reference's ``apply_patch`` (``application_manager/tasks/task.rb``) —
confirmed there is no delta/diff algorithm anywhere in the format.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import zlib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MIX1_HEADER = 0x3158494D
MIX2_HEADER = 0x3258494D

_PATCH_META_NAMES = (".w3dhub.patch", ".bhppatch")


class WWMixError(Exception):
    """Raised on a malformed/unreadable WWMix container.

    Callers doing an install should treat this as a hard install
    failure for the affected title, not something to silently paper
    over — an undetected corrupt patch application would leave a game
    data file quietly broken.
    """


@dataclass
class MixEntry:
    """One WWMix entry. ``blob`` is loaded lazily via :meth:`read`."""

    name: str
    content_offset: int = 0
    content_length: int = 0
    blob: bytes | None = None
    # Set when this entry was constructed directly from bytes (e.g. a
    # file freshly extracted from a patch package) rather than read from
    # an on-disk mix — read() is then a no-op, the blob is already final.
    _source_path: str | None = field(default=None, repr=False)

    def crc32(self) -> int:
        return zlib.crc32(self.name.upper().encode("utf-8")) & 0xFFFFFFFF

    def read(self) -> bool:
        """Load ``blob`` from ``_source_path`` if not already loaded."""
        if self.blob is not None:
            return True
        if self._source_path is None:
            return False
        try:
            with open(self._source_path, "rb") as f:
                f.seek(self.content_offset)
                self.blob = f.read(self.content_length)
        except OSError:
            return False
        return len(self.blob) == self.content_length


class WWMix:
    """One WWMix container. ``load()`` an existing file, or build fresh
    entries and ``save()``.
    """

    def __init__(self, path: str, *, encrypted: bool = False) -> None:
        self.path = path
        self.encrypted = encrypted
        self.entries: list[MixEntry] = []

    # ── Reading ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Read the header + entry tables. Raises :class:`WWMixError` on failure."""
        if not os.path.isfile(self.path):
            raise WWMixError(f"Path does not exist: {self.path}")
        with open(self.path, "rb") as f:
            data = f.read()
        if len(data) < 16:
            raise WWMixError(f"Too small to be a WWMix container: {self.path}")
        mime_type, file_data_offset, file_names_offset, _reserved = struct.unpack_from(
            "<iiii", data, 0,
        )
        if mime_type not in (MIX1_HEADER, MIX2_HEADER):
            raise WWMixError(f"Invalid mime type: {mime_type:#x} in {self.path}")
        self.encrypted = mime_type == MIX2_HEADER

        (file_count,) = struct.unpack_from("<i", data, file_data_offset)
        pos = file_data_offset + 4
        entries: list[MixEntry] = []
        for _ in range(file_count):
            _crc32, content_offset, content_length = struct.unpack_from("<III", data, pos)
            pos += 12
            entry = MixEntry(
                name="", content_offset=content_offset, content_length=content_length,
            )
            entry._source_path = self.path
            entries.append(entry)

        (name_count,) = struct.unpack_from("<i", data, file_names_offset)
        pos = file_names_offset + 4
        for i in range(name_count):
            name, pos = _read_cstring(data, pos)
            if i < len(entries):
                entries[i].name = name

        self.entries = entries

    # ── Writing ──────────────────────────────────────────────────────

    def save(self) -> None:
        """Write the container. Raises :class:`WWMixError` on failure."""
        if not self.entries:
            raise WWMixError("No entries to write.")
        if os.path.isdir(self.path):
            raise WWMixError(f"Path is a directory: {self.path}")

        # Sort by CRC32 of the uppercased name, matching the reference —
        # entry order on disk must be deterministic and CRC32-sorted for
        # the format to round-trip correctly against other readers.
        ordered = sorted(self.entries, key=lambda e: e.crc32())

        out = bytearray()
        out += struct.pack("<i", MIX2_HEADER if self.encrypted else MIX1_HEADER)
        out += b"\x00" * 12  # placeholder for file_data_offset/file_names_offset/reserved

        offsets: list[int] = []
        for entry in ordered:
            if entry.blob is None and not entry.read():
                raise WWMixError(f"Cannot read source data for entry: {entry.name}")
            offsets.append(len(out))
            assert entry.blob is not None
            out += entry.blob
            padding = (-len(out)) & 7
            out += b"\x00" * padding

        file_data_offset = len(out)
        out += struct.pack("<i", len(ordered))
        for entry, offset in zip(ordered, offsets, strict=True):
            out += struct.pack("<III", entry.crc32(), offset, len(entry.blob or b""))

        file_names_offset = len(out)
        out += struct.pack("<i", len(ordered))
        for entry in ordered:
            out += entry.name.encode("utf-8") + b"\x00"

        struct.pack_into("<iii", out, 4, file_data_offset, file_names_offset, 0)

        with open(self.path, "wb") as f:
            f.write(out)

    # ── Entry access ─────────────────────────────────────────────────

    def find(self, name: str) -> MixEntry | None:
        lname = name.lower()
        return next((e for e in self.entries if e.name.lower() == lname), None)

    def add_entry(self, entry: MixEntry, *, replace: bool = True) -> None:
        """Insert ``entry``, replacing any existing entry of the same name."""
        if replace:
            self.entries = [e for e in self.entries if e.name.lower() != entry.name.lower()]
        self.entries.append(entry)

    def remove(self, name: str) -> None:
        lname = name.lower()
        self.entries = [e for e in self.entries if e.name.lower() != lname]


def _read_cstring(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", errors="replace"), end + 1


# ── Patch application ───────────────────────────────────────────────────


def apply_patch(patch_mix_path: str, target_mix_path: str) -> None:
    """Merge a downloaded patch container into an existing target container.

    ``patch_mix_path`` is the ``<target-name>.patch`` file extracted from
    a patch package's zip (see module docstring). ``target_mix_path`` is
    the already-installed file being updated **in place** (write happens
    to a temp file first, then an atomic replace — never leaves a
    partially-written target on a crash).

    Raises :class:`WWMixError` if either container is unreadable, or if
    the patch has no metadata entry (``.w3dhub.patch``/``.bhppatch``) —
    both are treated as install-corrupting failures, not silently
    skipped.
    """
    patch_mix = WWMix(path=patch_mix_path)
    patch_mix.load()

    meta_entry = next(
        (e for e in patch_mix.entries if e.name.lower() in _PATCH_META_NAMES), None,
    )
    if meta_entry is None:
        raise WWMixError(
            f"Patch container has no metadata entry ({'/'.join(_PATCH_META_NAMES)}): "
            f"{patch_mix_path}",
        )
    if not meta_entry.read():
        raise WWMixError(f"Cannot read patch metadata entry in {patch_mix_path}")
    try:
        patch_info = json.loads(meta_entry.blob or b"{}")
    except ValueError as exc:
        raise WWMixError(f"Patch metadata is not valid JSON in {patch_mix_path}") from exc

    patch_mix.remove(meta_entry.name)

    target_mix = WWMix(path=target_mix_path, encrypted=patch_mix.encrypted)
    target_mix.load()

    removed_files = patch_info.get("removedFiles") or []
    for name in removed_files:
        target_mix.remove(name)

    updated_files = patch_info.get("updatedFiles") or []
    if updated_files:
        # The reference copies every remaining patch-mix entry into the
        # target for each "updated file" name — in practice the patch
        # mix's entries ARE the updated files (the metadata entry was
        # already stripped above), so this merges the whole set.
        for entry in patch_mix.entries:
            if not entry.read():
                raise WWMixError(
                    f"Cannot read patch entry {entry.name!r} from {patch_mix_path}",
                )
            target_mix.add_entry(
                MixEntry(name=entry.name, blob=entry.blob), replace=True,
            )

    tmp_path = f"{target_mix_path}.w3dhub-patch-tmp"
    temp_mix = WWMix(path=tmp_path, encrypted=target_mix.encrypted)
    temp_mix.entries = target_mix.entries
    temp_mix.save()
    os.replace(tmp_path, target_mix_path)

    logger.info(
        "[W3DHub] applied patch %s -> %s (%d removed, %d updated)",
        os.path.basename(patch_mix_path), os.path.basename(target_mix_path),
        len(removed_files), len(updated_files),
    )
