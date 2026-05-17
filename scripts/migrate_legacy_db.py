#!/usr/bin/env python3
"""Simple one-time migration from legacy DB to majestic-brain.

Run once after upgrading the plugin:
    python scripts/migrate_legacy_db.py

It copies the legacy database and markdown mirror to the new location
<hermes_home>/majestic-brain/ so the old directory can be deleted.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hermes_constants import get_hermes_home


def main() -> None:
    hermes_home = Path(get_hermes_home())

    # Try common legacy directory names
    old_dir = None
    old_db = None
    for candidate in ("gbrain", "gb", "brain"):
        d = hermes_home / candidate
        for db_name in ("gbrain.db", "brain.db", "majestic_brain.db"):
            db = d / db_name
            if db.exists():
                old_dir = d
                old_db = db
                break
        if old_db:
            break

    if not old_db:
        print("No legacy database found — nothing to migrate.")
        return

    new_dir = hermes_home / "majestic-brain"
    new_dir.mkdir(parents=True, exist_ok=True)

    new_db = new_dir / "majestic_brain.db"
    if new_db.exists():
        print(f"New DB already exists at {new_db} — aborting to avoid overwrite.")
        return

    print(f"Copying {old_db} -> {new_db}")
    shutil.copy2(old_db, new_db)

    old_md = old_dir / "markdown"
    if old_md.exists():
        new_md = new_dir / "markdown"
        print(f"Copying markdown mirror {old_md} -> {new_md}")
        shutil.copytree(old_md, new_md, dirs_exist_ok=True)

    print("Migration complete. You can now delete the old directory:")
    print(f"  Old: {old_dir}")
    print(f"  New: {new_dir}")


if __name__ == "__main__":
    main()
