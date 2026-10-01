"""
Project discovery.

Pure functions over the filesystem, no UI. The point of the hub is that it never
needs maintaining: point it at a folder and it works out what each project is, what
launches it, whether it has tests, and what state its repository is in.

That is the opposite of a hand-written menu, which is what the previous hub was -
and is why four projects built in a single day were already missing from it.

RUN THE TESTS
    python3 test_hub_core.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------------ constants

# a folder is worth showing if it contains at least one of these
LAUNCH_EXT = {".exe", ".bat", ".cmd", ".html", ".py", ".lnk"}
TEST_PATTERNS = [
    re.compile(r"^test[_-].*\.(py|js|ts)$", re.I),
    re.compile(r"^.*[_-]test\.(py|js|ts)$", re.I),
    re.compile(r"^.*\.test\.(js|ts)$", re.I),
]
# a .py sitting in the root is only a launcher if it looks like one
PY_LAUNCHER_HINT = re.compile(r"launch|start|run|main", re.I)

# folders that are never projects
SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env",
    "build", "dist", "release", "target", "site-packages",
}
SKIP_PREFIXES = ("$", ".", "~")

# names that mean "this is an installer, not the app"
EXE_DENY = re.compile(
    r"unins|setup|install|vcredist|update|crashpad|"
    r"^godot[_-]?v?\d|^godot\.exe$|^godot_console\.exe$|"
    r"^python[0-9.]*\.exe$|^AutoHotkey.*\.exe$",
    re.I,
)
# development shims that are aliases of the same launcher, not programs of their own
LOOSE_DENY = re.compile(
    r"^(uv|uvx|uvw|browser|browseruse|browser-use|browser-use-tui|bu)\.exe$", re.I)

# a folder holding one of these is a game project, not a built app
PROJECT_MARKERS = {
    "project.godot": "godot",
    "package.json": "node",
    "Cargo.toml": "rust",
}


@dataclass
class GitState:
    is_repo: bool = False
    branch: str = ""
    dirty: int = 0
    remote: str = ""
    commits: int = 0

    @property
    def github_slug(self) -> str:
        """owner/repo from an https or ssh remote, else empty."""
        if not self.remote:
            return ""
        m = re.search(r"github\.com[:/]+([^/]+/[^/\s]+?)(?:\.git)?$", self.remote.strip())
        return m.group(1) if m else ""


@dataclass
class Project:
    name: str
    path: Path
    entry_kind: str = ""          # exe | bat | html | py | none
    entry: Path | None = None
    tests: list[Path] = field(default_factory=list)
    git: GitState = field(default_factory=GitState)
    mtime: float = 0.0
    size_bytes: int = 0
    file_count: int = 0
    note: str = ""                # anything worth saying, e.g. why it has no entry
    loose: bool = False           # a standalone executable seen in a scan root
    origin: str = ""              # which root it was found under

    @property
    def has_entry(self) -> bool:
        return self.entry is not None

    @property
    def status_line(self) -> str:
        bits = []
        if self.entry_kind:
            bits.append(self.entry_kind.upper())
        if self.tests:
            bits.append(f"{len(self.tests)} test file{'s' if len(self.tests) != 1 else ''}")
        if self.git.is_repo:
            b = self.git.branch or "?"
            bits.append(f"git:{b}" + (f" +{self.git.dirty}" if self.git.dirty else " clean"))
        return "  ·  ".join(bits) if bits else "no entry point found"


# ------------------------------------------------------------------ helpers

def folder_size(path: Path, cap: int = 4000) -> tuple[int, int]:
    """(bytes, files). Stops after `cap` files so a huge folder cannot hang the scan.

    Deliberately skips the same directories as the project scan: node_modules and
    target/ are the difference between a 2 MB project and a 300 MB one, and the hub
    only needs to know roughly how big a project is.
    """
    total = 0
    n = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(SKIP_PREFIXES)]
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                continue
            n += 1
            if n >= cap:
                return total, n
    return total, n


def git_state(path: Path) -> GitState:
    """Read git state without a dependency on a git library."""
    if not (path / ".git").exists():
        return GitState()
    gs = GitState(is_repo=True)

    def run(*args: str) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(path), *args],
                capture_output=True, text=True, timeout=8,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    gs.branch = run("rev-parse", "--abbrev-ref", "HEAD")
    gs.remote = run("config", "--get", "remote.origin.url")
    status = run("status", "--porcelain")
    gs.dirty = len([l for l in status.splitlines() if l.strip()]) if status else 0
    count = run("rev-list", "--count", "HEAD")
    gs.commits = int(count) if count.isdigit() else 0
    return gs


def find_tests(path: Path) -> list[Path]:
    """Test files in the root or in a tests/ folder."""
    found: list[Path] = []
    for d in (path, path / "tests", path / "test"):
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and any(p.match(f.name) for p in TEST_PATTERNS):
                found.append(f)
    return found


def project_kind(path: Path) -> str:
    """What kind of project this is, from its marker files."""
    for marker, kind in PROJECT_MARKERS.items():
        if (path / marker).exists():
            return kind
    return ""


def pick_entry(path: Path, name: str) -> tuple[str, Path | None, str]:
    """Choose what launches this project. Returns (kind, path, note).

    Order matters. A built .exe named after the folder is the real product; a
    LAUNCHER.bat is the fallback that works without it; an .html is the fallback for a
    pure web game. `dist/` is searched after the root because a PyInstaller build
    there is real but is not the thing sitting next to it.
    """
    def exes_in(d: Path) -> list[Path]:
        if not d.is_dir():
            return []
        out = []
        for f in sorted(d.glob("*.exe")):
            if not EXE_DENY.search(f.name):
                out.append(f)
        return out

    root_exes = exes_in(path)
    # prefer the one named after the folder
    exact = [e for e in root_exes if e.stem.lower() == name.lower()]
    if exact:
        return "exe", exact[0], ""
    named = [e for e in root_exes if name.lower() in e.stem.lower()]
    if named:
        return "exe", named[0], ""
    if root_exes:
        return "exe", root_exes[0], ""

    for f in sorted(path.glob("*.bat")) + sorted(path.glob("*.cmd")):
        if re.search(r"launch|start|run", f.name, re.I) or len(list(path.glob("*.bat"))) == 1:
            return "bat", f, ""

    dist_exes = exes_in(path / "dist") + exes_in(path / "release")
    if dist_exes:
        return "exe", dist_exes[0], "built copy in dist/, not next to the folder"

    # A Godot project whose only .exe is the editor: say so rather than offering to
    # launch the editor as if it were the game.
    kind = project_kind(path)
    if kind == "godot":
        exported = [f for f in path.glob("*.exe") if not EXE_DENY.search(f.name)]
        if exported:
            return "exe", exported[0], "exported build"
        return "none", None, "Godot project - no exported build, open it in the editor"
    if kind and kind != "godot":
        return "none", None, f"{kind} project - no built output"

    htmls = [f for f in sorted(path.glob("*.html"))
             if not f.name.startswith("_") and "backup" not in f.name.lower()]
    if htmls:
        exact_h = [h for h in htmls if h.stem.lower() == name.lower().replace(" ", "")]
        if exact_h:
            return "html", exact_h[0], ""
        idx = [h for h in htmls if h.stem.lower() in ("index", "game")]
        if idx:
            return "html", idx[0], ""
        return "html", max(htmls, key=lambda h: h.stat().st_size), "picked the largest page"

    for f in sorted(path.glob("*.py")):
        if PY_LAUNCHER_HINT.search(f.name) or f.stem.lower() == name.lower().replace(" ", ""):
            return "py", f, ""

    return "none", None, "no exe, launcher, page or script found"


def is_candidate(f: Path) -> bool:
    """Could this file plausibly be a way in?

    Installers and engine binaries are excluded here as well as in `pick_entry`.
    Otherwise a folder holding nothing but `setup_app.exe` gets listed as a project
    with no entry point, which is just noise in the list.
    """
    if not f.is_file():
        return False
    ext = f.suffix.lower()
    if ext not in LAUNCH_EXT:
        return False
    if ext == ".exe" and EXE_DENY.search(f.name):
        return False
    if f.name.startswith("_") or "backup" in f.name.lower():
        return False
    return True


def looks_like_project(path: Path) -> bool:
    """A folder is a project if it has a way in, or a repository."""
    if (path / ".git").exists():
        return True
    if project_kind(path):
        return True
    try:
        if any(is_candidate(f) for f in path.iterdir()):
            return True
    except OSError:
        return False
    for sub in ("dist", "release", "src"):
        d = path / sub
        if d.is_dir():
            try:
                if any(is_candidate(f) for f in d.iterdir()):
                    return True
            except OSError:
                pass
    return False


def loose_tools(root: Path, origin: str) -> list[Project]:
    """Executables sitting directly in a scan root, as projects of their own.

    Some things are built as a single .exe with no folder around them, so a scanner
    that only looks at directories misses them entirely. Anything excluded as an
    installer or a development shim is skipped here too.
    """
    out: list[Project] = []
    if not root.is_dir():
        return out
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for f in entries:
        if not f.is_file() or f.suffix.lower() != ".exe":
            continue
        if EXE_DENY.search(f.name) or LOOSE_DENY.match(f.name):
            continue
        tests: list[Path] = []
        try:
            st = f.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        out.append(Project(
            name=f.stem,
            path=root,
            entry_kind="exe",
            entry=f,
            tests=tests,
            git=GitState(),
            mtime=mtime,
            size_bytes=size,
            file_count=1,
            note="standalone executable",
            loose=True,
            origin=origin,
        ))
    return out


def scan(roots: list[Path], with_git: bool = True,
         include_loose: bool = True) -> list[Project]:
    """Find every project under the given roots, newest first.

    Looks at the folders inside each root AND at any executable sitting directly in
    the root. Without the second part, everything built as a single .exe with no
    folder of its own is invisible.
    """
    out: list[Project] = []
    seen: set[str] = set()

    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name in SKIP_DIRS or child.name.startswith(SKIP_PREFIXES):
                continue
            key = str(child.resolve()).lower()
            if key in seen:
                continue
            if not looks_like_project(child):
                continue
            seen.add(key)

            kind, entry, note = pick_entry(child, child.name)
            size, count = folder_size(child)
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0.0

            out.append(Project(
                name=child.name,
                path=child,
                entry_kind=kind if entry else "",
                entry=entry,
                tests=find_tests(child),
                git=git_state(child) if with_git else GitState(),
                mtime=mtime,
                size_bytes=size,
                file_count=count,
                note=note,
                origin=str(root),
            ))

        if include_loose:
            out.extend(loose_tools(root, str(root)))

    out.sort(key=lambda p: p.mtime, reverse=True)
    return out


def summarise(projects: list[Project]) -> dict:
    """Counts for the header."""
    return {
        "total": len(projects),
        "launchable": sum(1 for p in projects if p.has_entry),
        "with_tests": sum(1 for p in projects if p.tests),
        "repos": sum(1 for p in projects if p.git.is_repo),
        "dirty": sum(1 for p in projects if p.git.is_repo and p.git.dirty),
        "on_github": sum(1 for p in projects if p.git.github_slug),
        "total_bytes": sum(p.size_bytes for p in projects),
    }


# ------------------------------------------------------------------ test runs

def test_command(test_file: Path) -> list[str] | None:
    """The command that runs one test file, or None if we do not know how.

    Deliberately conservative: an unknown extension returns None rather than guessing
    an interpreter, because a wrong guess looks like a failing test.
    """
    ext = test_file.suffix.lower()
    if ext == ".py":
        return [sys.executable, str(test_file)]
    if ext in (".js", ".mjs"):
        return ["node", str(test_file)]
    return None


_RESULT_RE = re.compile(r"passes\s*=\s*(\d+).*?fails\s*=\s*(\d+)", re.I | re.S)
_GROUPS = [
    (re.compile(r"(\d+)\s+passed", re.I), re.compile(r"(\d+)\s+failed", re.I)),   # pytest
    (re.compile(r"(\d+)\s+passing", re.I), re.compile(r"(\d+)\s+failing", re.I)), # mocha
]


def parse_test_result(output: str) -> tuple[int, int] | None:
    """Pull (passes, fails) out of a test runner's output.

    Handles the shapes the projects on this machine actually print: a custom
    "RESULT passes=N fails=M" line, pytest's "N passed, M failed", and mocha's
    "N passing, M failing".

    Each number is searched for SEPARATELY. The first version put them in one pattern
    with a lazy wildcard between, which matched "12 passing" and stopped before it
    reached "1 failing" - so a suite with a failure was reported as clean.
    """
    if not output:
        return None
    m = _RESULT_RE.search(output)
    if m:
        return int(m.group(1)), int(m.group(2))
    for ok_re, bad_re in _GROUPS:
        mok = ok_re.search(output)
        if mok:
            mbad = bad_re.search(output)
            return int(mok.group(1)), int(mbad.group(1)) if mbad else 0
    return None


def run_tests(project: "Project", timeout: int = 120) -> dict:
    """Run every test file in a project and summarise.

    Returns a dict, never raises: a project whose tests cannot run should show that,
    not take the hub down with it.
    """
    results = []
    for tf in project.tests:
        cmd = test_command(tf)
        if cmd is None:
            results.append({"file": tf.name, "ok": None, "note": "unknown test type"})
            continue
        try:
            r = subprocess.run(
                cmd, cwd=str(project.path), capture_output=True, text=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out = (r.stdout or "") + (r.stderr or "")
            parsed = parse_test_result(out)
            results.append({
                "file": tf.name,
                "ok": (r.returncode == 0) if parsed is None else (parsed[1] == 0),
                "passes": parsed[0] if parsed else None,
                "fails": parsed[1] if parsed else None,
                "code": r.returncode,
            })
        except subprocess.TimeoutExpired:
            results.append({"file": tf.name, "ok": False, "note": f"timed out after {timeout}s"})
        except OSError as e:
            results.append({"file": tf.name, "ok": False, "note": str(e)})

    ran = [r for r in results if r.get("ok") is not None]
    return {
        "files": len(results),
        "ran": len(ran),
        "passed": sum(1 for r in ran if r["ok"]),
        "failed": sum(1 for r in ran if not r["ok"]),
        "passes": sum(r.get("passes") or 0 for r in ran),
        "fails": sum(r.get("fails") or 0 for r in ran),
        "details": results,
    }


def launch_command(p: "Project") -> list[str] | None:
    """How to start a project. None when there is nothing to start."""
    if not p.entry:
        return None
    e = p.entry
    if p.entry_kind == "exe":
        return [str(e)]
    if p.entry_kind == "bat":
        return ["cmd", "/c", "start", "", str(e)]
    if p.entry_kind == "html":
        return ["cmd", "/c", "start", "", str(e)]
    if p.entry_kind == "py":
        return [sys.executable, str(e)]
    return None


def human_size(n: int) -> str:
    """Bytes as something readable. Plain, because the first version of this was
    three chained conditional expressions and was wrong."""
    if n < 1024:
        return f"{n} B"
    v = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        v /= 1024.0
        if v < 1024 or unit == "TB":
            return f"{v:.1f} {unit}"
    return f"{v:.1f} TB"
