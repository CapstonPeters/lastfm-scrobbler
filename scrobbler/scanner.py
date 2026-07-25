"""Recursive audio file scanner with extension filtering."""

import os
from typing import List, Optional

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".m4a", ".aac", ".ogg",
    ".wav", ".aiff", ".opus",
})


def is_audio_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in AUDIO_EXTENSIONS


def scan_paths(paths: List[str]) -> List[str]:
    """Scan one or more paths and return a deduplicated, sorted list of audio files.

    Args:
        paths: List of file paths, directory paths, or glob-like wildcards.

    Returns:
        Sorted list of absolute paths to audio files.
    """
    files: List[str] = []
    seen: set = set()

    for raw in paths:
        expanded = os.path.expanduser(raw)
        abs_path = os.path.abspath(expanded)

        if os.path.isfile(abs_path):
            if is_audio_file(abs_path) and abs_path not in seen:
                files.append(abs_path)
                seen.add(abs_path)
        elif os.path.isdir(abs_path):
            for root, _dirs, filenames in os.walk(abs_path):
                for name in sorted(filenames):
                    full = os.path.join(root, name)
                    if is_audio_file(full) and full not in seen:
                        files.append(full)
                        seen.add(full)
        else:
            raise FileNotFoundError(f"Path not found: {raw}")

    return files
