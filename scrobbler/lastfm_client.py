"""Last.fm API client with MD5 signing, batch scrobbling, and rate limiting."""

import hashlib
import time
from typing import List, Optional, Tuple

import requests

API_BASE = "https://ws.audioscrobbler.com/2.0/"
MAX_BATCH = 50
DELAY_BETWEEN_BATCHES = 2.0  # seconds


class LastFMClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_key: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self.dry_run = dry_run

    # ── Auth ─────────────────────────────────────────────────────────────────

    @staticmethod
    def get_auth_url(api_key: str) -> str:
        return (
            "https://www.last.fm/api/auth/"
            f"?api_key={api_key}"
        )

    def get_session(self, token: str) -> dict:
        """Exchange a token for a session key."""
        params = {
            "method": "auth.getSession",
            "api_key": self.api_key,
            "token": token,
        }
        params["api_sig"] = self._sign(params)
        return self._post(params)

    # ── Signing ──────────────────────────────────────────────────────────────

    def _sign(self, params: dict) -> str:
        """Build Last.fm api_sig: sorted by key, append secret, MD5."""
        sorted_keys = sorted(params.keys())
        raw = "".join(f"{k}{params[k]}" for k in sorted_keys if k != "format")
        raw += self.api_secret
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    # ── Scrobble ─────────────────────────────────────────────────────────────

    def scrobble_batch(
        self,
        tracks: List[dict],
        realtime: bool = True,
    ) -> List[dict]:
        """Scrobble up to 50 tracks in one request.

        Args:
            tracks: List of dicts with artist, track, album (opt),
                    timestamp (opt in realtime mode).
            realtime: If True, ignore stored timestamps and use now().

        Returns:
            List of result dicts with keys: id, status ('ok'|'failed'),
            error (str or None).
        """
        if not self.session_key:
            raise RuntimeError("No session key — call get_session() first")

        params: dict = {
            "method": "track.scrobble",
            "api_key": self.api_key,
            "sk": self.session_key,
        }

        # Last.fm requires ≥30s between scrobble timestamps
        base_ts = int(time.time())

        for i, t in enumerate(tracks[:MAX_BATCH]):
            params[f"artist[{i}]"] = t["artist"]
            params[f"track[{i}]"] = t["track"]
            if t.get("album"):
                params[f"album[{i}]"] = t["album"]
            if realtime:
                ts = base_ts - (len(tracks) - i) * 60  # spread backwards by 60s
            else:
                ts = t.get("timestamp", base_ts)
            params[f"timestamp[{i}]"] = str(ts)
            if t.get("albumArtist"):
                params[f"albumArtist[{i}]"] = t["albumArtist"]
            if t.get("trackNumber"):
                params[f"trackNumber[{i}]"] = str(t["trackNumber"])
            if t.get("duration_sec"):
                params[f"duration[{i}]"] = str(int(t["duration_sec"]))

        params["api_sig"] = self._sign(params)

        if self.dry_run:
            return [
                {"id": t.get("id"), "status": "ok", "error": None}
                for t in tracks[:MAX_BATCH]
            ]

        resp = self._post(params)

        results = []
        accepted = resp.get("scrobbles", {}).get("scrobble", [])
        if isinstance(accepted, dict):
            accepted = [accepted]

        for entry in accepted:
            # Check if track was accepted: ignoredMessage.code == "0" means success
            ignored = entry.get("ignoredMessage", {})
            is_accepted = ignored.get("code") == "0"
            results.append({
                "id": None,
                "status": "ok" if is_accepted else "failed",
                "error": ignored.get("#text") if not is_accepted else None,
            })

        # map back to track ids
        for i, r in enumerate(results):
            if i < len(tracks):
                r["id"] = tracks[i].get("id")

        return results

    # ── Internal ─────────────────────────────────────────────────────────────

    def _post(self, params: dict) -> dict:
        params["format"] = "json"
        resp = requests.post(API_BASE, data=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Last.fm error {body['error']}: {body.get('message', '')}")
        return body
