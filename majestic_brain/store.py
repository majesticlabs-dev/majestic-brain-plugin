"""SQLite/FTS5-backed note store for Majestic Brain.

Stores notes with extracted entities and supports full-text search with
an automatic FTS5 fallback to LIKE when FTS5 is unavailable.

Thread-safe via an RLock. Profile-scoped: each store lives at
``<hermes_home>/gbrain/gbrain.db``.

OpenHuman-style memory primitives: content_hash deduplication,
provenance fields (note_kind, source_type, source_ref, metadata_json),
and deterministic markdown mirror export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .extractor import extract, all_entity_names

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

VALID_NOTE_KINDS = {"fact", "episodic", "semantic", "artifact"}
VALID_SOURCE_TYPES = {
    "manual",
    "memory_write",
    "cron_report",
    "auto_fetch_artifact",
    "import",
    "unknown",
}

DEFAULT_NOTE_KIND = "fact"
DEFAULT_SOURCE_TYPE = "manual"


def _escape_like(value: str) -> str:
    """Escape user input for SQLite LIKE patterns."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _content_hash(content: str) -> str:
    """SHA-256 hex digest of stripped content for deduplication."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _safe_yaml_value(value: Any) -> str:
    """Format a value for simple YAML-like frontmatter without PyYAML."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_safe_yaml_value(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    content       TEXT NOT NULL,
    entities      TEXT DEFAULT '{}',
    content_hash  TEXT NOT NULL DEFAULT '',
    note_kind     TEXT NOT NULL DEFAULT 'fact',
    source_type   TEXT NOT NULL DEFAULT 'manual',
    source_ref    TEXT NOT NULL DEFAULT '',
    metadata_json TEXT DEFAULT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# Migration: add new columns to pre-existing databases that lack them.
_MIGRATIONS = [
    "ALTER TABLE notes ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN note_kind TEXT NOT NULL DEFAULT 'fact'",
    "ALTER TABLE notes ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE notes ADD COLUMN source_ref TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE notes ADD COLUMN metadata_json TEXT DEFAULT NULL",
    "ALTER TABLE notes ADD COLUMN updated_at TIMESTAMP DEFAULT ''",
]

_CREATE_INDEX_HASH = (
    "CREATE INDEX IF NOT EXISTS idx_notes_content_hash ON notes(content_hash)"
)


class MajesticBrainStore:
    """SQLite-backed note store with FTS5, entity linking, and memory primitives.

    Attributes:
        db_path: Path to the SQLite database file.
        markdown_dir: Path to the markdown mirror directory (or None if disabled).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        markdown_dir: Optional[str | Path] = None,
    ) -> None:
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

        # Markdown mirror directory
        if markdown_dir is not None:
            self.markdown_dir = Path(markdown_dir)
        else:
            self.markdown_dir = self.db_path.parent / "markdown"
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> bool:
        """Create tables and indexes. Returns True if FTS5 is available."""
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._apply_migrations()
            try:
                self._conn.executescript(_FTS_SCHEMA)
                self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')")
                self._conn.commit()
                return True
            except sqlite3.OperationalError:
                logger.info("FTS5 not available, falling back to LIKE search")
                return False

    def _apply_migrations(self) -> None:
        """Add new columns to existing databases (idempotent)."""
        cursor = self._conn.execute("PRAGMA table_info(notes)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        for migration_sql in _MIGRATIONS:
            parts = migration_sql.split()
            if len(parts) >= 6 and parts[0] == "ALTER" and parts[3] == "ADD" and parts[4] == "COLUMN":
                col_name = parts[5]
                if col_name not in existing_cols:
                    try:
                        self._conn.execute(migration_sql)
                        existing_cols.add(col_name)
                    except sqlite3.OperationalError:
                        pass

        # Backfill hashes for rows that predate content-addressed storage.
        try:
            rows = self._conn.execute(
                "SELECT note_id, content FROM notes WHERE content_hash = '' OR content_hash IS NULL"
            ).fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE notes SET content_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE note_id = ?",
                    (_content_hash(row["content"]), row["note_id"]),
                )
        except sqlite3.OperationalError:
            pass

        try:
            self._conn.execute(_CREATE_INDEX_HASH)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_note(
        self,
        content: str,
        *,
        note_kind: str = DEFAULT_NOTE_KIND,
        source_type: str = DEFAULT_SOURCE_TYPE,
        source_ref: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Insert a note, extract and link entities.

        Deduplicates by content_hash — if the exact content already exists,
        returns the existing note_id with ``duplicate=True``.
        """
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")

        if note_kind not in VALID_NOTE_KINDS:
            raise ValueError(
                f"Invalid note_kind '{note_kind}'. Must be one of: {sorted(VALID_NOTE_KINDS)}"
            )
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"Invalid source_type '{source_type}'. Must be one of: {sorted(VALID_SOURCE_TYPES)}"
            )

        content_hash = _content_hash(content)
        entities = extract(content)
        entity_names = all_entity_names(entities)
        aliases = entities.get("aliases", [])

        with self._lock:
            existing = self._conn.execute(
                """
                SELECT note_id, note_kind, source_type, source_ref, metadata_json
                FROM notes
                WHERE content_hash = ?
                LIMIT 1
                """,
                (content_hash,),
            ).fetchone()
            if existing is not None:
                return {
                    "note_id": existing["note_id"],
                    "entities": entities,
                    "aliases": aliases,
                    "content_hash": content_hash,
                    "note_kind": existing["note_kind"],
                    "source_type": existing["source_type"],
                    "source_ref": existing["source_ref"],
                    "metadata_json": existing["metadata_json"],
                    "duplicate": True,
                }

            metadata_json = json.dumps(metadata) if metadata else None
            now = _utc_now_iso()

            cur = self._conn.execute(
                (
                    "INSERT INTO notes "
                    "(content, entities, content_hash, note_kind, source_type, "
                    "source_ref, metadata_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    content,
                    json.dumps(entities),
                    content_hash,
                    note_kind,
                    source_type,
                    source_ref,
                    metadata_json,
                    now,
                    now,
                ),
            )
            note_id: int = cur.lastrowid  # type: ignore[assignment]

            for name in entity_names:
                entity_id = self._resolve_entity(name)
                self._link_note_entity(note_id, entity_id)

            for pair in aliases:
                self._add_alias(pair[0], pair[1])

            self._conn.commit()

        self._write_markdown_mirror(note_id, content, content_hash, note_kind,
                                     source_type, source_ref, entities, now)

        return {
            "note_id": note_id,
            "entities": entities,
            "aliases": aliases,
            "content_hash": content_hash,
            "note_kind": note_kind,
            "source_type": source_type,
            "source_ref": source_ref,
            "metadata_json": metadata_json,
            "duplicate": False,
        }

    # ------------------------------------------------------------------
    # Markdown mirror
    # ------------------------------------------------------------------

    def _write_markdown_mirror(
        self,
        note_id: int,
        content: str,
        content_hash: str,
        note_kind: str,
        source_type: str,
        source_ref: str,
        entities: Dict[str, Any],
        created_at: str,
    ) -> None:
        """Write a human-readable .md file for the note."""
        try:
            md_path = self.markdown_dir / f"note_{note_id:06d}.md"
            lines: List[str] = []
            lines.append("---")
            lines.append(f"note_id: {note_id}")
            lines.append(f"content_hash: {content_hash}")
            lines.append(f"note_kind: {note_kind}")
            lines.append(f"source_type: {source_type}")
            lines.append(f"source_ref: {_safe_yaml_value(source_ref)}")
            lines.append(f"created_at: {_safe_yaml_value(created_at)}")

            entity_names = all_entity_names(entities)
            if entity_names:
                lines.append(f"entities: {_safe_yaml_value(entity_names)}")
            else:
                lines.append("entities: []")

            lines.append("---")
            lines.append("")
            lines.append(content)
            lines.append("")

            md_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.debug("Markdown mirror write failed for note %s: %s", note_id, e)

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
                    return self._search_like(query, limit)
            return self._search_like(query, limit)

    def _search_fts(self, query: str, limit: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT n.note_id, n.content, n.entities, n.created_at,
                   n.content_hash, n.note_kind, n.source_type, n.source_ref,
                   n.metadata_json
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
            SELECT note_id, content, entities, created_at,
                   content_hash, note_kind, source_type, source_ref,
                   metadata_json
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
                       n.content_hash, n.note_kind, n.source_type, n.source_ref,
                       n.metadata_json,
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
                SELECT n.note_id, n.content, n.entities, n.created_at,
                       n.content_hash, n.note_kind, n.source_type, n.source_ref,
                       n.metadata_json
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

            note_kinds = {}
            try:
                rows = self._conn.execute(
                    "SELECT note_kind, COUNT(*) as cnt FROM notes GROUP BY note_kind"
                ).fetchall()
                note_kinds = {row["note_kind"]: row["cnt"] for row in rows}
            except sqlite3.OperationalError:
                pass

        return {
            "total_notes": total_notes,
            "total_entities": total_entities,
            "total_aliases": total_aliases,
            "note_kinds": note_kinds,
            "db_path": str(self.db_path),
            "markdown_dir": str(self.markdown_dir),
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
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])
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
        if "entities" in d and isinstance(d["entities"], str):
            try:
                d["entities"] = json.loads(d["entities"])
            except (json.JSONDecodeError, TypeError):
                pass
        if (
            "metadata_json" in d
            and isinstance(d["metadata_json"], str)
            and d["metadata_json"]
        ):
            try:
                d["metadata"] = json.loads(d["metadata_json"])
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


# Legacy alias — code importing GBrainStore from this module keeps working.
GBrainStore = MajesticBrainStore
