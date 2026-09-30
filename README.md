# Project Hub

Everything you have built, in one window, with its real state. Nothing to maintain.

## The problem it solves

The hub it replaces was a hand-written menu. That is why four projects built in a
single afternoon were already missing from it. A menu you have to update is a menu
that is always wrong.

This one scans. Point it at a folder and it works out what each project is, what
launches it, whether it has tests, and what state its repository is in.

## Play

Double-click **`Project Hub.exe`**. No installer, no setup.

Or run **`LAUNCHER.bat`**, which falls back to Python if the exe is missing. To point
it somewhere other than the Desktop:

```
Project Hub.exe "C:\Users\RDC\Desktop" "D:\Projects"
```

## What it shows

```
Project Hub      26 projects  ·  24 launchable  ·  11 with tests  ·  4 repos  ·  2.1 GB
```

For every folder it works out:

- **What launches it** — preferring the exe named after the folder, then any exe, then
  a launcher batch file, then a build sitting in `dist/`, then a web page, then a
  script. The fallback order is what makes a folder of source and a folder of half-built
  experiments both work.
- **Whether it has tests** — `test_*.py`, `test_*.js`, `*.test.js`, in the root or in
  `tests/`.
- **Repository state** — branch, how many files are dirty, commit count, and a GitHub
  link when there is one.
- **Space actually used**, skipping `node_modules`, `target/`, `dist/` and the rest of
  the build output. A 2 MB project with a 268 MB Rust `target/` reports 2 MB.

## What you can do with it

| Action | What it does |
|---|---|
| **Double-click** a row | launches that project |
| **Filter** | matches project names, test names and entry points as you type |
| **Click any column header** | sorts; names A–Z, sizes and dates largest/newest first |
| **Run tests** | runs that project's tests |
| **Run all tests** | runs every project that has tests, and reports a total |
| **Open folder** | opens it in Explorer |
| **GitHub** | opens the repository, when the remote points at GitHub |
| **Copy path** | puts the path on the clipboard |

Test runs happen off the UI thread, so the window never freezes, and the totals come
from the runners' own output rather than a guess.

## Verifying it

```
python3 test_hub_core.py     # 51 checks: discovery, entry selection, test finding,
                             #            git parsing, sizing, output parsing
python3 hub.py --selftest    # builds the whole UI, exercises it, tears it down
```

`--selftest` exists because the packaged build is `--windowed` and has nowhere to put an
error. It scans, builds the window, filters, sorts every column, checks the detail pane,
asserts that everything with an entry point also has a launch command, and tears down.
Add `--out=FILE` to write the report somewhere the frozen app can be checked from.

## Bugs its own tests caught

**Godot projects were being offered the engine.** Three folders resolved to
`Godot_v4.2.2-stable_win64.exe` — clicking them would have opened the Godot editor, not
the game. Engine binaries are now excluded, and a Godot project with no exported build
says so instead of offering the wrong thing.

**Installer-only folders were listed as projects.** A folder holding nothing but
`setup_app.exe` appeared with no way in, which is just noise. The same filter now
applies when deciding whether a folder is a project at all.

**A test suite with a failure could read as clean.** The output parser put the passed
and failed counts in one pattern with a lazy wildcard between them, so `"12 passing"`
matched and the search stopped before reaching `"1 failing"`. Each number is now
searched for separately.

**Sorting a size column smallest-first.** Clicking "Size" now gives the biggest first,
because that is the only reason to sort by size.

## Files

```
Project Hub.exe            the hub, self-contained
LAUNCHER.bat               fallback launcher
hub_core.py                discovery, sizing, git and test-run logic - no UI
hub.py                     the window
test_hub_core.py           the test suite
```

## A note on the escaped newline

Several files in this project were generated through a shell heredoc, and a `\n` inside
a string literal was interpreted into a real newline three separate times, each
producing an unterminated-string syntax error. `test_hub_core.py` now uses `chr(10)`
and contains no backslash escapes at all, which is why it says so at the top.
