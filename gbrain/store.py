"""Legacy compatibility shim for gbrain.store — re-exports from majestic_brain.store."""

from majestic_brain.store import (  # noqa: F401
    MajesticBrainStore,
    GBrainStore,
    VALID_NOTE_KINDS,
    VALID_SOURCE_TYPES,
    DEFAULT_NOTE_KIND,
    DEFAULT_SOURCE_TYPE,
)
