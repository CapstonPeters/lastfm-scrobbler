"""Per-user config — only stores the session key.

Platform-appropriate location:
  Linux   → ~/.config/lastfm-scrobbler/config.json
  Windows → %APPDATA%/lastfm-scrobbler/config.json
  macOS   → ~/Library/Application Support/lastfm-scrobbler/config.json

Developer credentials (API key + secret) are embedded in the app
and never shown to users.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "lastfm-scrobbler"


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_session_key() -> Optional[str]:
    """Return the stored session key, or None."""
    return load().get("session_key")


def set_session_key(sk: str) -> None:
    """Store a session key."""
    save({"session_key": sk})


def clear_session_key() -> None:
    """Remove the session key (force re-auth on next launch)."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()


def has_session_key() -> bool:
    return bool(get_session_key())
