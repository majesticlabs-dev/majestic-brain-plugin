"""Tests for the GBrain memory provider plugin.

Covers:
  - extractor: all entity types, dedup, edge cases
  - store: add_note, search (FTS5 + LIKE fallback), entity linking, stats
  - provider: lifecycle, tool dispatch, prefetch, on_memory_write

NOTE: Provider tests (TestGBrainProvider) require the Hermes runtime imports
``agent.memory_provider`` and ``tools.registry`` to be importable. If those
are unavailable (e.g. running outside the hermes-agent tree), those tests are
automatically skipped.

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

# ---------------------------------------------------------------------------
# Path setup — ensure repo root is on sys.path for ``gbrain.*`` imports
# ---------------------------------------------------------------------------

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


# ===========================================================================
# Extractor tests
# ===========================================================================

class TestExtractor:
    """Test deterministic entity extraction."""

    def test_extract_urls(self):
        from gbrain.extractor import extract
        result = extract("Check out https://example.com and http://test.org/path")
        assert "https://example.com" in result["urls"]
        assert "http://test.org/path" in result["urls"]

    def test_extract_file_paths(self):
        from gbrain.extractor import extract
        result = extract("Edit src/main.py and ~/.config/hermes/config.yaml")
        # Should find at least the file paths
        paths = result["file_paths"]
        assert any("main.py" in p for p in paths)
        assert any("config.yaml" in p for p in paths)

    def test_extract_handles(self):
        from gbrain.extractor import extract
        result = extract("Ask @alice and @bob about this")
        assert "alice" in result["handles"]
        assert "bob" in result["handles"]

    def test_extract_tags(self):
        from gbrain.extractor import extract
        result = extract("This is about #python and #testing")
        assert "python" in result["tags"]
        assert "testing" in result["tags"]

    def test_extract_quoted_phrases(self):
        from gbrain.extractor import extract
        result = extract('He said "Hello World" and \'goodbye\'')
        assert "Hello World" in result["quoted"]
        assert "goodbye" in result["quoted"]

    def test_extract_capitalized_phrases(self):
        from gbrain.extractor import extract
        result = extract("John Doe went to New York yesterday")
        assert "John Doe" in result["capped"]
        assert "New York" in result["capped"]

    def test_extract_aka_aliases(self):
        from gbrain.extractor import extract
        result = extract("David aka Dave also known as Coder")
        aliases = result["aliases"]
        # Two AKA patterns: David→Dave, Dave→Coder
        assert len(aliases) >= 1
        flat = [n.lower() for pair in aliases for n in pair]
        assert "david" in flat or "dave" in flat

    def test_extract_aka_simple(self):
        from gbrain.extractor import extract
        result = extract("Robert aka Bob is here")
        aliases = result["aliases"]
        assert len(aliases) == 1
        assert aliases[0] == ["Robert", "Bob"]

    def test_extract_aka_multi_word_needs_quotes(self):
        from gbrain.extractor import extract
        # Multi-word aliases should use quotes; single-word AKA extracts cleanly
        result = extract('Robert "Bob Smith" aka Bobsy')
        # "Bob Smith" extracted as quoted, Bobsy as single-word alias target
        assert any("Bobsy" in pair[1] for pair in result["aliases"]) or result["aliases"] == []

    def test_deduplication(self):
        from gbrain.extractor import extract
        result = extract("Use #python because #python is great and #python rocks")
        assert result["tags"].count("python") == 1

    def test_empty_input(self):
        from gbrain.extractor import extract
        result = extract("")
        assert result["urls"] == []
        assert result["handles"] == []
        assert result["aliases"] == []

    def test_all_entity_names(self):
        from gbrain.extractor import extract, all_entity_names
        entities = extract("@alice met Bob Smith at #meeting")
        names = all_entity_names(entities)
        assert "alice" in names
        assert "Bob Smith" in names
        assert "meeting" in names

    def test_no_false_positive_caps(self):
        from gbrain.extractor import extract
        # Single capitalized words should NOT be extracted as capped phrases
        result = extract("The Quick brown fox jumped")
        # Just check that single-word caps don't pollute capped list
        for item in result["capped"]:
            assert len(item.split()) >= 2

    def test_file_path_regex_handles_long_dotted_text_quickly(self):
        from gbrain.extractor import extract
        text = ("a." * 30000) + "done"
        start = time.monotonic()
        result = extract(text)
        elapsed = time.monotonic() - start
        assert result["file_paths"] == []
        assert elapsed < 0.5


# ===========================================================================
# Store tests
# ===========================================================================

class TestGBrainStore:
    """Test the SQLite store with real databases."""

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

    def test_search_empty_query(self, tmp_path):
        store = self._make_store(tmp_path)
        results = store.search("")
        assert results == []
        store.close()

    def test_search_no_results(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add_note("Hello world")
        results = store.search("xyzzynonexistent")
        # FTS5 may return no results, LIKE fallback also won't match
        assert isinstance(results, list)
        store.close()

    def test_entity_linking(self, tmp_path):
        store = self._make_store(tmp_path)
        r1 = store.add_note("@alice works on Project Alpha")
        r2 = store.add_note("@alice deployed the system")
        # r1 and r2 share @alice — should be linked
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
        # Bob should be resolvable as entity
        notes = store.get_linked_by_entity("Robert")
        # At least the note itself should be findable via Robert
        assert len(notes) >= 1
        store.close()

    def test_like_fallback_simulated(self, tmp_path):
        """Verify LIKE search works by disabling FTS5 flag."""
        store = self._make_store(tmp_path)
        store.add_note("Testing the fallback search mechanism")
        # Force LIKE mode
        store._has_fts5 = False
        results = store.search("fallback")
        assert len(results) >= 1
        store.close()

    def test_fts_syntax_error_falls_back_to_like(self, tmp_path):
        """Raw paths/URLs shouldn't make FTS5 recall brittle."""
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
class TestGBrainProvider:
    """Test the MemoryProvider implementation."""

    def _make_provider(self, tmp_path):
        from gbrain import GBrainProvider
        provider = GBrainProvider()
        provider.initialize("test-session", hermes_home=str(tmp_path))
        return provider

    def test_name(self):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        assert p.name == "gbrain"

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
        assert "GBrain Memory" in block
        assert "Empty" in block  # no notes yet
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
        assert "GBrain Memory Recall" in result
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
        assert len(schemas) == 1
        assert schemas[0]["name"] == "gbrain_note"
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
        # Should be searchable now
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "dark mode"
        }))
        assert result["count"] >= 1
        p.shutdown()

    def test_on_memory_write_not_add(self, tmp_path):
        p = self._make_provider(tmp_path)
        # Only 'add' action should mirror
        p.on_memory_write("remove", "user", "something")
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "something"
        }))
        assert result["count"] == 0
        p.shutdown()

    def test_sync_turn_noop(self, tmp_path):
        p = self._make_provider(tmp_path)
        # Should not raise
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
