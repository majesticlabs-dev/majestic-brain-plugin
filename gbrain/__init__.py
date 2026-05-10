"""GBrain memory provider — local SQLite/FTS5 note store with deterministic extraction.

Registers as a MemoryProvider plugin. Uses local SQLite with FTS5 for full-text
search, deterministic regex extraction of URLs/file paths/@handles/#tags/quoted
phrases/capitalized entities/AKA aliases, and entity-based note linking.

No network calls, no model calls, no external dependencies. Disabled unless
activated via ``memory.provider: gbrain`` in config.yaml.

Storage: ``<hermes_home>/gbrain/gbrain.db``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

from .store import GBrainStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

GBRAIN_NOTE_SCHEMA = {
    "name": "gbrain_note",
    "description": (
        "GBrain local note memory. Stores notes with auto-extracted entities "
        "(URLs, file paths, @handles, #tags, quoted phrases, capitalized names, "
        "AKA aliases) and links them into a searchable graph.\n\n"
        "ACTIONS:\n"
        "  add    — Store a note. Returns note_id, extracted entities, aliases.\n"
        "  search — Full-text search. Returns matching notes.\n"
        "  links  — Notes linked via shared entities to a given note_id or entity.\n"
        "  stats  — Store statistics (note count, entity count, etc.)."
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
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

class GBrainProvider(MemoryProvider):
    """GBrain memory provider — SQLite/FTS5 with deterministic extraction."""

    def __init__(self) -> None:
        self._store: Optional[GBrainStore] = None
        self._session_id: str = ""
        self._hermes_home: str = ""

    @property
    def name(self) -> str:
        return "gbrain"

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
        self._store = GBrainStore(db_path)
        logger.info("GBrain initialized: db=%s, fts5=%s",
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
                "# GBrain Memory\n"
                "Active. Empty note store — use gbrain_note(action='add') to store "
                "notes with auto-extracted entities (URLs, paths, handles, tags, aliases).\n"
                "Use gbrain_note(action='search') to recall notes.\n"
                "Use gbrain_note(action='links') to find related notes."
            )
        return (
            f"# GBrain Memory\n"
            f"Active. {notes} notes, {entities} entities. "
            f"Use gbrain_note to add, search, or find linked notes."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant notes for the upcoming turn."""
        if not self._store or not query:
            return ""
        try:
            results = self._store.search(query, limit=5)
            if not results:
                return ""
            lines = ["## GBrain Memory Recall"]
            for r in results:
                lines.append(f"- [note {r['note_id']}] {r['content'][:200]}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("GBrain prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """GBrain doesn't auto-sync turns — only explicit tool calls and on_memory_write."""
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [GBRAIN_NOTE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name != "gbrain_note":
            return tool_error(f"Unknown tool: {tool_name}")
        if not self._store:
            return json.dumps({"error": "GBrain not initialized"})

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
        """Mirror built-in memory writes into the GBrain store."""
        if action == "add" and self._store and content:
            try:
                self._store.add_note(content)
            except Exception as e:
                logger.debug("GBrain memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        if self._store:
            self._store.close()
            self._store = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_add(self, args: dict) -> str:
        content = args.get("content", "").strip()
        if not content:
            return json.dumps({"error": "content is required"})
        result = self._store.add_note(content)
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


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the GBrain memory provider with the plugin system."""
    provider = GBrainProvider()
    ctx.register_memory_provider(provider)
