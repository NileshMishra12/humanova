"""Refresh HumaNova's data from Nexus (read-only).   python scripts/sync_nexus.py
Needs NEXUS_EMAIL and NEXUS_PASSWORD in .env (a normal Nexus user, not an admin). Never prints the password."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import nexus_sync  # noqa: E402
from nexus_client import NexusError  # noqa: E402

if not config.NEXUS_LIVE:
    sys.exit("Set NEXUS_EMAIL and NEXUS_PASSWORD in .env first (see .env.example).")
print(f"Syncing from {config.NEXUS_URL} ...")
try:
    result = nexus_sync.sync()
except NexusError as e:
    sys.exit(f"Sync failed: {e}")
if not result["ok"]:
    sys.exit("\n".join(result["warnings"]))
print(f"Done at {result['time']}. Read: {result['tier']}.")
for name, n in result["counts"].items():
    print(f"  {name}: {n}")
if result["warnings"]:
    print("\nNot everything could be read:")
    for w in result["warnings"]:
        print(f"  - {w}")
print(f"\nThe old files were copied to data/{nexus_sync.BACKUP} the first time.")
