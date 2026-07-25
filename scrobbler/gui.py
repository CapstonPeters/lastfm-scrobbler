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
        """Simple session key entry dialog."""
        result = {"client": None}

        dialog = tk.Toplevel(self.root)
        dialog.title("Last.fm — Enter Session Key")
        dialog.geometry("480x200")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Paste your Last.fm session key to get started.\n\n"
                 "How to get one:\n"
                 "  • If you're migrating from another PC, copy it from\n"
                 "    %APPDATA%\\lastfm-scrobbler\\config.json\n"
                 "  • Or click \"Open Last.fm\" below to authorize via browser",
            wraplength=440,
            justify=tk.LEFT,
        ).pack(padx=20, pady=(15, 10))

        entry_frame = ttk.Frame(dialog)
        entry_frame.pack(fill=tk.X, padx=20)
        ttk.Label(entry_frame, text="Session Key:").pack(side=tk.LEFT)
        sk_var = tk.StringVar()
        ttk.Entry(entry_frame, textvariable=sk_var, show="*", width=40).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True,
        )

        def _save() -> None:
            sk = sk_var.get().strip()
            if not sk:
                return
            set_session_key(sk)
            result["client"] = LastFMClient(API_KEY, API_SECRET, session_key=sk)
            dialog.destroy()

        def _browser_auth() -> None:
            """Fallback: browser-based auth."""
            import webbrowser
            try:
                client = LastFMClient(API_KEY, API_SECRET)
                token = client.get_token()
                webbrowser.open(
                    f"https://www.last.fm/api/auth/?api_key={API_KEY}&token={token}"
                )
            except Exception as e:
                messagebox.showerror(
                    "Auth Error",
                    f"Could not contact Last.fm: {e}\n\n"
                    "Use the session key method instead.",
                    parent=dialog,
                )

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="Save", command=_save).pack(
            side=tk.LEFT, padx=5,
        )
        ttk.Button(btn_frame, text="Open Last.fm (browser auth)", command=_browser_auth).pack(
            side=tk.LEFT, padx=5,
        )
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(
            side=tk.LEFT, padx=5,
        )

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
                files = scan_paths([path])
                count = 0
                for fp in files:
                    meta = extract(fp)
                    self.qm.stage(meta)
                    count += 1
                self._invoke(lambda: self.status_var.set(
                    f"Scanned {count} tracks from {len(files)} files"
                ))
            except Exception as e:
                self._invoke(lambda: self.status_var.set(f"Scan error: {e}"))
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
            f"{'[DRY RUN] ' if dry else ''}Scrobbling {total} tracks…"
        )

        def worker() -> None:
            ok_total = 0
            fail_total = 0
            try:
                for i in range(0, len(pending), MAX_BATCH):
                    batch = pending[i : i + MAX_BATCH]
                    results = self.client.scrobble_batch(batch)

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

                    if ok_ids:
                        self.qm.mark_success(ok_ids)
                    if fail_ids:
                        for rid, reason in zip(fail_ids, fail_reasons):
                            self.qm.mark_failed([rid], str(reason))

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

                self._invoke(lambda f=final: self.status_var.set(f))

            except Exception as e:
                self._invoke(lambda: self.status_var.set(f"Error: {e}"))
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

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _refresh_tables(self) -> None:
        for tree, status in [
            (self.queue_tree, "PENDING"),
            (self.success_tree, "SUCCESS"),
            (self.failed_tree, "FAILED"),
        ]:
            for item in tree.get_children():
                tree.delete(item)

        with self.qm._conn() as conn:
            for row in conn.execute(
                "SELECT artist, track, album, status FROM scrobbles ORDER BY id"
            ):
                tree = {
                    "PENDING": self.queue_tree,
                    "SUCCESS": self.success_tree,
                    "FAILED": self.failed_tree,
                }.get(row["status"])
                if tree:
                    tree.insert(
                        "", tk.END,
                        values=(row["artist"], row["track"], row["album"]),
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
