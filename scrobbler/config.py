"""Config file management for Last.fm Scrobbler.

Stores credentials in platform-appropriate location:
  Linux   → ~/.config/lastfm-scrobbler/config.json
  Windows → %APPDATA%/lastfm-scrobbler/config.json
  macOS   → ~/Library/Application Support/lastfm-scrobbler/config.json

Never embed secrets in code — users provide their own Last.fm API credentials.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def _config_dir() -> Path:
    """Platform-appropriate config directory."""
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
    """Load config, returning {} if file doesn't exist."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save(data: dict) -> None:
    """Save config dict, creating directories as needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_credentials() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (api_key, api_secret, session_key) from config, or (None, None, None)."""
    cfg = load()
    return (
        cfg.get("api_key"),
        cfg.get("api_secret"),
        cfg.get("session_key"),
    )


def set_credentials(api_key: str, api_secret: str, session_key: str) -> None:
    """Store credentials in config."""
    save({
        "api_key": api_key,
        "api_secret": api_secret,
        "session_key": session_key,
        "updated_at": datetime.utcnow().isoformat(),
    })


def has_credentials() -> bool:
    """Check if all three credentials are configured."""
    api_key, api_secret, sk = get_credentials()
    return bool(api_key and api_secret and sk)
