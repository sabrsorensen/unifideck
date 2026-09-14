"""manifest.py — parsing and install-plan accumulation, against real fixtures.

The XML files under tests/fixtures/w3d_hub/ are unmodified manifests
fetched live from the W3D Hub alt API (w3dhub-api.w3d.cyberarm.dev,
unauthenticated) on 2026-09-14 — see docs/w3d-hub-store-spec.md §13. Using
real data here rather than hand-crafted XML is deliberate: the
accumulation algorithm's correctness depends on exact real-world shapes
(which files carry a <Patch> child, in what order, across how many
chained manifests) that would be easy to get subtly wrong by construction.
"""
from __future__ import annotations

from pathlib import Path

from unifideck.stores.w3d_hub.manifest import (
    build_install_plan,
    build_package_refs,
    parse_manifest_xml,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w3d_hub"


def _load(name: str):
    return parse_manifest_xml((FIXTURES / name).read_bytes())


# ── parse_manifest_xml ───────────────────────────────────────────────────


def test_parses_full_manifest_root_attrs():
    m = _load("apb_3.8.0.0.xml")
    assert m.game == "apb"
    assert m.type == "Full"
    assert m.version == "3.8.0.0"
    assert m.base_version is None
    assert m.is_full


def test_parses_patch_manifest_root_attrs():
    m = _load("apb_3.8.1.0.xml")
    assert m.type == "Patch"
    assert m.base_version == "3.8.0.2"
    assert not m.is_full


def test_full_manifest_has_no_patch_children():
    """Live-verified invariant: a Full manifest's files are all plain copies."""
    m = _load("apb_3.8.0.0.xml")
    assert len(m.files) == 60
    assert all(not f.is_patch for f in m.files)
    assert all(f.package for f in m.files)


def test_patch_manifest_mixes_plain_and_patch_files():
    m = _load("apb_3.8.1.0.xml")
    plain = [f for f in m.files if not f.is_patch]
    patched = [f for f in m.files if f.is_patch]
    removed = [f for f in m.files if f.removed]
    assert len(patched) == 40
    # 49 total = 40 patched + 9 non-patched; one of the 9 non-patched
    # entries is also removed-only (has no package, just marks a
    # deletion), so "plain" and "removed" overlap by one here.
    assert len(plain) == 9
    assert len(removed) == 1
    assert removed[0].name == "data/RA_Siege.mix"
    assert removed[0].removed_since == "3.8.1.0"


def test_patch_file_resolves_package_from_patch_child_not_top_level():
    m = _load("apb_3.8.1.0.xml")
    always = next(f for f in m.files if f.name == "data/Always.dat")
    assert always.is_patch
    assert always.package == "Always.patch.3.8.0.2"
    assert always.patch_from == "3.8.0.2"


def test_plain_file_resolves_package_from_top_level_attr():
    m = _load("apb_3.8.0.0.xml")
    always = next(f for f in m.files if f.name == "data/Always.dat")
    assert not always.is_patch
    assert always.package == "Always"


def test_woa_release_manifest_is_full():
    """woa is the one catalog title with no patch chain (see §13)."""
    m = _load("woa_1.0.1.3.xml")
    assert m.is_full
    assert m.base_version is None


# ── build_install_plan (the real apb chain, oldest to newest) ────────────


def _apb_chain():
    return [
        _load("apb_3.8.0.0.xml"),  # Full
        _load("apb_3.8.0.1.xml"),  # Patch, base 3.8.0.0
        _load("apb_3.8.0.2.xml"),  # Patch, base 3.8.0.1
        _load("apb_3.8.1.0.xml"),  # Patch, base 3.8.0.2
    ]


def test_install_plan_accumulates_every_patch_step_for_a_repeatedly_patched_file():
    """data/Always.dat is patched in more than one manifest in this chain —
    every step must survive accumulation, not just the latest."""
    files = build_install_plan(_apb_chain())
    always_entries = [f for f in files if f.name == "data/Always.dat"]
    # At least the base full copy + the 3.8.1.0 patch step; assert order
    # is oldest-version-first and the base entry is not a patch.
    assert len(always_entries) >= 2
    assert not always_entries[0].is_patch
    assert always_entries[0].version == "3.8.0.0"
    assert always_entries[-1].is_patch
    assert always_entries[-1].version == "3.8.1.0"


def test_install_plan_plain_file_supersedes_all_earlier_entries():
    """game.exe is a plain (non-patch) file in every apb manifest — only
    the newest version's entry should survive accumulation."""
    files = build_install_plan(_apb_chain())
    exe_entries = [f for f in files if f.name == "game.exe"]
    assert len(exe_entries) == 1
    assert exe_entries[0].version == "3.8.1.0"
    assert not exe_entries[0].is_patch


def test_install_plan_drops_removed_file():
    files = build_install_plan(_apb_chain())
    assert not any(f.name == "data/RA_Siege.mix" for f in files)


def test_install_plan_single_full_manifest_is_a_no_op_passthrough():
    files = build_install_plan([_load("woa_1.0.1.3.xml")])
    assert len(files) == len(_load("woa_1.0.1.3.xml").files)
    assert all(not f.is_patch for f in files)


# ── build_package_refs ────────────────────────────────────────────────────


def test_package_refs_dedup_a_full_package_shared_by_several_files():
    """Six files (fmod.dll, game.exe, MemoryManager.dll, scripts.dll,
    shared.dll, wwconfig.exe) were all redeclared at 3.8.1.0 and dedup to
    one ref at that version. Four others (binkw64.dll, d3dcompiler_47.dll,
    discord_game_sdk.dll, libconfigx64.dll) were never redeclared past the
    3.8.0.0 base, so their correct current copy is still that package
    version — real data, not every file changes on every patch. Both refs
    are needed; what must hold is that the older version is applied
    first, so a later extraction naturally overwrites anything that
    overlaps (it doesn't here — the two versions' file sets are disjoint —
    but the ordering guarantee is the thing under test, not this
    manifest's specific contents)."""
    files = build_install_plan(_apb_chain())
    refs = build_package_refs("apb", files)
    binaries_refs = [r for r in refs if r.name == "binaries"]
    versions = [r.version for r in binaries_refs]
    assert versions == ["3.8.0.0", "3.8.1.0"]
    assert all(not r.is_patch for r in binaries_refs)


def test_package_refs_full_entry_precedes_its_patch_entries():
    """Processing order matters: the base/full package for a file must be
    fetched+extracted before any patch package that touches it."""
    files = build_install_plan(_apb_chain())
    refs = build_package_refs("apb", files)
    always_idx = [i for i, r in enumerate(refs) if r.patch_file and r.patch_file.name == "data/Always.dat"]
    full_idx = next(i for i, r in enumerate(refs) if r.name == "Always" and not r.is_patch)
    assert always_idx, "expected at least one patch package for data/Always.dat"
    assert full_idx < min(always_idx)


def test_package_refs_patch_ref_carries_its_target_file():
    files = build_install_plan(_apb_chain())
    refs = build_package_refs("apb", files)
    patch_refs = [r for r in refs if r.is_patch and r.name == "Always.patch.3.8.0.2"]
    assert len(patch_refs) == 1
    assert patch_refs[0].patch_file.name == "data/Always.dat"


def test_package_refs_woa_are_all_full() -> None:
    files = build_install_plan([_load("woa_1.0.1.3.xml")])
    refs = build_package_refs("woa", files)
    assert refs
    assert all(not r.is_patch for r in refs)
