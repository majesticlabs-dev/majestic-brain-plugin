"""SQLite/FTS5-backed note store for GBrain.

Stores notes with extracted entities and supports full-text search with
an automatic FTS5 fallback to LIKE when FTS5 is unavailable.

Thread-safe via an RLock. Profile-scoped: each store lives at
``<hermes_home>/gbrain/gbrain.db``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .extractor import extract, all_entity_names

logger = logging.getLogger(__name__)


def _escape_like(value: str) -> str:
    """Escape user input for SQLite LIKE patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    entities    TEXT DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    aliases     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS note_entities (
    note_id   INTEGER REFERENCES notes(note_id) ON DELETE CASCADE,
    entity_id INTEGER REFERENCES entities(entity_id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_note_entities_entity ON note_entities(entity_id);
"""

# FTS5 schema — applied separately because it may fail on SQLite builds
# compiled without FTS5 support.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
    USING fts5(content, content=notes, content_rowid=note_id);

CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content) VALUES (new.note_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content)
        VALUES ('delete', old.note_id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content)
        VALUES ('delete', old.note_id, old.content);
    INSERT INTO notes_fts(rowid, content) VALUES (new.note_id, new.content);
END;
"""


class GBrainStore:
    """SQLite-backed note store with FTS5 and entity linking."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._has_fts5 = self._init_db()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> bool:
        """Create tables and indexes. Returns True if FTS5 is available."""
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            try:
                self._conn.executescript(_FTS_SCHEMA)
                self._conn.commit()
                return True
            except sqlite3.OperationalError:
                logger.info("FTS5 not available, falling back to LIKE search")
                return False

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_note(self, content: str) -> Dict[str, Any]:
        """Insert a note, extract and link entities.

        Returns dict with note_id, entities (dict), and aliases (list of pairs).
        """
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")

        entities = extract(content)
        entity_names = all_entity_names(entities)
        aliases = entities.get("aliases", [])

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notes (content, entities) VALUES (?, ?)",
                (content, json.dumps(entities)),
            )
            note_id: int = cur.lastrowid  # type: ignore[assignment]

            # Resolve and link entities
            for name in entity_names:
                entity_id = self._resolve_entity(name)
                self._link_note_entity(note_id, entity_id)

            # Store aliases on entity rows
            for pair in aliases:
                self._add_alias(pair[0], pair[1])

            self._conn.commit()

        return {
            "note_id": note_id,
            "entities": entities,
            "aliases": aliases,
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search notes using FTS5 (or LIKE fallback).

        Returns list of note dicts ordered by relevance.
        """
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 50))

        with self._lock:
            if self._has_fts5:
                try:
                    return self._search_fts(query, limit)
                except sqlite3.OperationalError:
                    # Raw user queries can contain FTS syntax characters
                    # (paths, URLs, apostrophes). Fall back instead of making
                    # recall brittle exactly when the user needs it.
                    return self._search_like(query, limit)
            return self._search_like(query, limit)

    def _search_fts(self, query: str, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT n.note_id, n.content, n.entities, n.created_at
            FROM notes n
            JOIN notes_fts fts ON fts.rowid = n.note_id
            WHERE notes_fts MATCH ?
            ORDER BY fts.rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _search_like(self, query: str, limit: int) -> List[Dict[str, Any]]:
        pattern = f"%{_escape_like(query)}%"
        rows = self._conn.execute(
            """
            SELECT note_id, content, entities, created_at
            FROM notes
            WHERE content LIKE ? ESCAPE '\\'
            ORDER BY note_id DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Entity linking
    # ------------------------------------------------------------------

    def get_linked_notes(self, note_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Return notes that share entities with the given note.

        Ordered by number of shared entities (most-linked first).
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT n.note_id, n.content, n.entities, n.created_at,
                       COUNT(*) AS shared_entities
                FROM notes n
                JOIN note_entities ne ON ne.note_id = n.note_id
                WHERE ne.entity_id IN (
                    SELECT entity_id FROM note_entities WHERE note_id = ?
                )
                  AND n.note_id != ?
                GROUP BY n.note_id
                ORDER BY shared_entities DESC, n.note_id DESC
                LIMIT ?
                """,
                (note_id, note_id, limit),
            ).fetchall()
            results = []
            for r in rows:
                d = self._row_to_dict(r)
                d["shared_entities"] = r["shared_entities"]
                results.append(d)
            return results

    def get_linked_by_entity(self, entity_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return notes linked to a named entity (resolves aliases)."""
        with self._lock:
            entity_id = self._find_entity(entity_name)
            if entity_id is None:
                return []
            rows = self._conn.execute(
                """
                SELECT n.note_id, n.content, n.entities, n.created_at
                FROM notes n
                JOIN note_entities ne ON ne.note_id = n.note_id
                WHERE ne.entity_id = ?
                ORDER BY n.note_id DESC
                LIMIT ?
                """,
                (entity_id, limit),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_notes = self._conn.execute(
                "SELECT COUNT(*) FROM notes"
            ).fetchone()[0]
            total_entities = self._conn.execute(
                "SELECT COUNT(*) FROM entities"
            ).fetchone()[0]
            total_aliases = self._conn.execute(
                "SELECT COUNT(*) FROM entities WHERE aliases != '' AND aliases IS NOT NULL"
            ).fetchone()[0]
        return {
            "total_notes": total_notes,
            "total_entities": total_entities,
            "total_aliases": total_aliases,
            "db_path": str(self.db_path),
            "fts5": self._has_fts5,
        }

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def _resolve_entity(self, name: str) -> int:
        """Find existing entity or create one. Returns entity_id."""
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])
        cur = self._conn.execute(
            "INSERT INTO entities (name) VALUES (?)", (name,)
        )
        return int(cur.lastrowid)  # type: ignore[return-value]

    def _find_entity(self, name: str) -> Optional[int]:
        """Find entity by name or alias. Returns entity_id or None."""
        # Direct name match
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])
        # Alias match (comma-separated aliases column)
        alias_pattern = f"%,{_escape_like(name)},%"
        row = self._conn.execute(
            "SELECT entity_id FROM entities"
            " WHERE ',' || aliases || ',' LIKE ? ESCAPE '\\'",
            (alias_pattern,),
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])
        return None

    def _link_note_entity(self, note_id: int, entity_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO note_entities (note_id, entity_id) VALUES (?, ?)",
            (note_id, entity_id),
        )

    def _add_alias(self, name: str, alias: str) -> None:
        """Record an alias on the entity row for *name*."""
        entity_id = self._resolve_entity(name)
        row = self._conn.execute(
            "SELECT aliases FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return
        existing = row["aliases"] or ""
        existing_parts = [a.strip() for a in existing.split(",") if a.strip()]
        if alias not in existing_parts:
            existing_parts.append(alias)
            self._conn.execute(
                "UPDATE entities SET aliases = ? WHERE entity_id = ?",
                (",".join(existing_parts), entity_id),
            )

    # ------------------------------------------------------------------
    # Row helper
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        # Parse entities JSON if present
        if "entities" in d and isinstance(d["entities"], str):
            try:
                d["entities"] = json.loads(d["entities"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
