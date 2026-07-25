"""Dead-letter queue: serialize failed tracks to JSON for retry."""

import json
from typing import List

from .queue_manager import QueueManager


def export_failed(qm: QueueManager, output_path: str = "failed_scrobbles.json") -> int:
    """Write all FAILED entries to a JSON file. Returns count exported."""
    failed = qm.failed_entries()
    with open(output_path, "w") as f:
        json.dump(failed, f, indent=2, default=str)
    return len(failed)


def import_failed(
    qm: QueueManager, input_path: str = "failed_scrobbles.json"
) -> int:
    """Read a failed_scrobbles.json file and reset to PENDING in-place.

    Returns count imported.
    """
    with open(input_path) as f:
        failed = json.load(f)

    count = len(failed)
    qm.reset_failed_to_pending()
    return count
