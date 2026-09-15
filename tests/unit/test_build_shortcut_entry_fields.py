"""``_build_shortcut_entry`` must write every field Steam actually loads.

Confirmed live 2026-09-14 on a real Deck: a brand-new W3D Hub shortcut
(never previously written by Steam, never reclaimed from an orphan) was
missing ``sortas``, and Steam silently dropped it from the live app list on
every restart — no error, no partial data, the shortcut just never existed
as far as ``collectionStore``/``appStore`` were concerned. ``[Unifideck]
W3D Hub`` in turn saw zero matching apps and deleted itself as an empty
collection (``collection-manager.ts``'s ``syncTab``).

This never showed up for any other store on that Deck because every other
store's shortcuts had already been through ``_update_existing_shortcut`` or
``_reclaim_orphan`` at least once — both mutate an *existing* dict in place
and so preserve whatever Steam's own writer originally put there. Only a
game with **no prior shortcut history at all** exercises the from-scratch
``_build_shortcut_entry`` path, which is why this was never caught until
W3D Hub's first-ever sync on a fresh store.

Pinned by diffing raw bytes: a real Steam-authored entry (``vdf.binary_loads``
over a manually-added non-Steam game's ``shortcuts.vdf`` record) against one
``_build_shortcut_entry`` produced, field by field. Whatever Steam includes
here, we must include too — the field set is the contract, not "make the game
launch," since Steam accepted the entry into its live app list *before*
checking any of that.
"""
from __future__ import annotations

from unifideck.core.types import Game
from unifideck.services.shortcut.reconcile_phases import _ReconcilePhasesMixin

LAUNCHER = "/var/lib/decky-loader/plugins/unifideck/bin/unifideck-launcher"

# Every field observed on a real, currently-loaded Steam shortcut entry
# (``vdf.binary_loads`` over a live ``shortcuts.vdf``, both a manually
# Steam-added game and a plugin-managed one Steam had already accepted).
# A missing key here is exactly the class of bug this test exists to catch.
STEAM_OBSERVED_FIELDS = frozenset({
    "appid", "AppName", "Exe", "StartDir", "icon", "ShortcutPath",
    "LaunchOptions", "IsHidden", "AllowDesktopConfig", "AllowOverlay",
    "OpenVR", "Devkit", "DevkitGameID", "DevkitOverrideAppID",
    "LastPlayTime", "FlatpakAppID", "sortas", "tags",
})


class _Host(_ReconcilePhasesMixin):
    _launcher_path = LAUNCHER


def _game(**overrides: object) -> Game:
    base: dict[str, object] = {
        "app_id": 123,
        "store": "w3dhub",
        "store_game_id": "apb-release",
        "title": "Red Alert: A Path Beyond",
        "installed": False,
    }
    base.update(overrides)
    return Game(**base)  # type: ignore[arg-type]


def test_a_from_scratch_shortcut_has_every_field_steam_actually_loads() -> None:
    entry = _Host()._build_shortcut_entry(_game(), app_id=-1077203002)
    missing = STEAM_OBSERVED_FIELDS - entry.keys()
    assert not missing, (
        f"_build_shortcut_entry is missing {sorted(missing)} — a shortcut "
        "field Steam's own writer always includes. A brand-new shortcut "
        "with no prior history silently fails to appear in Steam's live "
        "app list if it is missing any of these (confirmed live for "
        "'sortas' — see this module's docstring)."
    )


def test_sortas_is_present_and_empty_for_a_new_shortcut() -> None:
    """The specific field that actually broke — pinned directly, not just
    via set membership, so a future refactor can't reintroduce it under
    a different guise (e.g. present but ``None``)."""
    entry = _Host()._build_shortcut_entry(_game(), app_id=-1077203002)
    assert entry["sortas"] == ""
