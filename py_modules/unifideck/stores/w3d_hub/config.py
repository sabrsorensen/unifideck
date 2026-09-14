"""Frozen configuration for the W3D Hub store.

py_modules/unifideck/stores/w3d_hub/config.py

Follows the Ubisoft/Battle.net ``_FIELD_SPECS`` pattern (see
``stores/battlenet/config.py``'s docstring) — every key declared once with
its type and default, coercion + per-key fallback from ``from_mapping``.

``prefix_dir`` is deliberately a single path, not a per-game template —
see ``docs/w3d-hub-store-spec.md`` §3: every W3D Hub title shares one
Proton prefix, unlike Ubisoft/Battle.net's per-game isolation. There is no
vendor client whose update churn that isolation defends against here, and
every title shares one winetricks dependency profile (nix-dendrites'
``_package.nix`` already proves this by bootstrapping exactly one prefix
for every game the existing desktop launcher runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

# name -> (config key, default). The config key is relative to
# ``stores.w3dhub`` in the merged config.
_FIELD_SPECS: dict[str, tuple[str, Any]] = {
    "data_dir": ("data_dir", "~/.local/share/unifideck"),
    "prefix_dir": ("prefix_dir", "~/.local/share/unifideck/prefixes/w3dhub/shared"),
    "install_base": ("install_base", "~/.local/share/unifideck/prefixes/w3dhub/shared/drive_c/W3D Hub"),
    "package_cache_dir": ("package_cache_dir", "~/.local/share/unifideck/w3dhub_package_cache"),
    "api_endpoint": ("api_endpoint", "https://secure.w3dhub.com"),
    "alt_api_endpoint": ("alt_api_endpoint", "https://w3dhub-api.w3d.cyberarm.dev"),
    "gsh_endpoint": ("gsh_endpoint", "https://gsh.w3d.cyberarm.dev"),
    "parallel_downloads": ("parallel_downloads", 4),
    "request_timeout_seconds": ("request_timeout_seconds", 30),
    "catalog_max_age_seconds": ("catalog_max_age_seconds", 604800),
}


@dataclass(frozen=True, slots=True)
class W3DHubConfig:
    """Resolved W3D Hub settings."""

    data_dir: str = "~/.local/share/unifideck"
    prefix_dir: str = "~/.local/share/unifideck/prefixes/w3dhub/shared"
    install_base: str = "~/.local/share/unifideck/prefixes/w3dhub/shared/drive_c/W3D Hub"
    package_cache_dir: str = "~/.local/share/unifideck/w3dhub_package_cache"
    api_endpoint: str = "https://secure.w3dhub.com"
    alt_api_endpoint: str = "https://w3dhub-api.w3d.cyberarm.dev"
    gsh_endpoint: str = "https://gsh.w3d.cyberarm.dev"
    parallel_downloads: int = 4
    request_timeout_seconds: int = 30
    catalog_max_age_seconds: int = 604800

    @property
    def data_dir_path(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def prefix_dir_path(self) -> Path:
        return Path(self.prefix_dir).expanduser()

    @property
    def install_base_path(self) -> Path:
        return Path(self.install_base).expanduser()

    @property
    def package_cache_dir_path(self) -> Path:
        return Path(self.package_cache_dir).expanduser()

    @property
    def installed_state_path(self) -> Path:
        return self.data_dir_path / "w3dhub_installed.json"

    @property
    def refresh_token_path(self) -> Path:
        return self.data_dir_path / "w3dhub_session.json"


def _coerce(value: Any, default: Any) -> Any:
    """Coerce a config value to the default's type, falling back on failure."""
    if value is None:
        return default
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return str(value) if value != "" else default


def from_mapping(raw: dict[str, Any] | None) -> W3DHubConfig:
    """Build a config from a plain mapping. Unknown keys are ignored."""
    source = raw or {}
    kwargs = {
        name: _coerce(source.get(key), default)
        for name, (key, default) in _FIELD_SPECS.items()
    }
    return W3DHubConfig(**kwargs)


def from_config_manager(config: Any) -> W3DHubConfig:
    """Build a config from the plugin's ConfigManager, tolerating absence."""
    if config is None:
        return W3DHubConfig()
    getter = getattr(config, "get", None)
    if not callable(getter):
        return W3DHubConfig()
    try:
        raw = getter("stores.w3dhub", {})
    except Exception:  # config must never break store construction
        return W3DHubConfig()
    return from_mapping(raw if isinstance(raw, dict) else {})


FIELD_DEFAULTS: dict[str, Any] = {
    name: default for name, (_key, default) in _FIELD_SPECS.items()
}
DATACLASS_DEFAULTS: dict[str, Any] = {
    f.name: f.default for f in fields(W3DHubConfig) if f.default is not field
}
