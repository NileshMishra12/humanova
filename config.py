"""Settings. Loads .env (KEY=value lines); real environment variables win."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

DATA_DIR = Path(os.environ.get("HR_DATA_DIR") or ROOT / "data")
TODAY = os.environ.get("HR_TODAY", "2026-10-23")
MODEL = os.environ.get("HR_MODEL", "claude-sonnet-5-5")
FAST_MODEL = os.environ.get("HR_FAST_MODEL", "claude-haiku-5-5")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MOCK = os.environ.get("HR_MOCK") == "1" or not API_KEY
APP_PORT = int(os.environ.get("HR_PORT", "8788"))
