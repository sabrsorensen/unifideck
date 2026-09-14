"""ww_mix.py — WWMix container round-trip + patch-merge, against synthetic data.

No real downloaded WWMix packages are used here (that needs a live
install run) — instead this validates the reader against what the
writer itself produces (round-trip), which exercises the same binary
layout/offset logic a real file would, and builds a small synthetic
patch scenario end-to-end to validate the merge semantics traced from
the reference's apply_patch (see ww_mix.py's module docstring).
"""
from __future__ import annotations

import json
import struct

import pytest

from unifideck.stores.w3dhub.ww_mix import (
    MIX1_HEADER,
    MIX2_HEADER,
    MixEntry,
    WWMix,
    WWMixError,
    apply_patch,
)


def _mix(tmp_path, name: str, entries: dict[str, bytes], *, encrypted: bool = False) -> WWMix:
    mix = WWMix(path=str(tmp_path / name), encrypted=encrypted)
    for entry_name, blob in entries.items():
        mix.entries.append(MixEntry(name=entry_name, blob=blob))
    mix.save()
    return mix


# ── Round-trip ────────────────────────────────────────────────────────────


def test_round_trip_preserves_entries_and_content(tmp_path):
    written = _mix(tmp_path, "a.mix", {"foo.txt": b"hello", "bar.dat": b"\x00\x01\x02" * 10})

    read_back = WWMix(path=written.path)
    read_back.load()

    names = {e.name for e in read_back.entries}
    assert names == {"foo.txt", "bar.dat"}
    for entry in read_back.entries:
        assert entry.read()
        if entry.name == "foo.txt":
            assert entry.blob == b"hello"
        else:
            assert entry.blob == b"\x00\x01\x02" * 10


def test_round_trip_preserves_encrypted_flag_via_header_magic(tmp_path):
    _mix(tmp_path, "plain.mix", {"a": b"1"}, encrypted=False)
    _mix(tmp_path, "enc.mix", {"a": b"1"}, encrypted=True)

    plain = WWMix(path=str(tmp_path / "plain.mix"))
    plain.load()
    assert plain.encrypted is False

    enc = WWMix(path=str(tmp_path / "enc.mix"))
    enc.load()
    assert enc.encrypted is True


def test_header_magic_bytes_match_the_format_constants(tmp_path):
    _mix(tmp_path, "plain.mix", {"a": b"1"}, encrypted=False)
    raw = (tmp_path / "plain.mix").read_bytes()
    (mime_type,) = struct.unpack_from("<i", raw, 0)
    assert mime_type == MIX1_HEADER
    assert MIX1_HEADER != MIX2_HEADER


def test_load_rejects_a_file_with_no_valid_header(tmp_path):
    bad = tmp_path / "bad.mix"
    bad.write_bytes(b"not a mix file at all, too short or wrong magic")
    mix = WWMix(path=str(bad))
    with pytest.raises(WWMixError):
        mix.load()


def test_load_rejects_a_missing_file(tmp_path):
    mix = WWMix(path=str(tmp_path / "nope.mix"))
    with pytest.raises(WWMixError):
        mix.load()


def test_entries_are_written_sorted_by_crc32_of_uppercased_name(tmp_path):
    written = _mix(tmp_path, "sorted.mix", {"zzz.txt": b"z", "aaa.txt": b"a", "mmm.txt": b"m"})
    read_back = WWMix(path=written.path)
    read_back.load()
    crcs = [e.crc32() for e in read_back.entries]
    assert crcs == sorted(crcs)


# ── Entry manipulation ──────────────────────────────────────────────────


def test_add_entry_replace_true_supersedes_same_name_case_insensitively(tmp_path):
    mix = WWMix(path=str(tmp_path / "unused.mix"))
    mix.entries.append(MixEntry(name="Data/Foo.dat", blob=b"old"))
    mix.add_entry(MixEntry(name="data/foo.dat", blob=b"new"), replace=True)
    assert len(mix.entries) == 1
    assert mix.entries[0].blob == b"new"


def test_remove_is_case_insensitive(tmp_path):
    mix = WWMix(path=str(tmp_path / "unused.mix"))
    mix.entries.append(MixEntry(name="Data/Foo.dat", blob=b"x"))
    mix.remove("data/foo.dat")
    assert mix.entries == []


def test_find_is_case_insensitive(tmp_path):
    mix = WWMix(path=str(tmp_path / "unused.mix"))
    mix.entries.append(MixEntry(name="Data/Foo.dat", blob=b"x"))
    assert mix.find("DATA/FOO.DAT") is not None
    assert mix.find("nope") is None


# ── apply_patch (synthetic end-to-end) ──────────────────────────────────


def test_apply_patch_removes_and_updates_files(tmp_path):
    target_path = tmp_path / "target.mix"
    target = WWMix(path=str(target_path))
    target.entries = [
        MixEntry(name="keep.txt", blob=b"unchanged"),
        MixEntry(name="stale.txt", blob=b"to be removed"),
        MixEntry(name="update.txt", blob=b"old content"),
    ]
    target.save()

    patch_info = json.dumps({
        "removedFiles": ["stale.txt"],
        "updatedFiles": ["update.txt"],
    }).encode()
    patch_path = tmp_path / "target.mix.patch"
    patch = WWMix(path=str(patch_path))
    patch.entries = [
        MixEntry(name=".w3dhub.patch", blob=patch_info),
        MixEntry(name="update.txt", blob=b"new content"),
    ]
    patch.save()

    apply_patch(str(patch_path), str(target_path))

    result = WWMix(path=str(target_path))
    result.load()
    by_name = {e.name: e for e in result.entries}
    assert set(by_name) == {"keep.txt", "update.txt"}
    assert by_name["keep.txt"].read() and by_name["keep.txt"].blob == b"unchanged"
    assert by_name["update.txt"].read() and by_name["update.txt"].blob == b"new content"


def test_apply_patch_accepts_the_alternate_metadata_entry_name(tmp_path):
    """.bhppatch is the other name the reference recognizes."""
    target_path = tmp_path / "target.mix"
    target = WWMix(path=str(target_path))
    target.entries = [MixEntry(name="a.txt", blob=b"1")]
    target.save()

    patch_path = tmp_path / "target.mix.patch"
    patch = WWMix(path=str(patch_path))
    patch.entries = [
        MixEntry(name=".bhppatch", blob=json.dumps({"removedFiles": [], "updatedFiles": ["a.txt"]}).encode()),
        MixEntry(name="a.txt", blob=b"2"),
    ]
    patch.save()

    apply_patch(str(patch_path), str(target_path))

    result = WWMix(path=str(target_path))
    result.load()
    entry = result.find("a.txt")
    assert entry is not None
    assert entry.read() and entry.blob == b"2"


def test_apply_patch_raises_without_metadata_entry(tmp_path):
    target_path = tmp_path / "target.mix"
    t = WWMix(path=str(target_path))
    t.entries = [MixEntry(name="a.txt", blob=b"1")]
    t.save()

    patch_path = tmp_path / "target.mix.patch"
    p = WWMix(path=str(patch_path))
    p.entries = [MixEntry(name="a.txt", blob=b"2")]  # no .w3dhub.patch entry
    p.save()

    with pytest.raises(WWMixError):
        apply_patch(str(patch_path), str(target_path))


def test_apply_patch_is_atomic_no_partial_target_on_success(tmp_path):
    """The temp-then-replace path shouldn't leave the .w3dhub-patch-tmp file behind."""
    target_path = tmp_path / "target.mix"
    t = WWMix(path=str(target_path))
    t.entries = [MixEntry(name="a.txt", blob=b"1")]
    t.save()

    patch_path = tmp_path / "target.mix.patch"
    p = WWMix(path=str(patch_path))
    p.entries = [
        MixEntry(name=".w3dhub.patch", blob=json.dumps({"removedFiles": [], "updatedFiles": ["a.txt"]}).encode()),
        MixEntry(name="a.txt", blob=b"2"),
    ]
    p.save()

    apply_patch(str(patch_path), str(target_path))

    assert not (tmp_path / "target.mix.w3dhub-patch-tmp").exists()
    assert target_path.exists()
