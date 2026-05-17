"""Compatibility layer tests for the Majestic Brain refactor.

These tests verify that the renamed Majestic Brain plugin remains backward
compatible with existing Hermes deployments where:
  - config.yaml has `memory.provider: gbrain`
  - Plugin directory is `~/.hermes/plugins/gbrain/`
  - Tool calls use `gbrain_note`

And that new naming also works:
  - `memory.provider: majestic-brain`
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

_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


# ===========================================================================
# plugin.yaml compatibility tests
# ===========================================================================

class TestPluginYamlCompatibility:
    """Verify plugin.yaml declares names that match both legacy and new config."""

    @pytest.fixture
    def plugin_yaml(self):
        """Load and parse plugin.yaml."""
        import yaml  # stdlib in Python 3.11+, fallback to simple parse
        yaml_path = Path(_repo_root) / "gbrain" / "plugin.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        # Simple YAML parse — no dependency needed
        data = {}
        for line in text.splitlines():
            if ":" in line and not line.startswith(" ") and not line.startswith("#"):
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    data[key] = value
        return data

    def test_plugin_yaml_has_name_field(self, plugin_yaml):
        """plugin.yaml must have a 'name' field for Hermes discovery."""
        assert "name" in plugin_yaml

    def test_plugin_yaml_name_matches_gbrain_config(self, plugin_yaml):
        """plugin.yaml 'name' must include 'gbrain' so config `memory.provider: gbrain` matches.

        Hermes discovery matches plugin.yaml name against config value.
        The name must be 'gbrain' (the legacy config value) to avoid breaking
        existing deployments.
        """
        name = plugin_yaml.get("name", "")
        # The name must be 'gbrain' OR have 'gbrain' as an alias
        assert name == "gbrain" or "gbrain" in plugin_yaml.get("aliases", ""), (
            f"plugin.yaml name='{name}' does not match config 'memory.provider: gbrain'. "
            f"Either name must be 'gbrain' or aliases must include 'gbrain'. "
            f"Got: {plugin_yaml}"
        )

    def test_plugin_yaml_supports_majestic_brain_name(self, plugin_yaml):
        """plugin.yaml must also declare 'majestic-brain' as name or alias.

        This enables future config `memory.provider: majestic-brain`.
        """
        name = plugin_yaml.get("name", "")
        aliases = plugin_yaml.get("aliases", "")
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()] if aliases else []

        all_names = {name} | set(alias_list)
        assert "majestic-brain" in all_names, (
            f"Neither name nor aliases in plugin.yaml include 'majestic-brain'. "
            f"Got name='{name}', aliases={alias_list}"
        )

    def test_plugin_yaml_has_legacy_name_field(self, plugin_yaml):
        """plugin.yaml should have a 'legacy_name' field for documentation."""
        # Not strictly required for runtime, but documents the legacy name
        # We allow this to be absent if name is already 'gbrain'
        name = plugin_yaml.get("name", "")
        if name != "gbrain":
            assert "legacy_name" in plugin_yaml, (
                "plugin.yaml should declare legacy_name when name is not 'gbrain'"
            )


# ===========================================================================
# Provider name / matching compatibility tests
# ===========================================================================

class TestProviderNameCompatibility:
    """Verify provider name properties support both legacy and new names."""

    def _make_provider(self):
        from gbrain import GBrainProvider
        return GBrainProvider()

    def test_provider_name_returns_gbrain_for_config_match(self):
        """Provider .name must return 'gbrain' to match config `memory.provider: gbrain`.

        Hermes core does: if provider.name == config['memory']['provider']
        If this returns 'majestic-brain', it won't match 'gbrain' in config.
        """
        p = self._make_provider()
        # The name must be 'gbrain' to match existing config
        assert p.name == "gbrain", (
            f"Provider.name returns '{p.name}', expected 'gbrain' to match "
            f"config 'memory.provider: gbrain'"
        )

    def test_provider_has_matches_name_method(self):
        """Provider must implement matches_name() for flexible name matching.

        This allows Hermes to check if the provider matches a given config name
        without relying solely on exact .name comparison.
        """
        p = self._make_provider()
        assert hasattr(p, "matches_name"), (
            "GBrainProvider must implement matches_name(name) method for "
            "flexible provider name matching"
        )
        assert callable(p.matches_name), "matches_name must be callable"

    def test_matches_name_accepts_gbrain(self):
        """matches_name('gbrain') must return True."""
        p = self._make_provider()
        assert p.matches_name("gbrain") is True, (
            "matches_name('gbrain') must return True for legacy config compatibility"
        )

    def test_matches_name_accepts_majestic_brain(self):
        """matches_name('majestic-brain') must return True."""
        p = self._make_provider()
        assert p.matches_name("majestic-brain") is True, (
            "matches_name('majestic-brain') must return True for new naming"
        )

    def test_matches_name_rejects_unknown(self):
        """matches_name() must return False for unknown names."""
        p = self._make_provider()
        assert p.matches_name("unknown_provider") is False
        assert p.matches_name("redis") is False

    def test_provider_has_display_name(self):
        """Provider should have a display_name for UI/logging purposes."""
        p = self._make_provider()
        assert hasattr(p, "display_name"), (
            "GBrainProvider should have display_name property for human-readable name"
        )
        assert "Majestic Brain" in p.display_name or "Majestic" in p.display_name, (
            f"display_name should contain 'Majestic Brain', got '{p.display_name}'"
        )

    def test_provider_legacy_name_property(self):
        """Provider legacy_name must return 'gbrain'."""
        p = self._make_provider()
        assert p.legacy_name == "gbrain"

    def test_provider_names_includes_both(self):
        """Provider .names property must list both accepted names."""
        p = self._make_provider()
        assert hasattr(p, "names"), "Provider should have a .names property"
        names = p.names
        assert "gbrain" in names, f"'gbrain' not in provider.names: {names}"
        assert "majestic-brain" in names, f"'majestic-brain' not in provider.names: {names}"


# ===========================================================================
# Tool schema compatibility tests
# ===========================================================================

class TestToolSchemaCompatibility:
    """Verify tool schemas support both legacy and new tool names."""

    def _make_provider(self, tmp_path):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        return p

    def test_get_tool_schemas_returns_both_schemas(self, tmp_path):
        """get_tool_schemas() should return BOTH majestic_brain_note and gbrain_note schemas.

        Hermes uses get_tool_schemas() to register available tools with the LLM.
        If only majestic_brain_note is returned, existing prompts/tool_history
        referencing gbrain_note will break.
        """
        p = self._make_provider(tmp_path)
        schemas = p.get_tool_schemas()
        schema_names = {s["name"] for s in schemas}
        assert "majestic_brain_note" in schema_names, (
            f"majestic_brain_note not in tool schemas: {schema_names}"
        )
        assert "gbrain_note" in schema_names, (
            f"gbrain_note not in tool schemas: {schema_names}. "
            f"Legacy tool name must be registered for backward compatibility."
        )
        p.shutdown()

    def test_legacy_gbrain_note_tool_still_works(self, tmp_path):
        """gbrain_note tool must still handle all actions."""
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
        """majestic_brain_note tool must handle all actions."""
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
    """Verify DB paths remain at <hermes_home>/gbrain/gbrain.db (not renamed)."""

    def test_db_path_uses_gbrain_directory(self, tmp_path):
        """initialize() must create DB at <hermes_home>/gbrain/gbrain.db.

        Existing deployments have data at this path. Renaming would lose data.
        """
        from gbrain import GBrainProvider
        p = GBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))

        db_path = tmp_path / "gbrain" / "gbrain.db"
        assert db_path.exists(), (
            f"DB not found at expected path {db_path}. "
            f"DB path must remain <hermes_home>/gbrain/gbrain.db for data continuity."
        )
        p.shutdown()


# ===========================================================================
# System prompt compatibility tests
# ===========================================================================

class TestSystemPromptCompatibility:
    """Verify system prompt mentions both tool names."""

    def _make_provider(self, tmp_path):
        from gbrain import GBrainProvider
        p = GBrainProvider()
        p.initialize("test-session", hermes_home=str(tmp_path))
        return p

    def test_empty_store_prompt_mentions_primary_tool(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "majestic_brain_note" in block, (
            "System prompt should mention majestic_brain_note as primary tool"
        )
        p.shutdown()

    def test_empty_store_prompt_mentions_legacy_tool(self, tmp_path):
        p = self._make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "gbrain_note" in block, (
            "System prompt should mention gbrain_note for legacy compatibility"
        )
        p.shutdown()

    def test_prefetch_header_mentions_majestic_brain(self, tmp_path):
        p = self._make_provider(tmp_path)
        p.handle_tool_call("gbrain_note", {
            "action": "add", "content": "Test note for prefetch"
        })
        result = p.prefetch("Test note")
        assert "Majestic Brain" in result, (
            "Prefetch header should say 'Majestic Brain Memory Recall'"
        )
        p.shutdown()


# ===========================================================================
# Register function compatibility tests
# ===========================================================================

class TestRegisterCompatibility:
    """Verify register() function works correctly."""

    def test_register_creates_provider(self):
        """register() must create and register a GBrainProvider."""
        from gbrain import register
        mock_ctx = MagicMock()
        register(mock_ctx)
        mock_ctx.register_memory_provider.assert_called_once()
        provider = mock_ctx.register_memory_provider.call_args[0][0]
        assert hasattr(provider, "matches_name")
        assert provider.matches_name("gbrain")
        assert provider.matches_name("majestic-brain")
