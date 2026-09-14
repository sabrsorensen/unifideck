"""Generic coercion for a ``_FIELD_SPECS``-table frozen config dataclass.

Battle.net and W3D Hub each declare their config as one dict —
``{dataclass_field_name: (config_key, default)}`` — and need the exact
same three operations against it: coerce one raw value to its default's
type, build the dataclass from a plain mapping, and build it from
``ConfigManager`` (tolerating an absent/malformed section, since config
is user-editable and a typo there must not take the plugin down).
Extracted here after the second copy (``validate_architecture.py``
check 13, body-shape duplicate detection) rather than left as an
accepted-forever pair — see ``duplicate_bodies_baseline.json``'s own
rule: promote, don't grandfather.

**Deliberately not shared with Ubisoft's own ``_FIELD_SPECS``.** Ubisoft's
table carries an explicit per-field parser callable
(``(field_name, key, parser, default)``) — a different, more general
shape solving a different problem (arbitrary per-field parsing logic,
not just type-directed coercion). Unifying the two would mean forcing
the simpler stores' callers to supply a no-op parser, in exchange for
nothing either store needs. See ``stores/shared/config_reader.py``'s
docstring for the third, also-deliberately-separate pattern
(``StoreConfigReader``, for stores with no ``_FIELD_SPECS`` table at
all).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# name -> (config key, default). The config key is relative to whatever
# section string the caller passes to from_config_manager.
FieldSpecs = dict[str, tuple[str, Any]]


def field_specs_coerce(value: Any, default: Any) -> Any:
    """Coerce *value* to *default*'s type, falling back to *default* on failure."""
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


def field_specs_from_mapping(raw: dict[str, Any] | None, specs: FieldSpecs, factory: Callable[..., T]) -> T:
    """Build a config instance from a plain mapping. Unknown keys are ignored."""
    source = raw or {}
    kwargs = {
        name: field_specs_coerce(source.get(key), default)
        for name, (key, default) in specs.items()
    }
    return factory(**kwargs)


def field_specs_from_config_manager(
    config: Any, section: str, specs: FieldSpecs, factory: Callable[..., T],
) -> T:
    """Build a config instance from the plugin's ``ConfigManager``, tolerating absence.

    Args:
        config: the live manager, or ``None`` — a store must be able to
            construct before config is available.
        section: the dotted config section, e.g. ``"stores.battlenet"``.
        specs: this store's ``_FIELD_SPECS`` table.
        factory: the frozen config dataclass itself (called as
            ``factory(**kwargs)``, and with no arguments as the
            no-config/malformed-section fallback — every field must
            have a default for that to work, which every ``_FIELD_SPECS``
            config already needs for its own dataclass definition).
    """
    if config is None:
        return factory()
    getter = getattr(config, "get", None)
    if not callable(getter):
        return factory()
    try:
        raw = getter(section, {})
    except Exception:  # config must never break store construction
        return factory()
    return field_specs_from_mapping(raw if isinstance(raw, dict) else {}, specs, factory)
