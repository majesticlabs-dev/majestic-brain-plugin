"""Majestic Brain memory provider — local SQLite/FTS5 note store with deterministic extraction.

Registers as a MemoryProvider plugin. Uses local SQLite with FTS5 for full-text
search, deterministic regex extraction of URLs/file paths/@handles/#tags/quoted
phrases/capitalized entities/AKA aliases, and entity-based note linking.

No network calls, no model calls, no external dependencies. Disabled unless
activated via ``memory.provider: majestic-brain`` (or ``gbrain`` for legacy) in config.yaml.

Storage: ``<hermes_home>/gbrain/gbrain.db`` (preserved for data continuity)

Legacy compatibility: accepts both ``majestic_brain_note`` (primary) and
``gbrain_note`` (legacy) tool names. Provider primary name is ``majestic-brain``;
``gbrain`` is accepted as a legacy alias via ``matches_name()``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

from .store import MajesticBrainStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

MAJESTIC_BRAIN_NOTE_SCHEMA = {
    "name": "majestic_brain_note",
    "description": (
        "Majestic Brain local note memory. Stores notes with auto-extracted entities "
        "(URLs, file paths, @handles, #tags, quoted phrases, capitalized names, "
        "AKA aliases) and links them into a searchable graph.\n\n"
        "ACTIONS:\n"
        "  add    — Store a note. Returns note_id, extracted entities, aliases, "
        "content_hash, note_kind, source_type, duplicate.\n"
        "  search — Full-text search. Returns matching notes.\n"
        "  links  — Notes linked via shared entities to a given note_id or entity.\n"
        "  stats  — Store statistics (note count, entity count, note_kinds, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "links", "stats"],
                "description": "Action to perform.",
            },
            "content": {
                "type": "string",
                "description": "Note content (required for 'add').",
            },
            "query": {
                "type": "string",
                "description": "Search query (required for 'search').",
            },
            "note_id": {
                "type": "integer",
                "description": "Note ID for 'links' (find related notes).",
            },
            "entity": {
                "type": "string",
                "description": "Entity name for 'links' (find notes for entity).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 10).",
            },
            "note_kind": {
                "type": "string",
                "description": "Note kind: fact|episodic|semantic|artifact (default: fact).",
            },
            "source_type": {
                "type": "string",
                "description": "Source type: manual|memory_write|cron_report|auto_fetch_artifact|import|unknown (default: manual).",
            },
            "source_ref": {
                "type": "string",
                "description": "Source reference (e.g., URL, file path).",
            },
            "metadata": {
                "type": "object",
                "description": "Optional JSON-serializable provenance metadata.",
            },
        },
        "required": ["action"],
    },
}

# Legacy schema kept for backward compatibility
GBRAIN_NOTE_SCHEMA = {
    "name": "gbrain_note",
    "description": (
        "[Legacy] Use majestic_brain_note instead. "
        "GBrain local note memory. Stores notes with auto-extracted entities."
    ),
    "parameters": MAJESTIC_BRAIN_NOTE_SCHEMA["parameters"],
}

# Tools we accept in handle_tool_call
_ACCEPTED_TOOL_NAMES = {"majestic_brain_note", "gbrain_note"}


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

class MajesticBrainProvider(MemoryProvider):
    """Majestic Brain memory provider — SQLite/FTS5 with deterministic extraction.

    Primary name is 'majestic-brain' for config/discovery compatibility.
    Display name is 'Majestic Brain' for user-facing messages.
    Legacy 'gbrain' is accepted for matching via matches_name().
    """

    # All provider names that Hermes discovery may use to match this provider.
    _ALL_NAMES = frozenset({"majestic-brain", "gbrain", "majestic_brain"})

    def __init__(self) -> None:
        self._store: Optional[MajesticBrainStore] = None
        self._session_id: str = ""
        self._hermes_home: str = ""

    @property
    def name(self) -> str:
        """Return 'majestic-brain' — the primary provider name for config matching.

        This is the config-matching name, not the display name.
        Legacy 'gbrain' is also accepted via matches_name().
        """
        return "majestic-brain"

    @property
    def legacy_name(self) -> str:
        """Legacy provider name for backward compatibility."""
        return "gbrain"

    @property
    def display_name(self) -> str:
        """Human-readable name for UI/logging."""
        return "Majestic Brain"

    @property
    def names(self) -> frozenset:
        """All provider names accepted for matching."""
        return self._ALL_NAMES

    def matches_name(self, candidate: str) -> bool:
        """Check whether this provider matches the given name.

        Accepts 'majestic-brain' (primary/config), 'gbrain' (legacy),
        and 'majestic_brain' (Python/import-friendly).
        """
        return candidate in self._ALL_NAMES

    def is_available(self) -> bool:
        """Always available — SQLite is in stdlib, no external deps needed."""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", "")
        if not self._hermes_home:
            from hermes_constants import get_hermes_home
            self._hermes_home = str(get_hermes_home())

        db_dir = Path(self._hermes_home) / "gbrain"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "gbrain.db"
        self._store = MajesticBrainStore(db_path)
        logger.info("Majestic Brain initialized: db=%s, fts5=%s",
                     db_path, self._store._has_fts5)

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            stats = self._store.stats()
        except Exception:
            stats = {"total_notes": 0, "total_entities": 0}

        notes = stats.get("total_notes", 0)
        entities = stats.get("total_entities", 0)
        if notes == 0:
            return (
                "# Majestic Brain Memory\n"
                "Active. Empty note store — use majestic_brain_note(action='add') to store "
                "notes with auto-extracted entities (URLs, paths, handles, tags, aliases). "
                "Supports note_kind (fact|episodic|semantic|artifact) and source tracking.\n"
                "Use majestic_brain_note(action='search') to recall notes.\n"
                "Use majestic_brain_note(action='links') to find related notes.\n"
                "[Legacy tool name 'gbrain_note' also accepted.]"
            )
        return (
            f"# Majestic Brain Memory\n"
            f"Active. {notes} notes, {entities} entities. "
            f"Use majestic_brain_note to add, search, or find linked notes.\n"
            f"[Legacy tool name 'gbrain_note' also accepted.]"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant notes for the upcoming turn."""
        if not self._store or not query:
            return ""
        try:
            results = self._store.search(query, limit=5)
            if not results:
                return ""
            lines = ["## Majestic Brain Memory Recall"]
            for r in results:
                lines.append(f"- [note {r['note_id']}] {r['content'][:200]}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Majestic Brain prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Majestic Brain doesn't auto-sync turns — only explicit tool calls and on_memory_write."""
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [MAJESTIC_BRAIN_NOTE_SCHEMA, GBRAIN_NOTE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name not in _ACCEPTED_TOOL_NAMES:
            return tool_error(f"Unknown tool: {tool_name}")
        if not self._store:
            return json.dumps({"error": "Majestic Brain not initialized"})

        action = args.get("action", "")
        try:
            if action == "add":
                return self._handle_add(args)
            elif action == "search":
                return self._handle_search(args)
            elif action == "links":
                return self._handle_links(args)
            elif action == "stats":
                return self._handle_stats()
            else:
                return tool_error(f"Unknown action: {action}")
        except Exception as e:
            return tool_error(str(e))

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory writes into the Majestic Brain store."""
        if action == "add" and self._store and content:
            try:
                mem_meta = {
                    "action": action,
                    "target": target,
                }
                if metadata:
                    mem_meta.update(metadata)
                self._store.add_note(
                    content,
                    note_kind="fact",
                    source_type="memory_write",
                    source_ref=target,
                    metadata=mem_meta,
                )
            except Exception as e:
                logger.debug("Majestic Brain memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        if self._store:
            self._store.close()
            self._store = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_add(self, args: dict) -> str:
        content = args.get("content", "").strip()
        if not content:
            return json.dumps({"error": "content is required"})
        note_kind = args.get("note_kind", "fact")
        source_type = args.get("source_type", "manual")
        source_ref = args.get("source_ref", "")
        metadata = args.get("metadata")
        result = self._store.add_note(
            content,
            note_kind=note_kind,
            source_type=source_type,
            source_ref=source_ref,
            metadata=metadata,
        )
        return json.dumps(result)

    def _handle_search(self, args: dict) -> str:
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "query is required"})
        limit = max(1, min(int(args.get("limit", 10)), 50))
        results = self._store.search(query, limit=limit)
        return json.dumps({"results": results, "count": len(results)})

    def _handle_links(self, args: dict) -> str:
        note_id = args.get("note_id")
        entity = args.get("entity", "").strip()
        limit = max(1, min(int(args.get("limit", 10)), 50))

        if note_id is not None:
            results = self._store.get_linked_notes(int(note_id), limit=limit)
        elif entity:
            results = self._store.get_linked_by_entity(entity, limit=limit)
        else:
            return json.dumps({"error": "note_id or entity is required"})

        return json.dumps({"results": results, "count": len(results)})

    def _handle_stats(self) -> str:
        return json.dumps(self._store.stats())


# Legacy alias — code importing GBrainProvider from majestic_brain keeps working.
GBrainProvider = MajesticBrainProvider


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the Majestic Brain memory provider with the plugin system."""
    provider = MajesticBrainProvider()
    ctx.register_memory_provider(provider)
