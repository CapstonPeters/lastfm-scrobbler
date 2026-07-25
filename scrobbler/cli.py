"""CLI: scan, preview, scrobble, retry, auth."""

import argparse
import sys
import time
from typing import List

from .scanner import scan_paths
from .metadata import extract
from .queue_manager import QueueManager
from .lastfm_client import LastFMClient, MAX_BATCH, DELAY_BETWEEN_BATCHES
from .dead_letter import export_failed, import_failed


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scrobbler",
        description="Last.fm batch scrobbler from local audio files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    scan_p = sub.add_parser("scan", help="Scan audio files and stage in queue")
    scan_p.add_argument("paths", nargs="+", help="File or directory paths")
    scan_p.add_argument("--db", default="scrobble_staging.db",
                        help="SQLite queue path")

    # preview
    prev_p = sub.add_parser("preview", help="Preview queued tracks")
    prev_p.add_argument("--db", default="scrobble_staging.db")
    prev_p.add_argument("--limit", type=int, default=50)

    # scrobble
    scrob_p = sub.add_parser("scrobble", help="Send queued tracks to Last.fm")
    scrob_p.add_argument("--db", default="scrobble_staging.db")
    scrob_p.add_argument("--api-key", help="Last.fm API key (or env LASTFM_API_KEY)")
    scrob_p.add_argument("--api-secret", help="Last.fm API secret (or env LASTFM_API_SECRET)")
    scrob_p.add_argument("--session-key", help="Last.fm session key (or env LASTFM_SK)")
    scrob_p.add_argument("--dry-run", action="store_true",
                         help="Simulate scrobbling without sending")
    scrob_p.add_argument("--realtime", action="store_true", default=True,
                         help="Use current time for timestamps (default)")
    scrob_p.add_argument("--backdate", action="store_false", dest="realtime",
                         help="Use stored queue timestamps")
    scrob_p.add_argument("--daily-limit", type=int, default=3000)

    # retry
    retry_p = sub.add_parser("retry", help="Retry failed scrobbles")
    retry_p.add_argument("--db", default="scrobble_staging.db")
    retry_p.add_argument("--file", default="failed_scrobbles.json",
                         help="Import from dead-letter file")

    # auth
    auth_p = sub.add_parser("auth", help="Obtain Last.fm session key")
    auth_p.add_argument("--api-key", help="Last.fm API key")
    auth_p.add_argument("--api-secret", help="Last.fm API secret")
    auth_p.add_argument("--token", help="Auth token from last.fm/api/auth")

    # gui
    sub.add_parser("gui", help="Launch cross-platform graphical interface")

    return parser.parse_args(argv)


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_scan(args: argparse.Namespace) -> None:
    print(f"Scanning {len(args.paths)} path(s)...")
    files = scan_paths(args.paths)
    if not files:
        print("No audio files found.")
        return

    qm = QueueManager(args.db)
    count = 0
    for fp in files:
        meta = extract(fp)
        qm.stage(meta)
        count += 1
        print(f"  {meta.artist} — {meta.track}")

    print(f"\nStaged {count} tracks.")


def cmd_preview(args: argparse.Namespace) -> None:
    qm = QueueManager(args.db)
    entries = qm.all_entries()
    counts = qm.count_by_status()
    print(f"Queue: {counts}")
    print("-" * 60)
    for e in entries[: args.limit]:
        print(f"[{e['status'][:4]:4}] {e['artist'][:25]:25} — {e['track'][:30]:30}")


def cmd_scrobble(args: argparse.Namespace) -> None:
    import os

    api_key = args.api_key or os.environ.get("LASTFM_API_KEY")
    api_secret = args.api_secret or os.environ.get("LASTFM_API_SECRET")
    sk = args.session_key or os.environ.get("LASTFM_SK")

    if not api_key or not api_secret:
        print("Error: API key and secret required. Set env vars or pass --api-key/--api-secret.")
        sys.exit(1)

    qm = QueueManager(args.db)
    client = LastFMClient(api_key, api_secret, session_key=sk, dry_run=args.dry_run)

    if not sk:
        print("No session key. Run 'scrobbler auth' first.")
        sys.exit(1)

    pending = qm.pending()
    if not pending:
        print("No PENDING tracks.")
        return

    print(f"Processing {len(pending)} tracks (batch size {MAX_BATCH})...")

    total_success = 0
    total_failed = 0

    for i in range(0, len(pending), MAX_BATCH):
        batch = pending[i : i + MAX_BATCH]

        # rate limit check
        if not args.dry_run and qm.should_throttle(args.daily_limit):
            wait_sec = 3600
            print(f"\nDaily limit ({args.daily_limit}) reached. "
                  f"Waiting {wait_sec // 60} minutes...")
            time.sleep(wait_sec)
            # after wait, re-check pending (may have new entries added)
            pending = qm.pending()
            batch = pending[i : i + MAX_BATCH]

        results = client.scrobble_batch(batch, realtime=args.realtime)

        ok_ids = []
        fail_ids = []
        fail_reasons = []

        for r in results:
            rid = r.get("id")
            if r["status"] == "ok":
                ok_ids.append(rid)
                total_success += 1
            else:
                fail_ids.append(rid)
                fail_reasons.append(r.get("error", "unknown"))
                total_failed += 1

        if ok_ids:
            qm.mark_success(ok_ids)
        if fail_ids:
            for rid, reason in zip(fail_ids, fail_reasons):
                qm.mark_failed([rid], str(reason))

        qm.log_scrobble(len(batch))
        print(f"  Batch {i // MAX_BATCH + 1}: "
              f"{len(ok_ids)} ok, {len(fail_ids)} failed")

        if not args.dry_run and i + MAX_BATCH < len(pending):
            time.sleep(DELAY_BETWEEN_BATCHES)

    print(f"\nDone. {total_success} scrobbled, {total_failed} failed.")
    if total_failed:
        exported = export_failed(qm)
        print(f"Failed tracks exported to failed_scrobbles.json ({exported} entries).")


def cmd_retry(args: argparse.Namespace) -> None:
    qm = QueueManager(args.db)
    count = import_failed(qm, args.file)
    print(f"Re-staged {count} failed tracks as PENDING. Run 'scrobble' to retry.")


def cmd_auth(args: argparse.Namespace) -> None:
    import os

    api_key = args.api_key or os.environ.get("LASTFM_API_KEY")
    api_secret = args.api_secret or os.environ.get("LASTFM_API_SECRET")

    if args.token:
        # exchange token for session key
        client = LastFMClient(api_key, api_secret)
        session = client.get_session(args.token)
        print(f"Session key: {session['session']['key']}")
        print(f"Username:   {session['session']['name']}")
        print("\nExport: export LASTFM_SK='<session_key>'")
    else:
        url = LastFMClient.get_auth_url(api_key)
        print(f"Open this URL, authorize, then run:\n")
        print(f"  scrobbler auth --api-key {api_key} --api-secret {api_secret} "
              f"--token <token_from_callback>\n")
        print(f"Auth URL: {url}")


def cmd_gui(args: argparse.Namespace) -> None:
    from .gui import main as gui_main
    gui_main()


# ── Entry ────────────────────────────────────────────────────────────────────


def main(argv: List[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])
    handlers = {
        "scan": cmd_scan,
        "preview": cmd_preview,
        "scrobble": cmd_scrobble,
        "retry": cmd_retry,
        "auth": cmd_auth,
        "gui": cmd_gui,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
