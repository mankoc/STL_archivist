# Build StlArchivist: the web client, the server and the desktop shell.
#
#   .\build.ps1              # release binaries in rust\target\release
#   .\build.ps1 -Installer   # also the Windows installer (needs: cargo install tauri-cli --version 2.12.3)
#
# Needs Rust (rustup) and Node.js 24 on PATH.
param([switch]$Installer)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "== Web client"
Push-Location "$root\web"
npm ci --no-fund --no-audit
if ($LASTEXITCODE) { throw "npm ci failed" }
npm run build
if ($LASTEXITCODE) { throw "web build failed" }
Pop-Location

Write-Host "== Server and desktop shell"
Push-Location "$root\rust"
cargo build --release -p stl-server -p stl-desktop
if ($LASTEXITCODE) { throw "cargo build failed" }

if ($Installer) {
    Write-Host "== Installer"
    # tauri.bundle.json adds the server and the web client (installed beside the
    # shell, where it looks for them); kept apart so plain builds need neither.
    Push-Location "stl-desktop"
    cargo tauri build --config tauri.bundle.json
    if ($LASTEXITCODE) { throw "installer build failed" }
    Pop-Location
    Get-ChildItem "target\release\bundle\nsis\*.exe" | ForEach-Object { Write-Host "Installer: $($_.FullName)" }
}
Pop-Location
Write-Host "Done. Run rust\target\release\stl-desktop.exe"
