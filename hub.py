"""
Project Hub - finds everything you have built and tells you what state it is in.

Design notes worth keeping:

  * Nothing is hardcoded. The previous hub was a hand-written menu, which is why four
    projects built in one day were already missing from it. This scans.
  * Long work (test runs) happens off the UI thread, so the window never freezes.
  * A project that cannot be launched or whose tests cannot run says so, rather than
    being silently omitted.

RUN
    python3 hub.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import ttk
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_core as H

BG = "#0d1014"
PANEL = "#171c23"
PANEL2 = "#11151a"
LINE = "#252c36"
FG = "#e9eef5"
DIM = "#8b97a6"
ACC = "#5aa9ff"
GOOD = "#22c98a"
BAD = "#ff5f56"
GOLD = "#ffcc55"

# where to look. Overridable with an argument so the hub is not tied to one machine.
DEFAULT_ROOTS = [Path.home() / "Desktop"]


class Hub(tk.Tk):
    def __init__(self, roots):
        super().__init__()
        self.roots = roots
        self.projects: list[H.Project] = []
        self.shown: list[H.Project] = []
        self.sort_col = "modified"
        self.sort_desc = True

        self.title("Project Hub")
        self.geometry("1080x640")
        self.minsize(820, 480)
        self.configure(bg=BG)

        self._styles()
        self._build()
        self.refresh()

    # ------------------------------------------------------------- styling
    def _styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL2,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        s.configure("Treeview", background=PANEL2, fieldbackground=PANEL2,
                    foreground=FG, rowheight=25, borderwidth=0)
        s.configure("Treeview.Heading", background=PANEL, foreground=DIM,
                    relief="flat", font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", "#1d3550")],
              foreground=[("selected", FG)])
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Dim.TLabel", background=BG, foreground=DIM)
        s.configure("Head.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI", 13, "bold"))
        s.configure("TButton", background=PANEL, foreground=FG, borderwidth=1,
                    focuscolor=BG, padding=(10, 5))
        s.map("TButton", background=[("active", "#1f2833"), ("disabled", PANEL)],
              foreground=[("disabled", DIM)])
        s.configure("TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG)

    # ---------------------------------------------------------------- layout
    def _build(self):
        top = ttk.Frame(self, padding=(14, 12, 14, 8))
        top.pack(fill="x")

        ttk.Label(top, text="Project Hub", style="Head.TLabel").pack(side="left")
        self.summary = ttk.Label(top, text="", style="Dim.TLabel")
        self.summary.pack(side="left", padx=(14, 0))

        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Button(top, text="Run all tests", command=self.run_all_tests).pack(side="right", padx=(0, 6))

        bar = ttk.Frame(self, padding=(14, 0, 14, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="Filter", style="Dim.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.apply_filter())
        e = ttk.Entry(bar, textvariable=self.filter_var)
        e.pack(side="left", fill="x", expand=True, padx=(8, 8))
        e.focus_set()
        ttk.Button(bar, text="Clear", command=lambda: self.filter_var.set("")).pack(side="left")

        mid = ttk.Frame(self, padding=(14, 0, 14, 0))
        mid.pack(fill="both", expand=True)

        cols = ("name", "entry", "tests", "git", "size", "modified")
        heads = {"name": "Project", "entry": "Launches", "tests": "Tests",
                 "git": "Repository", "size": "Size", "modified": "Modified"}
        widths = {"name": 240, "entry": 230, "tests": 70, "git": 170,
                  "size": 80, "modified": 130}
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=heads[c], command=lambda cc=c: self.sort_by(cc))
            self.tree.column(c, width=widths[c],
                             anchor="w" if c in ("name", "entry", "git") else "center",
                             stretch=(c in ("name", "entry", "git")))
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.launch_selected())
        self.tree.bind("<Return>", lambda e: self.launch_selected())
        self.tree.tag_configure("dirty", foreground=GOLD)
        self.tree.tag_configure("noentry", foreground=DIM)

        act = ttk.Frame(self, padding=(14, 8, 14, 4))
        act.pack(fill="x")
        for text, fn in (("Launch", self.launch_selected),
                         ("Open folder", self.open_folder),
                         ("Run tests", self.run_selected_tests),
                         ("GitHub", self.open_github),
                         ("Copy path", self.copy_path)):
            ttk.Button(act, text=text, command=fn).pack(side="left", padx=(0, 6))

        self.status = ttk.Label(self, text="", style="Dim.TLabel",
                                padding=(14, 4, 14, 10))
        self.status.pack(fill="x")

        # a detail pane for the selected project
        self.detail = tk.Text(self, height=6, bg=PANEL2, fg=DIM, bd=0,
                              font=("Consolas", 9), wrap="word", padx=12, pady=8)
        self.detail.pack(fill="x", padx=14, pady=(0, 10))
        self.detail.configure(state="disabled")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())

    # ------------------------------------------------------------- scanning
    def refresh(self):
        self.set_status("scanning...")
        self.update_idletasks()
        t0 = time.time()
        self.projects = H.scan(self.roots)
        self.apply_filter()
        s = H.summarise(self.projects)
        self.summary.configure(
            text=f"{s['total']} projects  ·  {s['launchable']} launchable  ·  "
                 f"{s['with_tests']} with tests  ·  {s['repos']} repos  ·  "
                 f"{H.human_size(s['total_bytes'])}")
        self.set_status(f"scanned {len(self.roots)} folder(s) in {time.time()-t0:.1f}s")

    def apply_filter(self):
        q = self.filter_var.get().strip().lower()
        rows = [p for p in self.projects
                if not q or q in p.name.lower()
                or any(q in (t.name.lower()) for t in p.tests)
                or (p.entry and q in p.entry.name.lower())]
        self.shown = rows
        self.sort_rows()
        self.populate()

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            # first click on a column should give the useful direction: names read
            # best A-Z, but "size" and "modified" are only interesting largest or
            # newest first
            self.sort_col = col
            self.sort_desc = col in ("size", "modified", "tests")
        self.sort_rows()
        self.populate()

    def sort_rows(self):
        key = {
            "name": lambda p: p.name.lower(),
            "entry": lambda p: (p.entry_kind, p.entry.name.lower() if p.entry else ""),
            "tests": lambda p: len(p.tests),
            "git": lambda p: (p.git.is_repo, p.git.dirty),
            "size": lambda p: p.size_bytes,
            "modified": lambda p: p.mtime,
        }.get(self.sort_col, lambda p: p.mtime)
        self.shown.sort(key=key, reverse=self.sort_desc)

    def populate(self):
        self.tree.delete(*self.tree.get_children())
        for p in self.shown:
            entry = f"{p.entry_kind}: {p.entry.name}" if p.entry else "—"
            git = ""
            if p.git.is_repo:
                git = p.git.branch or "?"
                if p.git.dirty:
                    git += f"  +{p.git.dirty}"
                if p.git.github_slug:
                    git += "  ↗"
            t = len(p.tests)
            tags = []
            if not p.has_entry:
                tags.append("noentry")
            elif p.git.is_repo and p.git.dirty:
                tags.append("dirty")
            self.tree.insert("", "end", iid=str(p.path), tags=tuple(tags),
                             values=(p.name, entry, t if t else "—", git or "—",
                                     H.human_size(p.size_bytes),
                                     time.strftime("%b %d %H:%M", time.localtime(p.mtime))))

    # --------------------------------------------------------------- detail
    def selected(self) -> H.Project | None:
        sel = self.tree.selection()
        if not sel:
            return None
        path = Path(sel[0])
        return next((p for p in self.projects if p.path == path), None)

    def show_detail(self):
        p = self.selected()
        if not p:
            return
        lines = [
            str(p.path),
            f"{p.status_line}",
            f"{p.file_count} files, {H.human_size(p.size_bytes)}"
            + (f"   ·   {p.git.commits} commits" if p.git.commits else ""),
        ]
        if p.note:
            lines.append(f"note: {p.note}")
        if p.tests:
            lines.append("tests: " + ", ".join(t.name for t in p.tests))
        if p.git.github_slug:
            lines.append("github: https://github.com/" + p.git.github_slug)
        self._set_detail("\n".join(lines))

    def _set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def set_status(self, text):
        self.status.configure(text=text)

    # -------------------------------------------------------------- actions
    def launch_selected(self):
        p = self.selected()
        if not p:
            self.set_status("nothing selected")
            return
        cmd = H.launch_command(p)
        if not cmd:
            self.set_status(f"{p.name}: nothing to launch — {p.note or 'no entry point'}")
            return
        try:
            subprocess.Popen(cmd, cwd=str(p.path),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             if p.entry_kind != "bat" else 0)
            self.set_status(f"launched {p.name}  ({p.entry_kind}: {p.entry.name})")
        except OSError as e:
            self.set_status(f"could not launch {p.name}: {e}")

    def open_folder(self):
        p = self.selected()
        if not p:
            return
        try:
            os.startfile(str(p.path))  # noqa: S606
            self.set_status(f"opened {p.path}")
        except Exception as e:
            self.set_status(f"could not open the folder: {e}")

    def open_github(self):
        p = self.selected()
        if not p or not p.git.github_slug:
            self.set_status("this project has no github remote")
            return
        url = "https://github.com/" + p.git.github_slug
        webbrowser.open(url)
        self.set_status("opened " + url)

    def copy_path(self):
        p = self.selected()
        if not p:
            return
        self.clipboard_clear()
        self.clipboard_append(str(p.path))
        self.set_status("path copied")

    # ------------------------------------------------------------ test runs
    def run_selected_tests(self):
        p = self.selected()
        if not p:
            self.set_status("nothing selected")
            return
        if not p.tests:
            self.set_status(f"{p.name} has no tests")
            return
        self.set_status(f"running {len(p.tests)} test file(s) for {p.name}...")
        threading.Thread(target=self._run_worker, args=([p],), daemon=True).start()

    def run_all_tests(self):
        todo = [p for p in self.projects if p.tests]
        if not todo:
            self.set_status("no project here has tests")
            return
        self.set_status(f"running tests for {len(todo)} projects...")
        threading.Thread(target=self._run_worker, args=(todo,), daemon=True).start()

    def _run_worker(self, projects):
        lines, total_p, total_f = [], 0, 0
        for p in projects:
            r = H.run_tests(p)
            total_p += r["passes"]
            total_f += r["fails"]
            mark = "ok " if r["failed"] == 0 and r["ran"] else "!! "
            lines.append(f"{mark}{p.name:<26} {r['passed']}/{r['ran']} files, "
                         f"{r['passes']} passed, {r['fails']} failed")
        ok = total_f == 0
        self.after(0, lambda: self._set_detail("\n".join(lines)))
        self.after(0, lambda: self.set_status(
            f"{len(projects)} project(s): {total_p} passed, {total_f} failed"
            + ("  — all green" if ok else "  — FAILURES")))
        self.after(0, lambda: self.status.configure(foreground=GOOD if ok else BAD))


def selftest(roots, out_path=None):
    """Scan, build the whole UI, exercise it, tear it down - without showing a window.

    Needed because the packaged build is --windowed: if it fails on startup there is
    nowhere for the error to go, so the only way to know the frozen app works is to
    give it a mode that reports over stdout. It also makes the UI CI-able.
    """
    out = []
    ok = True

    def say(line):
        out.append(line)
        print(line)
        # the packaged build is --windowed and has no stdout, so results also go to a
        # file. Without this there is no way to verify the frozen app at all.
        if out_path:
            try:
                # chr(10) rather than a backslash-n escape: this line went through two
                # layers of quoting and the escape was interpreted into a real newline.
                Path(out_path).write_text(chr(10).join(out) + chr(10), encoding="utf-8")
            except OSError:
                pass

    projects = H.scan(roots)
    s = H.summarise(projects)
    say(f"  scanned {len(roots)} folder(s): {s['total']} projects, "
        f"{s['launchable']} launchable, {s['with_tests']} with tests, "
        f"{s['repos']} repos, {H.human_size(s['total_bytes'])}")
    if s["total"] == 0:
        say("  FAIL: found nothing at all")
        return 1

    hub = Hub(roots)
    hub.withdraw()
    rows = len(hub.tree.get_children())
    say(f"  window built, {rows} rows")
    ok &= rows == s["total"]

    hub.filter_var.set("a"); hub.update()
    filtered = len(hub.tree.get_children())
    hub.filter_var.set(""); hub.update()
    say(f"  filter works: 1-char filter -> {filtered} rows, cleared -> "
        f"{len(hub.tree.get_children())} rows")
    ok &= filtered <= rows

    for col in ("name", "size", "modified", "tests", "git", "entry"):
        hub.sort_col, hub.sort_desc = None, False
        hub.sort_by(col)
        hub.update()
    say("  every column sorts")
    ok &= len(hub.tree.get_children()) == rows

    kids = hub.tree.get_children()
    if kids:
        hub.tree.selection_set(kids[0]); hub.update()
        detail = hub.detail.get("1.0", "end").strip()
        say(f"  detail pane populated: {bool(detail)}")
        ok &= bool(detail)

    launchable = [p for p in projects if p.has_entry]
    say(f"  {len(launchable)} project(s) have something to launch")
    for p in projects:
        if p.has_entry and not H.launch_command(p):
            say(f"  FAIL: {p.name} has an entry but no launch command")
            ok = False

    hub.destroy()
    say("  teardown clean")
    say("  SELFTEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    roots = [Path(a) for a in args] or DEFAULT_ROOTS
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        print("  no folders to scan")
        return 1
    if "--selftest" in sys.argv:
        out = None
        for a in sys.argv:
            if a.startswith("--out="):
                out = a.split("=", 1)[1]
        return selftest(roots, out)
    Hub(roots).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
