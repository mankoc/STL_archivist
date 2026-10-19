#!/usr/bin/env sh
# Build stlLibrary: the web client, the server and the desktop shell.
#   ./build.sh            release binaries in rust/target/release
#   ./build.sh --server   only the web client and the server (headless hosts)
# Needs Rust (rustup) and Node.js 24 on PATH; the desktop shell also needs the
# WebKitGTK development packages on Linux (see https://tauri.app/start/prerequisites/).
set -e
root=$(cd "$(dirname "$0")" && pwd)

echo "== Web client"
(cd "$root/web" && npm ci --no-fund --no-audit && npm run build)

echo "== Rust"
cd "$root/rust"
if [ "$1" = "--server" ]; then
  cargo build --release -p stl-server
else
  cargo build --release -p stl-server -p stl-desktop
fi
echo "Done: binaries in rust/target/release"
