#!/usr/bin/env python3
"""Simple one-time migration from a legacy gbrain DB.

Migrates the old ``<hermes_home>/gbrain/gbrain.db`` (and optional markdown
mirror) to the new canonical location::

    <hermes_home>/majestic-brain/majestic_brain.db

Usage (defaults to the standard legacy path)::

    python scripts/migrate_legacy_db.py

Override source if your legacy DB lived elsewhere::

    python scripts/migrate_legacy_db.py --source-db /path/to/old.db

Optionally copy an existing markdown mirror too::

    python scripts/migrate_legacy_db.py --source-markdown /path/to/markdown
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from hermes_constants import get_hermes_home

# Legacy defaults — the old gbrain plugin stored its DB here.
_LEGACY_DB_DIR = "gbrain"
_LEGACY_DB_NAME = "gbrain.db"

# New canonical paths.
_NEW_DIR_NAME = "majestic-brain"
_NEW_DB_NAME = "majestic_brain.db"


def _default_legacy_db() -> Path:
    """Return the default legacy DB path: <hermes_home>/gbrain/gbrain.db."""
    return Path(get_hermes_home()) / _LEGACY_DB_DIR / _LEGACY_DB_NAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy gbrain database to majestic-brain.",
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=None,
        help=(
            "Path to the existing legacy SQLite database file. "
            "Defaults to <hermes_home>/gbrain/gbrain.db."
        ),
    )
    parser.add_argument(
        "--source-markdown",
        type=Path,
        default=None,
        help="Optional path to an existing markdown mirror directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination DB if it already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    source_db = args.source_db
    if source_db is None:
        source_db = _default_legacy_db()
        print(f"No --source-db provided; using legacy default: {source_db}")
    source_db = source_db.expanduser().resolve()

    if not source_db.exists():
        raise SystemExit(f"Source DB not found: {source_db}")

    hermes_home = Path(get_hermes_home())
    new_dir = hermes_home / _NEW_DIR_NAME
    new_dir.mkdir(parents=True, exist_ok=True)

    new_db = new_dir / _NEW_DB_NAME
    if new_db.exists() and not args.force:
        raise SystemExit(
            f"Destination DB already exists at {new_db}. Re-run with --force to overwrite."
        )

    print(f"Copying {source_db} -> {new_db}")
    shutil.copy2(source_db, new_db)

    if args.source_markdown:
        source_markdown = args.source_markdown.expanduser().resolve()
        if not source_markdown.exists():
            raise SystemExit(f"Source markdown directory not found: {source_markdown}")
        new_markdown = new_dir / "markdown"
        print(f"Copying markdown mirror {source_markdown} -> {new_markdown}")
        shutil.copytree(source_markdown, new_markdown, dirs_exist_ok=True)

    print("Migration complete.")
    print(f"New DB: {new_db}")


if __name__ == "__main__":
    main()
