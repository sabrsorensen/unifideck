"""``ownership/game_accounts.py`` — the game_accounts producer, pure parts.

Audit §3.5 finding A / GitHub #447: this is the producer for the cache
[[test_battlenet_game_account_gap]] measures the absence of. Covers the
network-free logic only — response parsing and the titleId -> program_id
join — per this repo's convention of not standing up an aiohttp test
server for the HTTP leg (see test_gamevault_library.py). The HTTP fetch
itself (``fetch_games_and_subs``) is exercised live via
``BattlenetStore._refresh_game_accounts`` on-device, not here.
"""
from __future__ import annotations

from unifideck.stores.battlenet.ownership.game_accounts import (
    cookie_header,
    parse_title_ids,
    resolve_program_ids,
)
from unifideck.stores.battlenet.ownership.pub_catalog import (
    CatalogEntry,
    MergedCatalog,
)

# ── cookie_header ──────────────────────────────────────────────────────


def test_cookie_header_includes_battle_net_cookies() -> None:
    cookies = [
        {"domain": ".battle.net", "name": "bnet.pam", "value": "abc"},
        {"domain": "account.battle.net", "name": "web.id", "value": "xyz"},
    ]
    header = cookie_header(cookies)
    assert "bnet.pam=abc" in header
    assert "web.id=xyz" in header


def test_cookie_header_excludes_other_domains() -> None:
    cookies = [
        {"domain": ".battle.net", "name": "bnet.pam", "value": "abc"},
        {"domain": ".amazon.com", "name": "session-id", "value": "nope"},
    ]
    header = cookie_header(cookies)
    assert "bnet.pam=abc" in header
    assert "session-id" not in header


def test_cookie_header_skips_malformed_rows() -> None:
    cookies = [
        {"domain": ".battle.net", "name": "bnet.pam", "value": "abc"},
        {"domain": ".battle.net", "name": None, "value": "no-name"},
        {"domain": ".battle.net", "value": "no-name-key"},
        {"domain": 123, "name": "bad-domain-type", "value": "x"},
    ]
    header = cookie_header(cookies)
    assert header == "bnet.pam=abc"


def test_cookie_header_empty_input() -> None:
    assert cookie_header([]) == ""


# ── parse_title_ids ─────────────────────────────────────────────────────


def test_parse_title_ids_extracts_ids() -> None:
    response = {
        "gameAccounts": [
            {"titleId": 1465140039, "localizedGameName": "Hearthstone"},
            {"titleId": 5730135, "localizedGameName": "World of Warcraft"},
        ],
    }
    assert parse_title_ids(response) == frozenset({1465140039, 5730135})


def test_parse_title_ids_missing_key_is_empty() -> None:
    assert parse_title_ids({}) == frozenset()


def test_parse_title_ids_not_a_list_is_empty() -> None:
    assert parse_title_ids({"gameAccounts": "not-a-list"}) == frozenset()


def test_parse_title_ids_skips_malformed_entries() -> None:
    response = {
        "gameAccounts": [
            {"titleId": 1465140039},
            "not-a-dict",
            {"titleId": "not-an-int"},
            {"no_title_id": True},
            None,
        ],
    }
    assert parse_title_ids(response) == frozenset({1465140039})


# ── resolve_program_ids ──────────────────────────────────────────────────


def _catalog(entries: dict[str, CatalogEntry]) -> MergedCatalog:
    return MergedCatalog(entries=entries)


def test_resolve_program_ids_joins_on_title_id() -> None:
    catalog = _catalog({
        "hs": CatalogEntry(product_id="hs", program_id="WTCG", title_id=1465140039),
        "wow": CatalogEntry(product_id="wow", program_id="WoW", title_id=5730135),
    })
    resolved = resolve_program_ids(frozenset({1465140039}), catalog)
    assert resolved == frozenset({"WTCG"})


def test_resolve_program_ids_drops_unmatched_title_ids() -> None:
    catalog = _catalog({
        "hs": CatalogEntry(product_id="hs", program_id="WTCG", title_id=1465140039),
    })
    # 999 has no catalog entry — dropped silently, not an error.
    resolved = resolve_program_ids(frozenset({1465140039, 999}), catalog)
    assert resolved == frozenset({"WTCG"})


def test_resolve_program_ids_ignores_entries_without_title_id() -> None:
    catalog = _catalog({
        "hs": CatalogEntry(product_id="hs", program_id="WTCG", title_id=None),
    })
    resolved = resolve_program_ids(frozenset({1465140039}), catalog)
    assert resolved == frozenset()


def test_resolve_program_ids_empty_title_ids_short_circuits() -> None:
    catalog = _catalog({
        "hs": CatalogEntry(product_id="hs", program_id="WTCG", title_id=1465140039),
    })
    assert resolve_program_ids(frozenset(), catalog) == frozenset()
