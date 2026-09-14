"""The free-to-play gap is measured, not silent.

Audit §3.5, finding A / GitHub #447. ``BattlenetStore._cached_game_accounts``
reads a ``game_accounts`` cache key that, until
``BattlenetStore._refresh_game_accounts`` (``ownership/game_accounts.py``)
landed, nothing in the tree ever wrote — the consumer shipped with the
initial Battle.net integration and the producer came later. So
``AccountFacts.game_account_programs`` could be permanently empty,
``rules._match_game_account`` could never match, and every title whose
catalog rule keys on ``game_account`` rather than ``license_id`` was
dropped: the free-to-play and subscription set. ``library.py``'s own
header measures it on a real account — 17 programs from licences, 22 with
game accounts.

The producer is a background web fetch (shared Edge profile, CDP cookie
read, ``games-and-subs``), so the cache can still legitimately be empty at
any given moment — first sign-in, no Edge injected, the fetch still in
flight, or it having failed this time. ``count_game_account_gated`` exists
for exactly that: it stays a real, useful signal regardless of *why* the
facts are missing, not just for the now-closed "nothing writes this at
all" case these tests originally pinned.
"""
from __future__ import annotations

from typing import Any

from unifideck.stores.battlenet.library import count_game_account_gated
from unifideck.stores.battlenet.ownership import AccountFacts

# One licence-gated title and one game-account-gated title. Shaped like the
# real PUB catalog: ``match`` against account facts, ``add_product`` action.
_CATALOG: dict[str, Any] = {
    "D3": {
        "run_each_rule": [
            {
                "match": {"license_id": 1},
                "actions": [
                    {"add_product": {"product_id": {"id": "d3", "type": "retail"}}},
                ],
            },
        ],
    },
    "WTCG": {
        "run_each_rule": [
            {
                "match": {"game_account": {"program_id": "WTCG"}},
                "actions": [
                    {"add_product": {"product_id": {"id": "hs", "type": "retail"}}},
                    {"add_tag": {"name": "play_for_free"}},
                ],
            },
        ],
    },
}


class _Catalog:
    """Minimal stand-in for ``MergedCatalog``'s one field used here."""

    def __init__(self, configs: dict[str, Any]) -> None:
        self.program_configurations = configs


def test_gap_is_counted_when_game_account_facts_are_missing() -> None:
    """The free title is missing and the count says so."""
    facts = AccountFacts(licence_ids=frozenset({1}))
    assert count_game_account_gated(_Catalog(_CATALOG), facts) == 1


def test_no_gap_reported_once_the_facts_exist() -> None:
    """The producer landing must silence this, not keep warning."""
    facts = AccountFacts(
        licence_ids=frozenset({1}),
        game_account_programs=frozenset({"WTCG"}),
    )
    assert count_game_account_gated(_Catalog(_CATALOG), facts) == 0


def test_no_gap_reported_for_a_purely_licence_gated_catalog() -> None:
    """No false alarm when the account really does own everything."""
    facts = AccountFacts(licence_ids=frozenset({1}))
    only_licences = {"D3": _CATALOG["D3"]}
    assert count_game_account_gated(_Catalog(only_licences), facts) == 0


def test_empty_catalog_reports_no_gap() -> None:
    facts = AccountFacts(licence_ids=frozenset({1}))
    assert count_game_account_gated(_Catalog({}), facts) == 0
