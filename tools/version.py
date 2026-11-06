"""Raise the version (CLAUDE.md, Documentation and versions) and add its changelog section.

    python tools/version.py NOTES.md

NOTES.md holds the section's text. The new version is printed.
"""

import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read(path):
    with open(ROOT / path, encoding="utf-8", newline="") as file:
        return file.read()


def write(path, text):
    with open(ROOT / path, "w", encoding="utf-8", newline="") as file:
        file.write(text)


def sub(path, pattern, replacement):
    text, count = re.subn(pattern, replacement, read(path), count=1, flags=re.M)
    if count != 1:
        sys.exit(f"{path}: {pattern} not found")
    write(path, text)


def next_version(version, today):
    year, week, _ = today.isocalendar()
    old_year, old_week, n = (int(part) for part in version.split("."))
    return f"{year}.{week}.{n + 1 if (old_year, old_week) == (year, week) else 0}"


def raise_version(notes, today):
    """The next version, written everywhere, with `notes` as its changelog section."""
    old = re.search(r'^version = "([^"]+)"', read("rust/Cargo.toml"), re.M).group(1)
    new = next_version(old, today)
    sub("rust/Cargo.toml", r'^version = "[^"]+"', f'version = "{new}"')
    sub("rust/stl-desktop/tauri.bundle.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("web/package.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("README.md", r"^Version: \S+", f"Version: {new}")
    lock = re.sub(r'("name": "stllibrary-web",\s*"version": ")[^"]+', lambda m: m.group(1) + new, read("web/package-lock.json"), count=2)
    write("web/package-lock.json", lock)
    text = read("CHANGELOG.md")
    eol = "\r\n" if "\r\n" in text else "\n"
    notes = eol.join(notes.strip().splitlines())
    at = text.index("## ")
    write("CHANGELOG.md", f"{text[:at]}## {new} — {today.isoformat()}{eol}{eol}{notes}{eol}{eol}{text[at:]}")
    return new


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(raise_version(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), datetime.date.today()))
