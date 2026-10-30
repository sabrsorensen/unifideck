"""Regression: GE-Proton must be extracted with the archive's read-only modes.

GE-Proton ships its Wine files read-only (``-r-xr-xr-x``) on purpose. On
every login Steam runs ``d3ddriverquery64.exe`` through *every* installed
compat tool concurrently against the shared ``compatdata/0`` prefix, whose
builtin DLLs are symlinks into whichever Proton install last touched it —
so one Wine version's prefix update writes straight through into another
version's install unless those files are read-only. ``filter="data"``
forces owner-write on every regular file, which silently removed that
protection: GE-Proton11-11 installed by Unifideck ended up with 614 of
its 793 64-bit DLLs replaced by other builds', and every Wine process in
it crashed at startup (Battle.net sign-in included).
"""
from __future__ import annotations

import io
import stat
import tarfile
from pathlib import Path

import unifideck.launcher.proton.infrastructure.ge_installer as ge_mod


def _add_file(tar: tarfile.TarFile, name: str, mode: int) -> None:
    data = b"MZ"
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tar.addfile(info, io.BytesIO(data))


def _build_tarball(path: Path) -> None:
    with tarfile.open(path, "w:gz") as tar:
        _add_file(tar, "GE-Proton-test/files/lib/wine/x86_64-windows/user32.dll", 0o555)
        _add_file(tar, "GE-Proton-test/files/bin/wine", 0o4777)
        _add_file(tar, "GE-Proton-test/user_settings.sample.py", 0o644)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_read_only_members_stay_read_only(tmp_path):
    tarball = tmp_path / "ge.tar.gz"
    _build_tarball(tarball)
    dest = tmp_path / "out"
    dest.mkdir()

    assert ge_mod._extract(tarball, dest)

    dll = dest / "GE-Proton-test/files/lib/wine/x86_64-windows/user32.dll"
    assert _mode(dll) == 0o555


def test_unsafe_bits_are_still_stripped(tmp_path):
    tarball = tmp_path / "ge.tar.gz"
    _build_tarball(tarball)
    dest = tmp_path / "out"
    dest.mkdir()

    assert ge_mod._extract(tarball, dest)

    # setuid and group/other write are dropped, as ``filter="data"`` did.
    assert _mode(dest / "GE-Proton-test/files/bin/wine") == 0o755
    assert _mode(dest / "GE-Proton-test/user_settings.sample.py") == 0o644
