"""Every non-wrapper store's ``install_game`` must accept the worker's call shape.

``services/download/worker.py``'s ``_run_install`` calls
``store.install_game(item.game_id, item.install_path or None,
progress_cb=progress_cb, **extra)`` *positionally* for any store that is not
a ``WRAPPER_STORES`` member (those go through ``dispatch_wrapper_install``
instead — a different call shape entirely, covered by
``test_download_worker_install_path.py``). That call is
``# type: ignore[call-arg]``'d against ``StoreBase``'s own abstract
``(self, game_id, **kwargs)`` signature, so nothing type-checks a concrete
store's actual positional accept — a store that doesn't declare a second
positional parameter (as W3D Hub's ``install_game`` didn't, in its first
cut) raises ``TypeError: install_game() takes 2 positional arguments but 3
were given`` at install time, not at review time.

This asserts every current non-wrapper store's real signature can bind the
worker's call shape, so the next store added without that second positional
parameter fails a test instead of a user's install.
"""
from __future__ import annotations

import inspect

from test_store_capabilities import _store_class

from unifideck.bootstrap.cache_registry import _STORE_CACHES
from unifideck.launcher.wrapper_stores import WRAPPER_STORES

NON_WRAPPER_STORES = sorted(set(_STORE_CACHES) - set(WRAPPER_STORES))


def test_every_non_wrapper_store_accepts_the_workers_install_call_shape() -> None:
    missing_class: list[str] = []
    cant_bind: list[str] = []
    for store in NON_WRAPPER_STORES:
        cls = _store_class(store)
        if cls is None:
            missing_class.append(store)
            continue
        sig = inspect.signature(cls.install_game)
        try:
            sig.bind(object(), "some-game-id", "/picked/install/path", progress_cb=None)
        except TypeError:
            cant_bind.append(store)
    assert not missing_class, f"could not import a *Store class for: {missing_class}"
    assert not cant_bind, (
        f"{cant_bind} cannot bind DownloadWorker._run_install's positional call "
        "shape (game_id, base_path, progress_cb=...) — add a second positional "
        "parameter (see w3dhub/store.py's install_game for why it's fine for a "
        "store to just ignore the value)"
    )
