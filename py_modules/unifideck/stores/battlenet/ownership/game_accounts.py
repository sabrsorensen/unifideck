"""Fetch and resolve the account's ``game_account`` facts from the web.

py_modules/unifideck/stores/battlenet/ownership/game_accounts.py

Closes audit §3.5 finding A / GitHub #447: :class:`AccountFacts`'s
``game_account_programs`` had a consumer (``rules._match_game_account``)
and no producer, so every free-to-play and subscription title — anything
whose PUB catalog rule keys on ``game_account`` rather than ``license_id``
— was silently dropped from the library.

``licenses.py`` already explains why licences are the *primary* ownership
source rather than this endpoint: measured 2026-08-09, ``games-and-subs``
returned 5 rows against a 27-licence account, because it enumerates *game
accounts* (titles with a service account: subscriptions, F2P titles the
user has actually opened), not entitlements. That is exactly the
complementary fact the PUB catalog's ``game_account`` rules need, though
— they are a different, smaller axis than ``license_id``, not a
duplicate of it.

Two pieces, kept separate because they fail independently and are each
independently testable:

* :func:`parse_title_ids` — the HTTP response's shape, measured live
  2026-08-09 (see ``docs/feasibility/battlenet.md`` §9): a ``titleId``
  per ``gameAccounts[]`` entry, e.g. Hearthstone ``1465140039``.
* :func:`resolve_program_ids` — ``titleId`` is neither the lowercase uid
  (``hsb``) nor the family code (``WTCG``) the PUB catalog's
  ``game_account: {program_id: ...}`` rules match against. The join key
  is :attr:`CatalogEntry.title_id`, already carried by every cached PUB
  catalog fragment for exactly this purpose — no separate static table
  to keep in sync with Blizzard's catalog.

Session material: the account-web session lives in cookies the shared
Edge profile picks up from an ordinary web login at
``https://account.battle.net/`` (the *same* browser GOG/Epic/Amazon/
Microsoft already use), read back via CDP ``Storage.getCookies`` rather
than decrypted from Edge's on-disk cookie jar — ``Storage.getCookies``
returns plaintext including httpOnly cookies, so there is nothing to
decrypt and no encryption-scheme coupling to Chromium's on-disk format.
Proven live 2026-08-09 (docs/feasibility/battlenet.md §9): HTTP 200 with
21 ``.battle.net`` cookies captured this way.

This is deliberately independent of the native-client sign-in
(``WrapperAuthMonitor`` / ``_auth_session_landed``): that produces the
licence ledger, this produces the secondary enrichment, and neither
should block or be blocked by the other failing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

    from .pub_catalog import MergedCatalog

logger = logging.getLogger(__name__)

GAMES_AND_SUBS_URL = "https://account.battle.net/api/games-and-subs"

# account.battle.net has shown basic bot-filtering on an empty/unusual
# User-Agent in manual testing; a plain, current desktop UA string is
# enough to pass as an ordinary browser request. Not trying to look like
# Edge specifically — the session cookie is what authenticates, not the
# UA — just avoiding "obviously a script" defaults like aiohttp's own.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT_S = 15.0


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    """Build a ``Cookie:`` header value from CDP ``Storage.getCookies`` rows.

    Filters to ``*.battle.net`` — the endpoint is on that domain and a
    stray cookie from an unrelated site in the shared profile should
    never be forwarded to it. Cookies without both a usable ``name`` and
    ``value`` are skipped rather than raising: a malformed row here is a
    reason to send one fewer cookie, not a reason to abort the whole
    fetch.
    """
    parts: list[str] = []
    for cookie in cookies:
        domain = cookie.get("domain")
        name = cookie.get("name")
        value = cookie.get("value")
        if not isinstance(domain, str) or not domain.endswith("battle.net"):
            continue
        if not isinstance(name, str) or not name or not isinstance(value, str):
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


async def fetch_games_and_subs(
    cookies: list[dict[str, Any]],
    *,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any] | None:
    """``GET`` the games-and-subs endpoint with the given cookies.

    Returns the parsed JSON body, or ``None`` on any failure (no
    cookies, network error, non-200, unparsable body) — this is
    secondary enrichment, so a failure here must never raise into the
    caller's sign-in or sync path. ``session`` is accepted for tests
    (inject a fake) and so a caller with a shared session can reuse
    its connection pool; a caller with none gets an ephemeral one.
    """
    import aiohttp as _aiohttp

    header = cookie_header(cookies)
    if not header:
        logger.info(
            "[Battlenet] games-and-subs: no .battle.net cookies to send",
        )
        return None
    own_session = session is None
    active_session = session if session is not None else _aiohttp.ClientSession()
    try:
        async with active_session.get(
            GAMES_AND_SUBS_URL,
            headers={"Cookie": header, "User-Agent": _USER_AGENT},
            timeout=_aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_S),
        ) as resp:
            if resp.status != 200:
                logger.info(
                    "[Battlenet] games-and-subs: HTTP %d", resp.status,
                )
                return None
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                logger.warning(
                    "[Battlenet] games-and-subs: response was not valid JSON",
                )
                return None
            return data if isinstance(data, dict) else None
    except _aiohttp.ClientError as exc:
        logger.info("[Battlenet] games-and-subs: request failed: %s", exc)
        return None
    finally:
        if own_session:
            await active_session.close()


def parse_title_ids(response: dict[str, Any]) -> frozenset[int]:
    """Extract the owned ``titleId`` set from a games-and-subs response body.

    Shape measured live 2026-08-09::

        {"gameAccounts": [{"titleId": 1465140039, ...}, ...]}

    Every field access is defensive — this is an unofficial, unversioned
    endpoint (docs/feasibility/battlenet.md §9's own heading calls it
    "not the way the July report assumed"), so a shape change degrades
    to an empty result rather than an exception reaching the sync path.
    """
    accounts = response.get("gameAccounts")
    if not isinstance(accounts, list):
        return frozenset()
    ids: set[int] = set()
    for entry in accounts:
        if not isinstance(entry, dict):
            continue
        title_id = entry.get("titleId")
        if isinstance(title_id, int):
            ids.add(title_id)
    return frozenset(ids)


def resolve_program_ids(
    title_ids: frozenset[int], catalog: MergedCatalog,
) -> frozenset[str]:
    """Join web ``titleId``s to PUB catalog ``program_id`` strings.

    ``AccountFacts.game_account_programs`` and ``rules._match_game_account``
    both key on the PUB catalog's string program id (e.g. ``"WTCG"``), not
    the web endpoint's numeric ``titleId`` (e.g. ``1465140039``) — they are
    different Blizzard id namespaces (docs/feasibility/battlenet.md §9).
    ``CatalogEntry.title_id`` is the recorded join key, carried by every
    cached PUB catalog fragment specifically for this. A ``titleId`` with
    no matching catalog entry is dropped silently: it is not a program the
    locally cached catalog knows how to grant anything for, whatever it is.
    """
    if not title_ids:
        return frozenset()
    by_title_id: dict[int, str] = {
        entry.title_id: entry.program_id
        for entry in catalog.entries.values()
        if entry.title_id is not None
    }
    return frozenset(
        by_title_id[tid] for tid in title_ids if tid in by_title_id
    )
