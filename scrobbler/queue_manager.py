"""SQLite-backed staging queue for scrobble operations."""

import sqlite3
import time
import json
from dataclasses import asdict
from typing import List, Optional, Iterator

from .metadata import TrackMeta

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrobbles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT    NOT NULL,
    artist      TEXT    NOT NULL,
    track       TEXT    NOT NULL,
    album       TEXT    DEFAULT '',
    album_artist TEXT   DEFAULT '',
    track_number INTEGER DEFAULT 0,
    duration_sec REAL   DEFAULT 0.0,
    timestamp   INTEGER NOT NULL,
    status      TEXT    DEFAULT 'PENDING',
    error_msg   TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rate_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scrobbled_at INTEGER NOT NULL,
    count       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrobbles_status ON scrobbles(status);
CREATE INDEX IF NOT EXISTS idx_rate_log_at ON rate_log(scrobbled_at);
"""


class QueueManager:
    def __init__(self, db_path: str = "scrobble_staging.db"):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ── insert / stage ──────────────────────────────────────────────────────

    def stage(self, track: TrackMeta, timestamp: Optional[int] = None) -> int:
        """Insert a single track with PENDING status."""
        ts = timestamp or int(time.time())
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO scrobbles
                   (file_path, artist, track, album, album_artist,
                    track_number, duration_sec, timestamp, status)
                   VALUES (?,?,?,?,?,?,?,?, 'PENDING')""",
                (track.file_path, track.artist, track.track, track.album,
                 track.album_artist, track.track_number, track.duration_sec, ts),
            )
            return cur.lastrowid

    def stage_batch(self, tracks: List[TrackMeta],
                    timestamps: Optional[List[int]] = None) -> int:
        """Insert multiple tracks; returns count staged."""
        with self._conn() as conn:
            rows = []
            now = int(time.time())
            for i, t in enumerate(tracks):
                ts = timestamps[i] if timestamps else now
                rows.append((t.file_path, t.artist, t.track, t.album,
                             t.album_artist, t.track_number, t.duration_sec, ts))
            conn.executemany(
                """INSERT INTO scrobbles
                   (file_path, artist, track, album, album_artist,
                    track_number, duration_sec, timestamp, status)
                   VALUES (?,?,?,?,?,?,?,?, 'PENDING')""",
                rows,
            )
            return len(rows)

    # ── read ─────────────────────────────────────────────────────────────────

    def pending(self, limit: Optional[int] = None) -> List[dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM scrobbles WHERE status='PENDING' ORDER BY id"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return [dict(r) for r in conn.execute(sql)]

    def all_entries(self) -> List[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM scrobbles ORDER BY id")]

    def count_by_status(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) cnt FROM scrobbles GROUP BY status"
            ).fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    # ── update ──────────────────────────────────────────────────────────────

    def mark_success(self, ids: List[int]) -> None:
        with self._conn() as conn:
            conn.executemany(
                "UPDATE scrobbles SET status='SUCCESS' WHERE id=?",
                [(i,) for i in ids],
            )

    def mark_failed(self, ids: List[int], error_msg: str = "") -> None:
        with self._conn() as conn:
            conn.executemany(
                "UPDATE scrobbles SET status='FAILED', error_msg=? WHERE id=?",
                [(error_msg, i) for i in ids],
            )

    # ── rate limiting ───────────────────────────────────────────────────────

    def log_scrobble(self, count: int = 50) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rate_log (scrobbled_at, count) VALUES (?,?)",
                (int(time.time()), count),
            )

    def scrobbles_in_window(self, window_sec: int = 86400) -> int:
        """Return total scrobbles in the last `window_sec` seconds."""
        cutoff = int(time.time()) - window_sec
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(count),0) total FROM rate_log "
                "WHERE scrobbled_at >= ?",
                (cutoff,),
            ).fetchone()
            return row["total"]

    def should_throttle(self, daily_limit: int = 3000) -> bool:
        return self.scrobbles_in_window(86400) >= daily_limit

    # ── dead-letter ─────────────────────────────────────────────────────────

    def failed_entries(self) -> List[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM scrobbles WHERE status='FAILED' ORDER BY id")]

    def reset_failed_to_pending(self) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE scrobbles SET status='PENDING', error_msg='' "
                "WHERE status='FAILED'")
            return cur.rowcount

    def clear_all(self) -> int:
        """Delete all tracks from the queue."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM scrobbles")
            return cur.rowcount
