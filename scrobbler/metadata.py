"""Metadata extraction from audio files with filename fallback."""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

# ── Dataclass ───────────────────────────────────────────────────────────────


@dataclass
class TrackMeta:
    file_path: str
    artist: str = "Unknown Artist"
    track: str = "Unknown Track"
    album: str = ""
    album_artist: str = ""
    track_number: int = 0
    duration_sec: float = 0.0

    # extra fields for queue / serialisation
    _id: Optional[int] = field(default=None, repr=False)
    timestamp: Optional[int] = field(default=None, repr=False)


# ── Extractors ──────────────────────────────────────────────────────────────


def _extract_id3(filepath: str) -> dict:
    """Read ID3v1 / ID3v2 tags from an MP3."""
    audio = MP3(filepath)
    tags = {}
    if audio.tags:
        for frame_key, field in [
            ("TPE1", "artist"),
            ("TIT2", "track"),
            ("TALB", "album"),
            ("TPE2", "album_artist"),
            ("TRCK", "track_number"),
        ]:
            frame = audio.tags.get(frame_key)
            if frame:
                tags[field] = str(frame)
    tags["duration_sec"] = audio.info.length if audio.info else 0.0
    return tags


def _extract_flac(filepath: str) -> dict:
    audio = FLAC(filepath)
    tags = {}
    if audio.tags:
        for vorbis_key, field in [
            ("artist", "artist"),
            ("title", "track"),
            ("album", "album"),
            ("albumartist", "album_artist"),
            ("tracknumber", "track_number"),
        ]:
            val = audio.tags.get(vorbis_key)
            if val:
                tags[field] = val[0]
    tags["duration_sec"] = audio.info.length if audio.info else 0.0
    return tags


def _extract_mp4(filepath: str) -> dict:
    audio = MP4(filepath)
    tags = {}
    if audio.tags:
        for atom_key, field in [
            ("\xa9ART", "artist"),
            ("\xa9nam", "track"),
            ("\xa9alb", "album"),
            ("aART", "album_artist"),
            ("trkn", "track_number"),
        ]:
            val = audio.tags.get(atom_key)
            if val:
                tags[field] = str(val[0])
    tags["duration_sec"] = audio.info.length if audio.info else 0.0
    return tags


def _extract_vorbis(filepath: str) -> dict:
    audio = OggVorbis(filepath)
    tags = {}
    if audio.tags:
        for vorbis_key, field in [
            ("artist", "artist"),
            ("title", "track"),
            ("album", "album"),
            ("albumartist", "album_artist"),
            ("tracknumber", "track_number"),
        ]:
            val = audio.tags.get(vorbis_key)
            if val:
                tags[field] = val[0]
    tags["duration_sec"] = audio.info.length if audio.info else 0.0
    return tags


def _extract_generic(filepath: str) -> dict:
    """Fallback for WAV, AIFF, etc. — little or no tag support in mutagen."""
    try:
        audio = mutagen.File(filepath)
        length = audio.info.length if audio and audio.info else 0.0
    except Exception:
        length = 0.0
    return {"duration_sec": length}


# ── Dispatcher ───────────────────────────────────────────────────────────────

_HANDLERS = {
    ".mp3": _extract_id3,
    ".flac": _extract_flac,
    ".m4a": _extract_mp4,
    ".aac": _extract_mp4,
    ".ogg": _extract_vorbis,
    ".opus": _extract_vorbis,
    ".wav": _extract_generic,
    ".aiff": _extract_generic,
}


# ── Filename fallback ───────────────────────────────────────────────────────

_FILENAME_PATTERNS = [
    # "Artist - Track" (with optional leading track number)
    re.compile(
        r"^(?:\d{1,3}[\s.\-_]*)?(?P<artist>.+?)\s*[\-–—]\s*(?P<track>.+?)\s*$"
    ),
    # "Artist/Track" (common in some collections)
    re.compile(r"^(?P<artist>[^/]+)\s*/\s*(?P<track>[^/]+)\s*$"),
]


def _parse_filename(filepath: str) -> dict:
    """Return {'artist': ..., 'track': ...} from filename if possible."""
    name = os.path.splitext(os.path.basename(filepath))[0].strip()
    for pattern in _FILENAME_PATTERNS:
        m = pattern.match(name)
        if m:
            return {"artist": m.group("artist").strip(),
                    "track": m.group("track").strip()}
    return {"artist": "Unknown Artist", "track": name}


# ── Public API ───────────────────────────────────────────────────────────────


def extract(filepath: str) -> TrackMeta:
    """Extract metadata from an audio file with filename fallback.

    Returns a ``TrackMeta`` that is guaranteed to have non-empty ``artist``
    and ``track`` fields.
    """
    ext = os.path.splitext(filepath)[1].lower()
    handler = _HANDLERS.get(ext, _extract_generic)

    try:
        tag_dict = handler(filepath)
    except Exception:
        tag_dict = {"duration_sec": 0.0}

    # build TrackMeta from tags
    artist = (tag_dict.get("artist") or "").strip()
    track = (tag_dict.get("track") or "").strip()
    album = (tag_dict.get("album") or "").strip()
    album_artist = (tag_dict.get("album_artist") or "").strip()

    # track_number as int
    try:
        tn = tag_dict.get("track_number", 0)
        track_number = int(str(tn).split("/")[0])
    except (ValueError, TypeError):
        track_number = 0

    duration = tag_dict.get("duration_sec", 0.0)

    # fallback: if artist or track is empty, use filename
    if not artist or not track:
        fb = _parse_filename(filepath)
        if not artist:
            artist = fb["artist"]
        if not track:
            track = fb["track"]

    return TrackMeta(
        file_path=filepath,
        artist=artist,
        track=track,
        album=album,
        album_artist=album_artist,
        track_number=track_number,
        duration_sec=duration,
    )
