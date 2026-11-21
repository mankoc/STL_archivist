"""Move the archive's MeshPeek crates to a release tag, for .github/workflows/meshpeek.yml.

    update_meshpeek.py current        prints the tag in use
    update_meshpeek.py set <tag>      sets it in the three manifests
    update_meshpeek.py note <tag>     adds its note under "## Unreleased" in CHANGELOG.md

The version rule is in CLAUDE.md (Documentation and versions).
"""

import re
import sys

from version import add_note, read, write

MANIFESTS = ["rust/stl-core/Cargo.toml", "rust/stl-server/Cargo.toml", "rust/stl-viewer/Cargo.toml"]
TAG = re.compile(r'(git = "https://github\.com/mankoc/meshpeek", tag = ")([^"]+)(")')
VERSION_TAG = re.compile(r"v\d+\.\d+\.\d+")


def checked(tag):
    """`tag`, when it is a version tag (it goes into manifests and the changelog)."""
    if not VERSION_TAG.fullmatch(tag):
        sys.exit(f"not a version tag: {tag!r}")
    return tag


def note(tag):
    add_note(
        f"- **The model viewer has MeshPeek {tag.lstrip('v')}'s loaders and renderer**: see its "
        f"[release notes](https://github.com/mankoc/meshpeek/releases/tag/{tag})."
    )


def main():
    command = sys.argv[1]
    if command == "current":
        print(TAG.search(read(MANIFESTS[0])).group(2))
    elif command == "set":
        for manifest in MANIFESTS:
            tag = checked(sys.argv[2])
            text, count = TAG.subn(lambda m: m.group(1) + tag + m.group(3), read(manifest))
            if count == 0:
                sys.exit(f"{manifest}: no MeshPeek tag")
            write(manifest, text)
    elif command == "note":
        note(checked(sys.argv[2]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
