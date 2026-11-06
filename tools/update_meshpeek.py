"""Move the archive's MeshPeek crates to a release tag, for .github/workflows/meshpeek.yml.

    update_meshpeek.py current        prints the tag in use
    update_meshpeek.py set <tag>      sets it in the three manifests
    update_meshpeek.py release <tag>  raises the version and adds the changelog section

The version rule is in CLAUDE.md (Documentation and versions).
"""

import datetime
import re
import sys

from version import raise_version, read, write

MANIFESTS = ["rust/stl-core/Cargo.toml", "rust/stl-server/Cargo.toml", "rust/stl-viewer/Cargo.toml"]
TAG = re.compile(r'(git = "https://github\.com/mankoc/meshpeek", tag = ")([^"]+)(")')


def release(tag):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    notes = (
        f"- **The model viewer has MeshPeek {tag.lstrip('v')}'s loaders and renderer**: see its "
        f"[release notes](https://github.com/mankoc/meshpeek/releases/tag/{tag})."
    )
    print(raise_version(notes, today))


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
