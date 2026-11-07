"""The version and the changelog (CLAUDE.md, Documentation and versions).

    python tools/version.py note NOTES.md     add NOTES.md's text under "## Unreleased" in CHANGELOG.md
    python tools/version.py release [DATE]    the next version, written everywhere; "## Unreleased" becomes its section
    python tools/version.py current           the version in rust/Cargo.toml
    python tools/version.py notes VERSION     a version's changelog section (the release notes)

Commits on development only add notes; a release (the Release workflow) raises the version.
"""

import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNRELEASED = "## Unreleased"


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


def current():
    return re.search(r'^version = "([^"]+)"', read("rust/Cargo.toml"), re.M).group(1)


def next_version(version, today):
    """`<ISO year>.<ISO week>.<N>`, N counting the week's releases from 0."""
    year, week, _ = today.isocalendar()
    old_year, old_week, n = (int(part) for part in version.split("."))
    return f"{year}.{week}.{n + 1 if (old_year, old_week) == (year, week) else 0}"


def set_version(new):
    sub("rust/Cargo.toml", r'^version = "[^"]+"', f'version = "{new}"')
    sub("rust/stl-desktop/tauri.bundle.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("web/package.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("README.md", r"^Version: \S+", f"Version: {new}")
    lock = re.sub(r'("name": "stllibrary-web",\s*"version": ")[^"]+', lambda m: m.group(1) + new, read("web/package-lock.json"), count=2)
    write("web/package-lock.json", lock)


def unreleased(text):
    """The span of the Unreleased section's body in the changelog (start, end), or None."""
    m = re.search(rf"^{UNRELEASED}[ \t]*\r?\n", text, re.M)
    if not m:
        return None
    end = re.search(r"^## ", text[m.end():], re.M)
    return m.end(), m.end() + end.start() if end else len(text)


def add_note(notes):
    text = read("CHANGELOG.md")
    eol = "\r\n" if "\r\n" in text else "\n"
    notes = eol.join(notes.strip().splitlines())
    span = unreleased(text)
    if span is None:
        at = text.index("## ")
        text = f"{text[:at]}{UNRELEASED}{eol}{eol}{notes}{eol}{eol}{text[at:]}"
    else:
        body = text[span[0]:span[1]].strip()
        merged = f"{eol}{body}{eol}{notes}{eol}{eol}" if body else f"{eol}{notes}{eol}{eol}"
        text = text[:span[0]] + merged + text[span[1]:]
    write("CHANGELOG.md", text)


def release(today):
    """Raise the version and give it the Unreleased notes; returns the new version."""
    text = read("CHANGELOG.md")
    span = unreleased(text)
    if span is None or not text[span[0]:span[1]].strip():
        sys.exit("CHANGELOG.md has no notes under '## Unreleased': nothing to release")
    heading = re.search(rf"^{UNRELEASED}[ \t]*(?=\r?$)", text, re.M)
    new = next_version(current(), today)
    set_version(new)
    write("CHANGELOG.md", text[:heading.start()] + f"## {new} — {today.isoformat()}" + text[heading.end():])
    return new


def notes_of(version):
    text = read("CHANGELOG.md")
    m = re.search(rf"^## {re.escape(version)}\b.*\r?\n", text, re.M)
    if not m:
        sys.exit(f"CHANGELOG.md has no section for {version}")
    end = re.search(r"^## ", text[m.end():], re.M)
    return text[m.end():m.end() + end.start() if end else len(text)].strip()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["note"] and len(args) == 2:
        add_note(pathlib.Path(args[1]).read_text(encoding="utf-8"))
    elif args[:1] == ["release"] and len(args) <= 2:
        print(release(datetime.date.fromisoformat(args[1]) if len(args) == 2 else datetime.datetime.now(datetime.timezone.utc).date()))
    elif args == ["current"]:
        print(current())
    elif args[:1] == ["notes"] and len(args) == 2:
        print(notes_of(args[1]))
    else:
        sys.exit(__doc__)
