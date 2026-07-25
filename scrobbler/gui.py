"""Tkinter GUI for the Last.fm Scrobbler — cross-platform (Linux/Windows/macOS)."""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

from .scanner import scan_paths
from .metadata import extract
from .queue_manager import QueueManager
from .lastfm_client import LastFMClient, MAX_BATCH, DELAY_BETWEEN_BATCHES
from .dead_letter import export_failed
from .config import get_session_key, set_session_key, has_session_key
from ._credentials import API_KEY, API_SECRET


class ScrobblerGUI:
    def __init__(self, db_path: str = "scrobble_staging.db"):
        self.db_path = db_path
        self.qm = QueueManager(db_path)
        self.client: Optional[LastFMClient] = None
        self.scan_running = False
        self.scrobble_running = False
        self._success_tracks: list = []

        self._build_ui()
        self._refresh_tables()

    # ── UI Construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("Last.fm Scrobbler")
        self.root.geometry("900x600")
        self.root.minsize(700, 400)

        # ── Top bar: folder picker + scan ──────────────────────────────────
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Music Folder:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.path_var, width=50).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True
        )
        ttk.Button(top, text="Browse…", command=self._browse).pack(side=tk.LEFT, padx=2)
        self.scan_btn = ttk.Button(top, text="Scan", command=self._scan)
        self.scan_btn.pack(side=tk.LEFT, padx=2)

        # ── Notebook (tabs) ────────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: Queue
        queue_frame = ttk.Frame(self.notebook)
        self.notebook.add(queue_frame, text="Queue")
        self.queue_tree = self._make_tree(queue_frame)

        # Tab 2: Success
        success_frame = ttk.Frame(self.notebook)
        self.notebook.add(success_frame, text="Success")
        self.success_tree = self._make_tree(success_frame)

        # Tab 3: Failed
        failed_frame = ttk.Frame(self.notebook)
        self.notebook.add(failed_frame, text="Failed")
        self.failed_tree = self._make_tree(failed_frame)

        # ── Bottom bar: actions + status ───────────────────────────────────
        bottom = ttk.Frame(self.root, padding=5)
        bottom.pack(fill=tk.X)

        self.preview_btn = ttk.Button(
            bottom, text="Preview", command=self._preview
        )
        self.preview_btn.pack(side=tk.LEFT, padx=2)

        self.scrobble_btn = ttk.Button(
            bottom, text="Scrobble ▶", command=self._scrobble
        )
        self.scrobble_btn.pack(side=tk.LEFT, padx=2)

        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Dry Run", variable=self.dry_var
        ).pack(side=tk.LEFT, padx=10)

        self.retry_btn = ttk.Button(
            bottom, text="Retry Failed", command=self._retry
        )
        self.retry_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(
            bottom, text="Clear Queue", command=lambda: self._clear_status("PENDING")
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            bottom, text="Clear Failed", command=lambda: self._clear_status("FAILED")
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            bottom, text="Clear Success", command=lambda: self._clear_status("SUCCESS")
        ).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bottom, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Button(
            bottom, text="⚙ Settings", command=self._show_setup
        ).pack(side=tk.LEFT, padx=2)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status_var).pack(
            side=tk.RIGHT, padx=5
        )

        # Progress bar
        self.progress = ttk.Progressbar(
            bottom, mode="indeterminate", length=150
        )
        self.progress.pack(side=tk.RIGHT, padx=5)

        # ── Auth: load from config, or show setup on first run ──────────────────
        self.client = self._init_auth()

    def _init_auth(self) -> Optional[LastFMClient]:
        """Load session key from config; show setup if missing."""
        sk = get_session_key()
        if sk:
            self.status_var.set("Authenticated ✓")
            return LastFMClient(API_KEY, API_SECRET, session_key=sk)

        self.status_var.set("Not authenticated — click Settings to log in")
        return self._show_setup()

    def _show_setup(self) -> Optional[LastFMClient]:
        """Login dialog — username/password (primary) or session key fallback."""
        result = {"client": None}

        dialog = tk.Toplevel(self.root)
        dialog.title("Last.fm — Sign In")
        dialog.geometry("420x320")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Sign in to Last.fm to start scrobbling.",
            font=("", 10, "bold"),
        ).pack(padx=20, pady=(15, 5))

        # Username
        frame_u = ttk.Frame(dialog)
        frame_u.pack(fill=tk.X, padx=20, pady=5)
        ttk.Label(frame_u, text="Username:", width=12).pack(side=tk.LEFT)
        username_var = tk.StringVar()
        ttk.Entry(frame_u, textvariable=username_var, width=35).pack(
            side=tk.LEFT, fill=tk.X, expand=True,
        )

        # Password
        frame_p = ttk.Frame(dialog)
        frame_p.pack(fill=tk.X, padx=20, pady=5)
        ttk.Label(frame_p, text="Password:", width=12).pack(side=tk.LEFT)
        password_var = tk.StringVar()
        ttk.Entry(frame_p, textvariable=password_var, show="*", width=35).pack(
            side=tk.LEFT, fill=tk.X, expand=True,
        )

        status_var = tk.StringVar()
        ttk.Label(dialog, textvariable=status_var, foreground="gray").pack(
            padx=20, pady=(5, 0),
        )

        def _login() -> None:
            username = username_var.get().strip()
            password = password_var.get().strip()
            if not username or not password:
                status_var.set("Enter username and password.")
                return
            status_var.set("Signing in…")
            dialog.update()
            try:
                client = LastFMClient(API_KEY, API_SECRET)
                resp = client.get_mobile_session(username, password)
                sk = resp["session"]["key"]
                set_session_key(sk)
                result["client"] = LastFMClient(API_KEY, API_SECRET, session_key=sk)
                dialog.destroy()
            except Exception as e:
                status_var.set(f"Error: {e}")

        ttk.Button(dialog, text="Sign In", command=_login).pack(pady=10)

        # Separator + session key fallback
        ttk.Separator(dialog, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=20, pady=5)
        ttk.Label(
            dialog,
            text="Or paste a session key from another device:",
            foreground="gray",
        ).pack(padx=20, anchor=tk.W)

        manual_frame = ttk.Frame(dialog)
        manual_frame.pack(fill=tk.X, padx=20, pady=(3, 10))
        sk_var = tk.StringVar()
        ttk.Entry(manual_frame, textvariable=sk_var, show="*", width=40).pack(
            side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True,
        )

        def _save_manual() -> None:
            sk = sk_var.get().strip()
            if not sk:
                return
            set_session_key(sk)
            result["client"] = LastFMClient(API_KEY, API_SECRET, session_key=sk)
            dialog.destroy()

        ttk.Button(manual_frame, text="Save", command=_save_manual).pack(side=tk.LEFT)

        self.root.wait_window(dialog)
        if result["client"]:
            self.status_var.set("Authenticated ✓")
        return result["client"]

    def _make_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        cols = ("artist", "track", "album")
        tree = ttk.Treeview(
            parent,
            columns=cols,
            show="headings",
            selectmode="extended",
        )
        tree.heading("artist", text="Artist")
        tree.heading("track", text="Track")
        tree.heading("album", text="Album")
        tree.column("artist", width=200)
        tree.column("track", width=250)
        tree.column("album", width=200)

        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    # ── Actions ─────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        path = filedialog.askdirectory(title="Select Music Folder")
        if path:
            self.path_var.set(path)

    def _scan(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("No Path", "Select a folder first.")
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path", f"Not a directory:\n{path}")
            return
        if self.scan_running:
            return

        self.scan_running = True
        self.scan_btn.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Scanning…")

        def worker() -> None:
            try:
                self.qm.clear_pending()
                self.qm.clear_failed()
                self.qm.clear_success()
                files = scan_paths([path])
                total_files = len(files)
                count = 0
                for fp in files:
                    meta = extract(fp)
                    self.qm.stage(meta)
                    count += 1
                    if count % 100 == 0 or count == total_files:
                        self._invoke(lambda c=count, t=total_files: self.status_var.set(
                            f"Scanning… {c}/{t} tracks"
                        ))
                self._invoke(lambda: self.status_var.set(
                    f"Scanned {count} tracks from {total_files} files"
                ))
            except Exception as e:
                err = str(e)
                self._invoke(lambda: self.status_var.set(f"Scan error: {err}"))
            finally:
                self._invoke(self._scan_done)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self) -> None:
        self.scan_running = False
        self.scan_btn.configure(state="normal")
        self.progress.stop()
        self._refresh_tables()

    def _preview(self) -> None:
        counts = self.qm.count_by_status()
        self.status_var.set(f"Queue: {counts}")
        self._refresh_tables()
        self.notebook.select(0)  # switch to Queue tab

    def _scrobble(self) -> None:
        if not self.client:
            messagebox.showerror(
                "No Auth",
                "Last.fm credentials not configured.\n"
                "Restart the app to open the setup dialog.",
            )
            return
        if self.scrobble_running:
            return

        pending = self.qm.pending()
        if not pending:
            messagebox.showinfo("Empty", "No pending tracks to scrobble.")
            return

        dry = self.dry_var.get()
        self.scrobble_running = True
        self.scrobble_btn.configure(state="disabled")
        self.progress.start(10)
        total = len(pending)
        self.status_var.set(
            f"{'[DRY RUN] ' if dry else ''}Scrobbling {total} tracks… "
            f"({self.qm.daily_remaining()} left today)"
        )

        def worker() -> None:
            ok_total = 0
            fail_total = 0
            try:
                import datetime
                log = []
                def log_msg(msg):
                    log.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

                log_msg(f"Starting scrobble: {len(pending)} tracks, dry_run={dry}")
                for i in range(0, len(pending), MAX_BATCH):
                    batch = pending[i : i + MAX_BATCH]
                    log_msg(f"Batch {i//MAX_BATCH+1}/{((len(pending)-1)//MAX_BATCH)+1}: {len(batch)} tracks")

                    results = self.client.scrobble_batch(batch)
                    log_msg(f"Results: {len(results)} entries")

                    ok_ids = []
                    fail_ids = []
                    fail_reasons = []
                    for r in results:
                        if r["status"] == "ok":
                            ok_ids.append(r["id"])
                            ok_total += 1
                        else:
                            fail_ids.append(r["id"])
                            fail_reasons.append(r.get("error") or "unknown")
                            fail_total += 1
                            log_msg(f"  FAILED id={r['id']}: {r.get('error')}")

                    log_msg(f"  {len(ok_ids)} ok, {len(fail_ids)} failed")

                    if ok_ids:
                        self.qm.remove_ids(ok_ids)
                        # Add session-level entries to success tab
                        for oid in ok_ids:
                            track = next((t for t in batch if t.get("id") == oid), None)
                            if track:
                                self._invoke(lambda t=track: self.success_tree.insert(
                                    "", tk.END,
                                    values=(t.get("artist",""), t.get("track",""), t.get("album","")),
                                ))
                    if fail_ids:
                        for rid, reason in zip(fail_ids, fail_reasons):
                            self.qm.mark_failed([rid], str(reason))
                        log_msg(f"  Marked {len(fail_ids)} as FAILED")

                    self.qm.log_scrobble(len(batch))

                    progress_text = (
                        f"{'[DRY RUN] ' if dry else ''}"
                        f"Batch {i // MAX_BATCH + 1}: "
                        f"{len(ok_ids)} ok, {len(fail_ids)} failed "
                        f"({min(i + MAX_BATCH, total)}/{total})"
                    )
                    self._invoke(lambda p=progress_text: self.status_var.set(p))

                    if not dry and i + MAX_BATCH < len(pending):
                        import time
                        time.sleep(DELAY_BETWEEN_BATCHES)

                final = (
                    f"{'[DRY RUN] ' if dry else ''}"
                    f"Done — {ok_total} scrobbled, {fail_total} failed"
                )
                if fail_total and not dry:
                    exported = export_failed(self.qm)
                    final += f" | {exported} saved to failed_scrobbles.json"

                # Write log to file for debugging
                log_path = self.qm.db_path.replace(".db", "_scrobble.log")
                with open(log_path, "w") as lf:
                    lf.write("\n".join(log))
                if ok_total == 0 and fail_total == 0:
                    final += f" | Log: {log_path}"

                self._invoke(lambda f=final: self.status_var.set(f))

            except Exception as e:
                # Write log to file on error
                err = str(e)
                if "403" in err:
                    err = "IP blocked by Last.fm — wait a few minutes or use a different network"
                log_msg(f"ERROR: {err}")
                try:
                    log_path = self.qm.db_path.replace(".db", "_scrobble.log")
                    with open(log_path, "w") as lf:
                        lf.write("\n".join(log))
                    self._invoke(lambda: self.status_var.set(f"Error: {err} | Log: {log_path}"))
                except:
                    self._invoke(lambda: self.status_var.set(f"Error: {err}"))
            finally:
                self._invoke(self._scrobble_done)

        threading.Thread(target=worker, daemon=True).start()

    def _scrobble_done(self) -> None:
        self.scrobble_running = False
        self.scrobble_btn.configure(state="normal")
        self.progress.stop()
        self._refresh_tables()

    def _retry(self) -> None:
        count = self.qm.reset_failed_to_pending()
        if count:
            self.status_var.set(f"Reset {count} failed → PENDING. Click Scrobble to retry.")
        else:
            self.status_var.set("No failed tracks to retry.")
        self._refresh_tables()

    def _clear_status(self, status: str) -> None:
        labels = {"PENDING": "queued tracks", "FAILED": "failed tracks", "SUCCESS": "successful tracks"}
        label = labels.get(status, f"{status} tracks")
        ok = messagebox.askyesno(
            f"Clear {label.title()}",
            f"Remove all {label}?",
        )
        if ok:
            method = {status: getattr(self.qm, f"clear_{status.lower()}") for status in labels}
            count = method[status]()
            self.status_var.set(f"Cleared {count} {label}.")
            self._refresh_tables()

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _refresh_tables(self) -> None:
        for tree in [self.queue_tree, self.success_tree, self.failed_tree]:
            for item in tree.get_children():
                tree.delete(item)

        with self.qm._conn() as conn:
            for row in conn.execute(
                "SELECT artist, track, album, status FROM scrobbles ORDER BY id"
            ):
                tree = {
                    "PENDING": self.queue_tree,
                    "FAILED": self.failed_tree,
                }.get(row["status"])
                if tree:
                    tree.insert(
                        "", tk.END,
                        values=(row["artist"], row["track"], row["album"]),
                    )

        # Show in-memory success list
        for artist, track, album in self._success_tracks:
            self.success_tree.insert(
                "", tk.END,
                values=(artist, track, album),
            )

    def _invoke(self, fn) -> None:
        """Thread-safe UI update."""
        self.root.after(0, fn)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    gui = ScrobblerGUI()
    gui.run()


if __name__ == "__main__":
    main()
