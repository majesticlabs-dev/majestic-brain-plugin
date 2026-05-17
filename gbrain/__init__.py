"""Legacy compatibility shim — re-exports from the canonical majestic_brain package.

This module keeps the ``gbrain`` import path alive so that:
  - ``from gbrain import GBrainProvider`` still works
  - ``from gbrain import register`` still works
  - Hermes discovers the plugin at ``~/.hermes/plugins/gbrain/`` via plugin.yaml

The real implementation lives in the ``majestic_brain`` package.
"""

# Re-export everything from the canonical package.
from majestic_brain import (  # noqa: F401
    MajesticBrainProvider,
    MajesticBrainStore,
    GBrainProvider as GBrainProvider,
    register,
    MAJESTIC_BRAIN_NOTE_SCHEMA,
    GBRAIN_NOTE_SCHEMA,
)

# Make ``from gbrain.store import GBrainStore`` work without duplicating code.
from majestic_brain.store import (  # noqa: F401
    MajesticBrainStore as MajesticBrainStore,
    GBrainStore as GBrainStore,
    VALID_NOTE_KINDS,
    VALID_SOURCE_TYPES,
    DEFAULT_NOTE_KIND,
    DEFAULT_SOURCE_TYPE,
)
