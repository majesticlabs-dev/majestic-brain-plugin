"""Shared test configuration for the Majestic Brain plugin test suite.

Adds repo root to sys.path so ``majestic_brain`` imports work
without installation. Also adds the Hermes agent directory to sys.path when
available so provider tests (requiring ``agent.memory_provider`` and
``tools.registry``) can run on developer machines.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root — ensures ``majestic_brain`` is importable.
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Hermes agent directory — needed for ``agent.memory_provider`` and
# ``tools.registry`` imports used by provider tests. Only injected when
# present so standalone pytest (e.g. CI without Hermes) still works for
# store/extractor tests.
_hermes_agent_dir = str(Path.home() / ".hermes" / "hermes-agent")
if Path(_hermes_agent_dir).is_dir() and _hermes_agent_dir not in sys.path:
    sys.path.insert(0, _hermes_agent_dir)
