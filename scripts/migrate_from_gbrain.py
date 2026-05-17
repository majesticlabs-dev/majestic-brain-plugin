#!/usr/bin/env python3
"""Simple one-time migration from legacy gbrain DB to majestic-brain.

Run once after upgrading the plugin:
    python scripts/migrate_from_gbrain.py

It copies gbrain.db and the markdown/ mirror to the new location
<hermes_home>/majestic-brain/ so the old gbrain/ directory can be deleted.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from hermes_constants import get_hermes_home


def main() -> None:
    hermes_home = Path(get_hermes_home())
    old_dir = hermes_home / "gbrain"
    new_dir = hermes_home / "majestic-brain"

    old_db = old_dir / "gbrain.db"
    if not old_db.exists():
        print("No legacy gbrain.db found — nothing to migrate.")
        return

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

    print("Migration complete. You can now delete the old gbrain/ directory.")
    print(f"Old: {old_dir}")
    print(f"New: {new_dir}")


if __name__ == "__main__":
    main()
