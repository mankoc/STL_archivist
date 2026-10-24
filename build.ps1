# Build StlArchivist: the web client, the server and the desktop shell.
#
#   .\build.ps1              # release binaries in rust\target\release
#   .\build.ps1 -Installer   # also the Windows installer (needs: cargo install tauri-cli --version 2.12.3)
#
# Needs Rust (rustup) and Node.js 24 on PATH.
param([switch]$Installer)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# One build at a time: two would fight over node_modules and the cargo target
# folder. Released when the build ends (`finally`): a build run in an open
# PowerShell session would otherwise hold it until the session closes. Windows
# also releases it when the process ends, even on a crash.
$lock = [System.Threading.Mutex]::new($false, "Local\StlArchivistBuild")
if (-not $lock.WaitOne(0)) { throw "another StlArchivist build is running; wait for it to finish" }
try {
    Write-Host "== Web client"
    Push-Location "$root\web"
    # A fresh checkout gets exactly the lockfile (npm ci); an existing node_modules is
    # brought up to it in place, as npm ci would first delete it (which fails while an
    # editor or a dev server holds files there).
    if (Test-Path "node_modules") { npm install --no-fund --no-audit } else { npm ci --no-fund --no-audit }
    if ($LASTEXITCODE) { throw "npm install failed" }
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
} finally {
    $lock.ReleaseMutex()
    $lock.Dispose()
}
