#!/usr/bin/env bash
# The server's Debian package: stl-server, the web client, a systemd service and a system user.
#   packaging/deb/build-server.sh VERSION OUT_DIR
# Run from the repository root after `npm run build` (web/dist) and
# `cargo build --release -p stl-server` (rust/target/release/stl-server).
set -euo pipefail
version="$1"
out="$2"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
arch="$(dpkg --print-architecture)"
pkg="$(mktemp -d)"
trap 'rm -rf "$pkg"' EXIT

install -Dm755 "$root/rust/target/release/stl-server" "$pkg/usr/bin/stl-server"
mkdir -p "$pkg/usr/share/stlarchivist"
cp -r "$root/web/dist" "$pkg/usr/share/stlarchivist/web"
install -Dm644 "$here/stlarchivist.service" "$pkg/lib/systemd/system/stlarchivist.service"
install -Dm644 "$here/default" "$pkg/etc/default/stlarchivist"
install -Dm644 "$root/LICENSE" "$pkg/usr/share/doc/stlarchivist-server/copyright"
mkdir -p "$pkg/DEBIAN"
for script in postinst prerm postrm; do
  install -m755 "$here/$script" "$pkg/DEBIAN/$script"
done
echo /etc/default/stlarchivist > "$pkg/DEBIAN/conffiles"

# The libraries the binary links to, as dpkg-shlibdeps finds them.
work="$(mktemp -d)"
mkdir -p "$work/debian"
echo "Source: stlarchivist-server" > "$work/debian/control"
depends="$(cd "$work" && dpkg-shlibdeps -O "$pkg/usr/bin/stl-server" 2> /dev/null | sed -n 's/^shlibs:Depends=//p')"
rm -rf "$work"

size="$(du -sk --exclude=DEBIAN "$pkg" | cut -f1)"
cat > "$pkg/DEBIAN/control" <<EOF
Package: stlarchivist-server
Version: $version
Section: net
Priority: optional
Architecture: $arch
Depends: ${depends:-libc6}, adduser
Suggests: unrar
Installed-Size: $size
Maintainer: StlArchivist <noreply@github.com>
Homepage: https://github.com/mankoc/STL_archivist
Description: Catalogue of 3D-printable model archives (server)
 StlArchivist catalogues collections of 3D-printable model archives (.zip, .7z,
 .rar): metadata, thumbnails, tags, duplicates and AI tagging, in a web client
 served by this server. Runs as the stlarchivist service on port 8770.
EOF

mkdir -p "$out"
file="$out/stlarchivist-server_${version}_${arch}.deb"
dpkg-deb --root-owner-group -Zxz --build "$pkg" "$file" > /dev/null
echo "$file"
