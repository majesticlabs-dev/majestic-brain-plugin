"""Compatibility layer tests for the Majestic Brain refactor.

These tests verify that the renamed Majestic Brain plugin remains backward
compatible with existing Hermes deployments where:
  - config.yaml has `memory.provider: gbrain`
  - Plugin directory is `~/.hermes/plugins/gbrain/`
  - Tool calls use `gbrain_note`

And that new naming also works:
  - `memory.provider: majestic-brain`
  - `memory.provider: majestic_brain`
  - Tool calls use `majestic_brain_note`

These tests must pass WITHOUT the Hermes runtime (no agent.memory_provider import).
They test the compatibility contract that Hermes discovery relies on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Path setup handled by conftest.py


# ===========================================================================
# plugin.yaml compatibility tests
# ===========================================================================

class TestPluginYamlCompatibility:
    """Verify plugin.yaml declares names that match both legacy and new config."""

    @pytest.fixture
    def gbrain_plugin_yaml(self):
        """Load and parse gbrain/plugin.yaml."""
        yaml_path = Path(__file__).resolve().parent.parent / "gbrain" / "plugin.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        data = {}
        for line in text.splitlines():
            if ":" in line and not line.startswith(" ") and not line.startswith("#"):
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    data[key] = value
        return data

    @pytest.fixture
    def majestic_brain_plugin_yaml(self):
        """Load and parse majestic_brain/plugin.yaml."""
        yaml_path = Path(__file__).resolve().parent.parent / "majestic_brain" / "plugin.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        data = {}
        for line in text.splitlines():
            if ":" in line and not line.startswith(" ") and not line.startswith("#"):
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    data[key] = value
        return data

    def test_gbrain_plugin_yaml_has_name_gbrain(self, gbrain_plugin_yaml):
        """gbrain/plugin.yaml name must be 'gbrain' for legacy discovery."""
        assert gbrain_plugin_yaml.get("name") == "gbrain"

    def test_gbrain_plugin_yaml_aliases_include_majestic_brain(self, gbrain_plugin_yaml):
        """gbrain/plugin.yaml aliases must include 'majestic-brain'."""
        aliases = gbrain_plugin_yaml.get("aliases", "")
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
        assert "majestic-brain" in alias_list

    def test_gbrain_plugin_yaml_has_legacy_name(self, gbrain_plugin_yaml):
        """gbrain/plugin.yaml must have legacy_name field."""
        assert "legacy_name" in gbrain_plugin_yaml
        assert gbrain_plugin_yaml["legacy_name"] == "gbrain"

    def test_majestic_brain_plugin_yaml_exists(self, majestic_brain_plugin_yaml):
        """majestic_brain/plugin.yaml must exist and have name."""
        assert "name" in majestic_brain_plugin_yaml

    def test_majestic_brain_plugin_yaml_name(self, majestic_brain_plugin_yaml):
        """majestic_brain/plugin.yaml name should be 'majestic-brain'."""
        assert majestic_brain_plugin_yaml.get("name") == "majestic-brain"

    def test_majestic_brain_plugin_yaml_aliases_gbrain(self, majestic_brain_plugin_yaml):
        """majestic_brain/plugin.yaml aliases must include 'gbrain'."""
        aliases = majestic_brain_plugin_yaml.get("aliases", "")
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
        assert "gbrain" in alias_list


# ===========================================================================
# Provider name / matching compatibility tests
# ===========================================================================

class TestProviderNameCompatibility:
    """Verify provider name properties support both legacy and new names."""

    def _make_provider(self):
        from majestic_brain import MajesticBrainProvider
        return MajesticBrainProvider()

    def _make_provider_via_gbrain(self):
        from gbrain import GBrainProvider
        return GBrainProvider()

    def test_provider_name_returns_majestic_brain_for_config_match(self):
        """Provider .name must return 'majestic-brain' — the primary name."""
        p = self._make_provider()
        assert p.name == "majestic-brain"

    def test_provider_name_returns_majestic_brain_via_legacy_import(self):
        """Same test but via the gbrain legacy import path."""
        p = self._make_provider_via_gbrain()
        assert p.name == "majestic-brain"

    def test_provider_has_matches_name_method(self):
        p = self._make_provider()
        assert hasattr(p, "matches_name")
        assert callable(p.matches_name)

    def test_matches_name_accepts_gbrain(self):
        p = self._make_provider()
        assert p.matches_name("gbrain") is True

    def test_matches_name_accepts_majestic_brain_hyphen(self):
        p = self._make_provider()
        assert p.matches_name("majestic-brain") is True

    def test_matches_name_accepts_majestic_brain_underscore(self):
        """matches_name('majestic_brain') must return True — Python/import-friendly alias."""
        p = self._make_provider()
        assert p.matches_name("majestic_brain") is True

    def test_matches_name_rejects_unknown(self):
        p = self._make_provider()
        assert p.matches_name("unknown_provider") is False
        assert p.matches_name("redis") is False

    def test_provider_has_display_name(self):
        p = self._make_provider()
        assert hasattr(p, "display_name")
        assert "Majestic Brain" in p.display_name

    def test_provider_legacy_name_property(self):
        p = self._make_provider()
        assert p.legacy_name == "gbrain"

    def test_provider_names_includes_all(self):
        p = self._make_provider()
        names = p.names
        assert "gbrain" in names
        assert "majestic-brain" in names
        assert "majestic_brain" in names


# ===========================================================================
# Tool schema compatibility tests
# ===========================================================================

class TestToolSchemaCompatibility:
    """Verify tool schemas support both legacy and new tool names."""

    def _make_provider(self, tmp_path):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        return p

    def _make_provider_via_gbrain(self, tmp_path):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        return p

    def test_get_tool_schemas_returns_both_schemas(self, tmp_path):
        p = self._make_provider(tmp_path)
        schemas = p.get_tool_schemas()
        schema_names = {s["name"] for s in schemas}
        assert "majestic_brain_note" in schema_names
        assert "gbrain_note" in schema_names
        p.shutdown()

    def test_both_schemas_via_gbrain_import(self, tmp_path):
        p = self._make_provider_via_gbrain(tmp_path)
        schemas = p.get_tool_schemas()
        schema_names = {s["name"] for s in schemas}
        assert "majestic_brain_note" in schema_names
        assert "gbrain_note" in schema_names
        p.shutdown()

    def test_legacy_gbrain_note_tool_still_works(self, tmp_path):
        p = self._make_provider(tmp_path)

        # add
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Legacy tool add test"
        }))
        assert "note_id" in result
        assert result["duplicate"] is False

        # search
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "Legacy tool"
        }))
        assert result["count"] >= 1

        # stats
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "stats"
        }))
        assert result["total_notes"] >= 1

        p.shutdown()

    def test_majestic_brain_note_tool_works(self, tmp_path):
        p = self._make_provider(tmp_path)

        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "add", "content": "New name tool add test"
        }))
        assert "note_id" in result
        assert result["duplicate"] is False
        p.shutdown()

    def test_both_tool_names_share_same_store(self, tmp_path):
        """Notes added via gbrain_note must be findable via majestic_brain_note and vice versa."""
        p = self._make_provider(tmp_path)

        # Add via legacy name
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Added via gbrain_note"
        })

        # Search via new name
        result = json.loads(p.handle_tool_call("majestic_brain_note", {
            "action": "search", "query": "gbrain_note"
        }))
        assert result["count"] >= 1

        # Add via new name
        p.handle_tool_call("majestic_brain_note", {
            "action": "add", "content": "Added via majestic_brain_note"
        })

        # Search via legacy name
        result = json.loads(p.handle_tool_call("gbrain_note", {
            "action": "search", "query": "majestic_brain_note"
        }))
        assert result["count"] >= 1

        p.shutdown()


# ===========================================================================
# DB path compatibility tests
# ===========================================================================

class TestDBPathCompatibility:
    """Verify DB paths remain at <hermes_home>/gbrain/gbrain.db for data continuity."""

    def test_db_path_uses_gbrain_directory(self, tmp_path):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))

        db_path = tmp_path / "gbrain" / "gbrain.db"
        assert db_path.exists(), (
            f"DB not found at expected path {db_path}. "
            f"DB path must remain <hermes_home>/gbrain/gbrain.db for data continuity."
        )
        p.shutdown()

    def test_db_path_via_gbrain_import(self, tmp_path):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))

        db_path = tmp_path / "gbrain" / "gbrain.db"
        assert db_path.exists()
        p.shutdown()

    def test_db_path_is_stable_across_names(self, tmp_path):
        """Same DB is used regardless of whether provider is discovered as majestic-brain or gbrain."""
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        p.handle_tool_call("majestic_brain_note", {
            "action": "add", "content": "Note via majestic-brain name"
        })
        p.shutdown()

        # Re-init via gbrain import — same store
        from gbrain import GBrainProvider
        p2 = GBrainProvider()
        p2.initialize("test-session-2", hermes_home=str(tmp_path))
        result = json.loads(p2.handle_tool_call("gbrain_note", {
            "action": "search", "query": "majestic-brain name"
        }))
        assert result["count"] >= 1
        p2.shutdown()


# ===========================================================================
# System prompt compatibility tests
# ===========================================================================

class TestSystemPromptCompatibility:
    """Verify system prompt mentions both tool names."""

    def _make_provider(self, tmp_path):
        from majestic_brain import MajesticBrainProvider
        p = MajesticBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        return p

    def test_empty_store_prompt_mentions_primary_tool(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "majestic_brain_note" in block
        p.shutdown()

    def test_empty_store_prompt_mentions_legacy_tool(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "gbrain_note" in block
        p.shutdown()

    def test_prefetch_header_mentions_majestic_brain(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Test note for prefetch"
        })
        result = p.prefetch("Test note")
        assert "Majestic Brain" in result
        p.shutdown()


# ===========================================================================
# Register function compatibility tests
# ===========================================================================

class TestRegisterCompatibility:
    """Verify register() function works correctly from both import paths."""

    def test_register_from_majestic_brain(self):
        from majestic_brain import register
        mock_ctx = MagicMock()
        register(mock_ctx)
        mock_ctx.register_memory_provider.assert_called_once()
        provider = mock_ctx.register_memory_provider.call_args[0][0]
        assert hasattr(provider, "matches_name")
        assert provider.matches_name("gbrain")
        assert provider.matches_name("majestic-brain")

    def test_register_from_gbrain(self):
        from gbrain import register
        mock_ctx = MagicMock()
        register(mock_ctx)
        mock_ctx.register_memory_provider.assert_called_once()
        provider = mock_ctx.register_memory_provider.call_args[0][0]
        assert hasattr(provider, "matches_name")
        assert provider.matches_name("gbrain")
        assert provider.matches_name("majestic-brain")


# ===========================================================================
# Cross-import identity tests
# ===========================================================================

class TestCrossImportIdentity:
    """Verify that legacy and canonical imports yield the same objects."""

    def test_provider_class_identity(self):
        from majestic_brain import MajesticBrainProvider
        from gbrain import GBrainProvider
        assert GBrainProvider is MajesticBrainProvider

    def test_store_class_identity(self):
        from majestic_brain.store import MajesticBrainStore
        from gbrain.store import GBrainStore
        assert GBrainStore is MajesticBrainStore

    def test_register_function_identity(self):
        from majestic_brain import register as mb_register
        from gbrain import register as gb_register
        assert gb_register is mb_register

    def test_extractor_function_identity(self):
        from majestic_brain.extractor import extract as mb_extract
        from gbrain.extractor import extract as gb_extract
        assert gb_extract is mb_extract
