"""Move the archive's MeshPeek crates to a release tag, for .github/workflows/meshpeek.yml.

    update_meshpeek.py current        prints the tag in use
    update_meshpeek.py set <tag>      sets it in the three manifests
    update_meshpeek.py release <tag>  raises the version and adds the changelog section

The version rule is in CLAUDE.md (Documentation and versions).
"""

import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFESTS = ["rust/stl-core/Cargo.toml", "rust/stl-server/Cargo.toml", "rust/stl-viewer/Cargo.toml"]
TAG = re.compile(r'(git = "https://github\.com/mankoc/meshpeek", tag = ")([^"]+)(")')


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


def release(tag):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    old = re.search(r'^version = "([^"]+)"', read("rust/Cargo.toml"), re.M).group(1)
    new = next_version(old, today)
    sub("rust/Cargo.toml", r'^version = "[^"]+"', f'version = "{new}"')
    sub("rust/stl-desktop/tauri.bundle.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("web/package.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("web/openapi.json", r'"version": "[^"]+"', f'"version": "{new}"')
    sub("README.md", r"^Version: \S+", f"Version: {new}")
    text = read("CHANGELOG.md")
    eol = "\r\n" if "\r\n" in text else "\n"
    section = (
        f"## {new} — {today.isoformat()}{eol}{eol}"
        f"- **The model viewer has MeshPeek {tag.lstrip('v')}'s loaders and renderer**: see its "
        f"[release notes](https://github.com/mankoc/meshpeek/releases/tag/{tag}).{eol}{eol}"
    )
    at = text.index("## ")
    write("CHANGELOG.md", text[:at] + section + text[at:])
    print(new)


def main():
    command = sys.argv[1]
    if command == "current":
        print(TAG.search(read(MANIFESTS[0])).group(2))
    elif command == "set":
        for manifest in MANIFESTS:
            text, count = TAG.subn(rf"\g<1>{sys.argv[2]}\g<3>", read(manifest))
            if count == 0:
                sys.exit(f"{manifest}: no MeshPeek tag")
            write(manifest, text)
    elif command == "release":
        release(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
