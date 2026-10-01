"""
Tests for the hub's discovery logic.

Uses a synthetic tree in a temp directory rather than the real Desktop, so the tests
are deterministic and do not depend on what happens to be on this machine today.

Note on style: no backslash escapes appear inside string literals anywhere in this
file. They were the source of a run of self-inflicted syntax errors when this file was
generated through a shell heredoc, and chr(10) is unambiguous.

RUN
    python3 test_hub_core.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hub_core as H

NL = chr(10)
passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + ("   -> " + str(extra) if extra else ""))


def rule(title):
    print("")
    print("  ---- " + title + " ----")


def touch(p: Path, content: str = "x", size: int = 0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    if size:
        with p.open("ab") as f:
            f.write(b"0" * size)
    return p


# ---------------------------------------------------------------- fixtures
tmp = Path(tempfile.mkdtemp(prefix="hubtest_"))


def build_tree():
    touch(tmp / "GoodApp" / "GoodApp.exe")
    touch(tmp / "GoodApp" / "test_good.py")
    touch(tmp / "GoodApp" / "README.md")

    touch(tmp / "OtherApp" / "something_else.exe")
    touch(tmp / "BatOnly" / "LAUNCHER.bat")
    touch(tmp / "DistApp" / "dist" / "DistApp.exe")
    touch(tmp / "WebGame" / "index.html")
    touch(tmp / "WebGame" / "styles.css")
    touch(tmp / "MultiPage" / "Other.html")
    touch(tmp / "MultiPage" / "MultiPage.html")

    touch(tmp / "GodotNoExport" / "project.godot")
    touch(tmp / "GodotNoExport" / "Godot_v4.2.2-stable_win64.exe")

    touch(tmp / "GodotExported" / "project.godot")
    touch(tmp / "GodotExported" / "Godot_v4.2.2-stable_win64.exe")
    touch(tmp / "GodotExported" / "MyGame.exe")

    touch(tmp / "InstallerOnly" / "setup_app.exe")
    touch(tmp / "NotAProject" / "notes.txt")

    touch(tmp / "WithTests" / "app.exe")
    touch(tmp / "WithTests" / "tests" / "test_one.js")
    touch(tmp / "WithTests" / "tests" / "two.test.js")

    touch(tmp / "RepoApp" / "RepoApp.exe")
    (tmp / "RepoApp" / ".git").mkdir(parents=True, exist_ok=True)

    touch(tmp / "BigApp" / "BigApp.exe")
    touch(tmp / "BigApp" / "node_modules" / "junk" / "a.js", size=200_000)
    touch(tmp / "BigApp" / "target" / "debug" / "b.bin", size=200_000)


build_tree()
projects = {p.name: p for p in H.scan([tmp], with_git=False)}
names = set(projects)
print("")
print("  discovered: " + str(sorted(names)))

# ------------------------------------------------------------ discovery set
rule("what counts as a project")
check("finds the normal apps", {"GoodApp", "OtherApp", "BatOnly"} <= names)
check("finds a project that only has a built exe in dist/", "DistApp" in names)
check("finds web games", "WebGame" in names)
check("finds Godot projects", "GodotNoExport" in names and "GodotExported" in names)
check("ignores a folder with nothing launchable", "NotAProject" not in names)
check("ignores an installer-only folder", "InstallerOnly" not in names)
check("finds a repo with no other marker", "RepoApp" in names)

# ------------------------------------------------------------ entry choice
rule("picking what launches")
p = projects["GoodApp"]
check("prefers the exe named after the folder",
      p.entry_kind == "exe" and p.entry.name == "GoodApp.exe", str(p.entry))
p = projects["OtherApp"]
check("accepts a differently named exe when there is only one",
      p.entry_kind == "exe" and p.entry.name == "something_else.exe", str(p.entry))
p = projects["BatOnly"]
check("falls back to the launcher batch file",
      p.entry_kind == "bat" and p.entry.name == "LAUNCHER.bat", str(p.entry))
p = projects["DistApp"]
check("finds a build sitting in dist/, and says so",
      p.entry_kind == "exe" and "dist" in p.note, p.note)
p = projects["WebGame"]
check("falls back to a web page",
      p.entry_kind == "html" and p.entry.name == "index.html", str(p.entry))
p = projects["MultiPage"]
check("picks the page named after the folder when there are several",
      p.entry.name == "MultiPage.html", p.entry.name)

# ------------------------------------------------------ the engine-binary bug
rule("engine binaries are not the app")
p = projects["GodotNoExport"]
check("a Godot project with no export is NOT offered the editor binary",
      p.entry is None, str(p.entry))
check("...and says why", "editor" in p.note.lower() or "exported" in p.note.lower(), p.note)
p = projects["GodotExported"]
check("an exported Godot game is found and is not the editor",
      p.entry_kind == "exe" and p.entry.name == "MyGame.exe", str(p.entry))
check("installers are never chosen", "InstallerOnly" not in projects)

# -------------------------------------------------------------- test discovery
rule("finding the tests")
p = projects["GoodApp"]
check("finds test_*.py in the root",
      len(p.tests) == 1 and p.tests[0].name == "test_good.py", str(p.tests))
p = projects["WithTests"]
check("finds tests in a tests/ subfolder", len(p.tests) == 2, str([t.name for t in p.tests]))
check("recognises *.test.js as a test file", any(t.name == "two.test.js" for t in p.tests))

# ------------------------------------------------------------------- git state
rule("repository state")
gs = H.GitState(remote="https://github.com/someone/some-repo.git")
check("parses an https github remote", gs.github_slug == "someone/some-repo", gs.github_slug)
gs = H.GitState(remote="git@github.com:someone/other.git")
check("parses an ssh github remote", gs.github_slug == "someone/other", gs.github_slug)
gs = H.GitState(remote="https://gitlab.com/x/y.git")
check("does not claim a github slug for a non-github remote", gs.github_slug == "")
check("a folder with no .git is not a repo", not H.git_state(tmp / "GoodApp").is_repo)

# ------------------------------------------------------------------- sizing
rule("sizing skips the heavy build folders")
p = projects["BigApp"]
check("node_modules and target are excluded from the size",
      p.size_bytes < 100_000, str(p.size_bytes) + " bytes - the junk leaked in")
check("the app's own files are still counted", p.size_bytes > 0)
check("file count is reported", p.file_count >= 1)
check("human_size is readable",
      H.human_size(0) == "0 B" and H.human_size(2048).endswith("KB")
      and H.human_size(5 * 1024 * 1024).endswith("MB"),
      H.human_size(0) + " / " + H.human_size(2048) + " / " + H.human_size(5 * 1024 * 1024))

# ---------------------------------------------------------------- parse results
rule("reading a test runner's output")
cases = [
    ("  RESULT  passes=36  fails=0", (36, 0)),
    ("================ 106 passed, 0 failed in 2.1s ================", (106, 0)),
    ("  12 passing (340ms)" + NL + "  1 failing", (12, 1)),
    ("  12 passing (340ms)", (12, 0)),
    ("3 passed", (3, 0)),
    ("all tests fine", None),
    ("", None),
]
for text, want in cases:
    got = H.parse_test_result(text)
    check("parses " + str(want) + " from " + repr(text[:38]), got == want, "got " + str(got))
check("a failure is never reported as clean",
      H.parse_test_result("12 passing, 1 failing") != (12, 0))

# --------------------------------------------------------------- launch commands
rule("what each entry kind runs")
cmd = H.launch_command(projects["GoodApp"])
check("an exe is run directly", bool(cmd) and cmd[0].endswith("GoodApp.exe"), str(cmd))
cmd = H.launch_command(projects["BatOnly"])
check("a batch file goes through cmd start",
      bool(cmd) and cmd[0] == "cmd" and cmd[1] == "/c", str(cmd))
cmd = H.launch_command(projects["WebGame"])
check("a web page is opened by the shell", bool(cmd) and cmd[1] == "/c", str(cmd))
check("nothing to launch means no command",
      H.launch_command(projects["GodotNoExport"]) is None)

weird = tmp / "Weird" / "test_thing.rb"
touch(weird)
check("an unknown test type is not guessed at", H.test_command(weird) is None)
check("python goes to the interpreter",
      H.test_command(tmp / "a" / "test_x.py")[0] == sys.executable)
check("javascript goes to node", H.test_command(tmp / "a" / "test_x.js")[0] == "node")

# ------------------------------------------------------------------ summary
rule("the summary")
sm = H.summarise(list(projects.values()))
check("counts the total", sm["total"] == len(projects))
check("counts what is launchable", sm["launchable"] >= 8, str(sm))
check("counts projects with tests", sm["with_tests"] >= 2, str(sm))
check("launchable never exceeds the total", sm["launchable"] <= sm["total"])

# --------------------------------------------------------------- status line
rule("the status line")
line = projects["GoodApp"].status_line
check("mentions the entry kind", "EXE" in line, line)
check("mentions the test count", "test" in line, line)
line = projects["GodotNoExport"].status_line
check("says so when there is nothing to launch", "no entry" in line.lower(), line)

# ------------------------------------------------------------- scan ordering
rule("ordering")
ordered = H.scan([tmp], with_git=False)
mtimes = [p.mtime for p in ordered]
check("results are newest first", mtimes == sorted(mtimes, reverse=True))

# ------------------------------------------------- loose executables in a root
rule("standalone executables with no folder around them")
loose_root = tmp / "toolbin"
touch(loose_root / "MyTool.exe")
touch(loose_root / "Another_Tool.exe")
touch(loose_root / "uv.exe")                  # a dev shim: not a program of its own
touch(loose_root / "browser.exe")             # same
touch(loose_root / "setup_thing.exe")         # an installer: not a program
touch(loose_root / "notes.txt")               # not an executable at all

loose = H.scan([loose_root])
byname = {p.name: p for p in loose}
check("finds a standalone executable", "MyTool" in byname, str(sorted(byname)))
check("finds them all", len([p for p in loose if p.loose]) == 2, str(len([p for p in loose if p.loose])))
check("a standalone exe is launchable",
      byname["MyTool"].entry_kind == "exe" and byname["MyTool"].entry.name == "MyTool.exe")
check("it is flagged as standalone, not a folder", byname["MyTool"].loose is True)
check("it says so", "standalone" in byname["MyTool"].note, byname["MyTool"].note)
check("dev shims are skipped", "uv" not in byname and "browser" not in byname)
check("installers are skipped", "setup_thing" not in byname)
check("non-executables are skipped", "notes" not in byname)
check("a standalone exe can be launched", bool(H.launch_command(byname["MyTool"])))

check("loose scanning can be turned off",
      len([p for p in H.scan([loose_root], include_loose=False) if p.loose]) == 0)

# a root holding both folders and executables should yield both
mixed = tmp / "mixedroot"
touch(mixed / "RealApp" / "RealApp.exe")
touch(mixed / "Loose.exe")
both = {p.name: p for p in H.scan([mixed])}
check("a root with both gives folders AND executables",
      "RealApp" in both and "Loose" in both, str(sorted(both)))
check("the folder entry is not marked loose", both["RealApp"].loose is False)
check("the executable entry is", both["Loose"].loose is True)

check("origin records which root it came from",
      both["Loose"].origin.endswith("mixedroot"), both["Loose"].origin)

shutil.rmtree(tmp, ignore_errors=True)

print("")
print("  RESULT  passes=" + str(passed) + "  fails=" + str(failed))
sys.exit(1 if failed else 0)
