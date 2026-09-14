"""W3D Hub store sub-package — public entry point.

py_modules/unifideck/stores/w3d_hub/__init__.py

No vendor client — Unifideck is its own API client, downloader, and
installer against W3D Hub's plain JSON/HTTPS backend. Discovered by
``StoreRegistry.auto_discover`` via the ``<name>/store.py`` layout, so no
registry edit is needed. See ``docs/w3d-hub-store-spec.md``.
"""

from .store import W3DHubStore

__all__ = ["W3DHubStore"]
