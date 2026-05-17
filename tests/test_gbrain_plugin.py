"""Tests for the Majestic Brain / GBrain memory provider plugin.

Covers:
  - extractor: all entity types, dedup, edge cases
  - store: add_note, search (FTS5 + LIKE fallback), entity linking, stats
  - provider: lifecycle, tool dispatch, prefetch, on_memory_write
  - imports: both majestic_brain and gbrain import paths work

NOTE: Provider tests (TestGBrainProvider, TestMajesticBrainProvider) require
the Hermes runtime imports ``agent.memory_provider`` and ``tools.registry`` to
be importable. If those are unavailable (e.g. running outside the hermes-agent
tree), those tests are automatically skipped.

Extractor and Store tests have zero external dependencies — they run everywhere.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Path setup handled by conftest.py — repo root and optional Hermes agent dir
# are already on sys.path.

# ===========================================================================
# Import path tests
# ===========================================================================

class TestImportPaths:
    """Verify both majestic_brain and gbrain import paths work."""

    def test_import_majestic_brain_provider(self):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        assert p.name == "majestic-brain"
        assert p.display_name == "Majestic Brain"

    def test_import_gbrain_provider_legacy(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        assert p.name == "majestic-brain"

    def test_gbrain_provider_is_majestic_brain_provider(self):
        from gbrain import GBrainProvider
        from majestic_brain import MajesticBrainProvider
        assert GBrainProvider is MajesticBrainProvider

    def test_import_majestic_brain_store(self):
        from majestic_brain.store import MajesticBrainStore
        assert MajesticBrainStore is not None

    def test_import_gbrain_store_legacy(self):
        from gbrain.store import GBrainStore
        assert GBrainStore is not None

    def test_gbrain_store_is_majestic_brain_store(self):
        from gbrain.store import GBrainStore
        from majestic_brain.store import MajesticBrainStore
        assert GBrainStore is MajesticBrainStore

    def test_import_extractor_from_both(self):
        from majestic_brain.extractor import extract as mb_extract
        from gbrain.extractor import extract as gb_extract
        assert mb_extract is gb_extract


# ===========================================================================
# Extractor tests
# ===========================================================================

class TestExtractor:
    """Test deterministic entity extraction."""

    def test_extract_urls(self):
        from majestic_brain.extractor import extract
        result = extract("Check out https://example.com and http://test.org/path")
        assert "https://example.com" in result["urls"]
        assert "http://test.org/path" in result["urls"]

    def test_extract_file_paths(self):
        from majestic_brain.extractor import extract
        result = extract("Edit src/main.py and ~/.config/hermes/config.yaml")
        paths = result["file_paths"]
        assert any("main.py" in p for p in paths)
        assert any("config.yaml" in p for p in paths)

    def test_extract_handles(self):
        from majestic_brain.extractor import extract
        result = extract("Ask @alice and @bob about this")
        assert "alice" in result["handles"]
        assert "bob" in result["handles"]

    def test_extract_tags(self):
        from majestic_brain.extractor import extract
        result = extract("This is about #python and #testing")
        assert "python" in result["tags"]
        assert "testing" in result["tags"]

    def test_extract_quoted_phrases(self):
        from majestic_brain.extractor import extract
        result = extract('He said "Hello World" and \'goodbye\'')
        assert "Hello World" in result["quoted"]
        assert "goodbye" in result["quoted"]

    def test_extract_capitalized_phrases(self):
        from majestic_brain.extractor import extract
        result = extract("John Doe went to New York yesterday")
        assert "John Doe" in result["capped"]
        assert "New York" in result["capped"]

    def test_extract_aka_aliases(self):
        from majestic_brain.extractor import extract
        result = extract("David aka Dave also known as Coder")
        aliases = result["aliases"]
        assert len(aliases) >= 1
        flat = [n.lower() for pair in aliases for n in pair]
        assert "david" in flat or "dave" in flat

    def test_extract_aka_simple(self):
        from majestic_brain.extractor import extract
        result = extract("Robert aka Bob is here")
        aliases = result["aliases"]
        assert len(aliases) == 1
        assert aliases[0] == ["Robert", "Bob"]

    def test_extract_aka_multi_word_needs_quotes(self):
        from majestic_brain.extractor import extract
        result = extract('Robert "Bob Smith" aka Bobsy')
        assert any("Bobsy" in pair[1] for pair in result["aliases"]) or result["aliases"] == []

    def test_deduplication(self):
        from majestic_brain.extractor import extract
        result = extract("Use #python because #python is great and #python rocks")
        assert result["tags"].count("python") == 1

    def test_empty_input(self):
        from majestic_brain.extractor import extract
        result = extract("")
        assert result["urls"] == []
        assert result["handles"] == []
        assert result["aliases"] == []

    def test_all_entity_names(self):
        from majestic_brain.extractor import extract, all_entity_names
        entities = extract("@alice met Bob Smith at #meeting")
        names = all_entity_names(entities)
        assert "alice" in names
        assert "Bob Smith" in names
        assert "meeting" in names

    def test_no_false_positive_caps(self):
        from majestic_brain.extractor import extract
        result = extract("The Quick brown fox jumped")
        for item in result["capped"]:
            assert len(item.split()) >= 2

    def test_file_path_regex_handles_long_dotted_text_quickly(self):
        from majestic_brain.extractor import extract
        text = ("a." * 30000) + "done"
        start = time.monotonic()
        result = extract(text)
        elapsed = time.monotonic() - start
        assert result["file_paths"] == []
        assert elapsed < 0.5

    def test_extractor_via_gbrain_import(self):
        """Extractor also works via the legacy gbrain shim."""
        from gbrain.extractor import extract
        result = extract("Visit #legacy and @handle")
        assert "legacy" in result["tags"]
        assert "handle" in result["handles"]


# ===========================================================================
# Store tests (MajesticBrainStore — canonical)
# ===========================================================================

class TestMajesticBrainStore:
    """Test the SQLite store via the canonical MajesticBrainStore class."""

    def _make_store(self, tmp_path):
        from majestic_brain.store import MajesticBrainStore
        db_path = tmp_path / "test_mb.db"
        return MajesticBrainStore(db_path)

    def test_add_note_returns_id_and_entities(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note("Meet @alice at #meeting about Project Alpha")
        assert "note_id" in result
        assert isinstance(result["note_id"], int)
        assert "entities" in result
        assert "alice" in result["entities"]["handles"]
        assert "meeting" in result["entities"]["tags"]
        store.close()

    def test_search_finds_note(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Python is my favorite programming language")
        results = store.search("Python")
        assert len(results) >= 1
        assert any("Python" in r["content"] for r in results)
        store.close()

    def test_entity_linking(self, tmp_path):
        store = self._make_store(tmp_path)
        r1 = store.add_note("@alice works on Project Alpha")
        r2 = store.add_note("@alice deployed the system")
        linked = store.get_linked_notes(r1["note_id"])
        assert len(linked) >= 1
        assert any(r["note_id"] == r2["note_id"] for r in linked)
        store.close()

    def test_content_hash_dedup(self, tmp_path):
        store = self._make_store(tmp_path)
        first = store.add_note("Same durable fact")
        second = store.add_note("Same durable fact")
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert second["note_id"] == first["note_id"]
        store.close()

    def test_stats(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Note one with @handle")
        store.add_note("Note two with #tag")
        stats = store.stats()
        assert stats["total_notes"] == 2
        assert stats["total_entities"] >= 2
        store.close()

    def test_add_empty_content_raises(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="content must not be empty"):
            store.add_note("")
        store.close()


# ===========================================================================
# Store tests (GBrainStore — legacy import path)
# ===========================================================================

class TestGBrainStore:
    """Test the SQLite store via the legacy GBrainStore class name."""

    def _make_store(self, tmp_path):
        from gbrain.store import GBrainStore
        db_path = tmp_path / "test_gbrain.db"
        return GBrainStore(db_path)

    def test_add_note_returns_id_and_entities(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note("Meet @alice at #meeting about Project Alpha")
        assert "note_id" in result
        assert isinstance(result["note_id"], int)
        assert "entities" in result
        assert "alice" in result["entities"]["handles"]
        assert "meeting" in result["entities"]["tags"]
        store.close()

    def test_search_finds_note(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Python is my favorite programming language")
        results = store.search("Python")
        assert len(results) >= 1
        assert any("Python" in r["content"] for r in results)
        store.close()

    def test_migrates_legacy_notes_and_backfills_hashes(self, tmp_path):
        from gbrain.store import GBrainStore
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE notes ("
            "note_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, "
            "entities TEXT DEFAULT '{}', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "INSERT INTO notes (content, entities) VALUES (?, ?)",
            ("Legacy Project Alpha note", "{}"),
        )
        conn.commit()
        conn.close()

        store = GBrainStore(db_path)
        cols = {row[1] for row in store._conn.execute("PRAGMA table_info(notes)").fetchall()}
        assert {"content_hash", "note_kind", "source_type", "source_ref", "metadata_json", "updated_at"} <= cols
        row = store._conn.execute("SELECT content_hash, note_kind, source_type FROM notes WHERE note_id = 1").fetchone()
        assert row["content_hash"]
        assert row["note_kind"] == "fact"
        assert row["source_type"] == "manual"
        assert store.search("Legacy Project Alpha")
        duplicate = store.add_note("Legacy Project Alpha note")
        assert duplicate["duplicate"] is True
        assert duplicate["note_id"] == 1
        store.close()

    def test_search_empty_query(self, tmp_path):
        store = self._make_store(tmp_path)
        results = store.search("")
        assert results == []
        store.close()

    def test_search_no_results(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Hello world")
        results = store.search("xyzzynonexistent")
        assert isinstance(results, list)
        store.close()

    def test_entity_linking(self, tmp_path):
        store = self._make_store(tmp_path)
        r1 = store.add_note("@alice works on Project Alpha")
        r2 = store.add_note("@alice deployed the system")
        linked = store.get_linked_notes(r1["note_id"])
        assert len(linked) >= 1
        assert any(r["note_id"] == r2["note_id"] for r in linked)
        store.close()

    def test_get_linked_by_entity(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("@bob likes #rust")
        store.add_note("@bob also likes #python")
        results = store.get_linked_by_entity("bob")
        assert len(results) >= 2
        store.close()

    def test_get_linked_by_entity_unknown(self, tmp_path):
        store = self._make_store(tmp_path)
        results = store.get_linked_by_entity("nonexistent")
        assert results == []
        store.close()

    def test_stats(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Note one with @handle")
        store.add_note("Note two with #tag")
        stats = store.stats()
        assert stats["total_notes"] == 2
        assert stats["total_entities"] >= 2
        assert "db_path" in stats
        store.close()

    def test_add_empty_content_raises(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="content must not be empty"):
            store.add_note("")
        store.close()

    def test_add_whitespace_content_raises(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError):
            store.add_note("   ")
        store.close()

    def test_url_extraction_in_store(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note("Check https://example.com for docs")
        assert any("example.com" in u for u in result["entities"]["urls"])
        store.close()

    def test_aka_creates_aliases(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note("Robert aka Bob is the lead")
        assert len(result["aliases"]) >= 1
        notes = store.get_linked_by_entity("Robert")
        assert len(notes) >= 1
        store.close()

    def test_like_fallback_simulated(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Testing the fallback search mechanism")
        store._has_fts5 = False
        results = store.search("fallback")
        assert len(results) >= 1
        store.close()

    def test_fts_syntax_error_falls_back_to_like(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Edit src/main.py before touching https://example.com/docs")
        results = store.search("src/main.py")
        assert len(results) >= 1
        assert "src/main.py" in results[0]["content"]
        store.close()

    def test_like_fallback_treats_wildcards_literally(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Plain note without percent sign")
        store._has_fts5 = False
        assert store.search("%") == []
        assert store.search("_") == []
        store.close()

    def test_alias_lookup_treats_wildcards_literally(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Robert aka Bob is the lead")
        assert store.get_linked_by_entity("%") == []
        assert store.get_linked_by_entity("_") == []
        store.close()

    def test_content_hash_deduplicates_exact_content(self, tmp_path):
        store = self._make_store(tmp_path)
        first = store.add_note("Same durable fact")
        second = store.add_note("Same durable fact")
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert second["note_id"] == first["note_id"]
        assert second["content_hash"] == first["content_hash"]
        assert store.stats()["total_notes"] == 1
        store.close()

    def test_provenance_fields_are_searchable_results(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note(
            "Weekly cron report mentions #revenue",
            note_kind="artifact",
            source_type="cron_report",
            source_ref="cron:weekly-summary",
            metadata={"job_id": "weekly-summary"},
        )
        assert result["note_kind"] == "artifact"
        assert result["source_type"] == "cron_report"
        found = store.search("Weekly cron report")
        assert found[0]["content_hash"] == result["content_hash"]
        assert found[0]["note_kind"] == "artifact"
        assert found[0]["source_type"] == "cron_report"
        assert found[0]["source_ref"] == "cron:weekly-summary"
        store.close()

    def test_markdown_mirror_written_with_frontmatter(self, tmp_path):
        store = self._make_store(tmp_path)
        result = store.add_note(
            "Captured context about Project Alpha",
            note_kind="semantic",
            source_type="import",
            source_ref="file:/tmp/source.md",
        )
        md_path = store.markdown_dir / f"note_{result['note_id']:06d}.md"
        assert md_path.exists()
        text = md_path.read_text(encoding="utf-8")
        assert "content_hash:" in text
        assert "note_kind: semantic" in text
        assert "source_type: import" in text
        assert "Captured context about Project Alpha" in text
        store.close()

    def test_invalid_note_kind_and_source_type_are_rejected(self, tmp_path):
        store = self._make_store(tmp_path)
        with pytest.raises(ValueError, match="Invalid note_kind"):
            store.add_note("Bad kind", note_kind="junk")
        with pytest.raises(ValueError, match="Invalid source_type"):
            store.add_note("Bad source", source_type="slack_firehose")
        store.close()


# ===========================================================================
# Provider lifecycle and tool dispatch tests
#
# These tests require the Hermes runtime (agent.memory_provider, tools.registry)
# to be importable. They are automatically skipped if running outside the
# hermes-agent tree.
# ===========================================================================

def _hermes_runtime_available() -> bool:
    """Check whether the Hermes runtime modules are importable."""
    try:
        import agent.memory_provider  # noqa: F401
        import tools.registry  # noqa: F401
        return True
    except ImportError:
        return False


_hermes_available = _hermes_runtime_available()
_hermes_skip = pytest.mark.skipif(
    not _hermes_available,
    reason="Hermes runtime (agent.memory_provider, tools.registry) not available",
)


@_hermes_skip
class TestMajesticBrainProvider:
    """Test the provider via the canonical MajesticBrainProvider import path."""

    def _make_provider(self, tmp_path):
        from majestic_brain import MajesticBrainProvider
        provider = MajesticBrainProvider()
        provider.initialize("test-session", hermes_home=str(tmp_path))
        return provider

    def test_name(self):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        assert p.name == "majestic-brain"
        assert p.legacy_name == "gbrain"
        assert p.display_name == "Majestic Brain"
        assert p.matches_name("gbrain")
        assert p.matches_name("majestic-brain")
        assert p.matches_name("majestic_brain")

    def test_is_available(self):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        assert p.is_available() is True

    def test_initialize(self, tmp_path):
        p = self._make_provider(tmp_path)
        assert p._store is not None
        assert p._session_id == "test-session"
        p.shutdown()

    def test_system_prompt_block(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "Majestic Brain Memory" in block
        assert "Empty" in block
        p.shutdown()

    def test_system_prompt_block_with_notes(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("majestic_brain_note", {"action": "add", "content": "Test note"})
        block = p.system_prompt_block()
        assert "1 notes" in block
        p.shutdown()

    def test_prefetch_empty(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = p.prefetch("")
        assert result == ""
        p.shutdown()

    def test_prefetch_with_match(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("majestic_brain_note", {"action": "add", "content": "Python is great"})
        result = p.prefetch("Python")
        assert "Majestic Brain Memory Recall" in result
        assert "Python is great" in result
        p.shutdown()

    def test_prefetch_no_match(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("majestic_brain_note", {"action": "add", "content": "Hello world"})
        result = p.prefetch("xyzzyplugh")
        assert result == ""
        p.shutdown()

    def test_get_tool_schemas(self, tmp_path):
        p = self._make_provider(tmp_path)
        schemas = p.get_tool_schemas()
        assert len(schemas) == 2
        schema_names = {s["name"] for s in schemas}
        assert "majestic_brain_note" in schema_names
        assert "gbrain_note" in schema_names
        p.shutdown()

    def test_primary_tool_name_adds_provenance(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "add",
            "content": "Cron artifact about #shipping",
            "note_kind": "artifact",
            "source_type": "cron_report",
            "source_ref": "cron:weekly-summary",
            "metadata": {"job_id": "weekly-summary"},
        }))
        assert result["note_kind"] == "artifact"
        assert result["source_type"] == "cron_report"
        assert result["duplicate"] is False
        p.shutdown()

    def test_handle_add(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "add", "content": "Meet @alice about #project"
        }))
        assert "note_id" in result
        assert result["entities"]["handles"] == ["alice"]
        p.shutdown()

    def test_handle_search(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("majestic_brain_note", {"action": "add", "content": "Deploy with Kubernetes"})
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "search", "query": "Kubernetes"
        }))
        assert result["count"] >= 1
        p.shutdown()

    def test_on_memory_write(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.on_memory_write("add", "user", "User prefers dark mode")
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "search", "query": "dark mode"
        }))
        assert result["count"] >= 1
        row = result["results"][0]
        assert row["source_type"] == "memory_write"
        p.shutdown()

    def test_shutdown(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.shutdown()
        assert p._store is None

    def test_config_schema_empty(self):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        assert p.get_config_schema() == []


@_hermes_skip
class TestGBrainProvider:
    """Test the provider via the legacy GBrainProvider import path."""

    def _make_provider(self, tmp_path):
        from gbrain import GBrainProvider
        provider = GBrainProvider()
        provider.initialize("test-session", hermes_home=str(tmp_path))
        return provider

    def test_name(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        assert p.name == "majestic-brain"
        assert p.legacy_name == "gbrain"
        assert p.display_name == "Majestic Brain"
        assert p.matches_name("gbrain")
        assert p.matches_name("majestic-brain")

    def test_is_available(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        assert p.is_available() is True

    def test_initialize(self, tmp_path):
        p = self._make_provider(tmp_path)
        assert p._store is not None
        assert p._session_id == "test-session"
        p.shutdown()

    def test_system_prompt_block(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "Majestic Brain Memory" in block
        assert "Empty" in block
        p.shutdown()

    def test_system_prompt_block_with_notes(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {"action": "add", "content": "Test note"})
        block = p.system_prompt_block()
        assert "1 notes" in block
        p.shutdown()

    def test_prefetch_empty(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = p.prefetch("")
        assert result == ""
        p.shutdown()

    def test_prefetch_with_match(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {"action": "add", "content": "Python is great"})
        result = p.prefetch("Python")
        assert "Majestic Brain Memory Recall" in result
        assert "Python is great" in result
        p.shutdown()

    def test_prefetch_no_match(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {"action": "add", "content": "Hello world"})
        result = p.prefetch("xyzzyplugh")
        assert result == ""
        p.shutdown()

    def test_get_tool_schemas(self, tmp_path):
        p = self._make_provider(tmp_path)
        schemas = p.get_tool_schemas()
        assert len(schemas) == 2
        schema_names = {s["name"] for s in schemas}
        assert "majestic_brain_note" in schema_names
        assert "gbrain_note" in schema_names
        p.shutdown()

    def test_legacy_tool_name_still_accepted(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Legacy add still works"
        }))
        assert result["duplicate"] is False
        assert "note_id" in result
        p.shutdown()

    def test_primary_tool_name_adds_provenance(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "add",
            "content": "Cron artifact about #shipping",
            "note_kind": "artifact",
            "source_type": "cron_report",
            "source_ref": "cron:weekly-summary",
            "metadata": {"job_id": "weekly-summary"},
        }))
        assert result["note_kind"] == "artifact"
        assert result["source_type"] == "cron_report"
        assert result["duplicate"] is False
        search = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "search", "query": "Cron artifact"
        }))
        row = search["results"][0]
        assert row["content_hash"] == result["content_hash"]
        assert row["note_kind"] == "artifact"
        assert row["source_type"] == "cron_report"
        assert row["source_ref"] == "cron:weekly-summary"
        p.shutdown()

    def test_handle_add(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Meet @alice about #project"
        }))
        assert "note_id" in result
        assert result["entities"]["handles"] == ["alice"]
        p.shutdown()

    def test_handle_add_empty(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "add", "content": ""
        }))
        assert "error" in result
        p.shutdown()

    def test_handle_search(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {"action": "add", "content": "Deploy with Kubernetes"})
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "Kubernetes"
        }))
        assert result["count"] >= 1
        assert any("Kubernetes" in r["content"] for r in result["results"])
        p.shutdown()

    def test_handle_search_empty_query(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": ""
        }))
        assert "error" in result
        p.shutdown()

    def test_handle_links_by_note_id(self, tmp_path):
        p = self._make_provider(tmp_path)
        r1 = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "@charlie works on backend"
        }))
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "@charlie fixed the bug"
        })
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "links", "note_id": r1["note_id"]
        }))
        assert result["count"] >= 1
        p.shutdown()

    def test_handle_links_by_entity(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "@dave reviews code"
        })
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "@dave writes tests"
        })
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "links", "entity": "dave"
        }))
        assert result["count"] >= 2
        p.shutdown()

    def test_handle_links_missing_params(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "links"
        }))
        assert "error" in result
        p.shutdown()

    def test_handle_stats(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {"action": "add", "content": "A note"})
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "stats"
        }))
        assert result["total_notes"] >= 1
        p.shutdown()

    def test_handle_unknown_action(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = p.handle_tool_call("gbrain_note", {"action": "explode"})
        assert "error" in json.loads(result) or "Unknown" in result
        p.shutdown()

    def test_handle_unknown_tool(self, tmp_path):
        p = self._make_provider(tmp_path)
        result = p.handle_tool_call("not_gbrain_note", {})
        assert "error" in json.loads(result) or "Unknown" in result
        p.shutdown()

    def test_handle_not_initialized(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        result = json.loads(p.handle_tool_call("gbrain_note", {"action": "stats"}))
        assert "error" in result

    def test_on_memory_write(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.on_memory_write("add", "user", "User prefers dark mode")
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "dark mode"
        }))
        assert result["count"] >= 1
        row = result["results"][0]
        assert row["note_kind"] == "fact"
        assert row["source_type"] == "memory_write"
        assert row["source_ref"] == "user"
        assert row["metadata"] == {"action": "add", "target": "user"}
        p.shutdown()

    def test_on_memory_write_not_add(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.on_memory_write("remove", "user", "something")
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "something"
        }))
        assert result["count"] == 0
        p.shutdown()

    def test_sync_turn_noop(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.sync_turn("user message", "assistant message")
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "stats"
        }))
        assert result["total_notes"] == 0
        p.shutdown()

    def test_shutdown(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.shutdown()
        assert p._store is None

    def test_config_schema_empty(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        assert p.get_config_schema() == []

    def test_search_limit_capped(self, tmp_path):
        p = self._make_provider(tmp_path)
        for i in range(5):
            p.handle_tool_call("gbrain_note", {
                "action": "add", "content": f"Note about testing {i}"
            })
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "testing", "limit": 3
        }))
        assert result["count"] <= 3
        p.shutdown()
